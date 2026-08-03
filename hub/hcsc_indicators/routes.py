"""Flask routes for HCSC Indicator Summary & Data Lineage — NPMO."""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from hub.audit import actions as audit_actions
from hub.dhis2_reports.security import ReportSecurityError, redact_report_detail
from hub.hcsc_indicators.service import HcscIndicatorService
from hub.hcsc_indicators.progress_compare import ProgressCompareService
from hub.jobs.auth import current_actor


def register_hcsc_indicator_routes(app: Flask) -> None:
    def _svc() -> HcscIndicatorService:
        return app.config["HCSC_INDICATORS"]

    def _progress() -> ProgressCompareService:
        return app.config["HCSC_PROGRESS_COMPARE"]

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
            "invalid_geographic_breakdown": 400,
            "invalid_section": 400,
            "dhis2_unconfigured": 400,
            "dhis2_error": 502,
            "duplicate_request": 409,
            "forbidden": 403,
        }.get(code, 400)
        return jsonify({"ok": False, "error": str(exc), "code": code}), http

    def _scope_params() -> dict[str, Any]:
        return {
            "env": request.args.get("environment") or "",
            "period": request.args.get("period") or "",
            "org_unit": request.args.get("orgUnit") or request.args.get("org_unit") or "",
            "disagg": request.args.get("disaggregation") or "none",
            "geographic_breakdown": (
                request.args.get("geographicBreakdown")
                or request.args.get("geographic_breakdown")
                or "none"
            ),
            "force": request.args.get("fresh") in {"1", "true", "yes"}
            or request.args.get("refresh") in {"1", "true", "yes"},
        }

    @app.get("/dhis2/hcsc-indicators")
    def dhis2_hcsc_indicators():
        boot = _svc().bootstrap()
        _audit(
            getattr(audit_actions, "HCSC_INDICATOR_VIEW", "HCSC_INDICATOR_VIEW"),
            target="hcsc-indicator-summary",
            detail="Opened Central Hub HCSC–RF",
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
                geographic_breakdown=p["geographic_breakdown"],
                force_refresh=p["force"],
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_REPORT"),
                target=f"hcsc-report:{p['env']}:{p['period']}:{p['org_unit']}",
                detail=(
                    f"Report adapters={payload.get('adapters_used')} "
                    f"dx={payload.get('timings', {}).get('dx_count')} "
                    f"geo={p['geographic_breakdown']} "
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

    @app.get("/api/dhis2/hcsc-indicators/export.csv")
    def api_hcsc_indicators_export_csv():
        p = _scope_params()
        try:
            body, filename = _svc().export_csv(
                environment=p["env"], period=p["period"], org_unit=p["org_unit"],
                disaggregation=p["disagg"], force_refresh=p["force"],
            )
            return Response(
                body, mimetype="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/hcsc-indicators/breakdown-estimate")
    def api_hcsc_indicators_breakdown_estimate():
        p = _scope_params()
        try:
            payload = _svc().breakdown_estimate(
                environment=p["env"],
                org_unit=p["org_unit"],
                geographic_breakdown=p["geographic_breakdown"],
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
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

    @app.get("/api/dhis2/hcsc-indicators/validation")
    def api_hcsc_indicators_validation():
        p = _scope_params()
        try:
            payload = _svc().validation_workspace(
                environment=p["env"],
                period=p["period"],
                org_unit=p["org_unit"],
                disaggregation=p["disagg"],
                force_refresh=p["force"],
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_VALIDATION"),
                target=f"hcsc-validation:{p['env']}:{p['period']}:{p['org_unit']}",
                detail=(
                    f"Validation rows={payload.get('summary', {}).get('total')} "
                    f"report_cache={payload.get('timings', {}).get('report_cache_hit')}"
                ),
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/hcsc-indicators/validation/snapshot")
    def api_hcsc_indicators_validation_snapshot():
        data = request.get_json(silent=True) or {}
        try:
            payload = _svc().save_validation_snapshot(
                environment=str(data.get("environment") or ""),
                period=str(data.get("period") or ""),
                org_unit=str(data.get("orgUnit") or data.get("org_unit") or ""),
                disaggregation=str(data.get("disaggregation") or "none"),
                note=(str(data.get("note")).strip() if data.get("note") else None),
            )
            _audit(
                getattr(audit_actions, "HCSC_INDICATOR_OVERVIEW", "HCSC_INDICATOR_VALIDATION_SNAPSHOT"),
                target="hcsc-validation-snapshot",
                detail=f"Saved evidence snapshot {payload.get('snapshot', {}).get('id')}",
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            return _json_error(exc)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/dhis2/hcsc-indicators/validation/notes")
    def api_hcsc_indicators_validation_notes():
        data = request.get_json(silent=True) or {}
        try:
            payload = _svc().add_validation_note(
                note=str(data.get("note") or ""),
                indicator_key=(str(data.get("indicator_key")).strip() if data.get("indicator_key") else None),
                environment=(str(data.get("environment")).strip() if data.get("environment") else None),
                period=(str(data.get("period")).strip() if data.get("period") else None),
                org_unit=(
                    str(data.get("orgUnit") or data.get("org_unit")).strip()
                    if (data.get("orgUnit") or data.get("org_unit"))
                    else None
                ),
            )
            return jsonify(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.get("/dhis2/hcsc-indicators/compare/progress-npmo")
    def dhis2_hcsc_progress_compare():
        boot = _progress().bootstrap()
        _audit(
            getattr(audit_actions, "HCSC_PROGRESS_COMPARE_VIEW", "HCSC_PROGRESS_COMPARE_VIEW"),
            target="progress-npmo-compare",
            detail="Opened Progress NPMO comparison",
        )
        return render_template(
            "hcsc_progress_compare.html",
            page_title=boot["page_title"],
            bootstrap=boot,
            dhis2_instance=app.config.get("DHIS2_INSTANCE"),
        )

    @app.get("/api/dhis2/hcsc-indicators/compare/progress-npmo/bootstrap")
    def api_hcsc_progress_compare_bootstrap():
        return jsonify(_progress().bootstrap())

    @app.post("/api/dhis2/hcsc-indicators/compare/progress-npmo")
    def api_hcsc_progress_compare_run():
        data = request.get_json(silent=True) or {}
        try:
            payload = _progress().compare(
                environment=str(data.get("environment") or ""),
                period=str(data.get("period") or ""),
                org_unit=str(data.get("orgUnit") or data.get("org_unit") or ""),
                force_refresh=bool(data.get("fresh") or data.get("force")),
                request_id=str(data.get("request_id") or ""),
            )
            _audit(
                getattr(audit_actions, "HCSC_PROGRESS_COMPARE_RUN", "HCSC_PROGRESS_COMPARE_RUN"),
                target=f"progress-npmo:{data.get('environment')}:{data.get('period')}",
                detail=(
                    f"overall={payload.get('overall', {}).get('status')} "
                    f"rows={payload.get('overall', {}).get('total')}"
                ),
            )
            return jsonify(payload)
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/hcsc-indicators/compare/progress-npmo/snapshot")
    def api_hcsc_progress_compare_snapshot():
        env = request.args.get("environment") or "stage"
        try:
            return jsonify(_progress().snapshot_html(environment=env))
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/hcsc-indicators/compare/progress-npmo/export")
    def api_hcsc_progress_compare_export():
        from flask import Response

        try:
            body, filename, mime = _progress().export(
                environment=str(request.args.get("environment") or ""),
                period=str(request.args.get("period") or ""),
                org_unit=str(request.args.get("orgUnit") or request.args.get("org_unit") or ""),
                format=str(request.args.get("format") or "json"),
                force_refresh=request.args.get("fresh") in {"1", "true", "yes"},
            )
            _audit(
                getattr(audit_actions, "HCSC_PROGRESS_COMPARE_EXPORT", "HCSC_PROGRESS_COMPARE_EXPORT"),
                target=filename,
                detail=f"format={request.args.get('format')}",
            )
            return Response(
                body,
                mimetype=mime,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except ReportSecurityError as exc:
            return _json_error(exc)

    # Dynamic detail route last so it cannot shadow overview/report/category/validation.
    @app.get("/api/dhis2/hcsc-indicators/<key>")
    def api_hcsc_indicator_detail(key: str):
        try:
            return jsonify(_svc().indicator_detail(key))
        except ReportSecurityError as exc:
            return _json_error(exc)
