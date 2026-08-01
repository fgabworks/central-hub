"""Flask HTTP + WebSocket routes for interactive Workspace Console terminals."""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from flask import Flask, current_app, jsonify, request, session

from hub.jobs.auth import current_actor, require_owner
from hub.notebook.workspace import read_workspace
from hub.workspace_console.prefs import (
    console_shell_bootstrap,
    load_console_prefs,
    save_console_prefs,
)
from hub.workspace_console.terminal.security import (
    TerminalSecurityError,
    mint_ws_ticket,
    origin_allowed,
    verify_ws_ticket,
)


def register_workspace_console_routes(app: Flask) -> None:
    def _svc():
        return app.config["WORKSPACE_CONSOLE"]

    def _terminals():
        return app.config.get("WC_TERMINALS")

    def _notebook_db():
        return app.config["NOTEBOOK"].db

    def _workspace() -> str:
        return read_workspace(request, _notebook_db())

    def _audit(action: str, **kwargs: Any) -> None:
        detail = kwargs.get("detail")
        if detail is None and "detail" not in kwargs:
            detail = {k: v for k, v in kwargs.items() if k != "action"}
        app.config["AUDIT"].append(action=action, detail=detail or {}, actor=current_actor())

    def _err(exc: Exception, status: int = 400):
        code = getattr(exc, "code", "error")
        return jsonify({"ok": False, "error": str(exc), "code": code}), status

    def _check_origin() -> bool:
        settings = app.config.get("SETTINGS")
        host = getattr(settings, "host", "127.0.0.1") if settings else "127.0.0.1"
        port = int(getattr(settings, "port", 8080) if settings else 8080)
        return origin_allowed(
            request.headers.get("Origin"),
            request.headers.get("Host"),
            hub_host=host,
            hub_port=port,
        )

    @app.get("/api/workspace-console/bootstrap")
    def api_workspace_console_bootstrap():
        payload = console_shell_bootstrap(_notebook_db(), workspace=_workspace())
        terms = _terminals()
        if terms is not None:
            payload["terminal_sessions_url"] = "/api/workspace-console/terminal/sessions"
            payload["terminal_ws_path"] = "/ws/workspace-console/terminal"
            payload["interactive_terminal"] = True
            payload["safety"] = {
                "controlled_terminal": True,
                "interactive_pty": True,
                "free_shell": False,
                "message": (
                    "Interactive terminal is jailed to connected repository paths. "
                    "AI assistants cannot execute commands."
                ),
            }
            settings = getattr(terms, "settings", None)
            payload["terminal_options"] = {
                "allow_cmd": bool(getattr(settings, "allow_cmd", False)),
                "max_sessions": int(getattr(settings, "max_sessions", 8)),
                "default_shell": "powershell" if __import__("os").name == "nt" else "bash",
            }
        return jsonify(payload)

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
        payload = _svc().ports()
        terms = _terminals()
        if terms is not None and payload.get("ports") is not None:
            payload["ports"] = terms.annotate_ports(list(payload["ports"]))
        return jsonify(payload)

    # ── Interactive PTY sessions ──────────────────────────────────────────

    @app.get("/api/workspace-console/terminal/sessions")
    @require_owner
    def api_wc_terminal_sessions_list():
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        if not _check_origin():
            return jsonify({"ok": False, "error": "Origin not allowed", "code": "origin"}), 403
        return jsonify({"ok": True, "sessions": terms.list_sessions(workspace=_workspace())})

    @app.post("/api/workspace-console/terminal/sessions")
    @require_owner
    def api_wc_terminal_sessions_create():
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        if not _check_origin():
            return jsonify({"ok": False, "error": "Origin not allowed", "code": "origin"}), 403
        payload = request.get_json(silent=True) or {}
        try:
            sess = terms.create(
                repository_id=str(payload.get("repository_id") or "").strip(),
                shell=payload.get("shell"),
                name=payload.get("name"),
                relative_cwd=payload.get("cwd"),
                workspace=_workspace(),
                actor=current_actor(),
                cols=payload.get("cols"),
                rows=payload.get("rows"),
                env_label=str(payload.get("environment") or "development"),
                split_group=payload.get("split_group"),
            )
            return jsonify({"ok": True, "session": sess})
        except TerminalSecurityError as exc:
            return _err(exc, 400)
        except Exception as exc:
            return _err(exc, 400)

    @app.get("/api/workspace-console/terminal/sessions/<session_id>")
    @require_owner
    def api_wc_terminal_session_get(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        sess = terms.get(session_id)
        if sess is None:
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404
        return jsonify({"ok": True, "session": sess.to_public()})

    @app.patch("/api/workspace-console/terminal/sessions/<session_id>")
    @require_owner
    def api_wc_terminal_session_patch(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        try:
            if "name" in payload:
                sess = terms.rename(session_id, str(payload.get("name") or ""))
            else:
                sess_obj = terms.get(session_id)
                if sess_obj is None:
                    raise TerminalSecurityError("Not found", code="not_found")
                sess = sess_obj.to_public()
            if payload.get("cols") or payload.get("rows"):
                sess = terms.resize(
                    session_id,
                    int(payload.get("cols") or sess.get("cols") or 120),
                    int(payload.get("rows") or sess.get("rows") or 32),
                )
            return jsonify({"ok": True, "session": sess})
        except TerminalSecurityError as exc:
            status = 404 if getattr(exc, "code", "") == "not_found" else 400
            return _err(exc, status)

    @app.post("/api/workspace-console/terminal/sessions/<session_id>/duplicate")
    @require_owner
    def api_wc_terminal_session_duplicate(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        try:
            sess = terms.duplicate(session_id, actor=current_actor())
            return jsonify({"ok": True, "session": sess})
        except TerminalSecurityError as exc:
            return _err(exc, 400)

    @app.post("/api/workspace-console/terminal/sessions/<session_id>/restart")
    @require_owner
    def api_wc_terminal_session_restart(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        try:
            sess = terms.restart(
                session_id, actor=current_actor(), confirm=bool(payload.get("confirm"))
            )
            return jsonify({"ok": True, "session": sess})
        except TerminalSecurityError as exc:
            return _err(exc, 400)

    @app.delete("/api/workspace-console/terminal/sessions/<session_id>")
    @require_owner
    def api_wc_terminal_session_close(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        payload = request.get_json(silent=True) or {}
        confirm = bool(payload.get("confirm") or request.args.get("confirm") == "1")
        try:
            sess = terms.close(session_id, confirm=confirm, reason="close")
            return jsonify({"ok": True, "session": sess})
        except TerminalSecurityError as exc:
            return _err(exc, 400)

    @app.post("/api/workspace-console/terminal/sessions/<session_id>/ticket")
    @require_owner
    def api_wc_terminal_session_ticket(session_id: str):
        terms = _terminals()
        if terms is None:
            return jsonify({"ok": False, "error": "Interactive terminal unavailable"}), 503
        if not _check_origin():
            return jsonify({"ok": False, "error": "Origin not allowed", "code": "origin"}), 403
        sess = terms.get(session_id)
        if sess is None:
            return jsonify({"ok": False, "error": "Not found", "code": "not_found"}), 404
        secret = app.secret_key or "central-hub"
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8", errors="replace")
        ttl = int(getattr(terms.settings, "ws_ticket_ttl_seconds", 60))
        ticket = mint_ws_ticket(
            secret=str(secret),
            session_id=session_id,
            actor=current_actor(),
            ttl_seconds=ttl,
        )
        return jsonify(
            {
                "ok": True,
                "ticket": ticket,
                "expires_in": ttl,
                "ws_url": f"/ws/workspace-console/terminal/{session_id}",
            }
        )

    @app.post("/api/workspace-console/terminal/insert")
    @require_owner
    def api_wc_terminal_insert_meta():
        """AI insert is client-side only. This endpoint documents/acknowledges intent — no execute."""
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or "").strip()
        # Do not audit command text. Only session id when present.
        _audit(
            "WC_TERMINAL_INSERT_SUGGESTION",
            detail={"session_id": session_id or None, "executed": False, "source": "assistant_ui"},
        )
        return jsonify(
            {
                "ok": True,
                "executed": False,
                "message": "Insert fills the terminal prompt only. The user must press Enter to run.",
            }
        )

    @app.post("/api/workspace-console/terminal/start")
    @require_owner
    def api_workspace_console_terminal_start():
        """Start an approved repository run profile (controlled profile launcher)."""
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

    # WebSocket via flask-sock (required for interactive typing).
    try:
        from flask_sock import Sock
    except ImportError:  # pragma: no cover
        app.logger.error(
            "flask-sock is not installed — interactive terminal WebSockets are disabled. "
            "Install requirements into the same Python that runs the hub "
            "(prefer .venv\\Scripts\\python.exe -m pip install -r requirements.txt)."
        )
        return

    sock = Sock(app)

    @sock.route("/ws/workspace-console/terminal/<session_id>")
    def ws_wc_terminal(ws, session_id: str):  # type: ignore[no-untyped-def]
        terms = current_app.config.get("WC_TERMINALS")
        if terms is None:
            ws.close(reason=1011, message=b"unavailable")
            return
        settings = current_app.config.get("SETTINGS")
        host = getattr(settings, "host", "127.0.0.1") if settings else "127.0.0.1"
        port = int(getattr(settings, "port", 8080) if settings else 8080)
        if not origin_allowed(
            request.headers.get("Origin"),
            request.headers.get("Host"),
            hub_host=host,
            hub_port=port,
        ):
            ws.send(json.dumps({"type": "error", "code": "origin", "error": "Origin not allowed"}))
            ws.close()
            return

        actor = current_actor()
        if actor != "owner":
            ws.send(json.dumps({"type": "error", "code": "auth", "error": "Authentication required"}))
            ws.close()
            return

        ticket = request.args.get("ticket") or ""
        secret = current_app.secret_key or "central-hub"
        if isinstance(secret, bytes):
            secret = secret.decode("utf-8", errors="replace")
        if not verify_ws_ticket(ticket, secret=str(secret), session_id=session_id, actor=actor):
            ws.send(json.dumps({"type": "error", "code": "ticket", "error": "Invalid or expired ticket"}))
            ws.close()
            return

        sess = terms.get(session_id)
        if sess is None or sess._pump is None:
            ws.send(json.dumps({"type": "error", "code": "not_found", "error": "Session not found"}))
            ws.close()
            return

        terms.register_ws(session_id)
        ws.send(json.dumps({"type": "ready", "session": sess.to_public()}))
        q = sess._pump.subscribe(maxsize=128)
        stop = threading.Event()

        def _pump_out() -> None:
            while not stop.is_set():
                try:
                    chunk = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk is None:
                    try:
                        ws.send(json.dumps({"type": "exit", "exit_code": sess.exit_code}))
                    except Exception:
                        pass
                    stop.set()
                    break
                try:
                    # Binary-ish payload as UTF-8 text for xterm (ANSI preserved).
                    text = chunk.decode("utf-8", errors="replace")
                    ws.send(json.dumps({"type": "out", "data": text}))
                except Exception:
                    stop.set()
                    break

        writer = threading.Thread(target=_pump_out, name=f"wc-ws-out-{session_id}", daemon=True)
        writer.start()
        try:
            while not stop.is_set():
                try:
                    raw = ws.receive(timeout=30)
                except Exception:
                    # Idle timeout — keep PTY alive; client may reconnect.
                    if not sess._pty or not sess._pty.alive:
                        break
                    continue
                if raw is None:
                    break
                try:
                    msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype == "in":
                    data = msg.get("data") or ""
                    # Never log/audit raw input.
                    try:
                        terms.write(session_id, data)
                    except TerminalSecurityError:
                        break
                elif mtype == "resize":
                    try:
                        terms.resize(session_id, int(msg.get("cols") or 80), int(msg.get("rows") or 24))
                    except Exception:
                        pass
                elif mtype == "ping":
                    try:
                        ws.send(json.dumps({"type": "pong"}))
                    except Exception:
                        break
                elif mtype == "close":
                    break
        finally:
            stop.set()
            if sess._pump:
                sess._pump.unsubscribe(q)
            terms.unregister_ws(session_id)
            try:
                ws.close()
            except Exception:
                pass
