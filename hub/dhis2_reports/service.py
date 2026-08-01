"""DHIS2 Report Workspace service — catalog, sync, preview, generate, view."""

from __future__ import annotations

import fnmatch
import time
from pathlib import Path
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.db import utcnow
from hub.dhis2_reports.bridge import (
    build_run_catalog,
    classify_catalog_entry,
    content_fingerprint,
    is_app_shell_template,
    parse_run_report_id,
    rewrite_report_html,
    validate_proxy_path,
)
from hub.dhis2_reports.cache import (
    CATALOG_CACHE,
    CAPABILITY_CACHE,
    METADATA_CACHE,
    ORG_UNIT_CACHE,
    PERIOD_CACHE,
    RESULT_CACHE,
    result_cache_key,
)
from hub.dhis2_reports.maintenance import (
    STAGE_MAINTENANCE_MESSAGE,
    environment_availability,
)
from hub.dhis2_reports.catalog import get_report, load_report_catalog
from hub.dhis2_reports.discovery import discover_report_parameters
from hub.dhis2_reports.models import ReportDefinition, ResolvedRun
from hub.dhis2_reports.periods import periods_payload
from hub.dhis2_reports.security import (
    ReportSecurityError,
    build_dhis2_report_url,
    build_hub_standard_render_path,
    build_standard_report_data_url,
    build_standard_report_open_url,
    configured_output_roots,
    iframe_sandbox_flags,
    period_to_dhis2_date,
    prepare_credentialed_report_html,
    redact_report_detail,
    resolve_report_html,
    scrub_parameters,
    validate_environment,
    validate_org_unit,
    validate_period,
)
from hub.dhis2_reports.standard_models import (
    SyncedStandardReport,
    favorite_key,
    parse_favorite_key,
)
from hub.dhis2_reports.standard_sync import StandardReportSyncService
from hub.dhis2_reports.store import ReportsStore
from hub.registry.models import Registry
from hub.repository_workspace.git_status import RepositoryGitStatus
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.run_profiles import load_run_profiles, prepare_launch
from hub.repository_workspace.security import resolve_repo_root
from hub.repository_workspace.settings import load_workspace_settings

AuditFn = Callable[[str, str, str, bool], None]
ClientFactory = Callable[[str], Dhis2Client]


class Dhis2ReportsService:
    def __init__(
        self,
        store: ReportsStore | None = None,
        *,
        process_manager: ProcessManager | None = None,
        audit: AuditFn | None = None,
        get_dhis2_base_url: Callable[[str], str | None] | None = None,
        client_factory: ClientFactory | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.store = store or ReportsStore()
        self.processes = process_manager or ProcessManager()
        self.audit = audit
        self.get_dhis2_base_url = get_dhis2_base_url or (lambda _env: None)
        self.client_factory = client_factory
        self.registry = registry
        self.standard_sync = (
            StandardReportSyncService(
                self.store,
                client_factory=client_factory,
                audit=self._audit if audit else None,
            )
            if client_factory
            else None
        )
        self._clients: dict[str, Dhis2Client] = {}
        self._inflight: dict[str, float] = {}

    def _client(self, environment: str) -> Dhis2Client:
        env = validate_environment(environment)
        if not self.client_factory:
            raise ReportSecurityError("DHIS2 client factory unavailable.", code="unavailable")
        client = self._clients.get(env)
        if client is None:
            client = self.client_factory(env)
            self._clients[env] = client
        return client

    def invalidate_report_caches(self, *, environment: str | None = None) -> None:
        if environment:
            env = validate_environment(environment)
            CATALOG_CACHE.invalidate_prefix(f"catalog:{env}")
            RESULT_CACHE.invalidate_prefix(f"result|{env}|")
            ORG_UNIT_CACHE.invalidate_prefix(f"ou:{env}:")
            CAPABILITY_CACHE.invalidate_prefix(f"cap:{env}")
            METADATA_CACHE.invalidate_prefix(f"meta:{env}:")
            PERIOD_CACHE.clear()
            self._clients.pop(env, None)
        else:
            CATALOG_CACHE.clear()
            RESULT_CACHE.clear()
            ORG_UNIT_CACHE.clear()
            CAPABILITY_CACHE.clear()
            METADATA_CACHE.clear()
            PERIOD_CACHE.clear()
            self._clients.clear()

    def list_run_catalog(self, environment: str) -> dict[str, Any]:
        return build_run_catalog(
            store=self.store,
            environment=environment,
            favorites=set(self.store.list_favorites()),
        )

    def detect_capabilities(self, environment: str) -> dict[str, Any]:
        env = validate_environment(environment)
        cached = CAPABILITY_CACHE.get(f"cap:{env}")
        if cached is not None:
            return {**cached, "cache": "hit"}
        from hub.dhis2_reports.standard_sync import detect_report_capabilities

        try:
            caps = detect_report_capabilities(self._client(env))
        except ReportSecurityError as exc:
            caps = {"ok": False, "detail": str(exc)}
        CAPABILITY_CACHE.set(f"cap:{env}", caps)
        return {**caps, "cache": "miss"}

    # Cascade leaf for Region→…→Barangay; levels below this are treated as leaves.
    _OU_CASCADE_LEAF_LEVEL = 5
    # Lean fields — never request children::size (counts every child and times out on large trees).
    _OU_CASCADE_FIELDS = "id,displayName,level"
    _OU_SEARCH_FIELDS = "id,displayName,name,code,path,level"
    _OU_CHILDREN_FIELDS = "children[id,displayName,level]"

    def search_org_units(
        self,
        environment: str,
        *,
        q: str = "",
        limit: int = 25,
        parent_id: str = "",
        level: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Search organisation units (name/code/UID) or cascade children like Live Processing.

        Cascading mode (LP parity):
        - no parent + level=2 → regions
        - parent_id set → direct children (province/municipality/barangay)

        Stage maintenance: serve env-scoped cache (including stale) with synced_at;
        do not call Stage unless refresh=True (user-initiated, single attempt);
        never fall back to Live data or mix caches across environments.
        """
        env = validate_environment(environment)
        avail = environment_availability(env)
        needle = (q or "").strip()
        parent = validate_org_unit(parent_id, required=False) if parent_id else ""
        level_i: int | None = None
        if level is not None and str(level).strip() != "":
            try:
                level_i = int(level)
            except (TypeError, ValueError) as exc:
                raise ReportSecurityError(
                    "Organisation unit level must be an integer.",
                    code="invalid_org_unit",
                ) from exc
            if level_i < 1 or level_i > 8:
                raise ReportSecurityError(
                    "Organisation unit level out of range.",
                    code="invalid_org_unit",
                )
        cascade = bool(parent or (level_i is not None and not needle))
        limit_i = self._org_unit_page_size(
            limit=limit, cascade=cascade, level=level_i, parent=bool(parent)
        )
        cache_key = f"ou:{env}:{needle.lower()}:{parent}:{level_i}:{limit_i}"
        maintenance = bool(avail.get("maintenance"))

        if refresh:
            ORG_UNIT_CACHE.delete(cache_key)

        # Prefer env-scoped cache (fresh, or stale during Stage maintenance).
        entry = ORG_UNIT_CACHE.get_entry(cache_key, allow_stale=maintenance and not refresh)
        if entry is not None and not refresh:
            return self._ou_cache_payload(
                entry["value"],
                cache="stale" if entry["stale"] else "hit",
                synced_at=entry["synced_at"],
                availability=avail,
            )

        # Maintenance with no cache and no user refresh → do not poll Stage.
        if maintenance and not refresh:
            raise ReportSecurityError(STAGE_MAINTENANCE_MESSAGE, code="maintenance")

        client = self._client(env)
        ou_timeout = self._org_unit_timeout(client)
        try:
            if parent:
                rows = self._fetch_ou_children(
                    client, parent, limit=limit_i, timeout=ou_timeout
                )
            elif cascade and level_i is not None:
                rows = self._fetch_ou_by_level(
                    client, level=level_i, limit=limit_i, timeout=ou_timeout
                )
            else:
                rows = self._fetch_ou_search(
                    client, needle=needle, limit=limit_i, timeout=ou_timeout
                )
        except Dhis2Error as exc:
            # User-initiated refresh during maintenance: keep prior Stage cache if any.
            stale = ORG_UNIT_CACHE.get_entry(cache_key, allow_stale=True)
            if stale is not None and env == "stage":
                return self._ou_cache_payload(
                    stale["value"],
                    cache="stale",
                    synced_at=stale["synced_at"],
                    availability=avail if maintenance else {
                        **avail,
                        "status": "degraded",
                        "message": STAGE_MAINTENANCE_MESSAGE
                        if maintenance
                        else redact_report_detail(exc.message),
                    },
                )
            if maintenance:
                raise ReportSecurityError(STAGE_MAINTENANCE_MESSAGE, code="maintenance") from exc
            raise ReportSecurityError(
                redact_report_detail(exc.message),
                code="unauthorized" if exc.status_code in {401, 403} else "unavailable",
            ) from exc

        payload = {
            "ok": True,
            "environment": env,
            "q": needle,
            "parent_id": parent,
            "level": level_i,
            "org_units": rows,
            "orgunits": rows,  # LP-compatible alias
            "count": len(rows),
        }
        synced_at = ORG_UNIT_CACHE.set(cache_key, payload)
        payload["synced_at"] = synced_at
        return self._ou_cache_payload(
            payload,
            cache="miss",
            synced_at=synced_at,
            availability=environment_availability(env),
        )

    @staticmethod
    def _ou_cache_payload(
        payload: dict[str, Any],
        *,
        cache: str,
        synced_at: str | None,
        availability: dict[str, Any],
    ) -> dict[str, Any]:
        stamp = (synced_at or payload.get("synced_at") or "").strip() or None
        out = {
            **payload,
            "ok": True,
            "cache": cache,
            "synced_at": stamp,
            "environment_status": availability.get("status") or "ok",
            "maintenance": bool(availability.get("maintenance")),
            "maintenance_message": availability.get("message")
            if availability.get("maintenance")
            else None,
        }
        return out

    @staticmethod
    def _org_unit_page_size(
        *,
        limit: int,
        cascade: bool,
        level: int | None,
        parent: bool,
    ) -> int:
        """Tight page sizes — regions are few; barangays under one parent rarely need 500."""
        requested = int(limit or 25)
        if not cascade:
            return max(1, min(requested, 50))
        if parent:
            return max(1, min(requested or 200, 300))
        if level == 2:
            return max(1, min(requested or 50, 80))
        if level in {3, 4}:
            return max(1, min(requested or 100, 200))
        return max(1, min(requested or 100, 200))

    @staticmethod
    def _org_unit_timeout(client: Any) -> float:
        """Fail fast for picker UX (~5s) instead of timeout×retries (~30s)."""
        try:
            base = float(getattr(getattr(client, "settings", None), "timeout_seconds", 10) or 10)
            probe = float(
                getattr(getattr(client, "settings", None), "probe_timeout_seconds", 5) or 5
            )
        except (TypeError, ValueError):
            base, probe = 10.0, 5.0
        return max(3.0, min(base, probe, 5.0))

    def _fetch_ou_children(
        self,
        client: Any,
        parent_uid: str,
        *,
        limit: int,
        timeout: float,
    ) -> list[dict[str, Any]]:
        """Direct children via nested fields — cheaper than list+filter+children::size."""
        data = client._get_json(  # noqa: SLF001
            f"/api/organisationUnits/{parent_uid}",
            params={"fields": self._OU_CHILDREN_FIELDS},
            timeout=timeout,
            retry_max=0,
        )
        rows = self._map_org_unit_rows(data.get("children") or [])
        rows.sort(key=lambda r: (r.get("name") or "").lower())
        return rows[:limit]

    def _fetch_ou_by_level(
        self,
        client: Any,
        *,
        level: int,
        limit: int,
        timeout: float,
    ) -> list[dict[str, Any]]:
        """List OUs at a hierarchy level (regions). Prefer nested country→children."""
        # Country root + nested children is typically much faster than level:eq:2
        # across a national tree (Live: ~50ms vs ~500ms+ for level filter).
        if level == 2:
            root_params = {
                "fields": f"id,{self._OU_CHILDREN_FIELDS}",
                "paging": "true",
                "pageSize": 5,
                "page": 1,
                "filter": "level:eq:1",
            }
            try:
                root_data = client._get_json(  # noqa: SLF001
                    "/api/organisationUnits",
                    params=root_params,
                    timeout=timeout,
                    retry_max=0,
                )
                roots = root_data.get("organisationUnits") or []
                if (
                    len(roots) == 1
                    and isinstance(roots[0], dict)
                    and isinstance(roots[0].get("children"), list)
                    and roots[0]["children"]
                ):
                    rows = self._map_org_unit_rows(roots[0].get("children") or [])
                    rows.sort(key=lambda r: (r.get("name") or "").lower())
                    return rows[:limit]
            except Dhis2Error as exc:
                # Don't stack a second timeout when Stage/Live is unreachable.
                msg = (exc.message or str(exc)).lower()
                if "timed out" in msg or "could not reach" in msg:
                    raise
                # Non-timeout errors fall through to level:eq filter.

        params: dict[str, Any] = {
            "fields": self._OU_CASCADE_FIELDS,
            "paging": "true",
            "pageSize": limit,
            "page": 1,
            "order": "name:asc",
            "filter": f"level:eq:{level}",
        }
        data = client._get_json(  # noqa: SLF001
            "/api/organisationUnits",
            params=params,
            timeout=timeout,
            retry_max=0,
        )
        return self._map_org_unit_rows(data.get("organisationUnits") or [])

    def _fetch_ou_search(
        self,
        client: Any,
        *,
        needle: str,
        limit: int,
        timeout: float,
    ) -> list[dict[str, Any]]:
        """Typeahead search by name/code/UID (no children::size)."""
        params: dict[str, Any] = {
            "fields": self._OU_SEARCH_FIELDS,
            "paging": "true",
            "pageSize": limit,
            "page": 1,
            "order": "name:asc",
        }
        if needle:
            if len(needle) == 11 and needle.isalnum():
                params["filter"] = f"id:eq:{needle}"
            else:
                params["filter"] = f"identifiable:token:{needle}"
        try:
            data = client._get_json(  # noqa: SLF001
                "/api/organisationUnits",
                params=params,
                timeout=timeout,
                retry_max=0,
            )
        except Dhis2Error as exc:
            if needle and "identifiable" in str(params.get("filter") or ""):
                params["filter"] = f"name:ilike:{needle}"
                data = client._get_json(  # noqa: SLF001
                    "/api/organisationUnits",
                    params=params,
                    timeout=timeout,
                    retry_max=0,
                )
            else:
                raise exc
        return self._map_org_unit_rows(data.get("organisationUnits") or [])

    @classmethod
    def _map_org_unit_rows(cls, items: list[Any]) -> list[dict[str, Any]]:
        level_labels = {
            2: "region",
            3: "province",
            4: "municipality_city",
            5: "barangay",
        }
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            children = item.get("children")
            child_count: int | None = None
            if isinstance(children, int):
                child_count = children
            elif isinstance(children, list):
                child_count = len(children)
            try:
                level_val = int(item.get("level")) if item.get("level") is not None else None
            except (TypeError, ValueError):
                level_val = None
            if child_count is not None:
                has_children = child_count > 0
            elif level_val is None:
                has_children = True
            else:
                # Infer without children::size: cascade continues until barangay.
                has_children = level_val < cls._OU_CASCADE_LEAF_LEVEL
            uid = item.get("id") or ""
            rows.append(
                {
                    "id": uid,
                    "uid": uid,  # LP-compatible
                    "name": item.get("displayName") or item.get("name") or "",
                    "code": item.get("code") or "",
                    "path": item.get("path") or "",
                    "level": level_val,
                    "level_label": level_labels.get(level_val or -1, ""),
                    "has_children": has_children,
                }
            )
        return rows

    def list_periods(
        self,
        *,
        remembered: str = "",
        period_type: str = "quarterly",
        relative_keys: list[str] | None = None,
        environment: str = "",
    ) -> dict[str, Any]:
        env = (environment or "").strip().lower() or "shared"
        rel_key = ",".join(sorted(str(k) for k in (relative_keys or []) if k))
        cache_key = f"periods:{env}:{period_type}:{remembered}:{rel_key}"
        cached = PERIOD_CACHE.get(cache_key)
        if cached is not None:
            return {**cached, "cache": "hit"}
        payload = periods_payload(
            remembered=remembered,
            period_type=period_type,
            relative_keys=relative_keys,
        )
        payload["environment"] = env if env != "shared" else None
        PERIOD_CACHE.set(cache_key, payload)
        return {**payload, "cache": "miss"}

    def standard_detail_payload(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
    ) -> dict[str, Any]:
        """Compact summary + discovery + diagnostics for the detail page."""
        env = validate_environment(environment)
        meta_key = f"meta:{env}:{uid}"
        cached = METADATA_CACHE.get(meta_key)
        if cached is not None:
            report = cached["report"]
            discovery = cached["discovery"]
            design_len = cached.get("design_len", 0)
            meta_cache = "hit"
        else:
            report_obj = self.get_standard_report(env, uid)
            design = self.store.get_synced_design_content(env, uid)
            discovery = discover_report_parameters(report_obj, design_html=design)
            report = report_obj.to_public()
            # Align public needs_* with discovery for UI consumers.
            report["needs_period"] = discovery["period_required"]
            report["needs_org_unit"] = discovery["org_unit_required"]
            report["parameter_discovery"] = discovery
            METADATA_CACHE.set(
                meta_key,
                {
                    "report": report,
                    "discovery": discovery,
                    "design_len": len(design or ""),
                },
            )
            design_len = len(design or "")
            meta_cache = "miss"

        urls = None
        error = None
        try:
            if period or org_unit or not (
                discovery.get("period_required") or discovery.get("org_unit_required")
            ):
                urls = self.standard_urls(env, uid, period=period, org_unit=org_unit)
        except ReportSecurityError as exc:
            error = str(exc)

        return {
            "report": report,
            "discovery": discovery,
            "urls": urls,
            "error": error,
            "form": {"period": period, "org_unit": org_unit},
            "diagnostics": {
                "uid": report.get("uid"),
                "report_parameters": report.get("report_parameters"),
                "relative_periods": report.get("relative_periods"),
                "data_source": report.get("data_source"),
                "html_design_available": report.get("html_design_available"),
                "design_content_bytes": design_len,
                "discovery_sources": discovery.get("sources"),
                "open_url": (urls or {}).get("open_url") if urls else "",
                "external_embed_url": (urls or {}).get("embed_url") if urls else "",
                "meta_cache": meta_cache,
            },
            "run_report_id": f"std:{env}:{uid}",
        }

    def _audit(self, action: str, target: str, detail: str, ok: bool = True) -> None:
        if self.audit:
            self.audit(action, target, redact_report_detail(detail), ok)

    def dashboard_summary(self) -> dict[str, Any]:
        catalog = load_report_catalog()
        summary = self.store.summary()
        synced = self.store.synced_summary()
        summary["report_count"] = synced["total_synced"] or len(catalog)
        summary["total_reports"] = synced["total_synced"]
        summary["catalog_count"] = len(catalog)
        summary["stage_synced"] = synced["stage_count"]
        summary["live_synced"] = synced["live_count"]
        summary["last_sync"] = synced.get("last_sync")
        return summary

    def list_library(
        self,
        *,
        q: str = "",
        report_type: str = "",
        repository_id: str = "",
        environment: str = "",
        favorites_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Legacy catalog library (repository/static + optional YAML shortcuts)."""
        favorites = self.store.list_favorites()
        rows: list[dict[str, Any]] = []
        needle = (q or "").strip().lower()
        for report in load_report_catalog():
            if report_type and report.report_type != report_type:
                continue
            if repository_id and report.repository_id != repository_id:
                continue
            if environment and environment not in report.environments:
                continue
            fav = report.id in favorites
            if favorites_only and not fav:
                continue
            if needle:
                hay = " ".join(
                    [
                        report.name,
                        report.description,
                        report.source,
                        report.report_type,
                        report.repository_id or "",
                        " ".join(report.tags),
                    ]
                ).lower()
                if needle not in hay:
                    continue
            rows.append(
                report.to_public(
                    last_run=self.store.last_run_for(report.id),
                    favorite=fav,
                )
            )
        return rows

    def list_standard_library(
        self,
        *,
        q: str = "",
        report_type: str = "",
        environment: str = "",
        html_available: str = "",
        favorites_only: bool = False,
    ) -> dict[str, Any]:
        """Synced DHIS2 standard reports, Stage/Live kept separate."""
        favorites = self.store.list_favorites()
        html_only: bool | None = None
        if html_available in {"1", "true", "yes"}:
            html_only = True
        elif html_available in {"0", "false", "no"}:
            html_only = False

        def _section(env: str) -> dict[str, Any]:
            rows = self.store.list_synced_reports(
                environment=env,
                report_type=report_type,
                html_only=html_only,
                q=q,
                favorites_only=favorites_only,
                favorites=favorites,
            )
            last = self.store.last_sync_for(env)
            return {
                "environment": env,
                "reports": [r.to_public() for r in rows],
                "count": len(rows),
                "last_sync": last,
            }

        if environment in {"stage", "live"}:
            sections = [_section(environment)]
        else:
            sections = [_section("stage"), _section("live")]
        types = sorted(
            {
                r.report_type
                for r in self.store.list_synced_reports()
                if r.report_type
            }
        )
        return {"sections": sections, "report_types": types}

    def set_favorite(self, report_id: str, favorite: bool) -> None:
        parsed = parse_favorite_key(report_id)
        if parsed:
            env, uid = parsed
            if self.store.get_synced_report(env, uid) is None:
                raise ReportSecurityError("Report not found.", code="not_found")
        elif get_report(report_id) is None:
            raise ReportSecurityError("Report not found.", code="not_found")
        self.store.set_favorite(report_id, favorite)
        self._audit("DHIS2_REPORT_FAVORITE", report_id, f"favorite={favorite}")

    def sync_standard_reports(
        self,
        environment: str,
        *,
        confirm_live: bool = False,
        cache_design_content: bool = False,
    ) -> dict[str, Any]:
        if not self.standard_sync:
            raise ReportSecurityError(
                "Standard report sync is not configured.",
                code="unavailable",
            )
        result = self.standard_sync.sync_environment(
            environment,
            confirm_live=confirm_live,
            cache_design_content=cache_design_content,
        )
        self.invalidate_report_caches(environment=environment)
        return result

    def refresh_standard_metadata(
        self,
        environment: str,
        uid: str,
        *,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        if not self.standard_sync:
            raise ReportSecurityError(
                "Standard report sync is not configured.",
                code="unavailable",
            )
        report = self.standard_sync.refresh_one(
            environment,
            uid,
            confirm_live=confirm_live,
            cache_design_content=True,
        )
        self.invalidate_report_caches(environment=environment)
        return report.to_public()

    def get_standard_report(self, environment: str, uid: str) -> SyncedStandardReport:
        env = validate_environment(environment)
        report = self.store.get_synced_report(env, uid)
        if report is None:
            raise ReportSecurityError("Synced report not found. Run Refresh sync first.", code="not_found")
        favorites = self.store.list_favorites()
        report.favorite = favorite_key(env, uid) in favorites
        return report

    def standard_urls(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
    ) -> dict[str, Any]:
        report = self.get_standard_report(environment, uid)
        env = report.environment
        base = self.get_dhis2_base_url(env)
        if not base:
            raise ReportSecurityError(
                f"DHIS2 {env} base URL is not configured.",
                code="dhis2_unconfigured",
            )
        period_v = validate_period(period, required=False)
        ou_v = validate_org_unit(org_unit, required=False)
        if report.needs_period and not period_v:
            raise ReportSecurityError("Period is required for this report.", code="invalid_period")
        if report.needs_org_unit and not ou_v:
            raise ReportSecurityError(
                "Organisation unit UID is required for this report.",
                code="invalid_org_unit",
            )
        open_url = build_standard_report_open_url(base_url=base, uid=report.uid)
        # External DHIS2 data.html (browser must already be logged in).
        embed_url = build_standard_report_data_url(
            base_url=base,
            uid=report.uid,
            period=period_v,
            org_unit=ou_v,
        )
        return {
            "report": report.to_public(),
            "open_url": open_url,
            "embed_url": embed_url,
            "period": period_v,
            "org_unit": ou_v,
            "date": period_to_dhis2_date(period_v),
            "prefer_embed": report.render_supported,
            "fallback_hint": (
                "Hub fetches the report HTML with your configured DHIS2 credentials "
                "(.env). Open in DHIS2 only if you need the full DHIS2 Reports app UI."
            ),
        }

    def standard_viewer_payload(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
        mode: str = "embed",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        # Confirm Live before any external URL / host checks so the gate is explicit.
        if env == "live" and not confirm_live:
            # Ensure the report exists first for a clearer 404 vs confirm UX.
            self.get_standard_report(env, uid)
            raise ReportSecurityError(
                "Live report view requires explicit confirmation.",
                code="confirm_required",
            )
        urls = self.standard_urls(environment, uid, period=period, org_unit=org_unit)
        report = urls["report"]
        hub_embed_url = build_hub_standard_render_path(
            environment=env,
            uid=report["uid"],
            period=urls.get("period") or "",
            org_unit=urls.get("org_unit") or "",
            confirm_live=bool(confirm_live) if env == "live" else False,
        )
        self._audit(
            "DHIS2_REPORT_VIEW",
            report["id"],
            f"mode={mode} pe={urls.get('period')} ou={urls.get('org_unit')} via=hub_credentials",
            True,
        )
        return {
            "kind": "standard_embed" if mode != "open" else "external",
            "report": report,
            "report_name": report["name"],
            # Prefer hub-proxied HTML (Basic Auth from .env) so the browser never logs in.
            "embed_url": hub_embed_url,
            "hub_embed_url": hub_embed_url,
            "external_embed_url": urls["embed_url"],
            "open_url": urls["open_url"],
            "url": urls["open_url"],
            "period": urls["period"],
            "org_unit": urls["org_unit"],
            "date": urls["date"],
            "sandbox": iframe_sandbox_flags(allow_scripts=True),
            "allow_scripts": True,
            "prefer_embed": urls["prefer_embed"],
            "fallback_hint": urls["fallback_hint"],
            "credentials_in_url": False,
            "uses_env_credentials": True,
        }

    def render_standard_html(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
        confirm_live: bool = False,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Fetch data.html with .env credentials and prepare it for iframe display."""
        t0 = time.perf_counter()
        env = validate_environment(environment)
        cache_key = result_cache_key(
            environment=env,
            report_id=uid,
            period=period or "",
            org_unit=org_unit or "",
            output_format="html",
        )
        t_cache0 = time.perf_counter()
        if use_cache:
            cached = RESULT_CACHE.get(cache_key)
            if cached is not None:
                timings = {
                    "cache_lookup_ms": int((time.perf_counter() - t_cache0) * 1000),
                    "metadata_ms": 0,
                    "dhis2_ms": 0,
                    "html_ms": 0,
                    "total_ms": int((time.perf_counter() - t0) * 1000),
                }
                return {**cached, "cache": "hit", "timings": timings}

        t_meta0 = time.perf_counter()
        # Touch metadata (path jail / existence) before DHIS2 fetch.
        self.get_standard_report(env, uid)
        meta_ms = int((time.perf_counter() - t_meta0) * 1000)

        t_dhis0 = time.perf_counter()
        data = self.download_standard_html(
            environment,
            uid,
            period=period,
            org_unit=org_unit,
            confirm_live=confirm_live,
        )
        dhis_ms = int((time.perf_counter() - t_dhis0) * 1000)

        t_html0 = time.perf_counter()
        base = self.get_dhis2_base_url(env) or ""
        prepared = rewrite_report_html(
            data["html"],
            environment=env,
            dhis2_base=base,
            confirm_live=bool(confirm_live) if env == "live" else False,
        )
        html_ms = int((time.perf_counter() - t_html0) * 1000)
        timings = {
            "cache_lookup_ms": int((t_meta0 - t_cache0) * 1000),
            "metadata_ms": meta_ms,
            "dhis2_ms": dhis_ms,
            "html_ms": html_ms,
            "total_ms": int((time.perf_counter() - t0) * 1000),
        }
        payload = {
            **data,
            "html": prepared,
            "base_url": base,
            "uses_env_credentials": True,
            "fingerprint": content_fingerprint(prepared),
            "cache": "miss",
            "source": data.get("source") or "data.html",
            "timings": timings,
        }
        # Do not store secrets — HTML only.
        RESULT_CACHE.set(
            cache_key,
            {
                "filename": payload["filename"],
                "html": prepared,
                "report": payload["report"],
                "period": payload["period"],
                "org_unit": payload["org_unit"],
                "base_url": base,
                "uses_env_credentials": True,
                "fingerprint": payload["fingerprint"],
                "source": payload["source"],
            },
        )
        return payload

    def generate_and_view(
        self,
        report_id: str,
        *,
        environment: str,
        period: str = "",
        org_unit: str = "",
        output_format: str = "html",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        """Primary Run action: validate → render → return hub viewer URL + diagnostics."""
        import time

        started = time.perf_counter()
        env = validate_environment(environment)
        kind, id_env, token = parse_run_report_id(report_id)
        if kind == "catalog":
            report = get_report(token)
            if report is None:
                raise ReportSecurityError("Report not found.", code="not_found")
            if classify_catalog_entry(report) == "dhis2_app_shell" or is_app_shell_template(
                report.url_template
            ):
                base = self.get_dhis2_base_url(env) or ""
                open_url = build_dhis2_report_url(
                    base_url=base,
                    url_template=report.url_template or "",
                    parameters=scrub_parameters(
                        {"period": period, "orgUnit": org_unit, "format": output_format}
                    ),
                )
                return {
                    "ok": True,
                    "browser_only": True,
                    "source_type": "dhis2_app_shell",
                    "message": (
                        "This catalog entry is the DHIS2 Reports/Pivot application shell, "
                        "not an individual report. Open it in DHIS2, or pick a Native Standard Report."
                    ),
                    "open_url": open_url,
                    "viewer_url": "",
                    "diagnostics": {
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "source_type": "dhis2_app_shell",
                        "resolved_url": open_url,
                    },
                }
            # Repository / static: fall back to generate()
            gen = self.generate(
                token,
                environment=env,
                period=period,
                org_unit=org_unit,
                output_format=output_format,
                confirm_live=confirm_live,
            )
            viewer = ""
            if gen.get("output_path"):
                viewer = f"/dhis2/reports/file?run_id={gen.get('id')}"
            elif gen.get("output_url"):
                viewer = str(gen.get("output_url") or "")
            return {
                "ok": True,
                "browser_only": False,
                "source_type": classify_catalog_entry(report),
                "viewer_url": viewer,
                "open_url": viewer,
                "download_url": f"/api/dhis2/reports/runs/{gen.get('id')}/download" if gen.get("id") else "",
                "run": gen,
                "diagnostics": {
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "source_type": classify_catalog_entry(report),
                },
            }

        if kind == "native":
            if id_env != env:
                raise ReportSecurityError(
                    "Report environment does not match the selected environment.",
                    code="environment_mismatch",
                )
            uid = token
        else:
            uid = token

        report = self.get_standard_report(env, uid)
        if not report.render_supported:
            open_url = build_standard_report_open_url(
                base_url=self.get_dhis2_base_url(env) or "", uid=uid
            )
            return {
                "ok": True,
                "browser_only": True,
                "source_type": "native_standard_unsupported",
                "message": "This report type is not supported for hub HTML rendering. Open in DHIS2.",
                "open_url": open_url,
                "viewer_url": "",
                "report": report.to_public(),
                "diagnostics": {
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "report_type": report.report_type,
                },
            }

        # Deduplicate concurrent identical requests.
        dedupe = result_cache_key(
            environment=env, report_id=uid, period=period, org_unit=org_unit, output_format="html"
        )
        if dedupe in self._inflight:
            cached = RESULT_CACHE.get(dedupe)
            if cached is not None:
                viewer = build_hub_standard_render_path(
                    environment=env,
                    uid=uid,
                    period=period,
                    org_unit=org_unit,
                    confirm_live=confirm_live if env == "live" else False,
                )
                return {
                    "ok": True,
                    "browser_only": False,
                    "source_type": "native_standard",
                    "viewer_url": viewer,
                    "open_url": build_standard_report_open_url(
                        base_url=self.get_dhis2_base_url(env) or "", uid=uid
                    ),
                    "download_url": f"/api/dhis2/reports/standard/{env}/{uid}/html?download=1&rendered=1"
                    + (f"&period={period}" if period else "")
                    + (f"&org_unit={org_unit}" if org_unit else "")
                    + ("&confirm_live=1" if env == "live" and confirm_live else ""),
                    "report": cached.get("report"),
                    "cache": "hit",
                    "diagnostics": {
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "cache": "hit",
                        "endpoint": f"/api/reports/{uid}/data.html",
                    },
                }

        self._inflight[dedupe] = time.time()
        try:
            rendered = self.render_standard_html(
                env,
                uid,
                period=period,
                org_unit=org_unit,
                confirm_live=confirm_live,
                use_cache=True,
            )
        finally:
            self._inflight.pop(dedupe, None)

        viewer = build_hub_standard_render_path(
            environment=env,
            uid=uid,
            period=period,
            org_unit=org_unit,
            confirm_live=confirm_live if env == "live" else False,
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        self._audit(
            "DHIS2_REPORT_GENERATE",
            report.id,
            f"generate_and_view pe={period} ou={org_unit} ms={elapsed} cache={rendered.get('cache')}",
            True,
        )
        return {
            "ok": True,
            "browser_only": False,
            "source_type": "native_standard",
            "viewer_url": viewer,
            "open_url": build_standard_report_open_url(
                base_url=self.get_dhis2_base_url(env) or "", uid=uid
            ),
            "download_url": (
                f"/api/dhis2/reports/standard/{env}/{uid}/html?download=1&rendered=1"
                + (f"&period={period}" if period else "")
                + (f"&org_unit={org_unit}" if org_unit else "")
                + ("&confirm_live=1" if env == "live" and confirm_live else "")
            ),
            "report": rendered.get("report"),
            "cache": rendered.get("cache"),
            "fingerprint": rendered.get("fingerprint"),
            "diagnostics": {
                "elapsed_ms": elapsed,
                "cache": rendered.get("cache"),
                "endpoint": f"/api/reports/{uid}/data.html",
                "period": rendered.get("period"),
                "org_unit": rendered.get("org_unit"),
                "fingerprint": rendered.get("fingerprint"),
                "source": rendered.get("source"),
                "timings": rendered.get("timings") or {},
                "warnings": [],
            },
        }

    def proxy_dhis2_asset(
        self,
        environment: str,
        path: str,
        *,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        """Credentialed GET of an allowlisted DHIS2 path (never expose auth to browser)."""
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            # Assets for a Live view that already confirmed can pass confirm_live=1 on query.
            # For safety, allow proxy only when confirm was given OR when Stage.
            raise ReportSecurityError(
                "Live asset proxy requires confirmation.",
                code="confirm_required",
            )
        safe = validate_proxy_path(path)
        client = self._client(env)
        try:
            # Split query for requests params
            from urllib.parse import parse_qsl, urlparse

            parsed = urlparse(safe)
            params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            raw = client._get_bytes(  # noqa: SLF001
                parsed.path,
                params=params or None,
                accept="*/*",
                max_bytes=8_000_000,
            )
        except Dhis2Error as exc:
            raise ReportSecurityError(
                redact_report_detail(exc.message),
                code="unauthorized" if exc.status_code in {401, 403} else "unavailable",
            ) from exc
        # Guess content type lightly
        lower = parsed.path.lower()
        if lower.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif lower.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif lower.endswith(".png"):
            ctype = "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            ctype = "image/jpeg"
        elif lower.endswith(".svg"):
            ctype = "image/svg+xml"
        elif lower.endswith((".html", ".htm")):
            ctype = "text/html; charset=utf-8"
        else:
            ctype = "application/octet-stream"
        return {"content": raw, "content_type": ctype, "path": safe}

    def fetch_standard_html_source(
        self,
        environment: str,
        uid: str,
        *,
        confirm_live: bool = False,
        prefer_design: bool = True,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live HTML source fetch requires explicit confirmation.",
                code="confirm_required",
            )
        report = self.get_standard_report(env, uid)
        html = ""
        source = "cache"
        if prefer_design:
            html = self.store.get_synced_design_content(env, uid)
        if not html and self.client_factory:
            client = self.client_factory(env)
            try:
                detail = client.get_metadata_object(
                    "reports",
                    uid,
                    fields="id,name,type,designContent",
                )
                raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
                html = str(raw.get("designContent") or "")
                source = "api_designContent"
                if html:
                    self.store.upsert_synced_report(report, design_content=html)
            except Dhis2Error as exc:
                raise ReportSecurityError(
                    redact_report_detail(exc.message),
                    code="unauthorized" if exc.status_code in {401, 403} else "unavailable",
                ) from exc
        if not html:
            raise ReportSecurityError(
                "No HTML design content available for this report.",
                code="missing_output",
            )
        self._audit("DHIS2_REPORT_VIEW_HTML", report.id, f"source={source}", True)
        return {
            "ok": True,
            "uid": uid,
            "environment": env,
            "name": report.name,
            "source": source,
            "html": html,
            "content_type": "text/html; charset=utf-8",
        }

    def download_standard_html(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        """GET /api/reports/{uid}/data.html via hub credentials (never exposed to browser).

        For HTML-type reports, falls back to cached/API designContent when data.html
        fails (common when Stage is slow, returns 400, or the report is design-only).
        """
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live HTML download requires explicit confirmation.",
                code="confirm_required",
            )
        if not self.client_factory:
            raise ReportSecurityError("DHIS2 client factory unavailable.", code="unavailable")
        report = self.get_standard_report(env, uid)
        period_v = validate_period(period, required=report.needs_period)
        ou_v = validate_org_unit(org_unit, required=report.needs_org_unit)
        params: dict[str, str] = {}
        date = period_to_dhis2_date(period_v)
        if date:
            params["date"] = date
        if period_v and period_v != date:
            # Some DHIS2 builds still honor pe= alongside date=.
            params.setdefault("pe", period_v)
        if ou_v:
            params["ou"] = ou_v
        client = self._client(env)
        html = ""
        source = "data.html"
        data_error: str | None = None
        try:
            html = client.get_text(
                f"/api/reports/{uid}/data.html",
                params=params or None,
                accept="text/html, */*",
                timeout=60,
            )
        except Dhis2Error as exc:
            data_error = redact_report_detail(exc.message)
            self._audit("DHIS2_REPORT_DOWNLOAD", report.id, data_error, False)
            html = self._fallback_html_design(env, uid, report)
            if html:
                source = "designContent_fallback"
            else:
                code = "unauthorized" if exc.status_code in {401, 403} else "unavailable"
                if exc.status_code == 404:
                    code = "not_found"
                if exc.status_code == 400:
                    code = "dhis2_bad_request"
                raise ReportSecurityError(data_error, code=code) from exc

        if not (html or "").strip():
            html = self._fallback_html_design(env, uid, report)
            source = "designContent_fallback" if html else source
        if not (html or "").strip():
            raise ReportSecurityError(
                data_error or "DHIS2 returned empty report HTML.",
                code="missing_output",
            )

        # Redact accidental secrets in body for audit only; return HTML for download.
        if any(s in html.lower() for s in ("password=", "authorization:", "bearer ")):
            raise ReportSecurityError(
                "Refusing to return HTML that appears to contain secrets.",
                code="secret_blocked",
            )
        self._audit(
            "DHIS2_REPORT_DOWNLOAD",
            report.id,
            f"{source} pe={period_v} ou={ou_v}",
            True,
        )
        filename = f"{report.uid}-{env}.html"
        return {
            "filename": filename,
            "html": html,
            "report": report.to_public(),
            "period": period_v,
            "org_unit": ou_v,
            "source": source,
        }

    def _fallback_html_design(self, environment: str, uid: str, report: Any) -> str:
        """Use synced/API designContent when data.html is unavailable."""
        rtype = str(getattr(report, "report_type", "") or "").upper()
        allow = rtype in {"HTML", ""} or bool(getattr(report, "html_design_available", False))
        if not allow:
            return ""
        html = self.store.get_synced_design_content(environment, uid)
        if html and html.strip():
            return html
        if not self.client_factory:
            return ""
        try:
            detail = self.client_factory(environment).get_metadata_object(
                "reports",
                uid,
                fields="id,name,type,designContent",
            )
            raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
            html = str(raw.get("designContent") or "")
            if html.strip():
                self.store.upsert_synced_report(report, design_content=html)
            return html
        except Exception:
            return ""


    def _collect_params(
        self,
        report: ReportDefinition,
        *,
        environment: str,
        period: str,
        org_unit: str,
        parameters: dict[str, Any] | None,
        output_format: str,
    ) -> dict[str, str]:
        env = validate_environment(environment)
        if env not in report.environments:
            raise ReportSecurityError(
                f"Report does not support environment {env}.",
                code="environment_blocked",
            )
        params = scrub_parameters(parameters)
        # Map period / org unit
        needs_period = any(
            (p.name == "period" or p.param_type == "period") and p.required
            for p in report.parameters
        )
        needs_ou = any(
            (p.name in {"orgUnit", "org_unit", "ou"} or p.param_type == "org_unit") and p.required
            for p in report.parameters
        )
        period_v = validate_period(period or params.get("period", ""), required=needs_period)
        ou_v = validate_org_unit(
            org_unit or params.get("orgUnit") or params.get("org_unit") or params.get("ou", ""),
            required=needs_ou,
        )
        if period_v:
            params["period"] = period_v
        if ou_v:
            params["orgUnit"] = ou_v
            params["org_unit"] = ou_v
            params.setdefault("ou", ou_v)
        params["format"] = (output_format or "html").strip().lower() or "html"
        params["report_id"] = report.id
        # Required custom params
        for spec in report.parameters:
            if spec.required and not params.get(spec.name) and not spec.default:
                if spec.param_type == "period" and period_v:
                    continue
                if spec.param_type == "org_unit" and ou_v:
                    continue
                raise ReportSecurityError(
                    f"Parameter {spec.name} is required.", code="missing_parameter"
                )
            if spec.name not in params and spec.default:
                params[spec.name] = spec.default
            if spec.choices and params.get(spec.name) and params[spec.name] not in spec.choices:
                raise ReportSecurityError(
                    f"Invalid choice for {spec.name}.", code="invalid_parameter"
                )
        return params

    def preview(
        self,
        report_id: str,
        *,
        environment: str,
        period: str = "",
        org_unit: str = "",
        parameters: dict[str, Any] | None = None,
        output_format: str = "html",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        report = get_report(report_id)
        if report is None:
            raise ReportSecurityError("Report not found.", code="not_found")
        env = validate_environment(environment)
        params = self._collect_params(
            report,
            environment=env,
            period=period,
            org_unit=org_unit,
            parameters=parameters,
            output_format=output_format,
        )
        resolved = ResolvedRun(
            report_id=report.id,
            environment=env,
            parameters=params,
            output_format=params.get("format", "html"),
            live_confirm_required=env == "live",
        )
        if env == "live" and not confirm_live:
            resolved.warnings.append("Live report runs require explicit confirmation.")

        if report.report_type == "dhis2_standard":
            base = self.get_dhis2_base_url(env) or ""
            resolved.resolved_url = build_dhis2_report_url(
                base_url=base,
                url_template=report.url_template or "",
                parameters=params,
            )
        elif report.report_type == "repository_html":
            if report.run_profile_id:
                profiles = {p.id: p for p in load_run_profiles()}
                profile = profiles.get(report.run_profile_id)
                if profile is None:
                    raise ReportSecurityError("Run profile not found.", code="not_found")
                resolved.command_preview = [profile.executable, *list(profile.args)]
            elif report.capability_id:
                resolved.command_preview = [
                    "hub-job",
                    report.repository_id or "",
                    report.capability_id,
                ]
            resolved.warnings.append(
                "Repository HTML generation uses approved profiles/capabilities only; "
                "calculation logic stays in the connected repository."
            )
        elif report.report_type == "static_html":
            path = self._resolve_static(report)
            resolved.resolved_url = f"/dhis2/reports/view?path={path.name}"
            resolved.warnings.append(f"Static file: {path.name}")

        self._audit(
            "DHIS2_REPORT_PREVIEW",
            report.id,
            f"env={env} type={report.report_type}",
        )
        return {
            "report": report.to_public(favorite=report.id in self.store.list_favorites()),
            "resolved": {
                "environment": resolved.environment,
                "parameters": resolved.parameters,
                "output_format": resolved.output_format,
                "resolved_url": resolved.resolved_url,
                "command_preview": resolved.command_preview,
                "warnings": resolved.warnings,
                "live_confirm_required": resolved.live_confirm_required,
            },
        }

    def _resolve_static(self, report: ReportDefinition) -> Path:
        roots = configured_output_roots(list(report.output_roots))
        local = None
        if report.repository_id and self.registry:
            repo = self.registry.get(report.repository_id)
            if repo:
                local = repo.local_path or repo.working_directory
        return resolve_report_html(
            report.static_relative_path or "",
            roots=roots,
            repository_id=report.repository_id,
            registry_local_path=local,
        )

    def _git_meta(self, repository_id: str | None) -> tuple[str, str]:
        if not repository_id or not self.registry:
            return "", ""
        repo = self.registry.get(repository_id)
        if not repo:
            return "", ""
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        if root is None:
            return "", ""
        try:
            git = RepositoryGitStatus(root, load_workspace_settings())
            summary = git.summary()
            return str(summary.get("branch") or ""), ""
        except Exception:  # noqa: BLE001
            return "", ""

    def generate(
        self,
        report_id: str,
        *,
        environment: str,
        period: str = "",
        org_unit: str = "",
        parameters: dict[str, Any] | None = None,
        output_format: str = "html",
        confirm_live: bool = False,
        actor: str = "",
        job_store: Any | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            report_id,
            environment=environment,
            period=period,
            org_unit=org_unit,
            parameters=parameters,
            output_format=output_format,
            confirm_live=confirm_live,
        )
        report = get_report(report_id)
        assert report is not None
        env = preview["resolved"]["environment"]
        params = preview["resolved"]["parameters"]
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live report runs require explicit confirmation.",
                code="confirm_required",
            )

        branch, commit = self._git_meta(report.repository_id)
        run = self.store.create_run(
            {
                "report_id": report.id,
                "report_name": report.name,
                "report_type": report.report_type,
                "environment": env,
                "period": params.get("period", ""),
                "org_unit": params.get("orgUnit", ""),
                "parameters": params,
                "repository_id": report.repository_id or "",
                "git_branch": branch,
                "git_commit": commit,
                "status": "running",
                "run_profile_id": report.run_profile_id or "",
                "actor": actor,
            }
        )

        try:
            if report.report_type == "dhis2_standard":
                url = preview["resolved"]["resolved_url"]
                self.store.update_run(
                    run["id"],
                    status="completed",
                    output_url=url or "",
                    finished_at=utcnow(),
                )
                self._audit(
                    "DHIS2_REPORT_GENERATE",
                    report.id,
                    f"type=dhis2_standard env={env} run={run['id']}",
                    True,
                )
            elif report.report_type == "static_html":
                path = self._resolve_static(report)
                self.store.update_run(
                    run["id"],
                    status="completed",
                    output_path=str(path),
                    finished_at=utcnow(),
                )
                self._audit(
                    "DHIS2_REPORT_GENERATE",
                    report.id,
                    f"type=static_html env={env} run={run['id']}",
                    True,
                )
            elif report.report_type == "repository_html":
                self._generate_repository_html(
                    report, run_id=run["id"], env=env, params=params, job_store=job_store, actor=actor
                )
            else:
                raise ReportSecurityError("Unsupported report type.", code="invalid_type")
        except ReportSecurityError as exc:
            self.store.update_run(
                run["id"],
                status="failed",
                error=str(exc),
                finished_at=utcnow(),
            )
            self._audit("DHIS2_REPORT_FAIL", report.id, f"run={run['id']} {exc}", False)
            raise

        return self.store.get_run(run["id"]) or run

    def _generate_repository_html(
        self,
        report: ReportDefinition,
        *,
        run_id: str,
        env: str,
        params: dict[str, str],
        job_store: Any | None,
        actor: str,
    ) -> None:
        # Prefer hub job capability when available
        if report.capability_id and report.repository_id and job_store is not None and self.registry:
            repo = self.registry.get(report.repository_id)
            if repo is None:
                raise ReportSecurityError("Repository not found.", code="not_found")
            if not any(c.id == report.capability_id for c in repo.capabilities):
                raise ReportSecurityError(
                    "Report capability is not registered on the repository.",
                    code="capability_missing",
                )
            job = job_store.create(
                repository_id=report.repository_id,
                capability_id=report.capability_id,
                dry_run=False,
                confirmed=True,
                actor=actor or "system",
                metadata={"report_id": report.id, "environment": env},
            )
            self.store.update_run(run_id, hub_job_id=job["id"], status="running")
            self._audit(
                "DHIS2_REPORT_GENERATE",
                report.id,
                f"type=repository_html job={job['id']} env={env}",
                True,
            )
            # Job worker runs async; mark completed when artifacts appear is left to refresh
            self.store.update_run(
                run_id,
                status="running",
                log_text=f"Submitted hub job {job['id']}",
            )
            return

        if not report.run_profile_id or not report.repository_id:
            raise ReportSecurityError(
                "repository_html requires an approved run profile or capability.",
                code="invalid_catalog",
            )
        if not self.registry:
            raise ReportSecurityError("Registry unavailable.", code="unavailable")
        repo = self.registry.get(report.repository_id)
        if repo is None:
            raise ReportSecurityError("Repository not found.", code="not_found")
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        if root is None:
            raise ReportSecurityError(
                "Repository local path unavailable for report generation.",
                code="unavailable",
            )
        profiles = {p.id: p for p in load_run_profiles()}
        profile = profiles.get(report.run_profile_id)
        if profile is None or not profile.applies_to(report.repository_id):
            raise ReportSecurityError("Run profile not allowed for repository.", code="profile_scope")

        port = profile.default_port
        launch = prepare_launch(
            profile,
            repo_id=report.repository_id,
            repository_path=root,
            environment="development" if env == "stage" else env,
            port=port,
            confirm_live=(env == "live"),
        )
        managed = self.processes.start(repo_id=report.repository_id, launch=launch)
        self.store.update_run(
            run_id,
            status="running",
            log_text=f"Started profile {profile.id} pid={managed.pid}",
        )
        # Poll briefly for HTML output under repo / configured roots
        deadline = time.time() + min(float(profile.startup_timeout_seconds) + 15, 120)
        found: Path | None = None
        roots = configured_output_roots(list(report.output_roots)) + [root]
        while time.time() < deadline:
            for base in roots:
                try:
                    for path in base.rglob("*"):
                        if not path.is_file():
                            continue
                        if not fnmatch.fnmatch(path.name.lower(), report.output_glob.lower()):
                            continue
                        # Prefer recently modified files
                        if path.stat().st_mtime >= time.time() - 180:
                            found = path
                            break
                except OSError:
                    continue
                if found:
                    break
            if found:
                break
            time.sleep(0.5)

        # Stop the managed process after generation attempt
        try:
            self.processes.stop(managed.run_id, reason="report generation finished")
        except Exception:  # noqa: BLE001
            pass

        if found is None:
            self.store.update_run(
                run_id,
                status="missing_output",
                error="Generation finished but no matching HTML output was found.",
                finished_at=utcnow(),
            )
            self._audit("DHIS2_REPORT_FAIL", report.id, f"run={run_id} missing_output", False)
            raise ReportSecurityError(
                "No HTML output found after generation.", code="missing_output"
            )

        self.store.update_run(
            run_id,
            status="completed",
            output_path=str(found),
            finished_at=utcnow(),
            log_text=f"Output {found.name}",
        )
        self._audit(
            "DHIS2_REPORT_GENERATE",
            report.id,
            f"type=repository_html env={env} run={run_id}",
            True,
        )

    def open_output(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ReportSecurityError("Run not found.", code="not_found")
        if run.get("output_url"):
            return {"kind": "url", "url": run["output_url"], "run": run}
        if run.get("output_path"):
            path = resolve_report_html(run["output_path"], roots=configured_output_roots())
            return {"kind": "file", "path": str(path), "run": run}
        raise ReportSecurityError("No output available for this run.", code="missing_output")

    def viewer_payload(self, *, path: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        allow_scripts = False
        report_name = "Report"
        if run_id:
            run = self.store.get_run(run_id)
            if run is None:
                raise ReportSecurityError("Run not found.", code="not_found")
            report = get_report(run["report_id"])
            allow_scripts = bool(report.allow_scripts) if report else False
            report_name = run.get("report_name") or report_name
            if run.get("output_url") and not run.get("output_path"):
                return {
                    "kind": "external",
                    "url": run["output_url"],
                    "report_name": report_name,
                    "sandbox": iframe_sandbox_flags(allow_scripts=False),
                    "run": run,
                }
            path = run.get("output_path")
        if not path:
            raise ReportSecurityError("Output path required.", code="missing_output")
        file_path = resolve_report_html(path, roots=configured_output_roots())
        try:
            html = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ReportSecurityError("Unable to read report HTML.", code="read_failed") from exc
        return {
            "kind": "html",
            "path": str(file_path),
            "name": file_path.name,
            "report_name": report_name,
            "html": html,
            "sandbox": iframe_sandbox_flags(allow_scripts=allow_scripts),
            "allow_scripts": allow_scripts,
        }
