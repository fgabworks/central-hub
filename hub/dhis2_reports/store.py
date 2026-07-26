"""Presets, favorites, and run history for DHIS2 Report Workspace."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hub.dhis2_reports.db import ReportsDatabase, utcnow
from hub.dhis2_reports.security import redact_report_detail, scrub_parameters


class ReportsStore:
    def __init__(self, db: ReportsDatabase | None = None) -> None:
        self.db = db or ReportsDatabase()

    # ---- favorites ----
    def list_favorites(self) -> set[str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT report_id FROM report_favorites").fetchall()
        return {str(r["report_id"]) for r in rows}

    def set_favorite(self, report_id: str, favorite: bool) -> None:
        with self.db.connect() as conn:
            if favorite:
                conn.execute(
                    "INSERT OR REPLACE INTO report_favorites (report_id, created_at) VALUES (?, ?)",
                    (report_id, utcnow()),
                )
            else:
                conn.execute("DELETE FROM report_favorites WHERE report_id = ?", (report_id,))

    # ---- presets ----
    def list_presets(self, *, report_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if report_id:
                rows = conn.execute(
                    "SELECT * FROM report_presets WHERE report_id = ? ORDER BY updated_at DESC",
                    (report_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM report_presets ORDER BY updated_at DESC"
                ).fetchall()
        return [self._hydrate_preset(dict(r)) for r in rows]

    def get_preset(self, preset_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_presets WHERE id = ?", (preset_id,)
            ).fetchone()
        return self._hydrate_preset(dict(row)) if row else None

    def save_preset(
        self,
        *,
        name: str,
        report_id: str,
        environment: str,
        period: str = "",
        org_unit: str = "",
        parameters: dict[str, Any] | None = None,
        output_format: str = "html",
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        pid = preset_id or uuid.uuid4().hex
        now = utcnow()
        params = scrub_parameters(parameters)
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM report_presets WHERE id = ?", (pid,)
            ).fetchone()
            created = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO report_presets (
                    id, name, report_id, environment, period, org_unit,
                    parameters_json, output_format, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    (name or "Preset").strip() or "Preset",
                    report_id,
                    environment,
                    period or "",
                    org_unit or "",
                    json.dumps(params),
                    output_format or "html",
                    created,
                    now,
                ),
            )
        return self.get_preset(pid) or {"id": pid}

    def duplicate_preset(self, preset_id: str, *, name: str | None = None) -> dict[str, Any]:
        src = self.get_preset(preset_id)
        if not src:
            raise KeyError("Preset not found")
        return self.save_preset(
            name=name or f"{src['name']} (copy)",
            report_id=src["report_id"],
            environment=src["environment"],
            period=src.get("period") or "",
            org_unit=src.get("org_unit") or "",
            parameters=src.get("parameters") or {},
            output_format=src.get("output_format") or "html",
        )

    def delete_preset(self, preset_id: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM report_presets WHERE id = ?", (preset_id,))
            return cur.rowcount > 0

    def _hydrate_preset(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            params = json.loads(row.get("parameters_json") or "{}")
        except json.JSONDecodeError:
            params = {}
        row["parameters"] = scrub_parameters(params if isinstance(params, dict) else {})
        return row

    # ---- history ----
    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = utcnow()
        params = scrub_parameters(payload.get("parameters"))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO report_runs (
                    id, report_id, report_name, report_type, environment, period, org_unit,
                    parameters_json, repository_id, git_branch, git_commit, status,
                    output_path, output_url, hub_job_id, run_profile_id, error, actor,
                    started_at, finished_at, log_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    payload.get("report_id") or "",
                    payload.get("report_name") or "",
                    payload.get("report_type") or "",
                    payload.get("environment") or "",
                    payload.get("period") or "",
                    payload.get("org_unit") or "",
                    json.dumps(params),
                    payload.get("repository_id") or "",
                    payload.get("git_branch") or "",
                    payload.get("git_commit") or "",
                    payload.get("status") or "queued",
                    payload.get("output_path") or "",
                    payload.get("output_url") or "",
                    payload.get("hub_job_id") or "",
                    payload.get("run_profile_id") or "",
                    payload.get("error") or "",
                    payload.get("actor") or "",
                    now,
                    "",
                    payload.get("log_text") or "",
                ),
            )
        return self.get_run(run_id) or {"id": run_id}

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "output_path",
            "output_url",
            "hub_job_id",
            "error",
            "finished_at",
            "log_text",
            "git_branch",
            "git_commit",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_run(run_id)
        if "error" in updates and updates["error"]:
            from hub.dhis2_reports.security import redact_report_detail

            updates["error"] = redact_report_detail(str(updates["error"]))
        if "log_text" in updates and updates["log_text"]:
            from hub.dhis2_reports.security import redact_report_detail

            updates["log_text"] = redact_report_detail(str(updates["log_text"]), limit=4000)
        cols = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [run_id]
        with self.db.connect() as conn:
            conn.execute(f"UPDATE report_runs SET {cols} WHERE id = ?", values)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM report_runs WHERE id = ?", (run_id,)).fetchone()
        return self._hydrate_run(dict(row)) if row else None

    def list_runs(
        self,
        *,
        report_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if report_id:
            clauses.append("report_id = ?")
            params.append(report_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM report_runs{where} ORDER BY started_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._hydrate_run(dict(r)) for r in rows]

    def summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            total_reports = None  # filled by service from catalog
            failed = conn.execute(
                "SELECT COUNT(*) AS c FROM report_runs WHERE status IN ('failed', 'missing_output')"
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT * FROM report_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return {
            "failed_count": int(failed or 0),
            "last_run": self._hydrate_run(dict(last)) if last else None,
            "total_reports": total_reports,
        }

    def _hydrate_run(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            params = json.loads(row.get("parameters_json") or "{}")
        except json.JSONDecodeError:
            params = {}
        row["parameters"] = scrub_parameters(params if isinstance(params, dict) else {})
        return row

    def last_run_for(self, report_id: str) -> dict[str, Any] | None:
        runs = self.list_runs(report_id=report_id, limit=1)
        return runs[0] if runs else None

    # ---- synced standard reports (Phase 1) ----
    def upsert_synced_report(
        self,
        report: Any,
        *,
        design_content: str = "",
    ) -> None:
        from hub.dhis2_reports.standard_models import dumps_json

        design = design_content or ""
        # Never persist secrets; design HTML is report markup only.
        if any(
            token in design.lower()
            for token in ("password=", "authorization:", "bearer ", "api_key=")
        ):
            design = ""
        with self.db.connect() as conn:
            if not design:
                existing = conn.execute(
                    "SELECT design_content FROM synced_standard_reports WHERE environment = ? AND uid = ?",
                    (report.environment, report.uid),
                ).fetchone()
                if existing and existing["design_content"]:
                    design = str(existing["design_content"])
            conn.execute(
                """
                INSERT OR REPLACE INTO synced_standard_reports (
                    environment, uid, name, report_type, report_params_json,
                    relative_periods_json, relative_periods_raw_json,
                    data_source_kind, data_source_id, data_source_name,
                    html_design_available, design_content, cache_strategy,
                    dhis2_version, last_synced_at, last_updated, created_at_remote,
                    unsupported_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.environment,
                    report.uid,
                    report.name,
                    report.report_type,
                    dumps_json(report.report_params),
                    dumps_json(report.relative_periods),
                    dumps_json(report.relative_periods_raw),
                    report.data_source_kind,
                    report.data_source_id,
                    report.data_source_name,
                    1 if report.html_design_available else 0,
                    design,
                    report.cache_strategy,
                    report.dhis2_version,
                    report.last_synced_at,
                    report.last_updated,
                    report.created,
                    report.unsupported_reason,
                ),
            )

    def prune_synced_reports(self, environment: str, keep_uids: set[str]) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT uid FROM synced_standard_reports WHERE environment = ?",
                (environment,),
            ).fetchall()
            removed = 0
            for row in rows:
                uid = str(row["uid"])
                if uid not in keep_uids:
                    conn.execute(
                        "DELETE FROM synced_standard_reports WHERE environment = ? AND uid = ?",
                        (environment, uid),
                    )
                    removed += 1
            return removed

    def get_synced_report(self, environment: str, uid: str) -> Any | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM synced_standard_reports WHERE environment = ? AND uid = ?",
                (environment, uid),
            ).fetchone()
        return self._hydrate_synced(dict(row)) if row else None

    def get_synced_design_content(self, environment: str, uid: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT design_content FROM synced_standard_reports WHERE environment = ? AND uid = ?",
                (environment, uid),
            ).fetchone()
        return str(row["design_content"] or "") if row else ""

    def list_synced_reports(
        self,
        *,
        environment: str | None = None,
        report_type: str = "",
        html_only: bool | None = None,
        q: str = "",
        favorites_only: bool = False,
        favorites: set[str] | None = None,
    ) -> list[Any]:
        from hub.dhis2_reports.standard_models import favorite_key

        clauses: list[str] = []
        params: list[Any] = []
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        if report_type:
            clauses.append("UPPER(report_type) = ?")
            params.append(report_type.upper())
        if html_only is True:
            clauses.append("html_design_available = 1")
        elif html_only is False:
            clauses.append("html_design_available = 0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT environment, uid, name, report_type, report_params_json,
                       relative_periods_json, relative_periods_raw_json,
                       data_source_kind, data_source_id, data_source_name,
                       html_design_available,
                       CASE WHEN length(design_content) > 0 THEN 1 ELSE 0 END AS has_design_content,
                       cache_strategy, dhis2_version, last_synced_at, last_updated,
                       created_at_remote, unsupported_reason
                FROM synced_standard_reports{where}
                ORDER BY environment ASC, name COLLATE NOCASE ASC
                """,
                params,
            ).fetchall()
        favs = favorites if favorites is not None else self.list_favorites()
        needle = (q or "").strip().lower()
        out: list[Any] = []
        for row in rows:
            report = self._hydrate_synced(dict(row))
            if report is None:
                continue
            report.favorite = favorite_key(report.environment, report.uid) in favs
            if favorites_only and not report.favorite:
                continue
            if needle:
                hay = " ".join(
                    [
                        report.name,
                        report.uid,
                        report.report_type,
                        report.environment,
                        report.data_source_name,
                        " ".join(report.relative_periods),
                    ]
                ).lower()
                if needle not in hay:
                    continue
            out.append(report)
        return out

    def synced_summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            stage = conn.execute(
                "SELECT COUNT(*) AS c FROM synced_standard_reports WHERE environment = 'stage'"
            ).fetchone()["c"]
            live = conn.execute(
                "SELECT COUNT(*) AS c FROM synced_standard_reports WHERE environment = 'live'"
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT * FROM standard_report_sync_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return {
            "stage_count": int(stage or 0),
            "live_count": int(live or 0),
            "total_synced": int(stage or 0) + int(live or 0),
            "last_sync": dict(last) if last else None,
        }

    def record_sync_run(
        self,
        *,
        environment: str,
        status: str,
        report_count: int,
        dhis2_version: str,
        detail: str,
        truncated: bool,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from hub.dhis2_reports.standard_models import dumps_json

        run_id = uuid.uuid4().hex
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO standard_report_sync_runs (
                    id, environment, status, report_count, dhis2_version, detail,
                    truncated, capabilities_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    environment,
                    status,
                    int(report_count),
                    dhis2_version or "",
                    redact_report_detail(detail) if detail else "",
                    1 if truncated else 0,
                    dumps_json(capabilities or {}),
                    now,
                    now,
                ),
            )
        return {"id": run_id, "environment": environment, "status": status}

    def last_sync_for(self, environment: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM standard_report_sync_runs
                WHERE environment = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (environment,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        from hub.dhis2_reports.standard_models import loads_json

        data["capabilities"] = loads_json(data.get("capabilities_json") or "{}", {})
        return data

    def _hydrate_synced(self, row: dict[str, Any]) -> Any | None:
        from hub.dhis2_reports.standard_models import SyncedStandardReport, loads_json

        if not row.get("uid"):
            return None
        params = loads_json(row.get("report_params_json") or "{}", {})
        periods = loads_json(row.get("relative_periods_json") or "[]", [])
        periods_raw = loads_json(row.get("relative_periods_raw_json") or "{}", {})
        return SyncedStandardReport(
            environment=str(row.get("environment") or ""),
            uid=str(row.get("uid") or ""),
            name=str(row.get("name") or ""),
            report_type=str(row.get("report_type") or ""),
            report_params=params if isinstance(params, dict) else {},
            relative_periods=list(periods) if isinstance(periods, list) else [],
            relative_periods_raw=periods_raw if isinstance(periods_raw, dict) else {},
            data_source_kind=str(row.get("data_source_kind") or ""),
            data_source_id=str(row.get("data_source_id") or ""),
            data_source_name=str(row.get("data_source_name") or ""),
            html_design_available=bool(row.get("html_design_available")),
            design_content_cached=bool(
                row.get("has_design_content")
                if "has_design_content" in row
                else row.get("design_content")
            ),
            cache_strategy=str(row.get("cache_strategy") or ""),
            dhis2_version=str(row.get("dhis2_version") or ""),
            last_synced_at=str(row.get("last_synced_at") or ""),
            last_updated=str(row.get("last_updated") or ""),
            created=str(row.get("created_at_remote") or ""),
            unsupported_reason=str(row.get("unsupported_reason") or ""),
        )
