"""Settings → Branding routes and the public logo asset."""

from __future__ import annotations

import io
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file, url_for

from hub.audit import actions as audit_actions
from hub.branding.service import DEFAULT_FULL, DEFAULT_ICON, MAX_BYTES, BrandingError, BrandingService
from hub.jobs.auth import require_owner


def public_branding(svc: BrandingService) -> dict[str, Any]:
    state = svc.state()
    icon_url = url_for("static", filename=DEFAULT_ICON)
    full_url = url_for("static", filename=DEFAULT_FULL)
    if state["custom"]:
        asset = url_for("branding_logo", v=state["version"])
        icon_url = asset
        full_url = asset
    logo_url = full_url if state["display"] == "full" else icon_url
    # Chat/AiriX avatars always use the icon asset, never the default full wordmark PNG.
    avatar_url = icon_url
    favicon_type = str(state.get("content_type") or "image/png") if state["custom"] else "image/png"
    return {
        **state,
        "logo_url": logo_url,
        "icon_url": icon_url,
        "avatar_url": avatar_url,
        "full_url": full_url,
        "favicon_type": favicon_type,
    }


def register_branding_routes(app: Flask) -> None:
    def _svc() -> BrandingService:
        return app.config["BRANDING"]

    def _audit(action: str, **kwargs: Any) -> None:
        app.config["AUDIT"].append(action=action, detail=kwargs or {})

    def _error(exc: BrandingError):
        status = 404 if exc.code == "not_found" else 400
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status

    @app.get("/settings/branding")
    def settings_branding():
        branding = public_branding(_svc())
        _audit(
            audit_actions.BRANDING_VIEW,
            custom=branding["custom"],
            display=branding["display"],
            avatar_display=branding["avatar_display"],
        )
        return render_template("settings_branding.html", branding=branding)

    @app.get("/api/settings/branding")
    def api_settings_branding():
        return jsonify({"ok": True, "branding": public_branding(_svc())})

    @app.post("/api/settings/branding")
    @require_owner
    def api_settings_branding_save():
        upload = request.files.get("logo")
        payload = None
        filename = ""
        if upload and str(upload.filename or "").strip():
            payload = upload.read(MAX_BYTES + 1)
            filename = str(upload.filename)
        try:
            state = _svc().save(
                display=request.form.get("display"),
                fit=request.form.get("fit"),
                payload=payload,
                filename=filename,
            )
        except BrandingError as exc:
            return _error(exc)
        _audit(
            audit_actions.BRANDING_SAVE,
            custom=state["custom"],
            display=state["display"],
            avatar_display="icon",
            fit=state["fit"],
            filename=state.get("filename") or "",
        )
        return jsonify({"ok": True, "branding": public_branding(_svc())})

    @app.post("/api/settings/branding/reset")
    @require_owner
    def api_settings_branding_reset():
        state = _svc().reset()
        _audit(audit_actions.BRANDING_RESET, display=state["display"], fit=state["fit"])
        return jsonify({"ok": True, "branding": public_branding(_svc())})

    @app.get("/branding/logo")
    def branding_logo():
        try:
            path, content_type = _svc().logo_file()
        except BrandingError as exc:
            return _error(exc)
        # Copy into memory so Windows does not keep the on-disk file locked.
        return send_file(
            io.BytesIO(path.read_bytes()),
            mimetype=content_type,
            download_name=path.name,
            max_age=3600,
        )
