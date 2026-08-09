"""Flask routes for Prompting & Agent Center."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.dock import dock_shell_bootstrap, load_dock_prefs, save_dock_prefs
from hub.jobs.auth import require_owner
from hub.notebook.workspace import read_workspace


def register_agent_center_routes(app: Flask) -> None:
    def _svc() -> AgentCenterService:
        return app.config["AGENT_CENTER"]

    def _audit(action: str, **kwargs: Any) -> None:
        detail = kwargs.get("detail")
        if detail is None and "detail" not in kwargs:
            detail = {k: v for k, v in kwargs.items() if k != "action"}
        app.config["AUDIT"].append(action=action, detail=detail or {})

    def _page(profile_id: str):
        svc = _svc()
        try:
            data = svc.page_bootstrap(profile_id)
        except ValueError:
            return jsonify({"error": "Unknown assistant profile"}), 404
        _audit("ASSISTANT_CENTER_VIEW", detail={"profile_id": profile_id})
        return render_template(
            "agent_center.html",
            bootstrap=data,
            modes=data["modes"],
            agents=data["agents"],
            repositories=data["repositories"],
            prompts=data["prompts"],
            history=data["history"],
            safety=data["safety"],
            profile=data["profile"],
            conversations=data["conversations"],
        )

    @app.get("/system/ai-connections")
    def ai_connections():
        # Instant cached/placeholder status; JS refreshes providers in the background.
        connections = _svc().connections.list(probe=False)
        _audit("AI_CONNECTIONS_VIEW", detail={"providers": len(connections), "cached": True})
        return render_template("ai_connections.html", connections=connections)

    @app.get("/api/ai-connections")
    def api_ai_connections():
        refresh = request.args.get("refresh") == "1"
        return jsonify(
            {
                "connections": _svc().connections.list(
                    refresh=refresh,
                    probe=True if refresh else request.args.get("probe", "0") == "1",
                )
            }
        )

    @app.post("/api/ai-connections/<agent_id>/<action>")
    @require_owner
    def api_ai_connection_action(agent_id: str, action: str):
        try:
            return jsonify(_svc().connections.action(agent_id, action))
        except KeyError:
            return jsonify({"error": "Unknown provider"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/personal/aira")
    def personal_aira():
        return _page("aira")

    @app.get("/work/airix")
    def work_airix():
        return _page("okarun")

    @app.get("/work/okarun")
    def work_okarun():
        # Legacy path — display name is AiriX.
        return redirect(url_for("work_airix"))

    @app.get("/agents")
    @app.get("/prompting")
    def agent_center():
        return redirect(url_for("work_airix"))

    @app.get("/api/assistant-dock/bootstrap")
    def api_assistant_dock_bootstrap():
        """Lightweight dock bootstrap — never probes providers."""
        notebook = app.config["NOTEBOOK"]
        workspace = read_workspace(request, notebook.db)
        return jsonify(
            dock_shell_bootstrap(
                notebook.db,
                workspace=workspace,
                endpoint=request.args.get("endpoint") or request.headers.get("X-Hub-Endpoint"),
            )
        )

    @app.get("/api/assistant-dock/prefs")
    def api_assistant_dock_prefs_get():
        notebook = app.config["NOTEBOOK"]
        workspace = read_workspace(request, notebook.db)
        prefs = load_dock_prefs(notebook.db, workspace)
        return jsonify({"ok": True, "prefs": prefs})

    @app.put("/api/assistant-dock/prefs")
    def api_assistant_dock_prefs_put():
        notebook = app.config["NOTEBOOK"]
        workspace = read_workspace(request, notebook.db)
        payload = request.get_json(silent=True) or {}
        prefs = save_dock_prefs(notebook.db, workspace, payload)
        return jsonify({"ok": True, "prefs": prefs})

    @app.get("/api/agents")
    @app.get("/api/assistants/<profile_id>/agents")
    def api_agents_list(profile_id: str = "okarun"):
        try:
            _svc().page_bootstrap(profile_id)
        except ValueError:
            return jsonify({"error": "Unknown assistant profile"}), 404
        mode = request.args.get("mode")
        refresh = request.args.get("refresh") == "1"
        # Default: serve cache/placeholder; refresh=1 probes providers.
        probe = refresh or request.args.get("probe", "0") == "1"
        return jsonify({"agents": _svc().list_agents(mode=mode, probe=probe, profile_id=profile_id)})

    @app.get("/api/agents/<agent_id>/models")
    @app.get("/api/assistants/<profile_id>/agents/<agent_id>/models")
    def api_agent_models(agent_id: str, profile_id: str = "okarun"):
        try:
            _svc().page_bootstrap(profile_id)
            mode = request.args.get("mode")
            return jsonify(_svc().list_models(agent_id, mode=mode))
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "unknown_profile"}), 404

    @app.post("/api/agents/context/preview")
    @app.post("/api/assistants/<profile_id>/context/preview")
    def api_context_preview(profile_id: str = "okarun"):
        payload = {**(request.get_json(silent=True) or {}), "profile_id": profile_id}
        try:
            preview = _svc().preview_context(payload)
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 400
        # Strip full packed prompt / file contents from JSON response size; keep preview fields.
        public = {
            k: v
            for k, v in preview.items()
            if k not in {"packed_prompt", "instruction_contents", "file_contents"}
        }
        return jsonify(public)

    @app.post("/api/agents/runs")
    @app.post("/api/assistants/<profile_id>/runs")
    @require_owner
    def api_agent_run_start(profile_id: str = "okarun"):
        payload = {**(request.get_json(silent=True) or {}), "profile_id": profile_id}
        try:
            run = _svc().start_run(payload)
            return jsonify({"run": _public_run(run)}), 201
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 400

    @app.get("/api/agents/runs/<run_id>")
    @app.get("/api/assistants/<profile_id>/runs/<run_id>")
    def api_agent_run_get(run_id: str, profile_id: str = "okarun"):
        try:
            run = _svc().get_run(run_id, profile_id=profile_id)
            return jsonify({"run": _public_run(run, include_body=True)})
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.post("/api/agents/runs/<run_id>/cancel")
    @app.post("/api/assistants/<profile_id>/runs/<run_id>/cancel")
    @require_owner
    def api_agent_run_cancel(run_id: str, profile_id: str = "okarun"):
        try:
            run = _svc().cancel_run(run_id, profile_id=profile_id)
            return jsonify({"run": _public_run(run, include_body=True)})
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.post("/api/assistants/<profile_id>/runs/<run_id>/retry")
    @require_owner
    def api_agent_run_retry(profile_id: str, run_id: str):
        try:
            run = _svc().retry_run(run_id, profile_id=profile_id)
            return jsonify({"run": _public_run(run)}), 201
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.get("/api/agents/runs")
    @app.get("/api/assistants/<profile_id>/runs")
    def api_agent_runs(profile_id: str = "okarun"):
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"runs": _svc().history(limit=limit or 50, profile_id=profile_id)})

    @app.get("/api/agents/prompts")
    @app.get("/api/assistants/<profile_id>/prompts")
    def api_prompts_list(profile_id: str = "okarun"):
        return jsonify({"prompts": _svc().store.list_prompts(profile_id=profile_id)})

    @app.post("/api/agents/prompts")
    @app.post("/api/assistants/<profile_id>/prompts")
    @require_owner
    def api_prompts_save(profile_id: str = "okarun"):
        payload = request.get_json(silent=True) or {}
        prompt = _svc().store.save_prompt(
            title=str(payload.get("title") or "Untitled prompt"),
            body=str(payload.get("body") or ""),
            mode=str(payload.get("mode") or "ask"),
            tags=list(payload.get("tags") or []),
            favorite=bool(payload.get("favorite")),
            prompt_id=payload.get("id"),
            profile_id=profile_id,
        )
        _audit("AGENT_PROMPT_SAVE", detail={"prompt_id": prompt.get("id")})
        return jsonify({"prompt": prompt}), 201

    @app.delete("/api/assistants/<profile_id>/prompts/<prompt_id>")
    @require_owner
    def api_prompts_delete(profile_id: str, prompt_id: str):
        ok = _svc().store.delete_prompt(prompt_id, profile_id=profile_id)
        if not ok:
            return jsonify({"error": "Not found"}), 404
        _audit("AGENT_PROMPT_DELETE", detail={"prompt_id": prompt_id})
        return jsonify({"ok": True})


def _public_run(run: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
    out = {
        "id": run.get("id"),
        "profile_id": run.get("profile_id"),
        "conversation_id": run.get("conversation_id"),
        "status": run.get("status"),
        "mode": run.get("mode"),
        "agent_id": run.get("agent_id"),
        "agent_label": run.get("agent_label"),
        "model": run.get("model"),
        "repository_ids": run.get("repository_ids") or [],
        "prompt": run.get("prompt") if include_body else (run.get("prompt") or "")[:200],
        "error": run.get("error") or "",
        "cancel_requested": bool(run.get("cancel_requested")),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "referenced_files": run.get("referenced_files") or [],
        "context": {
            "roots": (run.get("context") or {}).get("roots") or [],
            "files": (run.get("context") or {}).get("files") or [],
            "excluded_secrets": (run.get("context") or {}).get("excluded_secrets") or [],
            "packed_prompt_chars": (run.get("context") or {}).get("packed_prompt_chars"),
            "tools": (run.get("context") or {}).get("tools") or {},
            "included_sources": (run.get("context") or {}).get("included_sources") or [],
            "excluded_sources": (run.get("context") or {}).get("excluded_sources") or [],
            "connection": (run.get("context") or {}).get("connection") or {},
        },
    }
    if include_body:
        out["answer"] = run.get("answer") or ""
        out["logs"] = run.get("logs") or ""
        out["packed_prompt_preview"] = (run.get("packed_prompt") or "")[:1200]
        out["tool_activity"] = run.get("tool_activity") or []
        out["usage"] = run.get("usage") or {}
    else:
        out["tool_activity_count"] = len(run.get("tool_activity") or [])
        out["usage"] = run.get("usage") or {}
    return out
