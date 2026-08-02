"""Flask routes for Data Explorer."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from hub.audit import actions as audit_actions
from hub.data_explorer.security import ExplorerSafetyError
from hub.data_explorer.service import DataExplorerService
from hub.jobs.auth import current_actor
from hub.settings import ROOT_DIR


def register_data_explorer_routes(app: Flask) -> None:
    def _svc() -> DataExplorerService:
        return app.config["DATA_EXPLORER"]

    def _audit(action: str, *, target: str, detail: str, ok: bool = True) -> None:
        app.config["AUDIT"].append(
            action=action,
            actor=current_actor(),
            target=target,
            detail=detail[:2000],
            ok=ok,
        )

    def _err(exc: Exception, status: int = 400):
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
                "code": getattr(exc, "code", "explorer_rejected"),
            }
        ), status

    @app.get("/data-explorer")
    def data_explorer():
        env = request.args.get("environment") or "dev"
        boot = _svc().bootstrap(environment=env)
        _audit(
            audit_actions.DATA_EXPLORER_VIEW,
            target="data-explorer",
            detail=f"Opened Data Explorer env={env}",
        )
        return render_template(
            "data_explorer.html",
            page_title=boot["page_title"],
            subtitle=boot["subtitle"],
            bootstrap=boot,
            export_bootstrap=boot["approved_exports"],
            initial_tab=(request.args.get("tab") or "browse").strip().lower(),
        )

    @app.get("/api/data-explorer/bootstrap")
    def api_data_explorer_bootstrap():
        env = request.args.get("environment") or "dev"
        return jsonify(_svc().bootstrap(environment=env))

    @app.get("/api/data-explorer/tree")
    def api_data_explorer_tree():
        try:
            env = request.args.get("environment") or "dev"
            return jsonify({"ok": True, "tree": _svc().tree(environment=env, actor=current_actor())})
        except ExplorerSafetyError as exc:
            return _err(exc)

    @app.post("/api/data-explorer/refresh")
    def api_data_explorer_refresh():
        data = request.get_json(silent=True) or {}
        try:
            env = str(data.get("environment") or request.args.get("environment") or "dev")
            result = _svc().refresh_metadata(environment=env, actor=current_actor())
            _audit(audit_actions.DATA_EXPLORER_REFRESH, target=env, detail="metadata refresh")
            return jsonify(result)
        except ExplorerSafetyError as exc:
            return _err(exc)

    @app.get("/api/data-explorer/inventory")
    def api_data_explorer_inventory():
        try:
            env = request.args.get("environment") or "dev"
            return jsonify({"ok": True, "inventory": _svc().inventory(environment=env, actor=current_actor())})
        except ExplorerSafetyError as exc:
            return _err(exc)

    @app.get("/api/data-explorer/object")
    def api_data_explorer_object():
        try:
            return jsonify(
                {
                    "ok": True,
                    **_svc().object_detail(
                        environment=request.args.get("environment") or "dev",
                        schema=request.args.get("schema") or "",
                        name=request.args.get("name") or "",
                        actor=current_actor(),
                    ),
                }
            )
        except ExplorerSafetyError as exc:
            return _err(exc, 404)

    @app.post("/api/data-explorer/browse")
    def api_data_explorer_browse():
        data = request.get_json(silent=True) or {}
        try:
            result = _svc().browse(
                environment=str(data.get("environment") or "dev"),
                schema=str(data.get("schema") or ""),
                name=str(data.get("name") or ""),
                columns=data.get("columns"),
                filters=data.get("filters"),
                sort_column=data.get("sort_column"),
                sort_dir=str(data.get("sort_dir") or "asc"),
                page=int(data.get("page") or 1),
                page_size=data.get("page_size"),
                actor=current_actor(),
            )
            _audit(
                audit_actions.DATA_EXPLORER_BROWSE,
                target=f"{data.get('schema')}.{data.get('name')}",
                detail=f"page={data.get('page')} rows={result.get('returned_rows', result.get('page_size'))}",
            )
            return jsonify(result)
        except ExplorerSafetyError as exc:
            _audit(
                audit_actions.DATA_EXPLORER_BROWSE,
                target=str(data.get("name") or ""),
                detail=str(exc),
                ok=False,
            )
            return _err(exc)

    @app.post("/api/data-explorer/explain")
    def api_data_explorer_explain():
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(
                _svc().explain(
                    environment=str(data.get("environment") or "dev"),
                    schema=str(data.get("schema") or ""),
                    name=str(data.get("name") or ""),
                    actor=current_actor(),
                )
            )
        except ExplorerSafetyError as exc:
            return _err(exc)

    @app.post("/api/data-explorer/export")
    def api_data_explorer_export():
        data = request.get_json(silent=True) or {}
        try:
            result = _svc().export(
                environment=str(data.get("environment") or "dev"),
                schema=str(data.get("schema") or ""),
                name=str(data.get("name") or ""),
                columns=data.get("columns"),
                filters=data.get("filters"),
                format=str(data.get("format") or "csv"),
                actor=current_actor(),
                row_limit=data.get("row_limit"),
            )
            _audit(
                audit_actions.DATA_EXPLORER_EXPORT,
                target=f"{data.get('schema')}.{data.get('name')}",
                detail=f"fmt={result.get('format')} rows={result.get('exported_rows')}",
            )
            # Return download path relative token via filename under jail
            return jsonify({**result, "download_url": f"/api/data-explorer/download?file={result['filename']}&env={data.get('environment') or 'dev'}"})
        except ExplorerSafetyError as exc:
            return _err(exc)

    @app.get("/api/data-explorer/download")
    def api_data_explorer_download():
        env = (request.args.get("env") or "dev").strip().lower()
        filename = Path(request.args.get("file") or "").name
        if not filename or ".." in filename:
            return _err(ExplorerSafetyError("Invalid file"), 400)
        root = (ROOT_DIR / "data" / "data_explorer_exports" / env).resolve()
        path = (root / filename).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return _err(ExplorerSafetyError("Invalid path"), 403)
        if not path.is_file():
            return _err(ExplorerSafetyError("File not found"), 404)
        _audit(audit_actions.DATA_EXPLORER_DOWNLOAD, target=filename, detail=f"env={env}")
        return send_file(path, as_attachment=True, download_name=filename)

    @app.get("/api/data-explorer/favorites")
    def api_data_explorer_favorites():
        env = request.args.get("environment")
        return jsonify(
            {
                "ok": True,
                "favorites": _svc().store.list_favorites(
                    environment=env, actor=current_actor()
                ),
            }
        )

    @app.post("/api/data-explorer/favorites")
    def api_data_explorer_favorite_add():
        data = request.get_json(silent=True) or {}
        fav = _svc().store.add_favorite(
            environment=str(data.get("environment") or "dev"),
            schema=str(data.get("schema") or ""),
            object_name=str(data.get("name") or ""),
            object_type=str(data.get("object_type") or "table"),
            actor=current_actor(),
        )
        return jsonify({"ok": True, "favorite": fav})

    @app.delete("/api/data-explorer/favorites/<favorite_id>")
    def api_data_explorer_favorite_del(favorite_id: str):
        ok = _svc().store.remove_favorite(favorite_id)
        return jsonify({"ok": ok})

    @app.get("/api/data-explorer/audit")
    def api_data_explorer_audit():
        return jsonify({"ok": True, "audit": _svc().store.list_audit(limit=50)})
