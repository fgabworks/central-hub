"""Operations/Health routes for verified Central Hub process controls."""

from __future__ import annotations

from flask import Flask, jsonify, request

from hub.audit import actions as audit_actions
from hub.jobs.auth import current_actor, require_owner
from hub.repository_workspace.hub_process_manager import (
    STOP_CENTRAL_HUB_CONFIRMATION,
    CentralHubProcessManager,
)


def register_central_hub_process_routes(app: Flask) -> None:
    def _manager() -> CentralHubProcessManager:
        return app.config["CENTRAL_HUB_PROCESSES"]

    @app.get("/api/health/central-hub-processes")
    @require_owner
    def api_central_hub_processes():
        inventory = _manager().inventory()
        hub_count = len(inventory.get("hub_processes") or [])
        other_count = len(inventory.get("other_python") or [])
        app.config["AUDIT"].append(
            action=audit_actions.CENTRAL_HUB_PROCESS_SCAN,
            actor=current_actor(),
            target="central-hub",
            detail=f"hub={hub_count} other_python={other_count}",
            ok=True,
        )
        return jsonify({
            "ok": True,
            "count": hub_count,
            "hub_processes": inventory.get("hub_processes") or [],
            "other_python": inventory.get("other_python") or [],
            "instances": inventory.get("instances") or [],
            "current_pid": inventory.get("current_pid"),
            "registry": inventory.get("registry") or {},
            "stop_central_hub_phrase": STOP_CENTRAL_HUB_CONFIRMATION,
        })

    @app.post("/api/health/central-hub-processes/stop-stale")
    @require_owner
    def api_central_hub_stop_stale():
        payload = request.get_json(silent=True) or {}
        try:
            result = _manager().stop_stale(
                actor=current_actor(), confirm=bool(payload.get("confirm"))
            )
            return jsonify(result), 200 if result.get("ok") else 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "confirmation_required"}), 400

    @app.post("/api/health/central-hub-processes/stop")
    @require_owner
    def api_central_hub_stop_owned():
        payload = request.get_json(silent=True) or {}
        try:
            result = _manager().stop_owned(
                pid=int(payload.get("pid") or 0),
                identity_token=str(payload.get("identity_token") or ""),
                ownership_token_value=str(payload.get("ownership_token") or ""),
                actor=current_actor(),
                confirm=bool(payload.get("confirm")),
                include_tree=bool(payload.get("include_tree")),
            )
            return jsonify(result), (202 if result.get("queued") else (200 if result.get("ok") else 409))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "stop_refused"}), 400

    @app.post("/api/health/central-hub-processes/restart-one")
    @require_owner
    def api_central_hub_restart_owned():
        payload = request.get_json(silent=True) or {}
        try:
            status = _manager().restart_owned(
                pid=int(payload.get("pid") or 0),
                identity_token=str(payload.get("identity_token") or ""),
                ownership_token_value=str(payload.get("ownership_token") or ""),
                actor=current_actor(),
                confirm=bool(payload.get("confirm")),
            )
            return jsonify({"ok": True, **status}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "restart_refused"}), 400

    @app.post("/api/health/central-hub-processes/stop-central-hub")
    @require_owner
    def api_central_hub_stop_tree():
        payload = request.get_json(silent=True) or {}
        try:
            status = _manager().queue_control(
                action="stop_central_hub",
                actor=current_actor(),
                typed_confirmation=str(payload.get("typed_confirmation") or ""),
            )
            return jsonify({"ok": True, **status}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "confirmation_required"}), 400

    @app.post("/api/health/central-hub-processes/stop-all")
    @require_owner
    def api_central_hub_stop_all():
        payload = request.get_json(silent=True) or {}
        try:
            status = _manager().queue_control(
                action="stop_all",
                actor=current_actor(),
                typed_confirmation=str(payload.get("typed_confirmation") or ""),
            )
            return jsonify({"ok": True, **status}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "confirmation_required"}), 400

    @app.post("/api/health/central-hub-processes/restart")
    @require_owner
    def api_central_hub_restart():
        payload = request.get_json(silent=True) or {}
        try:
            status = _manager().queue_control(
                action="restart", actor=current_actor(), confirm=bool(payload.get("confirm"))
            )
            return jsonify({"ok": True, **status}), 202
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "confirmation_required"}), 400

    @app.get("/api/health/central-hub-processes/actions/<action_id>")
    @require_owner
    def api_central_hub_action_status(action_id: str):
        status = _manager().action_status(action_id)
        if status is None:
            return jsonify({"ok": False, "error": "Action not found."}), 404
        return jsonify({"ok": True, **status})
