"""Routes for the CLIMATE code workspace shell."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from hub.agent_center.redact import classify_provider_error, redact_text
from hub.climate.coding import ClimateCodingError
from hub.climate.service import ClimateService, normalize_workspace
from hub.repository_workspace.security import WorkspaceSecurityError

_LOG = logging.getLogger(__name__)


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
            "incomplete_cli": 409,
            "missing_cli": 409,
            "invalid_request": 400,
            "execution_error": 500,
            "server_error": 500,
        }.get(code, 400)
        return jsonify({"ok": False, "error": redact_text(str(exc), limit=400), "code": code}), http

    def _unexpected(exc: Exception):
        classified = classify_provider_error(str(exc))
        detail = classified.get("detail") or "Request failed"
        code = str(classified.get("code") or "server_error")
        lowered = str(exc).lower()
        if "multiple values for argument" in lowered:
            detail = "Invalid coding request."
            code = "invalid_request"
            http = 400
        else:
            http = 500 if code in {"execution_error", "server_error"} else {
                "not_found": 404,
                "authentication_required": 409,
                "unavailable": 409,
                "incomplete_cli": 409,
                "missing_cli": 409,
            }.get(code, 500)
            if code == "execution_error":
                code = "server_error"
        _LOG.error(
            "CLIMATE API unexpected error type=%s code=%s\n%s",
            type(exc).__name__,
            code,
            redact_text(traceback.format_exc(), limit=2000),
        )
        return jsonify({"ok": False, "error": redact_text(detail, limit=400), "code": code}), http

    def _json_body() -> dict[str, Any]:
        payload = request.get_json(silent=True) or {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _guarded(fn: Callable[[], Any]):
        try:
            return fn()
        except (ClimateCodingError, WorkspaceSecurityError) as exc:
            return _error(exc)
        except HTTPException:
            raise
        except Exception as exc:
            return _unexpected(exc)

    def _repo_call(workspace: str, repo_id: str, fn: Callable[[Any], dict[str, Any]]):
        def _run() -> Any:
            repo = _svc().require_repo(workspace, repo_id)
            return jsonify({"ok": True, **fn(repo)})
        return _guarded(_run)

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

    def _chat_page(workspace: str):
        ws = normalize_workspace(workspace)
        bootstrap = _svc().bootstrap(ws, "", surface="chat")
        return render_template(
            "climate_chat.html",
            climate=bootstrap,
            workspace=ws,
            skip_assistant_dock=True,
            skip_workspace_console=True,
        )

    @app.get("/work/climate")
    def work_climate():
        return _page("work")

    @app.get("/personal/climate")
    def personal_climate():
        return _page("personal")

    @app.get("/work/chat")
    def work_climate_chat():
        return _chat_page("work")

    @app.get("/personal/chat")
    def personal_climate_chat():
        return _chat_page("personal")

    @app.get("/api/climate/<workspace>/bootstrap")
    def api_climate_bootstrap(workspace: str):
        return _guarded(lambda: jsonify(_svc().bootstrap(workspace, str(request.args.get("repo") or ""))))

    @app.get("/api/climate/<workspace>/providers/<provider>/models")
    def api_climate_models(workspace: str, provider: str):
        def _run():
            normalize_workspace(workspace)
            return jsonify({"ok": True, **_svc().coding.models(
                provider, refresh=request.args.get("refresh") == "1"
            )})
        return _guarded(_run)

    @app.get("/api/climate/<workspace>/conversations")
    def api_climate_conversations(workspace: str):
        def _run():
            try:
                limit = int(request.args.get("limit") or 50)
            except ValueError as exc:
                raise ClimateCodingError("Invalid limit", code="invalid_request") from exc
            rows = _svc().conversations(
                workspace,
                repository_id=str(request.args.get("repository_id") or ""),
                surface=str(request.args.get("surface") or ""),
                limit=limit,
            )
            return jsonify({"ok": True, "conversations": rows})
        return _guarded(_run)

    @app.get("/api/climate/<workspace>/conversations/<conversation_id>")
    def api_climate_conversation(workspace: str, conversation_id: str):
        return _guarded(lambda: jsonify({
            "ok": True,
            "conversation": _svc().conversation(
                workspace,
                conversation_id,
                repository_id=str(request.args.get("repository_id") or ""),
                surface=str(request.args.get("surface") or ""),
            ),
        }))

    @app.put("/api/climate/<workspace>/conversations/<conversation_id>")
    def api_climate_conversation_update(workspace: str, conversation_id: str):
        def _run():
            payload = _json_body()
            return jsonify({
                "ok": True,
                "conversation": _svc().rename_conversation(
                    workspace,
                    conversation_id,
                    title=str(payload.get("title") or ""),
                    repository_id=str(request.args.get("repository_id") or ""),
                    surface=str(payload.get("surface") or request.args.get("surface") or ""),
                ),
            })
        return _guarded(_run)

    @app.get("/api/climate/<workspace>/providers/codex/rate-limits")
    def api_climate_codex_rate_limits(workspace: str):
        return _guarded(lambda: jsonify(_svc().codex_rate_limits(
            workspace, refresh=request.args.get("refresh") == "1"
        )))

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
            lambda repo: {"file": _svc().view_file(workspace, repo_id, path)},
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
        def _run():
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
        return _guarded(_run)

    @app.get("/api/climate/<workspace>/repositories/<repo_id>/debug")
    def api_climate_debug(workspace: str, repo_id: str):
        return _guarded(lambda: jsonify(_svc().debug(workspace, repo_id)))

    @app.post("/api/climate/<workspace>/chat/runs")
    def api_climate_chat_execute(workspace: str):
        def _run():
            payload = _json_body()
            payload.pop("workspace", None)
            return jsonify({"ok": True, "run": _svc().execute_chat(workspace, **payload)})
        return _guarded(_run)

    @app.post("/api/climate/<workspace>/workspace/runs")
    def api_climate_workspace_execute(workspace: str):
        def _run():
            payload = _json_body()
            payload.pop("workspace", None)
            repo_id = str(payload.pop("repository_id", "") or "")
            return jsonify({"ok": True, "run": _svc().execute(workspace, repo_id, **payload)})
        return _guarded(_run)

    @app.post("/api/climate/<workspace>/repositories/<repo_id>/runs")
    def api_climate_execute(workspace: str, repo_id: str):
        def _run():
            payload = _json_body()
            payload.pop("workspace", None)
            payload.pop("repository_id", None)
            return jsonify({"ok": True, "run": _svc().execute(workspace, repo_id, **payload)})
        return _guarded(_run)

    @app.get("/api/climate/<workspace>/runs/<run_id>")
    def api_climate_result(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "run": _svc().result(workspace, run_id)}))

    @app.post("/api/climate/<workspace>/runs/<run_id>/cancel")
    def api_climate_cancel(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "run": _svc().cancel(workspace, run_id)}))

    @app.get("/api/climate/<workspace>/runs/<run_id>/token-efficiency")
    def api_climate_token_efficiency(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "token_efficiency": _svc().token_efficiency_status(workspace, run_id)}))

    @app.post("/api/climate/<workspace>/runs/<run_id>/token-efficiency/evaluate")
    def api_climate_token_efficiency_evaluate(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "token_efficiency": _svc().evaluate_token_efficiency(workspace, run_id)}))

    @app.post("/api/climate/<workspace>/runs/<run_id>/token-efficiency/cancel")
    def api_climate_token_efficiency_cancel(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "token_efficiency": _svc().cancel_token_efficiency(workspace, run_id)}))

    @app.post("/api/climate/<workspace>/runs/<run_id>/accept")
    def api_climate_accept(workspace: str, run_id: str):
        return _guarded(lambda: jsonify(_svc().accept(workspace, run_id)))

    @app.post("/api/climate/<workspace>/runs/<run_id>/reject")
    def api_climate_reject(workspace: str, run_id: str):
        return _guarded(lambda: jsonify(_svc().reject(workspace, run_id)))

    @app.get("/api/climate/<workspace>/runs/<run_id>/test-profiles")
    def api_climate_test_profiles(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "profiles": _svc().test_profiles(workspace, run_id)}))

    @app.post("/api/climate/<workspace>/runs/<run_id>/tests/run")
    def api_climate_run_tests(workspace: str, run_id: str):
        def _run():
            payload = _json_body()
            return jsonify({"ok": True, "test_run": _svc().run_tests(workspace, run_id, str(payload.get("profile_id") or ""))})
        return _guarded(_run)

    @app.post("/api/climate/<workspace>/runs/<run_id>/tests/skip")
    def api_climate_skip_tests(workspace: str, run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "test_run": _svc().skip_tests(workspace, run_id)}))

    @app.get("/api/climate/<workspace>/test-runs/<test_run_id>")
    def api_climate_test_result(workspace: str, test_run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "test_run": _svc().test_result(workspace, test_run_id)}))

    @app.post("/api/climate/<workspace>/test-runs/<test_run_id>/cancel")
    def api_climate_cancel_tests(workspace: str, test_run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "test_run": _svc().cancel_tests(workspace, test_run_id)}))

    @app.post("/api/climate/<workspace>/test-runs/<test_run_id>/follow-up")
    def api_climate_test_follow_up(workspace: str, test_run_id: str):
        return _guarded(lambda: jsonify({"ok": True, "run": _svc().follow_up_test_failure(workspace, test_run_id)}))

    @app.errorhandler(404)
    def _climate_api_404(exc: HTTPException):
        if request.path.startswith("/api/climate/"):
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404
        return exc

    @app.errorhandler(405)
    def _climate_api_405(exc: HTTPException):
        if request.path.startswith("/api/climate/"):
            return jsonify({"ok": False, "error": "Method not allowed", "code": "method_not_allowed"}), 405
        return exc

    @app.errorhandler(500)
    def _climate_api_500(exc):
        if request.path.startswith("/api/climate/"):
            tb = "".join(traceback.format_exception(type(exc), exc, getattr(exc, "__traceback__", None)))
            _LOG.error("CLIMATE API unhandled 500\n%s", redact_text(tb, limit=2000))
            return jsonify({"ok": False, "error": "Request failed", "code": "server_error"}), 500
        return exc
