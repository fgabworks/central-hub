"""Flask routes for HCSC Indicator Summary & Data Lineage — NPMO."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, render_template, request

from hub.audit import actions as audit_actions
from hub.dhis2_reports.security import ReportSecurityError, redact_report_detail
from hub.hcsc_indicators.service import HcscIndicatorService
from hub.jobs.auth import current_actor


def register_hcsc_indicator_routes(app: Flask) -> None:
    def _svc() -> HcscIndicatorService:
        return app.config["HCSC_INDICATORS"]

    def _audit(action: str, *, target: str, detail: str, ok: bool = True) -> None:
        app.config["AUDIT"].append(
            action=action,
            actor=current_actor(),
            target=target,
            detail=redact_report_detail(detail),
            ok=ok,
        )

    def _json_error(exc: ReportSecurityError, status: int | None = None):
        code = getattr(exc, "code", "forbidden")
        http = status or {
            "not_found": 404,
            "invalid_period": 400,
            "invalid_org_unit": 400,
            "invalid_environment": 400,
            "invalid_disaggregation": 400,
            "invalid_section": 400,
            "dhis2_unconfigured": 400,
            "dhis2_error": 502,
        }.get(code, 400)
        return jsonify({"ok": False, "error": str(exc), "code": code}), http

    def _scope_params() -> dict[str, Any]:
        return {
            "env": request.args.get("environment") or "",
            "period": request.args.get("period") or "",
            "org_unit": request.args.get("orgUnit") or request.args.get("org_unit") or "",
            "disagg": request.args.get("disaggregation") or "none",
            "force": request.args.get("fresh") in {"1", "true", "yes"}
            or request.args.get("refresh") in {"1", "true", "yes"},
        }

    @app.get("/dhis2/hcsc-indicators")
    def hcsc_indicator_summary():
        boot = _svc().bootstrap()
        _audit(
            getattr(audit_actions, "HCSC_INDICATOR_VIEW", "HCSC_INDICATOR_VIEW"),
            target="hcsc-indicator-summary",
            detail="Opened HCSC Indicator Summary Overview",
        )
        return render_template(
            "hcsc_indicator_summary.html",
            page_title=boot["page_title"],
            bootstrap=boot,
            dhis2_instance=app.config.get("DHIS2_INSTANCE"),
        )

    @app.get("/api/dhis2/hcsc-indicators/bootstrap")
    def api_hcsc_indicators_bootstrap():
        return jsonify(_svc().bootstrap())

    @app.get("/api/dhis2/hcsc-indicators/registry")
    def api_hcsc_indicators_registry():
        force = request.args.get("refresh") in {"1", "true", "yes"}
        return jsonify(_svc().registry(force=force))

    @app.get("/api/dhis2/hcsc-indicators/design")
    def api_hcsc_indicators_design():
        force = request.args.get("refresh") in {"1", "true", "yes"}
        return jsonify(_svc().design_bindings(force=force))

    @app.get("/api/dhis2/hcsc-indicators/overview")
    def api_hcsc_indicators_overview():
        p = _scope_params()
        try:
            payload = _svc().overview(
                environment=p["env"],
                period=p["period"],
                org_unit=p["org_unit"],
                disaggregation=p["disagg"],
                force_refresh=p["force"],
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_OVERVIEW"),
                target=f"hcsc-overview:{p['env']}:{p['period']}:{p['org_unit']}",
                detail=(
                    f"Overview analytics batch dx={payload.get('timings', {}).get('dx_count')} "
                    f"cache_hit={payload.get('cache', {}).get('hit')}"
                ),
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_OVERVIEW"),
                target=f"hcsc-overview:{p['env']}",
                detail=str(exc),
                ok=False,
            )
            return _json_error(exc)

    @app.get("/api/dhis2/hcsc-indicators/report")
    def api_hcsc_indicators_report():
        p = _scope_params()
        try:
            payload = _svc().report(
                environment=p["env"],
                period=p["period"],
                org_unit=p["org_unit"],
                disaggregation=p["disagg"],
                force_refresh=p["force"],
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_REPORT"),
                target=f"hcsc-report:{p['env']}:{p['period']}:{p['org_unit']}",
                detail=(
                    f"Report adapters={payload.get('adapters_used')} "
                    f"dx={payload.get('timings', {}).get('dx_count')} "
                    f"cache_hit={payload.get('cache', {}).get('hit')}"
                ),
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_REPORT"),
                target=f"hcsc-report:{p['env']}",
                detail=str(exc),
                ok=False,
            )
            return _json_error(exc)

    @app.get("/api/dhis2/hcsc-indicators/category/<section>")
    def api_hcsc_indicators_category(section: str):
        p = _scope_params()
        try:
            payload = _svc().category(
                section=section,
                environment=p["env"],
                period=p["period"],
                org_unit=p["org_unit"],
                disaggregation=p["disagg"],
                force_refresh=p["force"],
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_CATEGORY"),
                target=f"hcsc-category:{section}:{p['env']}:{p['period']}:{p['org_unit']}",
                detail=(
                    f"Category {section} dx={payload.get('timings', {}).get('dx_count')} "
                    f"cache_hit={payload.get('cache', {}).get('hit')}"
                ),
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_CATEGORY"),
                target=f"hcsc-category:{section}:{p['env']}",
                detail=str(exc),
                ok=False,
            )
            return _json_error(exc)

    # Dynamic detail route last so it cannot shadow overview/report/category.
    @app.get("/api/dhis2/hcsc-indicators/<key>")
    def api_hcsc_indicator_detail(key: str):
        try:
            return jsonify(_svc().indicator_detail(key))
        except ReportSecurityError as exc:
            return _json_error(exc)
