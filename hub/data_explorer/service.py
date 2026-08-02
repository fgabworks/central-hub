"""Data Explorer service — catalog, browse, lineage, inventory, export."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from hub.data_explorer.browse import build_browse_query, generate_safe_query_text
from hub.data_explorer.classifier import build_inventory, classify_object
from hub.data_explorer.config import ExplorerConfig, get_explorer_config
from hub.data_explorer.discovery import (
    CatalogSnapshot,
    ObjectMeta,
    discover_catalog,
    invalidate_catalog_cache,
)
from hub.data_explorer.lineage import build_lineage_index, lineage_for_object
from hub.data_explorer.runner import ExplorerRunner
from hub.data_explorer.security import (
    ExplorerSafetyError,
    apply_column_policies,
    column_action,
    mask_row_values,
    normalize_environment,
)
from hub.data_explorer.store import ExplorerStore
from hub.export_engine import write_export
from hub.live_data_export.demo import ensure_export_demo_table
from hub.live_data_export.service import LiveDataExportService
from hub.sql_workspace.connections import SqlConnectionProfile, SqlConnectionRegistry
from hub.sql_workspace.demo import ensure_demo_database
from hub.settings import ROOT_DIR

log = logging.getLogger("hub.data_explorer")


class DataExplorerService:
    def __init__(
        self,
        *,
        connections: SqlConnectionRegistry | None = None,
        store: ExplorerStore | None = None,
        config: ExplorerConfig | None = None,
        runner: ExplorerRunner | None = None,
        export_service: LiveDataExportService | None = None,
    ) -> None:
        self.connections = connections
        self.store = store or ExplorerStore()
        self.config = config or get_explorer_config()
        self.runner = runner or ExplorerRunner()
        # Data Explorer owns the approved-source registry, export engine, job store,
        # presets, and export history. LIVE_DATA_EXPORT remains only as a
        # compatibility alias at the Flask boundary.
        self.exports = export_service or LiveDataExportService(
            connections=connections,
            store=self.store,
        )
        self.export_registry = self.exports.registry
        self.export_store = self.exports.store
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._export_root = ROOT_DIR / "data" / "data_explorer_exports"
        self._export_root.mkdir(parents=True, exist_ok=True)
        try:
            ensure_demo_database()
            ensure_export_demo_table()
        except Exception:  # noqa: BLE001
            pass

    def bootstrap(self, *, environment: str = "dev") -> dict[str, Any]:
        env = normalize_environment(environment)
        conn_status = self._connection_status(env)
        tree = None
        inventory = None
        error = None
        if conn_status["configured"]:
            try:
                catalog = self.catalog(environment=env, force=False, actor="system")
                tree = self._tree_from_catalog(catalog)
                inventory = self.inventory(environment=env, actor="system")
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        export_boot = self.exports.bootstrap()
        return {
            "page_title": "Central Hub Data Explorer",
            "subtitle": "Inspect database sources, report lineage, and export approved data",
            "environment": env,
            "connection": conn_status,
            "defaults": {
                "page_size": self.config.defaults.page_size,
                "max_page_size": self.config.defaults.max_page_size,
                "max_export_rows": self.config.defaults.max_export_rows,
                "max_rows_sync": self.config.defaults.max_rows_sync,
                "formats": list(self.config.defaults.formats),
                "connection_by_environment": dict(
                    self.config.defaults.connection_by_environment
                ),
            },
            "tree": tree,
            "inventory": inventory,
            "favorites": self.store.list_favorites(environment=env),
            "error": error,
            "live_configured": self._connection_status("live")["configured"],
            "stage_configured": self._connection_status("stage")["configured"],
            "approved_exports": export_boot,
        }

    def catalog(
        self, *, environment: str, force: bool = False, actor: str = "owner"
    ) -> CatalogSnapshot:
        env = normalize_environment(environment)
        profile = self._resolve_profile(env)
        snap = discover_catalog(
            profile, environment=env, force=force, cfg=self.config
        )
        self.store.audit(
            event="metadata_refresh" if force else "metadata_view",
            actor=actor,
            environment=env,
            object_ref=None,
            detail={
                "connection_id": profile.id,
                "object_count": len(snap.objects),
                "schemas": snap.schemas,
            },
        )
        return snap

    def tree(self, *, environment: str, actor: str = "owner") -> dict[str, Any]:
        catalog = self.catalog(environment=environment, actor=actor)
        return self._tree_from_catalog(catalog)

    def inventory(self, *, environment: str, actor: str = "owner") -> dict[str, Any]:
        catalog = self.catalog(environment=environment, actor=actor)
        lineage = build_lineage_index(
            catalog.objects, export_registry=self.export_registry
        )
        inv = build_inventory(catalog.objects, lineage_by_object=lineage, cfg=self.config)
        inv["environment"] = normalize_environment(environment)
        inv["connection_id"] = catalog.connection_id
        inv["unresolved_hub_lineage"] = lineage.get("__unresolved__") or {}
        self.store.audit(
            event="inventory",
            actor=actor,
            environment=inv["environment"],
            object_ref=None,
            detail={"object_count": inv["object_count"], "totals": inv["totals"]},
        )
        return inv

    def object_detail(
        self, *, environment: str, schema: str, name: str, actor: str = "owner"
    ) -> dict[str, Any]:
        obj = self._require_object(environment, schema, name)
        cls = classify_object(obj, self.config)
        catalog = self.catalog(environment=environment, actor=actor)
        lineage_idx = build_lineage_index(
            catalog.objects, export_registry=self.export_registry
        )
        lin = lineage_for_object(obj, lineage_idx)
        col_actions = {c.name: column_action(c.name, self.config) for c in obj.columns}
        browse = build_browse_query(
            obj,
            columns=[c.name for c in obj.columns if column_action(c.name) != "hide"],
            limit=min(25, self.config.defaults.page_size),
            dialect="sqlite" if catalog.driver == "sqlite" else "postgres",
        )
        self.store.audit(
            event="object_view",
            actor=actor,
            environment=normalize_environment(environment),
            object_ref=obj.full_name,
            detail={"object_type": obj.object_type, "columns": len(obj.columns)},
        )
        return {
            "object": obj.to_dict(),
            "classification": cls,
            "lineage": lin,
            "column_actions": {k: v for k, v in col_actions.items() if v},
            "safe_query": generate_safe_query_text(obj, browse),
            "source_repository": self._guess_repo(cls["group"]),
        }

    def browse(
        self,
        *,
        environment: str,
        schema: str,
        name: str,
        columns: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        sort_column: str | None = None,
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int | None = None,
        actor: str = "owner",
    ) -> dict[str, Any]:
        env = normalize_environment(environment)
        obj = self._require_object(env, schema, name)
        cls = classify_object(obj, self.config)
        if cls["browse_status"] == "deny":
            raise ExplorerSafetyError("Browsing denied for this object by policy")

        profile = self._resolve_profile(env)
        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        page = max(1, int(page or 1))
        size = int(page_size or self.config.defaults.page_size)
        size = max(1, min(size, self.config.defaults.max_page_size))
        if cls["browse_status"] == "preview_only":
            size = min(size, 25)

        offset = (page - 1) * size
        q = build_browse_query(
            obj,
            columns=columns,
            filters=filters,
            sort_column=sort_column,
            sort_dir=sort_dir,
            limit=size,
            offset=offset,
            dialect=dialect,
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {"env": env, "obj": obj.full_name, "sql": q.sql, "params": q.params},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:24]
        with self._lock:
            if fingerprint in self._inflight:
                raise ExplorerSafetyError("Duplicate browse request already in progress")
            self._inflight.add(fingerprint)
        try:
            total = self.runner.fetch_count(
                profile, q.count_sql, q.params, dialect=dialect
            )
            _cols, rows = self.runner.fetch(profile, q.sql, q.params, dialect=dialect)
            _, _, actions = apply_column_policies(q.columns)
            rows = mask_row_values(q.columns, rows, actions)
        finally:
            with self._lock:
                self._inflight.discard(fingerprint)

        self.store.audit(
            event="browse",
            actor=actor,
            environment=env,
            object_ref=obj.full_name,
            detail={
                "columns": q.columns,
                "filters": filters or [],
                "page": page,
                "page_size": size,
                "row_limit": size,
                "total_rows": total,
                "returned_rows": len(rows),
            },
        )
        return {
            "ok": True,
            "object": obj.full_name,
            "columns": q.columns,
            "rows": rows,
            "page": page,
            "page_size": size,
            "total_rows": total,
            "warnings": q.warnings,
            "safe_query": generate_safe_query_text(obj, q),
            "classification": cls,
        }

    def explain(
        self, *, environment: str, schema: str, name: str, actor: str = "owner"
    ) -> dict[str, Any]:
        env = normalize_environment(environment)
        obj = self._require_object(env, schema, name)
        profile = self._resolve_profile(env)
        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        q = build_browse_query(
            obj,
            columns=[c.name for c in obj.columns if column_action(c.name) != "hide"][:20],
            limit=10,
            dialect=dialect,
        )
        explain_sql = f"EXPLAIN {q.sql}"
        cols, rows = self.runner.fetch(
            profile, explain_sql, {k: v for k, v in q.params.items()}, dialect=dialect
        )
        self.store.audit(
            event="explain",
            actor=actor,
            environment=env,
            object_ref=obj.full_name,
            detail={"columns": q.columns},
        )
        return {"ok": True, "columns": cols, "rows": rows, "sql": explain_sql}

    def export(
        self,
        *,
        environment: str,
        schema: str,
        name: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]] | None,
        format: str,
        actor: str = "owner",
        row_limit: int | None = None,
    ) -> dict[str, Any]:
        env = normalize_environment(environment)
        obj = self._require_object(env, schema, name)
        cls = classify_object(obj, self.config)
        if cls["export_status"] == "deny":
            raise ExplorerSafetyError("Export denied for this object by policy")

        fmt = str(format or "csv").lower()
        if fmt not in self.config.defaults.formats:
            raise ExplorerSafetyError(f"Unsupported format: {fmt}")

        profile = self._resolve_profile(env)
        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        limit = int(row_limit or self.config.defaults.max_export_rows)
        limit = max(1, min(limit, self.config.defaults.max_export_rows))
        if cls["export_status"] == "restricted":
            limit = min(limit, 1000)

        q = build_browse_query(
            obj,
            columns=columns,
            filters=filters,
            limit=limit,
            offset=0,
            dialect=dialect,
            for_export=True,
        )
        # Estimate
        est = self.runner.fetch_count(profile, q.count_sql, q.params, dialect=dialect)
        _cols, rows = self.runner.fetch(profile, q.sql, q.params, dialect=dialect)
        _, _, actions = apply_column_policies(q.columns, for_export=True)
        rows = mask_row_values(q.columns, rows, actions)

        out_dir = self._export_root / env
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{obj.schema}_{obj.name}".replace(".", "_")
        suffix = {"xlsx": ".xlsx", "csv_gz": ".csv.gz", "csv": ".csv"}[fmt]
        path = out_dir / f"{safe_name}{suffix}"
        size = write_export(path, q.columns, rows, format=fmt)

        # Never log row contents
        log.info(
            "data_explorer_export env=%s object=%s rows=%s bytes=%s fmt=%s",
            env,
            obj.full_name,
            len(rows),
            size,
            fmt,
        )
        self.store.audit(
            event="export",
            actor=actor,
            environment=env,
            object_ref=obj.full_name,
            detail={
                "format": fmt,
                "columns": q.columns,
                "filters": filters or [],
                "estimated_rows": est,
                "exported_rows": len(rows),
                "file_size": size,
                "warnings": q.warnings,
            },
        )
        return {
            "ok": True,
            "path": str(path),
            "filename": path.name,
            "exported_rows": len(rows),
            "estimated_rows": est,
            "file_size": size,
            "format": fmt,
            "warnings": q.warnings,
            "async": False,
            "note": (
                "Large async job path available via Live Data Export for allowlisted sources; "
                "explorer sync export capped by policy."
                if est > self.config.defaults.max_rows_sync
                else None
            ),
        }

    def refresh_metadata(self, *, environment: str, actor: str) -> dict[str, Any]:
        env = normalize_environment(environment)
        profile = self._resolve_profile(env)
        invalidate_catalog_cache(env, profile.id)
        catalog = self.catalog(environment=env, force=True, actor=actor)
        return {
            "ok": True,
            "object_count": len(catalog.objects),
            "schemas": catalog.schemas,
            "tree": self._tree_from_catalog(catalog),
        }

    def _require_object(self, environment: str, schema: str, name: str) -> ObjectMeta:
        env = normalize_environment(environment)
        catalog = discover_catalog(
            self._resolve_profile(env), environment=env, cfg=self.config
        )
        schema = schema or ("main" if catalog.driver == "sqlite" else "public")
        for obj in catalog.objects:
            if obj.name == name and (obj.schema == schema or schema in ("", obj.schema)):
                return obj
        raise ExplorerSafetyError(f"Unknown or undiscovered object: {schema}.{name}")

    def _resolve_profile(self, environment: str) -> SqlConnectionProfile:
        if self.connections is None:
            raise ExplorerSafetyError("SQL connections are not configured")
        env = normalize_environment(environment)
        preferred = self.config.defaults.connection_by_environment.get(env, "")
        if not preferred:
            raise ExplorerSafetyError(f"No connection mapped for environment '{env}'")
        if env == "live" and preferred == "stage-ro":
            raise ExplorerSafetyError("Stage connection cannot be used for Live")
        if env == "stage" and preferred == "live-ro":
            raise ExplorerSafetyError("Live connection cannot be used for Stage")
        try:
            profile = self.connections.get_configured(preferred)
        except LookupError as exc:
            raise ExplorerSafetyError(str(exc)) from exc
        if preferred != "local-demo" and profile.environment.lower() != env and env != "dev":
            raise ExplorerSafetyError(
                f"Connection '{preferred}' environment mismatch for '{env}'"
            )
        return profile

    def _connection_status(self, environment: str) -> dict[str, Any]:
        env = normalize_environment(environment)
        cid = self.config.defaults.connection_by_environment.get(env, "")
        if not self.connections or not cid:
            return {"id": cid, "configured": False, "environment": env}
        profile = self.connections.get(cid)
        if not profile:
            return {"id": cid, "configured": False, "environment": env}
        return {
            "id": cid,
            "label": profile.label,
            "configured": profile.configured,
            "missing_fields": list(profile.missing_fields),
            "environment": env,
            "driver": profile.driver,
        }

    def _tree_from_catalog(self, catalog: CatalogSnapshot) -> dict[str, Any]:
        schemas: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for obj in catalog.objects:
            bucket = schemas.setdefault(
                obj.schema, {"tables": [], "views": [], "materialized_views": []}
            )
            entry = {
                "schema": obj.schema,
                "name": obj.name,
                "full_name": obj.full_name,
                "object_type": obj.object_type,
                "estimated_rows": obj.estimated_rows,
                "classification": classify_object(obj, self.config),
            }
            if obj.object_type == "view":
                bucket["views"].append(entry)
            elif obj.object_type == "materialized_view":
                bucket["materialized_views"].append(entry)
            else:
                bucket["tables"].append(entry)
        return {
            "environment": catalog.environment,
            "connection_id": catalog.connection_id,
            "schemas": [
                {
                    "name": s,
                    "tables": schemas[s]["tables"],
                    "views": schemas[s]["views"],
                    "materialized_views": schemas[s]["materialized_views"],
                }
                for s in sorted(schemas.keys())
            ],
        }

    @staticmethod
    def _guess_repo(group: str) -> str:
        return {
            "Linelist": "live-processing",
            "Tracker": "live-processing / DHIS2",
            "Analytics": "DHIS2 / live-processing",
            "Reporting": "live-processing / data_scripts",
            "HCSC/RF": "data_scripts / DHIS2",
            "Organisation Units": "DHIS2",
            "Application/Internal": "central-hub",
        }.get(group, "unresolved")
