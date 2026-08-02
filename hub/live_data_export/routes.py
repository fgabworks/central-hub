"""Flask routes for Live Data Export."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

from hub.audit import actions as audit_actions
from hub.jobs.auth import current_actor
from hub.live_data_export.registry import RegistryError
from hub.live_data_export.security import ExportSafetyError
from hub.live_data_export.service import LiveDataExportService


def register_live_data_export_routes(app: Flask) -> None:
    def _svc() -> LiveDataExportService:
        return app.config["LIVE_DATA_EXPORT"]

    def _audit(action: str, *, target: str, detail: str, ok: bool = True) -> None:
        app.config["AUDIT"].append(
            action=action,
            actor=current_actor(),
            target=target,
            detail=detail[:2000],
            ok=ok,
        )

    def _json_err(exc: Exception, status: int = 400):
        return jsonify({"ok": False, "error": str(exc)}), status

    @app.get("/live-data-export")
    def live_data_export():
        boot = _svc().bootstrap()
        _audit(
            audit_actions.LIVE_EXPORT_VIEW,
            target="live-data-export",
            detail="Opened Live Data Export",
        )
        return render_template(
            "live_data_export.html",
            page_title=boot["page_title"],
            subtitle=boot["subtitle"],
            bootstrap=boot,
        )

    @app.get("/api/live-data-export/bootstrap")
    def api_live_export_bootstrap():
        return jsonify(_svc().bootstrap())

    @app.get("/api/live-data-export/sources")
    def api_live_export_sources():
        env = request.args.get("environment") or None
        return jsonify({"ok": True, "sources": _svc().list_sources(environment=env)})

    @app.post("/api/live-data-export/preview")
    def api_live_export_preview():
        data = request.get_json(silent=True) or {}
        try:
            result = _svc().preview(
                source_key=str(data.get("source_key") or ""),
                filters=dict(data.get("filters") or {}),
                columns=data.get("columns"),
                actor=current_actor(),
            )
            _audit(
                audit_actions.LIVE_EXPORT_PREVIEW,
                target=str(data.get("source_key") or ""),
                detail=(
                    f"env={result.get('filters', {}).get('environment')} "
                    f"rows~={result.get('estimated_rows')} cols={len(result.get('columns') or [])}"
                ),
            )
            return jsonify(result)
        except (ExportSafetyError, RegistryError) as exc:
            _audit(
                audit_actions.LIVE_EXPORT_PREVIEW,
                target=str(data.get("source_key") or ""),
                detail=str(exc),
                ok=False,
            )
            return _json_err(exc, 400)

    @app.post("/api/live-data-export/export")
    def api_live_export_generate():
        data = request.get_json(silent=True) or {}
        try:
            result = _svc().export(
                source_key=str(data.get("source_key") or ""),
                filters=dict(data.get("filters") or {}),
                columns=data.get("columns"),
                format=str(data.get("format") or "csv"),
                actor=current_actor(),
                force_async=bool(data.get("force_async")),
            )
            job = result.get("job") or {}
            _audit(
                audit_actions.LIVE_EXPORT_GENERATE,
                target=str(data.get("source_key") or ""),
                detail=(
                    f"job={job.get('id')} mode={result.get('mode')} "
                    f"fmt={job.get('format')} status={job.get('status')}"
                ),
                ok=bool(result.get("ok")),
            )
            return jsonify(result), (200 if result.get("ok") or result.get("mode") == "async" else 400)
        except (ExportSafetyError, RegistryError) as exc:
            _audit(
                audit_actions.LIVE_EXPORT_GENERATE,
                target=str(data.get("source_key") or ""),
                detail=str(exc),
                ok=False,
            )
            return _json_err(exc, 400)

    @app.get("/api/live-data-export/jobs")
    def api_live_export_jobs():
        return jsonify({"ok": True, "jobs": _svc().list_jobs()})

    @app.get("/api/live-data-export/jobs/<job_id>")
    def api_live_export_job(job_id: str):
        job = _svc().get_job(job_id)
        if not job:
            return _json_err(ExportSafetyError("Export job not found"), 404)
        return jsonify({"ok": True, "job": job})

    @app.post("/api/live-data-export/jobs/<job_id>/cancel")
    def api_live_export_cancel(job_id: str):
        try:
            job = _svc().cancel(job_id, actor=current_actor())
            _audit(
                audit_actions.LIVE_EXPORT_CANCEL,
                target=job_id,
                detail=f"status={job.get('status')}",
            )
            return jsonify({"ok": True, "job": job})
        except ExportSafetyError as exc:
            return _json_err(exc, 400)

    @app.get("/api/live-data-export/jobs/<job_id>/download")
    def api_live_export_download(job_id: str):
        token = request.args.get("token") or ""
        try:
            path, filename, job = _svc().resolve_download(
                job_id, token=token, actor=current_actor()
            )
            _audit(
                audit_actions.LIVE_EXPORT_DOWNLOAD,
                target=job_id,
                detail=f"source={job.get('source_key')} size={job.get('file_size')}",
            )
            return send_file(
                path,
                as_attachment=True,
                download_name=filename,
                mimetype=_mime(job.get("format") or "csv"),
            )
        except ExportSafetyError as exc:
            _audit(
                audit_actions.LIVE_EXPORT_DOWNLOAD,
                target=job_id,
                detail=str(exc),
                ok=False,
            )
            status = 410 if "expired" in str(exc).lower() else 403
            return _json_err(exc, status)

    @app.get("/api/live-data-export/history")
    def api_live_export_history():
        return jsonify({"ok": True, "history": _svc().store.list_history(limit=50)})

    @app.get("/api/live-data-export/presets")
    def api_live_export_presets():
        return jsonify({"ok": True, "presets": _svc().store.list_presets(limit=50)})

    @app.post("/api/live-data-export/presets")
    def api_live_export_preset_save():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return _json_err(ExportSafetyError("Preset name is required"))
        preset = _svc().store.save_preset(
            name=name,
            source_key=str(data.get("source_key") or ""),
            environment=str((data.get("filters") or {}).get("environment") or data.get("environment") or "live"),
            filters=dict(data.get("filters") or {}),
            columns=list(data.get("columns") or []),
            format=str(data.get("format") or "csv"),
            actor=current_actor(),
        )
        _audit(
            audit_actions.LIVE_EXPORT_PRESET_SAVE,
            target=preset["id"],
            detail=f"name={name} source={preset['source_key']}",
        )
        return jsonify({"ok": True, "preset": preset})

    @app.delete("/api/live-data-export/presets/<preset_id>")
    def api_live_export_preset_delete(preset_id: str):
        ok = _svc().store.delete_preset(preset_id)
        _audit(
            audit_actions.LIVE_EXPORT_PRESET_DELETE,
            target=preset_id,
            detail="deleted" if ok else "missing",
            ok=ok,
        )
        return jsonify({"ok": ok})


def _mime(fmt: str) -> str:
    return {
        "csv": "text/csv",
        "csv_gz": "application/gzip",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(fmt, "application/octet-stream")
