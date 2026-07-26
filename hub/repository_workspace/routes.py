"""Flask routes for Repository Workspace (Phases 1–2)."""

from __future__ import annotations

from typing import Any

from flask import Flask, abort, jsonify, render_template, request, url_for

from hub.adapters import AdapterManager
from hub.audit import actions as audit_actions
from hub.jobs.auth import current_actor
from hub.registry.loader import RegistryError
from hub.registry.models import Registry
from hub.registry.status import ui_repo_status
from hub.registry.store import RegistryStore
from hub.repository_workspace.connect import preview_connect, save_connect
from hub.repository_workspace.security import WorkspaceSecurityError, redact_audit_detail
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.run_profiles import live_runs_allowed


def register_repository_workspace_routes(app: Flask) -> None:
    def _svc() -> RepositoryWorkspaceService:
        return app.config["REPO_WORKSPACE"]

    def _repo(repo_id: str):
        registry: Registry | None = app.config["REGISTRY"]
        if registry is None:
            abort(503)
        repo = registry.get(repo_id)
        if repo is None:
            abort(404)
        return repo

    def _audit(action: str, *, target: str, detail: str, ok: bool = True) -> None:
        app.config["AUDIT"].append(
            action=action,
            actor=current_actor(),
            target=target,
            detail=redact_audit_detail(detail),
            ok=ok,
        )

    def _json_error(exc: WorkspaceSecurityError, status: int | None = None):
        code = getattr(exc, "code", "forbidden")
        http = status or {
            "not_found": 404,
            "unavailable": 409,
            "confirm_required": 400,
            "exists": 409,
            "duplicate_run": 409,
            "port_occupied": 409,
            "live_blocked": 403,
            "environment_blocked": 403,
            "profile_scope": 403,
            "cwd_escape": 400,
            "exe_missing": 400,
            "start_failed": 500,
            "confirm_remote_mismatch": 400,
            "confirm_replace_path": 400,
            "missing_path": 400,
            "missing": 400,
            "not_directory": 400,
            "inaccessible": 400,
            "scan_failed": 400,
        }.get(code, 400)
        return jsonify({"ok": False, "error": str(exc), "code": code}), http

    def _tab_context(repo, tab: str) -> dict[str, Any]:
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        health = adapters.check_repository(repo) if adapters else None
        status = ui_repo_status(repo, health)
        avail = _svc().availability(repo)
        return {
            "repository": repo,
            "health": health,
            "status": status,
            "workspace": avail,
            "active_tab": tab,
            "tabs": [
                {"id": "overview", "label": "Overview", "endpoint": "repository_detail"},
                {"id": "files", "label": "Files", "endpoint": "repository_files"},
                {"id": "changes", "label": "Changes", "endpoint": "repository_changes"},
                {"id": "run", "label": "Run", "endpoint": "repository_run"},
                {"id": "logs", "label": "Logs", "endpoint": "repository_logs"},
                {"id": "settings", "label": "Settings", "endpoint": "repository_settings"},
            ],
        }

    @app.get("/repositories/<repo_id>")
    def repository_detail(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "overview")
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail="Opened repository overview",
        )
        return render_template("repository_detail.html", **ctx)

    @app.get("/repositories/<repo_id>/files")
    def repository_files(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "files")
        path = (request.args.get("path") or "").strip()
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail=f"Opened files tab path={path or '/'}",
        )
        return render_template(
            "repository_workspace_files.html",
            initial_path=path,
            **ctx,
        )

    @app.get("/repositories/<repo_id>/changes")
    def repository_changes(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "changes")
        changes = None
        error = None
        if ctx["workspace"]["available"]:
            try:
                changes = _svc().changes(repo)
            except WorkspaceSecurityError as exc:
                error = str(exc)
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail="Opened changes tab",
        )
        return render_template(
            "repository_workspace_changes.html",
            changes=changes,
            error=error,
            **ctx,
        )

    @app.get("/repositories/<repo_id>/run")
    def repository_run(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "run")
        profiles = []
        runs = []
        if ctx["workspace"]["available"]:
            try:
                profiles = _svc().list_profiles(repo)
                runs = _svc().list_runs(repo)
            except WorkspaceSecurityError:
                profiles = []
                runs = []
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail="Opened run tab",
        )
        return render_template(
            "repository_workspace_run.html",
            profiles=profiles,
            runs=runs,
            live_runs_allowed=live_runs_allowed(),
            **ctx,
        )

    @app.get("/repositories/<repo_id>/logs")
    def repository_logs(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "logs")
        runs = []
        if ctx["workspace"]["available"]:
            try:
                runs = _svc().list_runs(repo)
            except WorkspaceSecurityError:
                runs = []
        selected = (request.args.get("run") or "").strip()
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail=f"Opened logs tab run={selected or '-'}",
        )
        return render_template(
            "repository_workspace_logs.html",
            runs=runs,
            selected_run_id=selected,
            **ctx,
        )

    @app.get("/repositories/<repo_id>/settings")
    def repository_settings(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "settings")
        return render_template("repository_workspace_settings.html", **ctx)

    @app.get("/repositories/<repo_id>/connect")
    def repository_connect(repo_id: str):
        repo = _repo(repo_id)
        ctx = _tab_context(repo, "overview")
        _audit(
            audit_actions.REPO_WS_VIEW,
            target=repo_id,
            detail="Opened connect local workspace",
        )
        return render_template(
            "repository_workspace_connect.html",
            edit_url=url_for("repository_edit", repo_id=repo_id),
            **ctx,
        )

    # ---- JSON API ----

    @app.get("/api/repositories/<repo_id>/workspace/tree")
    def api_repo_ws_tree(repo_id: str):
        repo = _repo(repo_id)
        try:
            data = _svc().tree(repo)
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/file")
    def api_repo_ws_file(repo_id: str):
        repo = _repo(repo_id)
        path = (request.args.get("path") or "").strip()
        try:
            data = _svc().preview(repo, path)
            _audit(
                audit_actions.REPO_WS_READ,
                target=repo_id,
                detail=f"Preview {path}",
            )
            return jsonify({"ok": True, "file": data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/search")
    def api_repo_ws_search(repo_id: str):
        repo = _repo(repo_id)
        q = (request.args.get("q") or "").strip()
        mode = (request.args.get("mode") or "filename").strip().lower()
        if mode not in {"filename", "content"}:
            mode = "filename"
        try:
            data = _svc().search(repo, q=q, mode=mode)
            _audit(
                audit_actions.REPO_WS_SEARCH,
                target=repo_id,
                detail=f"Search mode={mode} q_len={len(q)} hits={data['count']}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/preview-save")
    def api_repo_ws_preview_save(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        content = payload.get("content")
        if not isinstance(content, str):
            return jsonify({"ok": False, "error": "content must be a string", "code": "bad_request"}), 400
        try:
            data = _svc().preview_save(repo, path, content)
            _audit(
                audit_actions.REPO_WS_DIFF_PREVIEW,
                target=repo_id,
                detail=f"Diff preview {path} changed={data.get('changed')}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/save")
    def api_repo_ws_save(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        content = payload.get("content")
        confirm = bool(payload.get("confirm"))
        if not isinstance(content, str):
            return jsonify({"ok": False, "error": "content must be a string", "code": "bad_request"}), 400
        try:
            data = _svc().save(repo, path, content, confirm=confirm)
            _audit(
                audit_actions.REPO_WS_SAVE,
                target=repo_id,
                detail=f"Saved {path} bytes={data.get('bytes')}",
                ok=True,
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            _audit(
                audit_actions.REPO_WS_SAVE,
                target=repo_id,
                detail=f"Save blocked {path}: {exc}",
                ok=False,
            )
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/revert")
    def api_repo_ws_revert(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        try:
            data = _svc().revert(repo, path)
            _audit(
                audit_actions.REPO_WS_REVERT,
                target=repo_id,
                detail=f"Reverted buffer {path}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/create")
    def api_repo_ws_create(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        content = payload.get("content") if isinstance(payload.get("content"), str) else ""
        confirm = bool(payload.get("confirm"))
        try:
            data = _svc().create(repo, path, content, confirm=confirm)
            _audit(
                audit_actions.REPO_WS_CREATE,
                target=repo_id,
                detail=f"Created {path}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/rename")
    def api_repo_ws_rename(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        new_path = str(payload.get("new_path") or "").strip()
        confirm = bool(payload.get("confirm"))
        try:
            data = _svc().rename(repo, path, new_path, confirm=confirm)
            _audit(
                audit_actions.REPO_WS_RENAME,
                target=repo_id,
                detail=f"Renamed {path} -> {new_path}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/delete")
    def api_repo_ws_delete(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        confirm = bool(payload.get("confirm"))
        try:
            data = _svc().delete(repo, path, confirm=confirm)
            _audit(
                audit_actions.REPO_WS_DELETE,
                target=repo_id,
                detail=f"Deleted {path}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/changes")
    def api_repo_ws_changes(repo_id: str):
        repo = _repo(repo_id)
        try:
            data = _svc().changes(repo)
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/diff")
    def api_repo_ws_diff(repo_id: str):
        repo = _repo(repo_id)
        path = (request.args.get("path") or "").strip() or None
        side = (request.args.get("view") or "unified").strip().lower() == "side"
        try:
            data = _svc().diff(repo, path, side_by_side=side)
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/open")
    def api_repo_ws_open(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        target = str(payload.get("target") or "").strip()
        path = str(payload.get("path") or "").strip() or None
        try:
            data = _svc().open_external(repo, target, path)
            _audit(
                audit_actions.REPO_WS_OPEN_EXTERNAL,
                target=repo_id,
                detail=f"Open {target} path={path or '.'}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    # ---- Phase 2 run / logs API ----

    @app.get("/api/repositories/<repo_id>/workspace/profiles")
    def api_repo_ws_profiles(repo_id: str):
        repo = _repo(repo_id)
        try:
            return jsonify(
                {
                    "ok": True,
                    "profiles": _svc().list_profiles(repo),
                    "live_runs_allowed": live_runs_allowed(),
                }
            )
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/runs")
    def api_repo_ws_runs(repo_id: str):
        repo = _repo(repo_id)
        try:
            return jsonify({"ok": True, "runs": _svc().list_runs(repo)})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/runs/<run_id>")
    def api_repo_ws_run_get(repo_id: str, run_id: str):
        repo = _repo(repo_id)
        try:
            return jsonify({"ok": True, "run": _svc().get_run(repo, run_id)})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/runs/preview")
    def api_repo_ws_run_preview(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        try:
            data = _svc().preview_run(
                repo,
                profile_id=str(payload.get("profile_id") or "").strip(),
                environment=str(payload.get("environment") or "development").strip(),
                port=int(payload.get("port") or 0),
                confirm_live=bool(payload.get("confirm_live")),
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid port", "code": "invalid_port"}), 400

    @app.post("/api/repositories/<repo_id>/workspace/runs/find-port")
    def api_repo_ws_find_port(repo_id: str):
        _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        try:
            preferred = int(payload.get("port") or payload.get("preferred") or 8000)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid port", "code": "invalid_port"}), 400
        data = _svc().find_port(preferred)
        _audit(
            audit_actions.REPO_WS_RUN_PORT,
            target=repo_id,
            detail=f"find-port preferred={preferred} result={data.get('port')}",
        )
        return jsonify({"ok": True, **data})

    @app.post("/api/repositories/<repo_id>/workspace/runs/start")
    def api_repo_ws_run_start(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        try:
            run = _svc().start_run(
                repo,
                profile_id=str(payload.get("profile_id") or "").strip(),
                environment=str(payload.get("environment") or "development").strip(),
                port=int(payload.get("port") or 0),
                confirm_live=bool(payload.get("confirm_live")),
            )
            return jsonify({"ok": True, "run": run})
        except WorkspaceSecurityError as exc:
            _audit(
                audit_actions.REPO_WS_RUN_FAIL,
                target=repo_id,
                detail=f"start failed: {exc}",
                ok=False,
            )
            return _json_error(exc)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid port", "code": "invalid_port"}), 400

    @app.post("/api/repositories/<repo_id>/workspace/runs/<run_id>/stop")
    def api_repo_ws_run_stop(repo_id: str, run_id: str):
        repo = _repo(repo_id)
        try:
            run = _svc().stop_run(repo, run_id)
            return jsonify({"ok": True, "run": run})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/runs/<run_id>/restart")
    def api_repo_ws_run_restart(repo_id: str, run_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        try:
            run = _svc().restart_run(
                repo, run_id, confirm_live=bool(payload.get("confirm_live"))
            )
            return jsonify({"ok": True, "run": run})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.get("/api/repositories/<repo_id>/workspace/runs/<run_id>/logs")
    def api_repo_ws_run_logs(repo_id: str, run_id: str):
        repo = _repo(repo_id)
        try:
            offset = int(request.args.get("offset") or 0)
            limit = int(request.args.get("limit") or 300)
        except ValueError:
            offset, limit = 0, 300
        try:
            data = _svc().read_logs(repo, run_id, offset=offset, limit=limit)
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    # ---- Connect Local Workspace ----

    @app.post("/api/repositories/<repo_id>/workspace/connect/scan")
    def api_repo_ws_connect_scan(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        try:
            data = preview_connect(repo, path=path)
            _audit(
                audit_actions.REPO_WS_CONNECT_SCAN,
                target=repo_id,
                detail=f"Scanned workspace folder={data['scan'].get('folder_name')} "
                f"git={data['scan'].get('is_git')} mismatch={data['scan'].get('remote_mismatch')}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/connect/preview")
    def api_repo_ws_connect_preview(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path") or "").strip()
        try:
            data = preview_connect(repo, path=path)
            _audit(
                audit_actions.REPO_WS_CONNECT_PREVIEW,
                target=repo_id,
                detail=f"Preview connect folder={data['scan'].get('folder_name')}",
            )
            return jsonify({"ok": True, **data})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)

    @app.post("/api/repositories/<repo_id>/workspace/connect/save")
    def api_repo_ws_connect_save(repo_id: str):
        repo = _repo(repo_id)
        payload = request.get_json(silent=True) or {}
        store_factory = app.config.get("REGISTRY_STORE_FACTORY")
        reload_fn = app.config.get("RELOAD_REGISTRY")
        if not callable(store_factory):
            return jsonify({"ok": False, "error": "Registry store unavailable", "code": "unavailable"}), 503
        store: RegistryStore = store_factory()

        def _connect_audit(action: str, target: str, detail: str, ok: bool = True) -> None:
            _audit(action, target=target, detail=detail, ok=ok)

        try:
            result = save_connect(
                repo,
                store=store,
                path=str(payload.get("path") or "").strip(),
                name=str(payload.get("name") or "").strip() or None,
                git_url=str(payload.get("git_url") or "").strip() or None,
                confirm_save=bool(payload.get("confirm_save")),
                confirm_remote_mismatch=bool(payload.get("confirm_remote_mismatch")),
                confirm_replace_path=bool(payload.get("confirm_replace_path")),
                selected_profiles=list(payload.get("selected_profiles") or []),
                audit=_connect_audit,
            )
            if callable(reload_fn):
                reload_fn()
            _audit(
                audit_actions.REGISTRY_UPDATE,
                target=repo_id,
                detail=f"Connected local workspace profiles={len(result.get('profiles_added') or [])}",
            )
            return jsonify({"ok": True, **result})
        except WorkspaceSecurityError as exc:
            return _json_error(exc)
        except RegistryError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "registry_error"}), 400
