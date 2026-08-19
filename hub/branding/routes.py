"""Settings → Branding routes and public logo/avatar assets."""

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
    avatar_url = icon_url
    if state["custom_logo"]:
        full_url = url_for("branding_logo", v=state["version"])
    if state["custom_avatar"]:
        avatar_url = url_for("branding_avatar", v=state["avatar_version"])
    logo_url = full_url if state["display"] == "full" else icon_url
    favicon_type = "image/png"
    return {
        **state,
        "fit": "contain",
        "logo_url": logo_url,
        "icon_url": icon_url,
        "avatar_url": avatar_url,
        "full_url": full_url,
        "favicon_type": favicon_type,
        "logo_kind": _kind(state.get("content_type") if state["custom_logo"] else "image/png"),
        "avatar_kind": _kind(state.get("avatar_content_type") if state["custom_avatar"] else "image/png"),
    }


def _kind(content_type: str) -> str:
    raw = str(content_type or "").lower()
    if "svg" in raw:
        return "SVG"
    if "webp" in raw:
        return "WEBP"
    return "PNG"


def _read_upload(upload) -> tuple[bytes | None, str]:
    if not upload or not str(upload.filename or "").strip():
        return None, ""
    return upload.read(MAX_BYTES + 1), str(upload.filename)


def register_branding_routes(app: Flask) -> None:
    def _svc() -> BrandingService:
        return app.config["BRANDING"]

    def _audit(action: str, **kwargs: Any) -> None:
        app.config["AUDIT"].append(action=action, detail=kwargs or {})

    def _error(exc: BrandingError):
        status = 404 if exc.code == "not_found" else 400
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status

    def _send_asset(path, content_type: str):
        return send_file(
            io.BytesIO(path.read_bytes()),
            mimetype=content_type,
            download_name=path.name,
            max_age=3600,
        )

    @app.get("/settings/branding")
    def settings_branding():
        branding = public_branding(_svc())
        _audit(
            audit_actions.BRANDING_VIEW,
            custom=branding["custom"],
            custom_logo=branding["custom_logo"],
            custom_avatar=branding["custom_avatar"],
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
        logo_payload, logo_name = _read_upload(request.files.get("logo"))
        avatar_payload, avatar_name = _read_upload(request.files.get("avatar"))
        try:
            state = _svc().save(
                display=request.form.get("display"),
                fit="contain",
                payload=logo_payload,
                filename=logo_name,
                avatar_payload=avatar_payload,
                avatar_filename=avatar_name,
                remove_logo=str(request.form.get("remove_logo") or "") in {"1", "true", "on"},
                remove_avatar=str(request.form.get("remove_avatar") or "") in {"1", "true", "on"},
            )
        except BrandingError as exc:
            return _error(exc)
        _audit(
            audit_actions.BRANDING_SAVE,
            custom=state["custom"],
            custom_logo=state["custom_logo"],
            custom_avatar=state["custom_avatar"],
            display=state["display"],
            avatar_display="icon",
            fit=state["fit"],
            filename=state.get("filename") or "",
            avatar_filename=state.get("avatar_filename") or "",
            remove_logo=str(request.form.get("remove_logo") or "") in {"1", "true", "on"},
            remove_avatar=str(request.form.get("remove_avatar") or "") in {"1", "true", "on"},
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
        return _send_asset(path, content_type)

    @app.get("/branding/avatar")
    def branding_avatar():
        try:
            path, content_type = _svc().avatar_file()
        except BrandingError as exc:
            return _error(exc)
        return _send_asset(path, content_type)
