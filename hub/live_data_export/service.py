"""Live Data Export service — preview, sync export, background jobs, downloads."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from hub.live_data_export.demo import ensure_export_demo_table
from hub.export_engine import write_export
from hub.live_data_export.query import build_select, estimate_export_bytes
from hub.live_data_export.registry import (
    ExportSource,
    LiveExportRegistry,
    get_registry,
)
from hub.live_data_export.runner import ExportRunner
from hub.live_data_export.security import (
    ExportSafetyError,
    normalize_environment,
    sanitize_filename,
)
from hub.live_data_export.store import LiveExportStore, utcnow
from hub.sql_workspace.connections import SqlConnectionProfile, SqlConnectionRegistry
from hub.sql_workspace.demo import ensure_demo_database

log = logging.getLogger("hub.live_data_export")


class LiveDataExportService:
    def __init__(
        self,
        *,
        registry: LiveExportRegistry | None = None,
        store: LiveExportStore | None = None,
        connections: SqlConnectionRegistry | None = None,
        runner: ExportRunner | None = None,
    ) -> None:
        self.registry = registry or get_registry()
        self.store = store or LiveExportStore()
        self.connections = connections
        self.runner = runner or ExportRunner()
        self._lock = threading.Lock()
        self._ensure_demo()

    def _ensure_demo(self) -> None:
        try:
            ensure_demo_database()
            ensure_export_demo_table()
        except Exception:  # noqa: BLE001
            log.debug("Demo export table seed skipped", exc_info=True)

    def bootstrap(self) -> dict[str, Any]:
        self.store.expire_old()
        sources = [s.to_public_dict() for s in self.registry.list_sources()]
        return {
            "page_title": "Central Hub Live Data Export",
            "subtitle": "Secure exports from approved Live database sources",
            "sources": sources,
            "defaults": {
                "max_rows_sync": self.registry.defaults.max_rows_sync,
                "max_rows_hard": self.registry.defaults.max_rows_hard,
                "preview_rows": self.registry.defaults.preview_rows,
                "large_export_rows": self.registry.defaults.large_export_rows,
                "download_ttl_seconds": self.registry.defaults.download_ttl_seconds,
                "formats": list(self.registry.defaults.formats),
                "connection_by_environment": dict(
                    self.registry.defaults.connection_by_environment
                ),
            },
            "jobs": self.store.list_jobs(limit=30),
            "presets": self.store.list_presets(limit=30),
            "history": self.store.list_history(limit=30),
        }

    def list_sources(self, *, environment: str | None = None) -> list[dict[str, Any]]:
        return [s.to_public_dict() for s in self.registry.list_sources(environment=environment)]

    def preview(
        self,
        *,
        source_key: str,
        filters: dict[str, Any],
        columns: list[str] | None,
        actor: str,
    ) -> dict[str, Any]:
        env = normalize_environment(str((filters or {}).get("environment") or "live"))
        source = self.registry.require_available(source_key, environment=env)
        profile = self._resolve_profile(source, env)
        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        built = build_select(
            source,
            filters={**(filters or {}), "environment": env},
            columns=columns,
            dialect=dialect,
            for_preview=True,
            preview_limit=self.registry.defaults.preview_rows,
        )
        # Count uses identical filter scope (without preview limit)
        count_built = build_select(
            source,
            filters={**(filters or {}), "environment": env},
            columns=columns,
            dialect=dialect,
            for_preview=False,
        )
        estimated_rows = self.runner.fetch_count(profile, count_built)
        _, sample_rows = self.runner.fetch_all(profile, built)
        warnings: list[str] = []
        if source.sensitive_columns:
            warnings.append(
                "Sensitive columns are excluded by registry policy and cannot be selected."
            )
        if estimated_rows > self.registry.defaults.large_export_rows:
            warnings.append(
                f"Large result (~{estimated_rows:,} rows). Export will use a background job."
            )
        if estimated_rows > source.maximum_rows:
            warnings.append(
                f"Estimated rows exceed source maximum ({source.maximum_rows:,}); "
                "export will be capped."
            )
        fmt = "csv"
        est_bytes = estimate_export_bytes(estimated_rows, len(built.columns), format=fmt)
        public_filters = {k: v for k, v in built.filters.items() if not str(k).startswith("_")}
        self.store.add_history(
            event="preview",
            actor=actor,
            detail={
                "source_key": source_key,
                "environment": env,
                "filters": public_filters,
                "columns": built.columns,
                "estimated_rows": estimated_rows,
            },
        )
        return {
            "ok": True,
            "source_key": source_key,
            "display_name": source.display_name,
            "description": source.description,
            "filters": public_filters,
            "columns": built.columns,
            "estimated_rows": estimated_rows,
            "estimated_bytes": est_bytes,
            "sample_rows": sample_rows,
            "sample_limit": self.registry.defaults.preview_rows,
            "warnings": warnings,
            "state": "preview_ready",
        }

    def export(
        self,
        *,
        source_key: str,
        filters: dict[str, Any],
        columns: list[str] | None,
        format: str,
        actor: str,
        force_async: bool = False,
    ) -> dict[str, Any]:
        env = normalize_environment(str((filters or {}).get("environment") or "live"))
        source = self.registry.require_available(source_key, environment=env)
        fmt = str(format or "csv").lower().strip()
        if fmt not in source.supported_formats:
            raise ExportSafetyError(f"Format not supported for this source: {fmt}")
        if fmt not in ("csv", "xlsx", "csv_gz"):
            raise ExportSafetyError(f"Unsupported format: {fmt}")

        profile = self._resolve_profile(source, env)
        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        count_built = build_select(
            source,
            filters={**(filters or {}), "environment": env},
            columns=columns,
            dialect=dialect,
            for_preview=False,
        )
        estimated_rows = self.runner.fetch_count(profile, count_built)
        public_filters = {
            k: v for k, v in count_built.filters.items() if not str(k).startswith("_")
        }
        fingerprint = self._fingerprint(
            source_key, env, fmt, public_filters, count_built.columns
        )
        dup = self.store.find_active_duplicate(fingerprint)
        if dup:
            raise ExportSafetyError(
                f"Duplicate export already in progress ({dup['id']}). "
                "Cancel it or wait for completion."
            )

        ttl = self.registry.defaults.download_ttl_seconds
        job = self.store.create_job(
            source_key=source_key,
            environment=env,
            format=fmt,
            filters=public_filters,
            columns=count_built.columns,
            actor=actor,
            fingerprint=fingerprint,
            estimated_rows=estimated_rows,
            expires_at=self.store.default_expiry(ttl),
        )
        self.store.add_history(
            event="export_queued",
            actor=actor,
            job_id=job["id"],
            detail={
                "source_key": source_key,
                "environment": env,
                "format": fmt,
                "columns": count_built.columns,
                "filters": public_filters,
                "estimated_rows": estimated_rows,
            },
        )

        use_async = force_async or estimated_rows > self.registry.defaults.max_rows_sync
        if use_async:
            thread = threading.Thread(
                target=self._run_job,
                args=(job["id"],),
                name=f"live-export-{job['id']}",
                daemon=True,
            )
            thread.start()
            return {
                "ok": True,
                "mode": "async",
                "state": "export_queued",
                "job": self.store.get_job(job["id"]),
            }

        # Synchronous path for small results
        result = self._run_job(job["id"])
        return {
            "ok": result.get("status") == "ready",
            "mode": "sync",
            "state": "ready_to_download" if result.get("status") == "ready" else result.get("status"),
            "job": result,
            "error": result.get("error"),
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get_job(job_id)
        if not job:
            return None
        return self._public_job(job)

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [self._public_job(j) for j in self.store.list_jobs(limit=limit)]

    def cancel(self, job_id: str, *, actor: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise ExportSafetyError("Export job not found")
        updated = self.store.request_cancel(job_id)
        self.store.add_history(
            event="export_cancelled",
            actor=actor,
            job_id=job_id,
            detail={"reason": "Cancelled by user", "status": (updated or {}).get("status")},
        )
        assert updated is not None
        return self._public_job(updated)

    def resolve_download(
        self, job_id: str, *, token: str, actor: str
    ) -> tuple[Path, str, dict[str, Any]]:
        self.store.expire_old()
        job = self.store.get_job(job_id)
        if not job:
            raise ExportSafetyError("Export job not found")
        if job.get("download_token") != token:
            raise ExportSafetyError("Invalid download token")
        if job["status"] == "expired":
            raise ExportSafetyError("Download has expired")
        if job["status"] != "ready":
            raise ExportSafetyError(f"Export is not ready (status={job['status']})")
        expires = job.get("expires_at") or ""
        if expires and expires < utcnow():
            self.store.update_job(job_id, status="expired", message="Expired")
            raise ExportSafetyError("Download has expired")
        path = Path(job.get("file_path") or "")
        if path.is_dir():
            files = sorted(path.glob("export.*"))
            if not files:
                raise ExportSafetyError("Export file missing")
            path = files[0]
        if not path.is_file():
            raise ExportSafetyError("Export file missing")
        # Jail under artifacts root
        try:
            path.resolve().relative_to(self.store.artifacts_root.resolve())
        except ValueError as exc:
            raise ExportSafetyError("Invalid export path") from exc
        self.store.add_history(
            event="download",
            actor=actor,
            job_id=job_id,
            detail={
                "source_key": job["source_key"],
                "environment": job["environment"],
                "format": job["format"],
                "file_size": job.get("file_size"),
                "exported_rows": job.get("exported_rows"),
            },
        )
        filename = path.name
        return path, filename, self._public_job(job)

    def _run_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            return {"id": job_id, "status": "failed", "error": "Job not found"}
        if job["status"] == "cancelled":
            return self._public_job(job)

        with self._lock:
            # Re-check cancel / duplicate races lightly
            job = self.store.get_job(job_id) or job

        self.store.update_job(
            job_id,
            status="reading",
            message="Reading",
            progress_pct=5,
            started_at=utcnow(),
        )
        try:
            source = self.registry.require_available(
                job["source_key"], environment=job["environment"]
            )
            profile = self._resolve_profile(source, job["environment"])
            dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
            built = build_select(
                source,
                filters={**job["filters"], "environment": job["environment"]},
                columns=job["columns"],
                dialect=dialect,
                for_preview=False,
            )

            def _cancel() -> bool:
                current = self.store.get_job(job_id)
                return bool(current and current["status"] == "cancelled")

            if _cancel():
                return self._public_job(self.store.get_job(job_id) or job)

            columns, rows = self.runner.fetch_all(profile, built, cancel_check=_cancel)
            if _cancel():
                return self._public_job(self.store.get_job(job_id) or job)

            self.store.update_job(
                job_id,
                status="writing",
                message="Writing",
                progress_pct=70,
                exported_rows=len(rows),
            )

            art_dir = Path(job["file_path"])
            art_dir.mkdir(parents=True, exist_ok=True)
            safe = sanitize_filename(f"{source.source_key}_{job['environment']}")
            fmt = job["format"]
            suffix = {"xlsx": ".xlsx", "csv_gz": ".csv.gz", "csv": ".csv"}[fmt]
            out = art_dir / f"export_{safe}{suffix}"
            size = write_export(out, columns, rows, format=fmt)

            # Never log row contents — metadata only
            log.info(
                "live_export_ready job=%s source=%s rows=%s bytes=%s format=%s",
                job_id,
                job["source_key"],
                len(rows),
                size,
                fmt,
            )
            updated = self.store.update_job(
                job_id,
                status="ready",
                message="Ready",
                progress_pct=100,
                exported_rows=len(rows),
                file_size=size,
                file_path=str(out),
                finished_at=utcnow(),
            )
            self.store.add_history(
                event="export_ready",
                actor=job["actor"],
                job_id=job_id,
                detail={
                    "source_key": job["source_key"],
                    "environment": job["environment"],
                    "format": fmt,
                    "exported_rows": len(rows),
                    "file_size": size,
                    "estimated_rows": job.get("estimated_rows"),
                },
            )
            return self._public_job(updated or job)
        except Exception as exc:  # noqa: BLE001
            current = self.store.get_job(job_id)
            if current and current["status"] == "cancelled":
                return self._public_job(current)
            # Do not include SQL or credentials in error stored for UI
            message = str(exc)
            if "password" in message.lower() or "secret" in message.lower():
                message = "Export failed (details redacted)"
            updated = self.store.update_job(
                job_id,
                status="failed",
                message="Failed",
                error=message[:500],
                finished_at=utcnow(),
            )
            self.store.add_history(
                event="export_failed",
                actor=job["actor"],
                job_id=job_id,
                detail={"error": message[:500], "source_key": job["source_key"]},
            )
            log.warning("live_export_failed job=%s error=%s", job_id, message[:200])
            return self._public_job(updated or job)

    def _resolve_profile(
        self, source: ExportSource, environment: str
    ) -> SqlConnectionProfile:
        if self.connections is None:
            raise ExportSafetyError("SQL connections are not configured")
        env = normalize_environment(environment)
        # Stage/Live isolation: never cross-wire environments
        preferred = source.connection_id or self.registry.defaults.connection_by_environment.get(
            env, ""
        )
        if not preferred:
            raise ExportSafetyError(f"No connection mapped for environment '{env}'")
        if env == "live" and preferred == "stage-ro":
            raise ExportSafetyError("Stage connection cannot be used for Live exports")
        if env == "stage" and preferred == "live-ro":
            raise ExportSafetyError("Live connection cannot be used for Stage exports")
        try:
            profile = self.connections.get_configured(preferred)
        except LookupError as exc:
            raise ExportSafetyError(str(exc)) from exc
        # Enforce env isolation for non-demo sources
        if preferred != "local-demo" and profile.environment.lower() != env and env != "dev":
            # Allow demo connection for any env in Phase 1 when source pins local-demo
            if source.connection_id != "local-demo":
                raise ExportSafetyError(
                    f"Connection '{preferred}' environment '{profile.environment}' "
                    f"does not match requested '{env}'"
                )
        return profile

    @staticmethod
    def _fingerprint(
        source_key: str,
        environment: str,
        format: str,
        filters: dict[str, Any],
        columns: list[str],
    ) -> str:
        payload = {
            "source_key": source_key,
            "environment": environment,
            "format": format,
            "filters": filters,
            "columns": columns,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        """Strip internal paths from API responses; keep token for authorized download URL."""
        return {
            "id": job["id"],
            "source_key": job["source_key"],
            "environment": job["environment"],
            "format": job["format"],
            "status": job["status"],
            "actor": job["actor"],
            "filters": job.get("filters") or {},
            "columns": job.get("columns") or [],
            "estimated_rows": job.get("estimated_rows"),
            "exported_rows": job.get("exported_rows"),
            "file_size": job.get("file_size"),
            "download_token": job.get("download_token"),
            "expires_at": job.get("expires_at"),
            "error": job.get("error"),
            "progress_pct": job.get("progress_pct"),
            "message": job.get("message"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "state": _status_to_state(job.get("status") or ""),
        }


def _status_to_state(status: str) -> str:
    return {
        "queued": "export_queued",
        "reading": "exporting",
        "writing": "exporting",
        "ready": "ready_to_download",
        "failed": "failed",
        "cancelled": "cancelled",
        "expired": "expired",
    }.get(status, status)
