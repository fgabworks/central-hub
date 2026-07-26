"""Flask routes for Prompting & Agent Center."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, render_template, request

from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.jobs.auth import require_owner


def register_agent_center_routes(app: Flask) -> None:
    def _svc() -> AgentCenterService:
        return app.config["AGENT_CENTER"]

    def _audit(action: str, **kwargs: Any) -> None:
        detail = kwargs.get("detail")
        if detail is None and "detail" not in kwargs:
            detail = {k: v for k, v in kwargs.items() if k != "action"}
        app.config["AUDIT"].append(action=action, detail=detail or {})

    @app.get("/agents")
    @app.get("/prompting")
    def agent_center():
        svc = _svc()
        data = svc.page_bootstrap()
        _audit("AGENT_CENTER_VIEW")
        return render_template(
            "agent_center.html",
            bootstrap=data,
            modes=data["modes"],
            agents=data["agents"],
            repositories=data["repositories"],
            prompts=data["prompts"],
            history=data["history"],
            safety=data["safety"],
        )

    @app.get("/api/agents")
    def api_agents_list():
        mode = request.args.get("mode")
        return jsonify({"agents": _svc().list_agents(mode=mode)})

    @app.get("/api/agents/<agent_id>/models")
    def api_agent_models(agent_id: str):
        try:
            mode = request.args.get("mode")
            return jsonify(_svc().list_models(agent_id, mode=mode))
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.post("/api/agents/context/preview")
    def api_context_preview():
        payload = request.get_json(silent=True) or {}
        preview = _svc().preview_context(payload)
        # Strip full packed prompt / file contents from JSON response size; keep preview fields.
        public = {
            k: v
            for k, v in preview.items()
            if k not in {"packed_prompt", "instruction_contents", "file_contents"}
        }
        return jsonify(public)

    @app.post("/api/agents/runs")
    @require_owner
    def api_agent_run_start():
        payload = request.get_json(silent=True) or {}
        try:
            run = _svc().start_run(payload)
            return jsonify({"run": _public_run(run)}), 201
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 400

    @app.get("/api/agents/runs/<run_id>")
    def api_agent_run_get(run_id: str):
        try:
            run = _svc().get_run(run_id)
            return jsonify({"run": _public_run(run, include_body=True)})
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.post("/api/agents/runs/<run_id>/cancel")
    @require_owner
    def api_agent_run_cancel(run_id: str):
        try:
            run = _svc().cancel_run(run_id)
            return jsonify({"run": _public_run(run, include_body=True)})
        except AgentCenterError as exc:
            return jsonify({"error": str(exc), "code": exc.code}), 404

    @app.get("/api/agents/runs")
    def api_agent_runs():
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"runs": _svc().history(limit=limit or 50)})

    @app.get("/api/agents/prompts")
    def api_prompts_list():
        return jsonify({"prompts": _svc().store.list_prompts()})

    @app.post("/api/agents/prompts")
    @require_owner
    def api_prompts_save():
        payload = request.get_json(silent=True) or {}
        prompt = _svc().store.save_prompt(
            title=str(payload.get("title") or "Untitled prompt"),
            body=str(payload.get("body") or ""),
            mode=str(payload.get("mode") or "ask"),
            tags=list(payload.get("tags") or []),
            favorite=bool(payload.get("favorite")),
            prompt_id=payload.get("id"),
        )
        _audit("AGENT_PROMPT_SAVE", detail={"prompt_id": prompt.get("id")})
        return jsonify({"prompt": prompt}), 201

    @app.delete("/api/agents/prompts/<prompt_id>")
    @require_owner
    def api_prompts_delete(prompt_id: str):
        ok = _svc().store.delete_prompt(prompt_id)
        if not ok:
            return jsonify({"error": "Not found"}), 404
        _audit("AGENT_PROMPT_DELETE", detail={"prompt_id": prompt_id})
        return jsonify({"ok": True})


def _public_run(run: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
    out = {
        "id": run.get("id"),
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
