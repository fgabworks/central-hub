"""Synchronize DHIS2 standard report metadata (GET-only; DHIS2 is source of truth)."""

from __future__ import annotations

from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.db import utcnow
from hub.dhis2_reports.security import ReportSecurityError, redact_report_detail, validate_environment
from hub.dhis2_reports.standard_models import (
    REPORT_DETAIL_FIELDS,
    REPORT_LIST_FIELDS,
    SyncedStandardReport,
    normalize_report_payload,
)
from hub.dhis2_reports.store import ReportsStore

ClientFactory = Callable[[str], Dhis2Client]


def detect_report_capabilities(client: Dhis2Client) -> dict[str, Any]:
    """Probe DHIS2 version and whether /api/reports is available (GET only)."""
    version = ""
    system_name = ""
    try:
        info = client._get_json(  # noqa: SLF001 — intentional shared probe
            "/api/system/info",
            params={"fields": "version,systemName,instanceName"},
            timeout=client.settings.probe_timeout_seconds,
        )
        version = str(info.get("version") or "")
        system_name = str(info.get("systemName") or info.get("instanceName") or "")
    except Dhis2Error as exc:
        return {
            "ok": False,
            "version": "",
            "system_name": "",
            "reports_list": False,
            "report_data_html": False,
            "design_content": False,
            "detail": redact_report_detail(exc.message),
        }

    reports_list = False
    design_content = False
    try:
        sample = client.iter_collection(
            "reports",
            fields="id,name,type,designContent",
            page_size=1,
            max_pages=1,
            normalize=False,
        )
        reports_list = True
        items = sample.get("items") or []
        if items and isinstance(items[0], dict):
            design_content = "designContent" in items[0] or True
        else:
            design_content = True
    except Dhis2Error:
        reports_list = False

    return {
        "ok": True,
        "version": version,
        "system_name": system_name,
        "reports_list": reports_list,
        "report_data_html": reports_list,  # documented alongside /api/reports
        "design_content": design_content and reports_list,
        "detail": "Capabilities probed via GET.",
    }


class StandardReportSyncService:
    """Fetch accessible /api/reports into local metadata cache (no DHIS2 writes)."""

    def __init__(
        self,
        store: ReportsStore,
        *,
        client_factory: ClientFactory,
        audit: Callable[[str, str, str, bool], None] | None = None,
    ) -> None:
        self.store = store
        self.client_factory = client_factory
        self.audit = audit

    def _audit(self, action: str, target: str, detail: str, ok: bool = True) -> None:
        if self.audit:
            self.audit(action, target, redact_report_detail(detail), ok)

    def sync_environment(
        self,
        environment: str,
        *,
        confirm_live: bool = False,
        cache_design_content: bool = False,
        max_pages: int = 50,
        page_size: int = 100,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live standard-report sync requires explicit confirmation.",
                code="confirm_required",
            )

        client = self.client_factory(env)
        if not client.settings.is_configured or not client.settings.enabled:
            raise ReportSecurityError(
                f"DHIS2 {env} is not configured.",
                code="dhis2_unconfigured",
            )

        started = utcnow()
        capabilities = detect_report_capabilities(client)
        if not capabilities.get("reports_list"):
            self.store.record_sync_run(
                environment=env,
                status="failed",
                report_count=0,
                dhis2_version=str(capabilities.get("version") or ""),
                detail=str(capabilities.get("detail") or "reports API unavailable"),
                truncated=False,
                capabilities=capabilities,
            )
            self._audit(
                "DHIS2_REPORT_SYNC",
                env,
                f"failed reports_api version={capabilities.get('version')}",
                False,
            )
            raise ReportSecurityError(
                "DHIS2 reports collection is unavailable or unauthorized.",
                code="unavailable",
            )

        version = str(capabilities.get("version") or "")
        try:
            walked = client.iter_collection(
                "reports",
                fields=REPORT_LIST_FIELDS,
                page_size=page_size,
                max_pages=max_pages,
                normalize=False,
            )
        except Dhis2Error as exc:
            self.store.record_sync_run(
                environment=env,
                status="failed",
                report_count=0,
                dhis2_version=version,
                detail=exc.message,
                truncated=False,
                capabilities=capabilities,
            )
            self._audit("DHIS2_REPORT_SYNC", env, f"failed {exc.message}", False)
            code = "unauthorized" if exc.status_code in {401, 403} else "unavailable"
            raise ReportSecurityError(
                redact_report_detail(exc.message),
                code=code,
            ) from exc

        synced_at = utcnow()
        reports: list[SyncedStandardReport] = []
        for raw in walked.get("items") or []:
            if not isinstance(raw, dict):
                continue
            uid = str(raw.get("id") or "").strip()
            if not uid:
                continue
            design_text = str(raw.get("designContent") or "")
            # Optional detail GET when list payload omitted designContent.
            if cache_design_content and not design_text:
                try:
                    detail = client.get_metadata_object(
                        "reports",
                        uid,
                        fields=REPORT_DETAIL_FIELDS,
                    )
                    payload = detail.get("raw") if isinstance(detail.get("raw"), dict) else raw
                    design_text = str(payload.get("designContent") or "")
                    raw = payload
                except Dhis2Error:
                    design_text = ""

            report = normalize_report_payload(
                raw,
                environment=env,
                dhis2_version=version,
                last_synced_at=synced_at,
                cache_design=bool(design_text),
            )
            if not report.uid:
                continue
            reports.append(report)
            self.store.upsert_synced_report(
                report,
                design_content=design_text,
            )

        # Drop stale rows for this environment (DHIS2 is source of truth).
        keep_uids = {r.uid for r in reports}
        removed = self.store.prune_synced_reports(env, keep_uids)

        truncated = bool(walked.get("truncated"))
        result = {
            "ok": True,
            "environment": env,
            "count": len(reports),
            "removed": removed,
            "truncated": truncated,
            "pages_fetched": walked.get("pages_fetched"),
            "pager_total": walked.get("total"),
            "dhis2_version": version,
            "capabilities": capabilities,
            "started_at": started,
            "finished_at": synced_at,
            "writes": False,
        }
        self.store.record_sync_run(
            environment=env,
            status="completed",
            report_count=len(reports),
            dhis2_version=version,
            detail=f"synced={len(reports)} removed={removed} truncated={truncated}",
            truncated=truncated,
            capabilities=capabilities,
        )
        self._audit(
            "DHIS2_REPORT_SYNC",
            env,
            f"count={len(reports)} truncated={truncated} version={version}",
            True,
        )
        return result

    def refresh_one(
        self,
        environment: str,
        uid: str,
        *,
        confirm_live: bool = False,
        cache_design_content: bool = True,
    ) -> SyncedStandardReport:
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live metadata refresh requires explicit confirmation.",
                code="confirm_required",
            )
        client = self.client_factory(env)
        if not client.settings.is_configured:
            raise ReportSecurityError(
                f"DHIS2 {env} is not configured.",
                code="dhis2_unconfigured",
            )
        version = ""
        try:
            info = client._get_json(  # noqa: SLF001
                "/api/system/info",
                params={"fields": "version"},
                timeout=client.settings.probe_timeout_seconds,
            )
            version = str(info.get("version") or "")
        except Dhis2Error:
            version = ""
        try:
            detail = client.get_metadata_object(
                "reports",
                uid,
                fields=REPORT_DETAIL_FIELDS,
            )
        except Dhis2Error as exc:
            self._audit("DHIS2_REPORT_REFRESH", f"{env}:{uid}", exc.message, False)
            code = "unauthorized" if exc.status_code in {401, 403} else "not_found"
            if exc.status_code == 404:
                code = "not_found"
            raise ReportSecurityError(
                redact_report_detail(exc.message),
                code=code,
            ) from exc

        raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
        design = str(raw.get("designContent") or "") if cache_design_content else ""
        report = normalize_report_payload(
            raw,
            environment=env,
            dhis2_version=version,
            last_synced_at=utcnow(),
            cache_design=bool(design),
        )
        if not report.uid:
            raise ReportSecurityError("Report metadata missing UID.", code="not_found")
        self.store.upsert_synced_report(
            report,
            design_content=design if cache_design_content else "",
        )
        self._audit("DHIS2_REPORT_REFRESH", report.id, f"type={report.report_type}", True)
        return report
