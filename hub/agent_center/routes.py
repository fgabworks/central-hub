"""Flask routes for Prompting & Agent Center."""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.dock import dock_shell_bootstrap, load_dock_prefs, save_dock_prefs
from hub.jobs.auth import require_owner
from hub.notebook.workspace import read_workspace
from hub.audit import actions as audit_actions


def register_agent_center_routes(app: Flask) -> None:
    def _svc() -> AgentCenterService:
        return app.config["AGENT_CENTER"]

    def _router():
        return app.config["AIRIX_ROUTER"]

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
        coding = _svc().connections.list_coding_clis(probe=False, include_models=False)
        others = [
            row
            for row in _svc().connections.list(probe=False)
            if row.get("id") not in {"codex", "claude-code", "cursor-agent"}
        ]
        defaults = _svc().connections.coding_defaults()
        _audit(
            "AI_CONNECTIONS_VIEW",
            detail={"coding": len(coding), "other": len(others), "cached": True},
        )
        return render_template(
            "ai_connections.html",
            connections=coding,
            other_connections=others,
            coding_defaults=defaults,
        )

    @app.get("/api/ai-connections")
    def api_ai_connections():
        refresh = request.args.get("refresh") == "1"
        coding_only = request.args.get("coding") == "1"
        include_models = request.args.get("models") == "1"
        if coding_only:
            rows = _svc().connections.list_coding_clis(
                refresh=refresh,
                probe=True if refresh else request.args.get("probe", "0") == "1",
                include_models=include_models,
            )
        else:
            rows = _svc().connections.list(
                refresh=refresh,
                probe=True if refresh else request.args.get("probe", "0") == "1",
            )
        return jsonify(
            {
                "connections": rows,
                "coding_defaults": _svc().connections.coding_defaults(),
            }
        )

    @app.get("/api/ai-connections/coding-defaults")
    def api_ai_coding_defaults_get():
        return jsonify({"ok": True, "defaults": _svc().connections.coding_defaults()})

    @app.put("/api/ai-connections/coding-defaults")
    @require_owner
    def api_ai_coding_defaults_put():
        payload = request.get_json(silent=True) or {}
        try:
            defaults = _svc().connections.set_coding_defaults(
                default_provider=payload.get("default_provider"),
                default_models=payload.get("default_models"),
                chat=payload.get("chat"),
                workspace=payload.get("workspace"),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "defaults": defaults})

    @app.post("/api/ai-connections/<agent_id>/<action>")
    @require_owner
    def api_ai_connection_action(agent_id: str, action: str):
        try:
            return jsonify(_svc().connections.action(agent_id, action))
        except KeyError:
            return jsonify({"error": "Unknown provider"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    def _provider_settings():
        return _svc().provider_settings

    def _provider_http_error(exc: Exception):
        from hub.agent_center.provider_catalog import scrub_public_payload
        from hub.agent_center.redact import redact_text

        code = getattr(exc, "code", "invalid_request")
        status = 404 if code == "not_found" else 400
        return (
            jsonify(
                scrub_public_payload(
                    {"ok": False, "error": redact_text(str(exc), limit=240), "code": code}
                )
            ),
            status,
        )

    @app.get("/settings/ai-providers")
    def settings_ai_providers():
        providers = _provider_settings().list_providers(probe=False)
        _audit("AI_PROVIDERS_VIEW", detail={"count": len(providers)})
        return render_template(
            "settings_ai_providers.html",
            providers=providers,
        )

    @app.get("/api/settings/ai-providers")
    def api_settings_ai_providers():
        from hub.agent_center.provider_catalog import scrub_public_payload

        probe = request.args.get("probe") == "1"
        return jsonify(
            scrub_public_payload({"ok": True, "providers": _provider_settings().list_providers(probe=probe)})
        )

    @app.post("/api/settings/ai-providers/<provider_id>/key")
    @require_owner
    def api_settings_ai_provider_set_key(provider_id: str):
        from hub.agent_center.provider_catalog import scrub_public_payload
        from hub.agent_center.provider_settings import ProviderSettingsError

        payload = request.get_json(silent=True) or {}
        try:
            provider = _provider_settings().set_key(provider_id, str(payload.get("api_key") or ""))
        except ProviderSettingsError as exc:
            return _provider_http_error(exc)
        except ValueError as exc:
            return _provider_http_error(exc)
        return jsonify(scrub_public_payload({"ok": True, "provider": provider}))

    @app.delete("/api/settings/ai-providers/<provider_id>/key")
    @require_owner
    def api_settings_ai_provider_remove_key(provider_id: str):
        from hub.agent_center.provider_catalog import scrub_public_payload
        from hub.agent_center.provider_settings import ProviderSettingsError

        try:
            provider = _provider_settings().remove_key(provider_id)
        except ProviderSettingsError as exc:
            return _provider_http_error(exc)
        except ValueError as exc:
            return _provider_http_error(exc)
        return jsonify(scrub_public_payload({"ok": True, "provider": provider}))

    @app.post("/api/settings/ai-providers/<provider_id>/test")
    @require_owner
    def api_settings_ai_provider_test(provider_id: str):
        from hub.agent_center.provider_catalog import scrub_public_payload
        from hub.agent_center.provider_settings import ProviderSettingsError

        try:
            result = _provider_settings().test_connection(provider_id)
        except ProviderSettingsError as exc:
            return _provider_http_error(exc)
        except KeyError:
            return _provider_http_error(ProviderSettingsError("Unknown provider", code="not_found"))
        except ValueError as exc:
            return _provider_http_error(exc)
        _audit(
            "AI_PROVIDER_TEST",
            detail={"provider_id": provider_id, "ok": bool(result.get("ok"))},
        )
        return jsonify(scrub_public_payload(result))

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

    # ---- AiriX Smart Routing (Phase 5: cost + RBAC + findings) ----
    # Canonical: /api/assistants/airix/routing/*
    # Legacy:    /api/assistants/okarun/routing/* (compatibility)

    def _routing_work_ok(profile_id: str) -> bool:
        from hub.agent_center.routing.profile import is_work_routing_profile

        return is_work_routing_profile(profile_id)

    def _routing_actor() -> str:
        try:
            from hub.jobs.auth import current_actor

            return current_actor() or "owner"
        except Exception:  # noqa: BLE001
            return "owner"

    def _routing_http_error(exc: AgentCenterError):
        status = 400
        if exc.code in {"approval_required", "permission_denied"}:
            status = 403
        elif exc.code in {
            "duplicate_execution",
            "identical_retry_blocked",
            "retry_limit",
            "budget_exceeded",
        }:
            status = 409
        elif exc.code in {"execution_not_found"}:
            status = 404
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), status

    @app.get("/api/assistants/<profile_id>/routing/providers")
    def api_airix_routing_providers(profile_id: str):
        if profile_id not in {"airix", "okarun", "aira"}:
            return jsonify({"ok": False, "error": "Unknown assistant profile"}), 404
        probe = request.args.get("probe") == "1"
        return jsonify(
            {
                "ok": True,
                "phase": 5,
                "providers": _router().list_available_providers(probe=probe),
                "roles": _router().list_roles(),
                "rbac_roles": _router().list_rbac_roles(),
            }
        )

    @app.get("/api/assistants/<profile_id>/routing/roles")
    def api_airix_routing_roles(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify({"ok": False, "error": "Smart Routing roles are AiriX (Work) only"}), 400
        return jsonify(
            {
                "ok": True,
                "phase": 5,
                "roles": _router().list_roles(),
                "rbac_roles": _router().list_rbac_roles(),
            }
        )

    @app.get("/api/assistants/<profile_id>/routing/permissions")
    def api_airix_routing_permissions_get(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify({"ok": False, "error": "Smart Routing permissions are AiriX (Work) only"}), 400
        actor = _routing_actor()
        return jsonify({"ok": True, "phase": 5, "permissions": _router().rbac_snapshot(actor, workspace="work")})

    @app.get("/api/assistants/<profile_id>/routing/acl")
    def api_airix_routing_acl_get(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify({"ok": False, "error": "Smart Routing ACL is AiriX (Work) only"}), 400
        try:
            rows = _router().list_acl(workspace="work", actor=_routing_actor())
        except AgentCenterError as exc:
            return _routing_http_error(exc)
        return jsonify({"ok": True, "phase": 5, "acl": rows, "rbac_roles": _router().list_rbac_roles()})

    @app.put("/api/assistants/<profile_id>/routing/acl")
    def api_airix_routing_acl_put(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify({"ok": False, "error": "Smart Routing ACL is AiriX (Work) only"}), 400
        payload = request.get_json(silent=True) or {}
        target = str(payload.get("actor") or "").strip()
        role_id = str(payload.get("role_id") or "").strip()
        if not target or not role_id:
            return jsonify({"ok": False, "error": "actor and role_id are required"}), 400
        try:
            row = _router().set_acl_role(
                target, role_id, workspace="work", actor=_routing_actor()
            )
        except AgentCenterError as exc:
            return _routing_http_error(exc)
        _audit(
            audit_actions.AIRIX_ROUTING_SETTINGS,
            detail={"acl_actor": target, "role_id": role_id},
        )
        return jsonify({"ok": True, "assignment": row})

    @app.get("/api/assistants/<profile_id>/routing/settings")
    def api_airix_routing_settings_get(profile_id: str):
        if profile_id not in {"airix", "okarun", "aira"}:
            return jsonify({"ok": False, "error": "Unknown assistant profile"}), 404
        workspace = "personal" if profile_id == "aira" else "work"
        return jsonify({"ok": True, "settings": _router().get_settings(workspace).public()})

    @app.put("/api/assistants/<profile_id>/routing/settings")
    def api_airix_routing_settings_put(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing settings are Work/AiriX only"}
            ), 400
        payload = request.get_json(silent=True) or {}
        try:
            settings = _router().save_settings(
                payload, workspace="work", actor=_routing_actor()
            )
        except AgentCenterError as exc:
            return _routing_http_error(exc)
        _audit(
            audit_actions.AIRIX_ROUTING_SETTINGS,
            detail={"mode": settings.mode, "max_retries": settings.max_retries},
        )
        return jsonify({"ok": True, "settings": settings.public()})

    @app.get("/api/assistants/<profile_id>/routing/analytics")
    def api_airix_routing_analytics(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing analytics are AiriX (Work) only"}
            ), 400
        data = _router().analytics(workspace="work", actor=_routing_actor())
        _audit(audit_actions.AIRIX_ROUTING_ANALYTICS, detail={"executions": data.get("executions_total")})
        return jsonify({"ok": True, "analytics": data})

    @app.get("/api/assistants/<profile_id>/routing/sessions/<session_id>")
    def api_airix_routing_session_get(profile_id: str, session_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify({"ok": False, "error": "Smart Routing sessions are AiriX (Work) only"}), 400
        row = _router().get_session(session_id, workspace="work", actor=_routing_actor())
        if row is None:
            return jsonify({"ok": False, "error": "Session not found", "code": "execution_not_found"}), 404
        return jsonify({"ok": True, "session": row})

    @app.post("/api/assistants/<profile_id>/routing/recommend")
    def api_airix_routing_recommend(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing is AiriX (Work) only"}
            ), 400
        payload = request.get_json(silent=True) or {}
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"ok": False, "error": "prompt is required"}), 400
        probe = bool(payload.get("probe_providers"))
        session_id = str(payload.get("session_id") or "").strip() or None
        repository_ids = list(payload.get("repository_ids") or [])
        actor = _routing_actor()
        rec = _router().recommend_route(
            prompt,
            workspace="work",
            actor=actor,
            probe_providers=probe,
            session_id=session_id,
            repository_ids=repository_ids,
        )
        plan = _router().build_execution_plan(
            prompt,
            workspace="work",
            actor=actor,
            recommendation=rec,
            session_id=session_id,
            repository_ids=repository_ids,
        )
        _audit(
            audit_actions.AIRIX_ROUTING_RECOMMEND,
            detail={
                "task_type": rec.task_type,
                "complexity": rec.complexity,
                "recommended_agent": rec.recommended_agent,
                "tier": rec.recommended_tier,
                "role_id": rec.role_id,
                "history_influenced": rec.history_influenced,
                "execution": "ready",
            },
        )
        return jsonify(
            {
                "ok": True,
                "phase": 5,
                "recommendation": rec.public(),
                "plan": plan.public(),
            }
        )

    @app.post("/api/assistants/<profile_id>/routing/execute")
    @require_owner
    def api_airix_routing_execute(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing is AiriX (Work) only"}
            ), 400
        payload = request.get_json(silent=True) or {}
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"ok": False, "error": "prompt is required"}), 400
        agent_override = str(payload.get("agent_override") or "").strip() or None
        approve_codex = bool(payload.get("approve_codex"))
        force = bool(payload.get("force"))
        repository_ids = list(payload.get("repository_ids") or [])
        active_repository_id = str(payload.get("active_repository_id") or "").strip() or None
        selected_repository_id = str(payload.get("selected_repository_id") or "").strip() or None
        session_id = str(payload.get("session_id") or "").strip() or None
        orchestrate = payload.get("orchestrate")
        model = str(payload.get("model") or "").strip() or None
        routing_mode = str(payload.get("routing_mode") or "smart").strip() or "smart"
        interaction_mode_raw = payload.get("interaction_mode")
        interaction_mode = (
            str(interaction_mode_raw).strip() or None
            if interaction_mode_raw is not None
            else None
        )
        conversation_id = str(payload.get("conversation_id") or "").strip() or None
        context_fingerprint = str(payload.get("context_fingerprint") or "").strip() or None
        context_sources = list(payload.get("context_sources") or [])
        dhis2_environment = str(payload.get("dhis2_environment") or "").strip() or None
        try:
            attempt = int(payload.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        previous_partial = str(payload.get("previous_partial") or "")
        actor = _routing_actor()
        try:
            result = _router().execute_route(
                prompt,
                workspace="work",
                actor=actor,
                agent_override=agent_override,
                repository_ids=repository_ids,
                active_repository_id=active_repository_id,
                selected_repository_id=selected_repository_id,
                approve_codex=approve_codex,
                force=force,
                attempt=attempt,
                previous_partial=previous_partial,
                session_id=session_id,
                orchestrate=None if orchestrate is None else bool(orchestrate),
                model=model,
                routing_mode=routing_mode,
                conversation_id=conversation_id,
                context_fingerprint=context_fingerprint,
                interaction_mode=interaction_mode,
                context_sources=context_sources,
                dhis2_environment=dhis2_environment,
            )
        except AgentCenterError as exc:
            return _routing_http_error(exc)
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "router_unavailable"}), 503
        execution = result.get("execution") or {}
        _audit(
            audit_actions.AIRIX_ROUTING_EXECUTE,
            detail={
                "execution_id": execution.get("id"),
                "provider_id": execution.get("provider_id"),
                "status": execution.get("status"),
                "manual_override": bool(agent_override) or bool(execution.get("manual_override")),
                "selected_provider": execution.get("selected_provider")
                or agent_override
                or "",
                "recommended_provider": execution.get("recommended_provider") or "",
                "resolved_provider": execution.get("resolved_provider")
                or execution.get("adapter_id")
                or execution.get("provider_id")
                or "",
                "selected_model": model
                or execution.get("selected_model")
                or "",
                "recommended_model": execution.get("recommended_model") or "",
                "resolved_model": execution.get("resolved_model")
                or execution.get("model")
                or "",
                "fallback_reason": execution.get("fallback_reason")
                or execution.get("fallback_from")
                or "",
                "mode": execution.get("mode"),
                "attempt": attempt,
            },
        )
        return jsonify(result)

    @app.post("/api/assistants/<profile_id>/routing/cancel")
    @require_owner
    def api_airix_routing_cancel(profile_id: str):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing is AiriX (Work) only"}
            ), 400
        payload = request.get_json(silent=True) or {}
        execution_id = str(payload.get("execution_id") or "").strip()
        if not execution_id:
            return jsonify({"ok": False, "error": "execution_id is required"}), 400
        try:
            row = _router().cancel_execution(execution_id)
        except AgentCenterError as exc:
            return _routing_http_error(exc)
        _audit(
            audit_actions.AIRIX_ROUTING_CANCEL,
            detail={"execution_id": execution_id, "status": row.get("status")},
        )
        return jsonify({"ok": True, "execution": row})

    @app.get("/api/assistants/<profile_id>/routing/status/<execution_id>")
    @app.get("/api/assistants/<profile_id>/routing/status")
    def api_airix_routing_status(profile_id: str, execution_id: str | None = None):
        if not _routing_work_ok(profile_id):
            return jsonify(
                {"ok": False, "error": "Smart Routing is AiriX (Work) only"}
            ), 400
        eid = (execution_id or request.args.get("execution_id") or "").strip()
        if not eid:
            return jsonify({"ok": False, "error": "execution_id is required"}), 400
        row = _router().execution_status(eid)
        if row is None:
            return jsonify({"ok": False, "error": "Execution not found", "code": "execution_not_found"}), 404
        return jsonify({"ok": True, "execution": row})

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

    @app.get("/api/agents/repositories")
    @app.get("/api/assistants/<profile_id>/repositories")
    def api_agent_repositories(profile_id: str = "okarun"):
        try:
            _svc().page_bootstrap(profile_id)
        except ValueError:
            return jsonify({"error": "Unknown assistant profile"}), 404
        repos = _svc().repositories(profile_id)
        return jsonify(
            {
                "repositories": [
                    {
                        "id": r.get("id"),
                        "name": r.get("name") or r.get("label") or r.get("id"),
                        "path": r.get("local_path") or r.get("path") or "",
                        "selectable": bool(r.get("selectable")),
                        "connected": bool(r.get("selectable")),
                        "intelligence": r.get("intelligence") or {},
                    }
                    for r in repos
                    if r.get("id") and r.get("selectable")
                ]
            }
        )

    @app.get("/api/repositories/<repo_id>/intelligence")
    def api_repository_intelligence(repo_id: str):
        try:
            return jsonify({"ok": True, **_svc().repository_intelligence.knowledge(repo_id)})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.post("/api/repositories/<repo_id>/intelligence/scan")
    @require_owner
    def api_repository_intelligence_scan(repo_id: str):
        try:
            result = _svc().repository_intelligence.scan(
                repo_id, incremental=False, trigger="manual_scan"
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        _audit(
            "REPOSITORY_INTELLIGENCE_SCAN",
            detail={
                "repository_id": repo_id,
                "status": result.get("status"),
                "indexed_commit": result.get("indexed_commit"),
                "telemetry": result.get("last_scan_telemetry") or {},
            },
        )
        return jsonify({"ok": result.get("status") == "current", "status": result})

    @app.post("/api/repositories/<repo_id>/intelligence/refresh")
    @require_owner
    def api_repository_intelligence_refresh(repo_id: str):
        try:
            before = _svc().repository_intelligence.get_status(repo_id)
            changed = list(before.get("changed_files") or [])
            result = _svc().repository_intelligence.scan(
                repo_id,
                incremental=bool(before.get("last_scan")),
                changed_files=changed or None,
                trigger="manual_refresh",
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        _audit(
            "REPOSITORY_INTELLIGENCE_REFRESH",
            detail={
                "repository_id": repo_id,
                "status": result.get("status"),
                "changed_files": len(changed),
                "telemetry": result.get("last_scan_telemetry") or {},
            },
        )
        return jsonify({"ok": result.get("status") == "current", "status": result})

    @app.get("/api/repositories/<repo_id>/repobrain")
    def api_repobrain(repo_id: str):
        try:
            snapshot = _svc().repobrain.get_snapshot(
                repo_id, refresh=request.args.get("refresh") == "1"
            )
            return jsonify({
                "ok": snapshot is not None,
                "snapshot": snapshot,
                "history": _svc().repobrain.history(repo_id),
            })
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.post("/api/repositories/<repo_id>/repobrain/refresh")
    @require_owner
    def api_repobrain_refresh(repo_id: str):
        try:
            result = _svc().repobrain.build(repo_id)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        _audit(
            "REPOBRAIN_REFRESH",
            detail={
                "repository_id": repo_id,
                "snapshot_id": result.get("id"),
                "version": result.get("version"),
                "reused": bool(result.get("reused")),
                "refresh": result.get("refresh") or {},
            },
        )
        return jsonify({"ok": True, "snapshot": result})

    @app.post("/api/repositories/<repo_id>/repobrain/rebuild")
    @require_owner
    def api_repobrain_rebuild(repo_id: str):
        try:
            result = _svc().repobrain.full_rebuild(repo_id)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        _audit(
            "REPOBRAIN_FULL_REBUILD",
            detail={
                "repository_id": repo_id,
                "snapshot_id": result.get("id"),
                "version": result.get("version"),
                "refresh": result.get("refresh") or {},
            },
        )
        return jsonify({"ok": True, "snapshot": result})

    @app.get("/api/repobrain/relationships")
    def api_repobrain_relationships():
        snapshot = _svc().repobrain.get_cross_snapshot(
            refresh=request.args.get("refresh") == "1"
        )
        return jsonify({
            "ok": snapshot is not None,
            "snapshot": snapshot,
            "history": _svc().repobrain.cross_history(),
        })

    @app.post("/api/repobrain/relationships/refresh")
    @require_owner
    def api_repobrain_relationships_refresh():
        try:
            result = _svc().repobrain.build_cross_snapshot()
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        _audit(
            "REPOBRAIN_CROSS_REFRESH",
            detail={
                "snapshot_id": result.get("id"),
                "version": result.get("version"),
                "reused": bool(result.get("reused")),
                "refresh": result.get("refresh") or {},
            },
        )
        return jsonify({"ok": True, "snapshot": result})

    @app.post("/api/repobrain/relationships/rebuild")
    @require_owner
    def api_repobrain_relationships_rebuild():
        try:
            result = _svc().repobrain.full_rebuild_cross_snapshot()
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        _audit(
            "REPOBRAIN_CROSS_FULL_REBUILD",
            detail={
                "snapshot_id": result.get("id"),
                "version": result.get("version"),
                "refresh": result.get("refresh") or {},
            },
        )
        return jsonify({"ok": True, "snapshot": result})

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
            "grounding": (run.get("context") or {}).get("grounding") or {},
            "evidence_packet": (run.get("context") or {}).get("evidence_packet") or {},
            "selected_model": (run.get("context") or {}).get("selected_model") or "",
            "resolved_model": (run.get("context") or {}).get("resolved_model")
            or run.get("model")
            or "",
        },
    }
    grounding = out["context"].get("grounding") or {}
    if grounding:
        out["grounding"] = grounding
    out["selected_model"] = out["context"]["selected_model"]
    out["resolved_model"] = out["context"]["resolved_model"]
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
