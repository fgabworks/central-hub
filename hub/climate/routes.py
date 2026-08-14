"""Routes for the CLIMATE code workspace shell."""

from __future__ import annotations

from typing import Any, Callable

from flask import Flask, jsonify, render_template, request

from hub.climate.coding import ClimateCodingError
from hub.climate.service import ClimateService, normalize_workspace
from hub.repository_workspace.security import WorkspaceSecurityError


def register_climate_routes(app: Flask) -> None:
    def _svc() -> ClimateService:
        return app.config["CLIMATE"]

    def _error(exc: Exception, status: int | None = None):
        code = getattr(exc, "code", "error")
        http = status or {
            "not_found": 404,
            "workspace_isolation": 403,
            "authentication_required": 409,
            "unavailable": 409,
            "repository_unavailable": 409,
            "proposal_conflict": 409,
            "proposal_closed": 409,
        }.get(code, 400)
        return jsonify({"ok": False, "error": str(exc), "code": code}), http

    def _repo_call(workspace: str, repo_id: str, fn: Callable[[Any], dict[str, Any]]):
        try:
            repo = _svc().require_repo(workspace, repo_id)
            return jsonify({"ok": True, **fn(repo)})
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)

    def _page(workspace: str):
        ws = normalize_workspace(workspace)
        initial_repo = str(request.args.get("repo") or "").strip()
        bootstrap = _svc().bootstrap(ws, initial_repo)
        return render_template(
            "climate.html",
            climate=bootstrap,
            workspace=ws,
            skip_assistant_dock=True,
            skip_activity_rail=True,
            skip_workspace_console=True,
            skip_global_notepad=True,
        )

    @app.get("/work/climate")
    def work_climate():
        return _page("work")

    @app.get("/personal/climate")
    def personal_climate():
        return _page("personal")

    @app.get("/api/climate/<workspace>/bootstrap")
    def api_climate_bootstrap(workspace: str):
        try:
            return jsonify(_svc().bootstrap(workspace, str(request.args.get("repo") or "")))
        except ClimateCodingError as exc:
            return _error(exc)

    @app.get("/api/climate/<workspace>/providers/<provider>/models")
    def api_climate_models(workspace: str, provider: str):
        try:
            normalize_workspace(workspace)
            return jsonify({"ok": True, **_svc().coding.models(
                provider, refresh=request.args.get("refresh") == "1"
            )})
        except ClimateCodingError as exc:
            return _error(exc)

    @app.get("/api/climate/<workspace>/providers/codex/rate-limits")
    def api_climate_codex_rate_limits(workspace: str):
        try:
            return jsonify(_svc().codex_rate_limits(
                workspace, refresh=request.args.get("refresh") == "1"
            ))
        except ClimateCodingError as exc:
            return _error(exc)

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/tree")
    def api_climate_tree(workspace: str, repo_id: str):
        show_excluded = request.args.get("show_excluded") == "1"
        return _repo_call(
            workspace,
            repo_id,
            lambda repo: _svc().repository_workspace.tree(
                repo, include_excluded=show_excluded
            ),
        )

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/file")
    def api_climate_file(workspace: str, repo_id: str):
        path = str(request.args.get("path") or "")
        return _repo_call(
            workspace, repo_id,
            lambda repo: {"file": _svc().repository_workspace.preview(repo, path)},
        )

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/search")
    def api_climate_search(workspace: str, repo_id: str):
        q = str(request.args.get("q") or "").strip()
        mode = str(request.args.get("mode") or "content").lower()
        if mode not in {"content", "filename"}:
            mode = "content"
        return _repo_call(
            workspace, repo_id,
            lambda repo: _svc().repository_workspace.search(repo, q=q, mode=mode),
        )

    @app.post("/api/climate/<workspace>/repositories/<repo_id>/preview-save")
    def api_climate_preview_save(workspace: str, repo_id: str):
        payload = request.get_json(silent=True) or {}
        content = payload.get("content")
        if not isinstance(content, str):
            return _error(ClimateCodingError("content must be a string"))
        return _repo_call(
            workspace, repo_id,
            lambda repo: _svc().repository_workspace.preview_save(
                repo, str(payload.get("path") or ""), content
            ),
        )

    @app.post("/api/climate/<workspace>/repositories/<repo_id>/save")
    def api_climate_save(workspace: str, repo_id: str):
        payload = request.get_json(silent=True) or {}
        content = payload.get("content")
        if not isinstance(content, str):
            return _error(ClimateCodingError("content must be a string"))
        return _repo_call(
            workspace, repo_id,
            lambda repo: _svc().repository_workspace.save(
                repo, str(payload.get("path") or ""), content,
                confirm=bool(payload.get("confirm")),
            ),
        )

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/git/status")
    def api_climate_git_status(workspace: str, repo_id: str):
        return _repo_call(workspace, repo_id, _svc().repository_workspace.changes)

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/git/diff")
    def api_climate_git_diff(workspace: str, repo_id: str):
        path = str(request.args.get("path") or "").strip() or None
        return _repo_call(
            workspace, repo_id,
            lambda repo: _svc().repository_workspace.diff(repo, path),
        )

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/ports")
    def api_climate_ports(workspace: str, repo_id: str):
        try:
            payload = _svc().ports(workspace, repo_id)
            terms = app.config.get("WC_TERMINALS")
            if terms is not None and payload.get("ports") is not None:
                payload["ports"] = terms.annotate_ports(list(payload["ports"]))
                for row in payload["ports"]:
                    if row.get("terminal_owned") or row.get("terminal_session_id"):
                        row["source"] = "terminal"
                        row["session"] = row.get("terminal_name") or row.get("terminal_session_id") or row.get("session")
                    elif row.get("terminal_name") and not row.get("session"):
                        row["session"] = row.get("terminal_name")
                    row.pop("can_stop", None)
            payload["count"] = len(payload.get("ports") or [])
            return jsonify({"ok": True, **payload})
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/debug")
    def api_climate_debug(workspace: str, repo_id: str):
        try:
            return jsonify(_svc().debug(workspace, repo_id))
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)

    @app.post("/api/climate/<workspace>/repositories/<repo_id>/runs")
    def api_climate_execute(workspace: str, repo_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify({"ok": True, "run": _svc().execute(workspace, repo_id, **payload)})
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)

    @app.get("/api/climate/<workspace>/runs/<run_id>")
    def api_climate_result(workspace: str, run_id: str):
        try:
            return jsonify({"ok": True, "run": _svc().result(workspace, run_id)})
        except ClimateCodingError as exc:
            return _error(exc)

    @app.post("/api/climate/<workspace>/runs/<run_id>/cancel")
    def api_climate_cancel(workspace: str, run_id: str):
        try:
            return jsonify({"ok": True, "run": _svc().cancel(workspace, run_id)})
        except ClimateCodingError as exc:
            return _error(exc)

    @app.post("/api/climate/<workspace>/runs/<run_id>/accept")
    def api_climate_accept(workspace: str, run_id: str):
        try:
            return jsonify(_svc().accept(workspace, run_id))
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)

    @app.post("/api/climate/<workspace>/runs/<run_id>/reject")
    def api_climate_reject(workspace: str, run_id: str):
        try:
            return jsonify(_svc().reject(workspace, run_id))
        except ClimateCodingError as exc:
            return _error(exc)
