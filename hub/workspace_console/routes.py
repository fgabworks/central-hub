"""Flask routes for the VS Code-style Workspace Console."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request

from hub.jobs.auth import require_owner
from hub.notebook.workspace import read_workspace
from hub.workspace_console.prefs import (
    console_shell_bootstrap,
    load_console_prefs,
    save_console_prefs,
)


def register_workspace_console_routes(app: Flask) -> None:
    def _svc():
        return app.config["WORKSPACE_CONSOLE"]

    def _notebook_db():
        return app.config["NOTEBOOK"].db

    def _workspace() -> str:
        return read_workspace(request, _notebook_db())

    def _audit(action: str, **kwargs: Any) -> None:
        detail = kwargs.get("detail")
        if detail is None and "detail" not in kwargs:
            detail = {k: v for k, v in kwargs.items() if k != "action"}
        app.config["AUDIT"].append(action=action, detail=detail or {})

    @app.get("/api/workspace-console/bootstrap")
    def api_workspace_console_bootstrap():
        return jsonify(console_shell_bootstrap(_notebook_db(), workspace=_workspace()))

    @app.get("/api/workspace-console/prefs")
    def api_workspace_console_prefs_get():
        prefs = load_console_prefs(_notebook_db(), _workspace())
        return jsonify({"ok": True, "prefs": prefs})

    @app.put("/api/workspace-console/prefs")
    def api_workspace_console_prefs_put():
        payload = request.get_json(silent=True) or {}
        prefs = save_console_prefs(_notebook_db(), _workspace(), payload)
        return jsonify({"ok": True, "prefs": prefs})

    @app.get("/api/workspace-console/problems")
    def api_workspace_console_problems():
        limit = request.args.get("limit", 80, type=int)
        return jsonify(_svc().problems(limit=limit or 80))

    @app.get("/api/workspace-console/output")
    def api_workspace_console_output():
        return jsonify(
            _svc().output(
                source=request.args.get("source") or "all",
                repo_id=request.args.get("repo_id") or "",
                run_id=request.args.get("run_id") or "",
                offset=request.args.get("offset", 0, type=int) or 0,
                limit=request.args.get("limit", 200, type=int) or 200,
            )
        )

    @app.get("/api/workspace-console/debug")
    def api_workspace_console_debug():
        return jsonify(_svc().debug(limit=request.args.get("limit", 60, type=int) or 60))

    @app.get("/api/workspace-console/terminal")
    def api_workspace_console_terminal():
        return jsonify(_svc().terminal_catalog())

    @app.get("/api/workspace-console/ports")
    def api_workspace_console_ports():
        # Explicit user-driven tab load only — never called from page navigation bootstrap.
        return jsonify(_svc().ports())

    @app.post("/api/workspace-console/terminal/start")
    @require_owner
    def api_workspace_console_terminal_start():
        """Start an approved repository run profile (controlled terminal)."""
        payload = request.get_json(silent=True) or {}
        repo_id = str(payload.get("repository_id") or "").strip()
        profile_id = str(payload.get("profile_id") or "").strip()
        environment = str(payload.get("environment") or "development").strip() or "development"
        if not repo_id or not profile_id:
            return jsonify({"ok": False, "error": "repository_id and profile_id are required"}), 400
        registry = app.config.get("REGISTRY")
        workspace = app.config.get("REPO_WORKSPACE")
        if registry is None or workspace is None:
            return jsonify({"ok": False, "error": "Repository workspace unavailable"}), 503
        repo = registry.get(repo_id)
        if repo is None:
            return jsonify({"ok": False, "error": "Unknown repository"}), 404
        try:
            port = payload.get("port")
            port_i = int(port) if port not in (None, "") else None
            run = workspace.start_run(
                repo,
                profile_id=profile_id,
                environment=environment,
                port=port_i,
                confirm_live=bool(payload.get("confirm_live")),
            )
            _audit(
                "WORKSPACE_CONSOLE_TERMINAL_START",
                detail={"repository_id": repo_id, "profile_id": profile_id, "environment": environment},
            )
            return jsonify({"ok": True, "run": run})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/workspace-console/ports/stop")
    @require_owner
    def api_workspace_console_ports_stop():
        """Proxy to existing verified stop path — no duplicated fingerprints."""
        payload = request.get_json(silent=True) or {}
        repo_id = str(payload.get("repository_id") or "").strip()
        registry = app.config.get("REGISTRY")
        workspace = app.config.get("REPO_WORKSPACE")
        if registry is None or workspace is None:
            return jsonify({"ok": False, "error": "Repository workspace unavailable"}), 503
        repo = registry.get(repo_id)
        if repo is None:
            return jsonify({"ok": False, "error": "Unknown repository"}), 404
        try:
            result = workspace.stop_detected_process(
                repo,
                pid=int(payload.get("pid")),
                identity_token=str(payload.get("identity_token") or ""),
                force=bool(payload.get("force")),
                confirm=bool(payload.get("confirm")),
                typed_confirm=payload.get("typed_confirm"),
                run_id=payload.get("run_id"),
                port=payload.get("port"),
                managed_by_hub=payload.get("managed_by_hub"),
                confidence=payload.get("confidence"),
            )
            _audit(
                "WORKSPACE_CONSOLE_PORT_STOP",
                detail={"repository_id": repo_id, "pid": payload.get("pid"), "ok": True},
            )
            return jsonify({"ok": True, **result})
        except Exception as exc:
            code = getattr(exc, "code", "error")
            return jsonify({"ok": False, "error": str(exc), "code": code}), 400
