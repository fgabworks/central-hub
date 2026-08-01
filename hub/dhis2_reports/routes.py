"""Flask routes for DHIS2 Report Workspace + Standard Report Manager (Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

from hub.audit import actions as audit_actions
from hub.dhis2_reports.catalog import get_report, load_report_catalog
from hub.dhis2_reports.security import ReportSecurityError, redact_report_detail
from hub.dhis2_reports.service import Dhis2ReportsService
from hub.jobs.auth import current_actor
from hub.registry.models import Registry


def register_dhis2_reports_routes(app: Flask) -> None:
    def _svc() -> Dhis2ReportsService:
        return app.config["DHIS2_REPORTS"]

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
            "confirm_required": 400,
            "missing_output": 404,
            "host_blocked": 403,
            "path_escape": 403,
            "secret_blocked": 403,
            "environment_blocked": 403,
            "unavailable": 409,
            "unauthorized": 403,
            "dhis2_unconfigured": 400,
            "invalid_period": 400,
            "invalid_org_unit": 400,
            "ssrf_blocked": 403,
            "proxy_path_blocked": 403,
            "path_traversal": 403,
            "invalid_proxy_path": 400,
            "environment_mismatch": 400,
            "invalid_report_id": 400,
        }.get(code, 400)
        return jsonify({"ok": False, "error": str(exc), "code": code}), http

    def _tabs(active: str) -> list[dict[str, str]]:
        return [
            {"id": "library", "label": "Report Library", "endpoint": "dhis2_reports_library"},
            {"id": "run", "label": "Run Report", "endpoint": "dhis2_reports_run"},
            {"id": "presets", "label": "Saved Presets", "endpoint": "dhis2_reports_presets"},
            {"id": "history", "label": "History", "endpoint": "dhis2_reports_history"},
            {"id": "settings", "label": "Settings", "endpoint": "dhis2_reports_settings"},
        ]

    def _ctx(active: str) -> dict[str, Any]:
        return {
            "page_title": "DHIS2 Reports",
            "active_tab": active,
            "tabs": _tabs(active),
            "dhis2_instance": app.config.get("DHIS2_INSTANCE"),
        }

    @app.get("/dhis2/reports")
    def dhis2_reports_library():
        q = (request.args.get("q") or "").strip()
        rtype = (request.args.get("type") or "").strip()
        env = (request.args.get("environment") or "").strip()
        html_av = (request.args.get("html") or "").strip()
        fav = request.args.get("favorites") in {"1", "true", "yes"}
        repo = (request.args.get("repository") or "").strip()

        standard = _svc().list_standard_library(
            q=q,
            report_type=rtype,
            environment=env,
            html_available=html_av,
            favorites_only=fav,
        )
        catalog_rows = _svc().list_library(
            q=q,
            report_type="" if rtype and rtype.upper() == rtype else rtype,
            repository_id=repo,
            environment=env,
            favorites_only=fav,
        )
        # When filtering by DHIS2 report type (HTML, JASPER_*), hide unrelated catalog noise.
        if rtype and rtype.upper() in {"HTML", "JASPER_REPORT_TABLE", "JASPER_JDBC", "JASPERREPORT"}:
            catalog_rows = []
        elif rtype in {"dhis2_standard", "repository_html", "static_html"}:
            standard = {"sections": [], "report_types": standard.get("report_types") or []}

        _audit(audit_actions.DHIS2_REPORT_VIEW, target="library", detail=f"q={q} type={rtype}")
        registry: Registry | None = app.config.get("REGISTRY")
        repos = [
            {"id": r.id, "name": r.name}
            for r in (registry.repositories if registry else [])
        ]
        sync_meta = {
            "stage": _svc().store.last_sync_for("stage"),
            "live": _svc().store.last_sync_for("live"),
        }
        return render_template(
            "dhis2_reports_library.html",
            standard=standard,
            reports=catalog_rows,
            sync_meta=sync_meta,
            filters={
                "q": q,
                "type": rtype,
                "repository": repo,
                "environment": env,
                "html": html_av,
                "favorites": fav,
            },
            repositories=repos,
            **_ctx("library"),
        )

    @app.get("/dhis2/reports/standard/<environment>/<uid>")
    def dhis2_reports_standard_detail(environment: str, uid: str):
        period = (request.args.get("period") or "").strip()
        org_unit = (request.args.get("org_unit") or request.args.get("ou") or "").strip()
        try:
            payload = _svc().standard_detail_payload(
                environment, uid, period=period, org_unit=org_unit
            )
        except ReportSecurityError:
            abort(404)
        return render_template(
            "dhis2_reports_standard_detail.html",
            report=payload["report"],
            discovery=payload["discovery"],
            diagnostics=payload["diagnostics"],
            urls=payload["urls"],
            form=payload["form"],
            error=payload["error"],
            run_report_id=payload["run_report_id"],
            **_ctx("library"),
        )

    @app.get("/dhis2/reports/standard/<environment>/<uid>/view")
    def dhis2_reports_standard_view(environment: str, uid: str):
        period = (request.args.get("period") or "").strip()
        org_unit = (request.args.get("org_unit") or request.args.get("ou") or "").strip()
        confirm_live = request.args.get("confirm_live") in {"1", "true", "yes"}
        try:
            viewer = _svc().standard_viewer_payload(
                environment,
                uid,
                period=period,
                org_unit=org_unit,
                confirm_live=confirm_live,
            )
            return render_template(
                "dhis2_reports_standard_viewer.html",
                viewer=viewer,
                **_ctx("library"),
            )
        except ReportSecurityError as exc:
            if getattr(exc, "code", "") == "confirm_required":
                return render_template(
                    "dhis2_reports_standard_viewer.html",
                    viewer=None,
                    confirm_required=True,
                    environment=environment,
                    uid=uid,
                    period=period,
                    org_unit=org_unit,
                    **_ctx("library"),
                ), 400
            abort(400 if getattr(exc, "code", "") != "not_found" else 404)

    @app.get("/dhis2/reports/standard/<environment>/<uid>/rendered")
    def dhis2_reports_standard_rendered(environment: str, uid: str):
        """Serve data.html fetched with .env DHIS2 credentials (never expose secrets)."""
        confirm_live = request.args.get("confirm_live") in {"1", "true", "yes"}
        period = (request.args.get("period") or "").strip()
        org_unit = (request.args.get("org_unit") or request.args.get("ou") or "").strip()
        try:
            data = _svc().render_standard_html(
                environment,
                uid,
                period=period,
                org_unit=org_unit,
                confirm_live=confirm_live,
            )
        except ReportSecurityError as exc:
            code = getattr(exc, "code", "") or "error"
            message = redact_report_detail(str(exc))
            status = {
                "confirm_required": 400,
                "not_found": 404,
                "missing_output": 404,
                "unauthorized": 401,
                "dhis2_bad_request": 502,
                "unavailable": 502,
            }.get(code, 400)
            # Friendly iframe body instead of Flask's bare "Bad Request" page.
            body = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Report unavailable</title>"
                "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
                "padding:1.25rem;line-height:1.45}code{color:#f6c}a{color:#9cf}</style>"
                "</head><body>"
                "<h1>Report could not be rendered</h1>"
                f"<p>{message}</p>"
                "<p>The hub tried <code>/api/reports/{uid}/data.html</code> with your "
                ".env credentials. For HTML design reports it also tries cached design "
                "content. Use <strong>Open in DHIS2</strong> if the report needs the "
                "full Reports app, or set period / organisation unit and retry.</p>"
                f"<p class='muted'>code=<code>{code}</code></p>"
                "</body></html>"
            )
            resp = Response(body, status=status, mimetype="text/html; charset=utf-8")
            resp.headers["Cache-Control"] = "private, no-store"
            return resp
        resp = Response(data["html"], mimetype="text/html; charset=utf-8")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data: blob: https: http: 'self'; "
            "style-src 'unsafe-inline' https: http: 'self'; "
            "script-src 'unsafe-inline' 'unsafe-eval' https: http: 'self'; "
            "font-src https: http: data: 'self'; "
            "connect-src 'none'; base-uri https: http:; form-action 'none'; "
            "frame-ancestors 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, no-store"
        if data.get("source"):
            resp.headers["X-Hub-Report-Source"] = str(data.get("source"))[:80]
        return resp

    @app.get("/dhis2/reports/standard/<environment>/<uid>/html")
    def dhis2_reports_standard_html_source(environment: str, uid: str):
        confirm_live = request.args.get("confirm_live") in {"1", "true", "yes"}
        try:
            payload = _svc().fetch_standard_html_source(
                environment, uid, confirm_live=confirm_live
            )
            return render_template(
                "dhis2_reports_standard_html.html",
                payload=payload,
                **_ctx("library"),
            )
        except ReportSecurityError as exc:
            if getattr(exc, "code", "") == "confirm_required":
                return render_template(
                    "dhis2_reports_standard_html.html",
                    payload=None,
                    confirm_required=True,
                    environment=environment,
                    uid=uid,
                    **_ctx("library"),
                ), 400
            abort(404 if getattr(exc, "code", "") in {"not_found", "missing_output"} else 400)

    @app.get("/dhis2/reports/run")
    def dhis2_reports_run():
        report_id = (request.args.get("report") or "").strip()
        preset_id = (request.args.get("preset") or "").strip()
        env = (request.args.get("environment") or "").strip().lower()
        preferred = env if env in {"stage", "live"} else None
        preset = _svc().store.get_preset(preset_id) if preset_id else None
        if preferred is None and preset:
            preferred = str(preset.get("environment") or "") or None
        if preferred is None:
            preferred = "stage"
        catalog = _svc().list_run_catalog(preferred)
        _audit(audit_actions.DHIS2_REPORT_VIEW, target="run", detail=f"report={report_id}")
        return render_template(
            "dhis2_reports_run.html",
            run_catalog=catalog,
            selected_report_id=report_id or (preset.get("report_id") if preset else ""),
            preset=preset,
            preferred_env=preferred,
            **_ctx("run"),
        )

    @app.get("/dhis2/reports/proxy/<environment>")
    def dhis2_reports_proxy(environment: str):
        """Credentialed asset/API proxy — browser never sees DHIS2 auth."""
        confirm_live = request.args.get("confirm_live") in {"1", "true", "yes"}
        path = request.args.get("path") or ""
        try:
            data = _svc().proxy_dhis2_asset(
                environment, path, confirm_live=confirm_live
            )
        except ReportSecurityError as exc:
            code = getattr(exc, "code", "")
            if code == "confirm_required":
                abort(400)
            if code in {"ssrf_blocked", "proxy_path_blocked", "path_traversal"}:
                abort(403)
            if code == "unauthorized":
                abort(401)
            abort(400)
        resp = Response(data["content"], mimetype=data["content_type"])
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=60"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return resp

    @app.get("/dhis2/reports/presets")
    def dhis2_reports_presets():
        presets = _svc().store.list_presets()
        _audit(audit_actions.DHIS2_REPORT_VIEW, target="presets", detail=f"count={len(presets)}")
        return render_template(
            "dhis2_reports_presets.html",
            presets=presets,
            **_ctx("presets"),
        )

    @app.get("/dhis2/reports/history")
    def dhis2_reports_history():
        status = (request.args.get("status") or "").strip()
        runs = _svc().store.list_runs(status=status or None, limit=200)
        _audit(audit_actions.DHIS2_REPORT_VIEW, target="history", detail=f"status={status}")
        return render_template(
            "dhis2_reports_history.html",
            runs=runs,
            status_filter=status,
            **_ctx("history"),
        )

    @app.get("/dhis2/reports/settings")
    def dhis2_reports_settings():
        catalog = load_report_catalog()
        synced = _svc().store.synced_summary()
        return render_template(
            "dhis2_reports_settings.html",
            catalog_count=len(catalog),
            synced=synced,
            **_ctx("settings"),
        )

    @app.get("/dhis2/reports/view")
    def dhis2_reports_view():
        run_id = (request.args.get("run") or "").strip() or None
        path = (request.args.get("path") or "").strip() or None
        try:
            payload = _svc().viewer_payload(path=path, run_id=run_id)
            _audit(
                audit_actions.DHIS2_REPORT_VIEW_HTML,
                target=run_id or (path or "html"),
                detail=f"kind={payload.get('kind')}",
            )
            return render_template(
                "dhis2_reports_viewer.html",
                viewer=payload,
                **_ctx("history"),
            )
        except ReportSecurityError as exc:
            abort(404 if getattr(exc, "code", "") in {"not_found", "missing_output"} else 400)

    @app.get("/dhis2/reports/file")
    def dhis2_reports_file():
        """Serve allowlisted HTML for sandboxed iframe src."""
        run_id = (request.args.get("run") or "").strip() or None
        path = (request.args.get("path") or "").strip() or None
        try:
            payload = _svc().viewer_payload(path=path, run_id=run_id)
        except ReportSecurityError:
            abort(404)
        if payload.get("kind") != "html":
            abort(400)
        resp = Response(payload["html"], mimetype="text/html; charset=utf-8")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data: blob: 'self'; style-src 'unsafe-inline' 'self'; "
            "font-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ---- APIs ----

    @app.post("/api/dhis2/reports/sync")
    def api_dhis2_reports_sync():
        payload = request.get_json(silent=True) or {}
        env = str(payload.get("environment") or "stage").strip().lower()
        try:
            result = _svc().sync_standard_reports(
                env,
                confirm_live=bool(payload.get("confirm_live")),
                cache_design_content=bool(payload.get("cache_design_content")),
            )
            return jsonify({"ok": True, **result})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/reports/standard/<environment>/<uid>/refresh")
    def api_dhis2_report_refresh(environment: str, uid: str):
        payload = request.get_json(silent=True) or {}
        try:
            report = _svc().refresh_standard_metadata(
                environment,
                uid,
                confirm_live=bool(payload.get("confirm_live")),
            )
            return jsonify({"ok": True, "report": report})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/standard/<environment>/<uid>")
    def api_dhis2_report_standard_get(environment: str, uid: str):
        try:
            report = _svc().get_standard_report(environment, uid)
            return jsonify({"ok": True, "report": report.to_public()})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/standard/<environment>/<uid>/urls")
    def api_dhis2_report_standard_urls(environment: str, uid: str):
        try:
            data = _svc().standard_urls(
                environment,
                uid,
                period=(request.args.get("period") or "").strip(),
                org_unit=(request.args.get("org_unit") or request.args.get("ou") or "").strip(),
            )
            # Never include credentials.
            return jsonify(
                {
                    "ok": True,
                    "report": data["report"],
                    "open_url": data["open_url"],
                    "embed_url": data["embed_url"],
                    "period": data["period"],
                    "org_unit": data["org_unit"],
                    "fallback_hint": data["fallback_hint"],
                }
            )
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/standard/<environment>/<uid>/html")
    def api_dhis2_report_standard_html(environment: str, uid: str):
        confirm_live = request.args.get("confirm_live") in {"1", "true", "yes"}
        download = request.args.get("download") in {"1", "true", "yes"}
        rendered = request.args.get("rendered") in {"1", "true", "yes"}
        try:
            if rendered or download:
                data = _svc().download_standard_html(
                    environment,
                    uid,
                    period=(request.args.get("period") or "").strip(),
                    org_unit=(request.args.get("org_unit") or request.args.get("ou") or "").strip(),
                    confirm_live=confirm_live,
                )
                _audit(
                    audit_actions.DHIS2_REPORT_DOWNLOAD,
                    target=f"std:{environment}:{uid}",
                    detail="data.html",
                )
                return Response(
                    data["html"],
                    mimetype="text/html; charset=utf-8",
                    headers={
                        "Content-Disposition": (
                            f'attachment; filename="{data["filename"]}"'
                            if download
                            else "inline"
                        ),
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            payload = _svc().fetch_standard_html_source(
                environment, uid, confirm_live=confirm_live
            )
            return jsonify({"ok": True, **{k: v for k, v in payload.items() if k != "html"}, "html": payload["html"]})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/reports/<report_id>/favorite")
    def api_dhis2_report_favorite(report_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            _svc().set_favorite(report_id, bool(payload.get("favorite", True)))
            return jsonify({"ok": True})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/reports/preview")
    def api_dhis2_report_preview():
        payload = request.get_json(silent=True) or {}
        try:
            data = _svc().preview(
                str(payload.get("report_id") or "").strip(),
                environment=str(payload.get("environment") or "stage"),
                period=str(payload.get("period") or ""),
                org_unit=str(payload.get("org_unit") or payload.get("orgUnit") or ""),
                parameters=payload.get("parameters") or {},
                output_format=str(payload.get("output_format") or "html"),
                confirm_live=bool(payload.get("confirm_live")),
            )
            return jsonify({"ok": True, **data})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/reports/generate")
    def api_dhis2_report_generate():
        payload = request.get_json(silent=True) or {}
        try:
            run = _svc().generate(
                str(payload.get("report_id") or "").strip(),
                environment=str(payload.get("environment") or "stage"),
                period=str(payload.get("period") or ""),
                org_unit=str(payload.get("org_unit") or payload.get("orgUnit") or ""),
                parameters=payload.get("parameters") or {},
                output_format=str(payload.get("output_format") or "html"),
                confirm_live=bool(payload.get("confirm_live")),
                actor=current_actor(),
                job_store=app.config.get("JOB_STORE"),
            )
            return jsonify({"ok": True, "run": run})
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/run-catalog")
    def api_dhis2_report_run_catalog():
        env = (request.args.get("environment") or "stage").strip()
        try:
            return jsonify(_svc().list_run_catalog(env))
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/capabilities")
    def api_dhis2_report_capabilities():
        env = (request.args.get("environment") or "stage").strip()
        try:
            return jsonify(_svc().detect_capabilities(env))
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/periods")
    def api_dhis2_report_periods():
        """Period options for report controls (cached; calendar helpers, env-keyed)."""
        remembered = (request.args.get("remembered") or "").strip()
        period_type = (request.args.get("period_type") or "quarterly").strip()
        environment = (request.args.get("environment") or "").strip()
        relative_raw = (request.args.get("relative") or request.args.get("relative_keys") or "").strip()
        relative_keys = [p.strip() for p in relative_raw.split(",") if p.strip()]
        return jsonify(
            _svc().list_periods(
                remembered=remembered,
                period_type=period_type,
                relative_keys=relative_keys or None,
                environment=environment,
            )
        )

    @app.get("/api/dhis2/reports/org-units")
    def api_dhis2_report_org_units():
        env = (request.args.get("environment") or "stage").strip()
        q = (request.args.get("q") or "").strip()
        parent_id = (request.args.get("parent_id") or request.args.get("parent") or "").strip()
        limit = request.args.get("limit", 25, type=int) or 25
        try:
            return jsonify(
                _svc().search_org_units(env, q=q, limit=limit, parent_id=parent_id)
            )
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/dhis2/reports/generate-and-view")
    def api_dhis2_report_generate_and_view():
        payload = request.get_json(silent=True) or {}
        try:
            data = _svc().generate_and_view(
                str(payload.get("report_id") or "").strip(),
                environment=str(payload.get("environment") or "stage"),
                period=str(payload.get("period") or ""),
                org_unit=str(payload.get("org_unit") or payload.get("orgUnit") or ""),
                output_format=str(payload.get("output_format") or "html"),
                confirm_live=bool(payload.get("confirm_live")),
            )
            return jsonify(data)
        except ReportSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/dhis2/reports/runs")
    def api_dhis2_report_runs():
        status = (request.args.get("status") or "").strip() or None
        return jsonify({"ok": True, "runs": _svc().store.list_runs(status=status, limit=200)})

    @app.get("/api/dhis2/reports/runs/<run_id>")
    def api_dhis2_report_run_get(run_id: str):
        run = _svc().store.get_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404
        return jsonify({"ok": True, "run": run})

    @app.post("/api/dhis2/reports/presets")
    def api_dhis2_report_preset_save():
        payload = request.get_json(silent=True) or {}
        try:
            preset = _svc().store.save_preset(
                name=str(payload.get("name") or "Preset"),
                report_id=str(payload.get("report_id") or "").strip(),
                environment=str(payload.get("environment") or "stage"),
                period=str(payload.get("period") or ""),
                org_unit=str(payload.get("org_unit") or ""),
                parameters=payload.get("parameters") or {},
                output_format=str(payload.get("output_format") or "html"),
                preset_id=str(payload.get("id") or "").strip() or None,
            )
            _audit(
                audit_actions.DHIS2_REPORT_PRESET_SAVE,
                target=preset["id"],
                detail=f"report={preset.get('report_id')}",
            )
            return jsonify({"ok": True, "preset": preset})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": redact_report_detail(str(exc))}), 400

    @app.post("/api/dhis2/reports/presets/<preset_id>/duplicate")
    def api_dhis2_report_preset_dup(preset_id: str):
        try:
            preset = _svc().store.duplicate_preset(preset_id)
            _audit(audit_actions.DHIS2_REPORT_PRESET_SAVE, target=preset["id"], detail="duplicate")
            return jsonify({"ok": True, "preset": preset})
        except KeyError:
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404

    @app.post("/api/dhis2/reports/presets/<preset_id>/delete")
    def api_dhis2_report_preset_delete(preset_id: str):
        payload = request.get_json(silent=True) or {}
        if not payload.get("confirm"):
            return jsonify({"ok": False, "error": "Confirmation required", "code": "confirm_required"}), 400
        ok = _svc().store.delete_preset(preset_id)
        if not ok:
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404
        _audit(audit_actions.DHIS2_REPORT_PRESET_DELETE, target=preset_id, detail="deleted")
        return jsonify({"ok": True})

    @app.get("/api/dhis2/reports/download/<run_id>")
    def api_dhis2_report_download(run_id: str):
        try:
            data = _svc().open_output(run_id)
            if data["kind"] == "url":
                _audit(audit_actions.DHIS2_REPORT_DOWNLOAD, target=run_id, detail="redirect-url")
                return redirect(data["url"])
            path = Path(data["path"])
            _audit(audit_actions.DHIS2_REPORT_DOWNLOAD, target=run_id, detail=f"file={path.name}")
            return Response(
                path.read_bytes(),
                mimetype="text/html; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{path.name}"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except ReportSecurityError as exc:
            return _json_error(exc)
