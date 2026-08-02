"""Operations/Health routes for verified Central Hub process controls."""

from __future__ import annotations

from flask import Flask, jsonify, request

from hub.audit import actions as audit_actions
from hub.jobs.auth import current_actor, require_owner
from hub.repository_workspace.hub_process_manager import CentralHubProcessManager


def register_central_hub_process_routes(app: Flask) -> None:
    def _manager() -> CentralHubProcessManager:
        return app.config["CENTRAL_HUB_PROCESSES"]

    @app.get("/api/health/central-hub-processes")
    @require_owner
    def api_central_hub_processes():
        instances = [item.to_public() for item in _manager().scan()]
        app.config["AUDIT"].append(
            action=audit_actions.CENTRAL_HUB_PROCESS_SCAN,
            actor=current_actor(),
            target="central-hub",
            detail=f"verified instances={len(instances)}",
            ok=True,
        )
        return jsonify({
            "ok": True,
            "count": len(instances),
            "instances": instances,
            "current_pid": next(
                (item["pid"] for item in instances if item.get("current")), None
            ),
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
