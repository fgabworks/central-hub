"""Orchestration for Prompting & Agent Center."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hub.agent_center.adapters import build_adapters
from hub.agent_center.adapters.base import AgentAdapter, public_availability
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.codex_safety import assert_safe_codex_argv, resolve_approved_repo_cwd
from hub.agent_center.context_builder import build_context_preview, selectable_repositories
from hub.agent_center.connections import AgentConnectionRegistry
from hub.agent_center.models import (
    DEFAULT_TIMEOUT_SECONDS,
    DISABLED_MODES,
    MAX_PROMPT_CHARS,
    MODES,
    mode_label,
    normalize_mode,
)
from hub.agent_center.openai_runner import OpenAIRunner
from hub.agent_center.openai_settings import OpenAISettings, load_openai_settings
from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.profiles import PROFILES, get_profile, normalize_tools
from hub.agent_center.runner import AgentRunner
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry
from hub.settings import ROOT_DIR

AuditFn = Callable[..., None]


class AgentCenterError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class AgentCenterService:
    def __init__(
        self,
        registry: Registry,
        *,
        store: AgentCenterStore | None = None,
        adapters: list[AgentAdapter] | None = None,
        audit: AuditFn | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        openai_settings: OpenAISettings | None = None,
        notebook: Any | None = None,
        sql_store: Any | None = None,
        uid_index: Any | None = None,
        email: Any | None = None,
        calendar: Any | None = None,
        job_store: Any | None = None,
        audit_store: Any | None = None,
        notepad_factory: Callable[[str], Any] | None = None,
        dhis2_reports: Any | None = None,
        sql_executor: Any | None = None,
        sql_connections: Any | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or AgentCenterStore()
        self.adapters = adapters if adapters is not None else build_adapters()
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self.openai_settings = openai_settings or load_openai_settings()
        self.notebook = notebook
        self.sql_store = sql_store
        self.sql_executor = sql_executor
        self.sql_connections = sql_connections
        self.uid_index = uid_index
        self.email = email
        self.calendar = calendar
        self.job_store = job_store
        self.audit_store = audit_store
        self.notepad_factory = notepad_factory
        self.dhis2_reports = dhis2_reports
        self.runner = AgentRunner(self.store, audit=audit)
        self.openai_runner = OpenAIRunner(
            self.store,
            settings=self.openai_settings,
            audit=audit,
        )
        self.api_runners: dict[str, OpenAIRunner] = {"openai-api": self.openai_runner}
        for adapter in self.adapters:
            if adapter.descriptor.id == "grok" and hasattr(adapter, "settings"):
                self.api_runners["grok"] = OpenAIRunner(
                    self.store, settings=adapter.settings, client=adapter.client, audit=audit
                )
        connection_providers = {"codex", "claude_code", "cursor_agent", "openai_api", "xai_api"}
        provider_adapters = [a for a in self.adapters if a.descriptor.provider in connection_providers]
        self.connections = AgentConnectionRegistry(provider_adapters, self.store, audit=audit)

    def list_modes(self) -> list[dict[str, Any]]:
        rows = [{"id": m, "label": mode_label(m), "enabled": True} for m in MODES]
        for m in DISABLED_MODES:
            rows.append({"id": m, "label": mode_label(m), "enabled": False, "note": "Not yet available"})
        return rows

    def list_agents(
        self,
        *,
        mode: str | None = None,
        probe: bool = True,
        profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        mode_n = normalize_mode(mode) if mode else None
        out: list[dict[str, Any]] = []
        for adapter in self.adapters:
            allowed = getattr(adapter, "profiles_allowed", None)
            if profile_id and allowed and profile_id not in allowed:
                continue
            if adapter.descriptor.id not in self.connections.adapters:
                av = adapter.availability()
                row = public_availability(av)
                row["connection_state"] = "connected"
            else:
                connection = self.connections.get(adapter.descriptor.id, probe=probe)
                models: list[str] = []
                source = "none"
                if hasattr(adapter, "list_models"):
                    try:
                        models, source = adapter.list_models()
                    except Exception:
                        models, source = [], "none"
                row = {
                    "id": adapter.descriptor.id,
                    "label": adapter.descriptor.label,
                    "status": connection["state"],
                    "connection_state": connection["state"],
                    "detail": connection["detail"],
                    "executable_found": connection["installed"],
                    "installed": connection.get("installed"),
                    "authenticated": connection.get("authenticated"),
                    "version": connection.get("version") or "",
                    "modes": list(adapter.descriptor.modes),
                    "models": models,
                    "models_source": source,
                    "supports_cancel": True,
                    "supports_streaming": True,
                    "runnable": connection["state"] == "connected",
                    "capabilities": connection["capabilities"],
                    "from_cache": bool(connection.get("from_cache")),
                    "pending_refresh": bool(connection.get("pending_refresh")),
                }
            row["is_api"] = bool(getattr(adapter, "is_api_adapter", False))
            if mode_n and mode_n not in row["modes"]:
                row["runnable"] = False
                row["detail"] = (row.get("detail") or "") + f" · mode {mode_n} unsupported"
            out.append(row)
        return out

    def get_agent(self, agent_id: str) -> AgentAdapter | None:
        for adapter in self.adapters:
            if adapter.descriptor.id == agent_id:
                return adapter
        return None

    def list_models(self, agent_id: str, *, mode: str | None = None) -> dict[str, Any]:
        adapter = self.get_agent(agent_id)
        if adapter is None:
            raise AgentCenterError(f"Unknown agent: {agent_id}", code="unknown_agent")
        mode_n = normalize_mode(mode) if mode else "ask"
        connection = self.connections.get(agent_id) if agent_id in self.connections.adapters else None
        av = adapter.availability()
        if hasattr(adapter, "list_model_details"):
            details = adapter.list_model_details(mode=mode_n)
            return {
                "agent_id": agent_id,
                "mode": mode_n,
                "models": details.get("models") or [],
                "model_details": details.get("model_details") or [],
                "groups": details.get("groups") or {},
                "recommended_model": details.get("recommended_model"),
                "recommendation_reason": details.get("recommendation_reason"),
                "models_source": details.get("models_source"),
                "default_model": getattr(getattr(adapter, "settings", None), "default_model", ""),
                "reasoning_efforts": details.get("reasoning_efforts") or [],
                "status": connection["state"] if connection else av.status,
                "runnable": (connection is None or connection["state"] == "connected") and bool(details.get("models")),
                "error": details.get("error") or "",
            }

        models, source = adapter.list_models()
        return {
            "agent_id": agent_id,
            "mode": mode_n,
            "models": models,
            "model_details": [{"id": m, "display_name": m, "availability": "available"} for m in models],
            "groups": {},
            "recommended_model": models[0] if models else None,
            "recommendation_reason": "managed",
            "models_source": source,
            "default_model": "",
            "reasoning_efforts": [],
            "status": av.status,
            "runnable": av.status in {"available", "degraded"} and bool(models),
            "error": "",
        }

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        profile = get_profile(profile_id)
        return selectable_repositories(self.registry) if profile.repositories_allowed else []

    def resolve_repository_ids(
        self,
        profile_id: str,
        *,
        repository_ids: list[str] | None = None,
        agent_id: str = "",
        active_repository_id: str | None = None,
        selected_repository_id: str | None = None,
        raise_on_error: bool = True,
    ) -> list[str]:
        """
        Resolve repository scope for a run.

        Priority: explicit → persisted selection → active workspace → sole connected.
        Never blind-picks the first of many. Raises when a coding CLI needs a repo
        and none can be resolved.
        """
        from hub.agent_center.repository_context import resolve_repository_context

        try:
            selectable = self.repositories(profile_id)
        except Exception:  # noqa: BLE001
            selectable = []
        resolved = resolve_repository_context(
            agent_id=agent_id,
            repository_ids=repository_ids,
            active_repository_id=active_repository_id,
            selected_repository_id=selected_repository_id,
            repositories=selectable,
        )
        if not resolved["ok"]:
            if raise_on_error:
                raise AgentCenterError(
                    str(resolved.get("error") or "Repository required"),
                    code=str(resolved.get("code") or "repository_required"),
                )
            return []
        return list(resolved.get("repository_ids") or [])

    def default_repository_ids(
        self,
        profile_id: str,
        *,
        repository_ids: list[str] | None = None,
        agent_id: str = "",
        active_repository_id: str | None = None,
        selected_repository_id: str | None = None,
    ) -> list[str]:
        """Compatibility wrapper — does not raise; returns [] when unresolved."""
        return self.resolve_repository_ids(
            profile_id,
            repository_ids=repository_ids,
            agent_id=agent_id,
            active_repository_id=active_repository_id,
            selected_repository_id=selected_repository_id,
            raise_on_error=False,
        )

    def tools_context(
        self,
        *,
        profile_id: str = "okarun",
        tool_ids: list[str] | None = None,
        repository_ids: list[str] | None = None,
    ) -> AgentToolsContext:
        profile = get_profile(profile_id)
        tools = normalize_tools(profile, tool_ids)
        return AgentToolsContext(
            registry=self.registry,
            repository_ids=list(repository_ids or []),
            notebook=self.notebook,
            sql_store=self.sql_store,
            uid_index=self.uid_index,
            email=self.email,
            calendar=self.calendar,
            job_store=self.job_store,
            audit_store=self.audit_store,
            dhis2_reports=self.dhis2_reports,
            notepad_factory=self.notepad_factory,
            profile_id=profile.id,
            workspace=profile.workspace,
            allowed_tools=set(tools),
        )

    def prepare_grounding(
        self,
        prompt: str,
        *,
        profile_id: str = "okarun",
        repository_ids: list[str] | None = None,
        tool_ids: list[str] | None = None,
        evidence_packet: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect evidence and grounding rules for a prompt."""
        from hub.agent_center.grounding import (
            collect_evidence_packet,
            format_evidence_for_prompt,
            grounding_rules_text,
            requires_project_grounding,
            resolve_prompt_scope,
        )

        repos = list(repository_ids or [])
        scope = resolve_prompt_scope(prompt, repository_ids=repos)
        requires = scope.requires_project_evidence
        if not scope.use_selected_repo:
            # Explicit broader/national/GK scope — do not force repo evidence.
            collect_repos = []
        else:
            collect_repos = repos
        packet = evidence_packet
        if packet is None and (requires or scope.try_deterministic_tools):
            ctx = self.tools_context(
                profile_id=profile_id,
                tool_ids=tool_ids,
                repository_ids=collect_repos,
            )
            packet = collect_evidence_packet(prompt, ctx, repository_ids=collect_repos)
        elif packet is None:
            from hub.agent_center.grounding import empty_evidence_packet

            packet = empty_evidence_packet(repository_ids=collect_repos)
        rules = grounding_rules_text(
            repository_ids=repos if scope.use_selected_repo else [],
            requires=requires,
            scope=scope,
        )
        return {
            "required": requires,
            "evidence_packet": packet,
            "evidence_packet_text": format_evidence_for_prompt(packet),
            "grounding_rules": rules,
            "usable": bool((packet or {}).get("usable")),
            "scope": scope.public(),
            "allow_general_knowledge": scope.allow_general_knowledge,
        }

    def preview_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = get_profile(str(payload.get("profile_id") or "okarun"))
        except ValueError as exc:
            raise AgentCenterError(str(exc), code="unknown_profile") from exc
        requested_tools = payload.get("tool_ids")
        selected_tools = normalize_tools(
            profile, list(requested_tools) if isinstance(requested_tools, list) else None
        )
        repos = list(payload.get("repository_ids") or [])
        grounding = self.prepare_grounding(
            str(payload.get("prompt") or ""),
            profile_id=profile.id,
            repository_ids=repos,
            tool_ids=selected_tools,
            evidence_packet=payload.get("evidence_packet")
            if isinstance(payload.get("evidence_packet"), dict)
            else None,
        )
        preview = build_context_preview(
            self.registry,
            repository_ids=repos,
            mode=str(payload.get("mode") or "ask"),
            prompt=str(payload.get("prompt") or ""),
            query_hints=list(payload.get("hints") or []),
            explicit_files=dict(payload.get("files") or {}),
            profile=profile,
            selected_tools=selected_tools,
            grounding_rules=str(grounding.get("grounding_rules") or ""),
            evidence_packet_text=str(grounding.get("evidence_packet_text") or ""),
            evidence_packet=grounding.get("evidence_packet") or {},
        )
        preview["tools"] = {
            "enabled": selected_tools,
            "disabled": [
                "edit",
                "terminal",
                "sql_execute",
                "email_action",
                "calendar_action",
                "dhis2_write",
                "repository_run",
                "auto_apply",
            ],
        }
        preview["grounding"] = {
            "required": grounding.get("required"),
            "usable": grounding.get("usable"),
        }
        return preview

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = get_profile(str(payload.get("profile_id") or "okarun"))
        except ValueError as exc:
            raise AgentCenterError(str(exc), code="unknown_profile") from exc
        payload = {**payload, "profile_id": profile.id}
        mode = normalize_mode(str(payload.get("mode") or "ask"))
        if mode in DISABLED_MODES:
            raise AgentCenterError("Edit/Test modes are not yet available", code="mode_disabled")
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise AgentCenterError("Prompt is required", code="prompt_required")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AgentCenterError(f"Prompt exceeds {MAX_PROMPT_CHARS} characters", code="prompt_too_long")

        agent_id = str(payload.get("agent_id") or "").strip()
        adapter = self.get_agent(agent_id)
        if adapter is None:
            raise AgentCenterError(f"Unknown agent: {agent_id}", code="unknown_agent")
        allowed_profiles = getattr(adapter, "profiles_allowed", None)
        if allowed_profiles and profile.id not in allowed_profiles:
            raise AgentCenterError(
                f"{adapter.descriptor.label} is available for AiriX only in this MVP",
                code="profile_unsupported",
            )
        resolved_repos = self.resolve_repository_ids(
            profile.id,
            repository_ids=list(payload.get("repository_ids") or []),
            agent_id=agent_id,
            active_repository_id=str(payload.get("active_repository_id") or "").strip() or None,
            selected_repository_id=str(payload.get("selected_repository_id") or "").strip() or None,
        )
        payload = {**payload, "repository_ids": resolved_repos}

        from hub.agent_center.grounding import (
            apply_grounding_to_answer,
            evaluate_answer_grounding,
            format_cannot_verify,
        )
        from hub.agent_center.repository_context import agent_requires_repository

        grounding = self.prepare_grounding(
            prompt,
            profile_id=profile.id,
            repository_ids=resolved_repos,
            tool_ids=list(payload.get("tool_ids") or []) or None,
            evidence_packet=payload.get("evidence_packet")
            if isinstance(payload.get("evidence_packet"), dict)
            else None,
        )
        payload["evidence_packet"] = grounding.get("evidence_packet")
        payload["grounding_rules"] = grounding.get("grounding_rules")
        # Coding CLIs have no Hub tool loop — never send project questions without evidence.
        if (
            grounding.get("required")
            and agent_requires_repository(agent_id)
            and not grounding.get("usable")
            and not bool(payload.get("allow_general_knowledge"))
            and not bool((grounding.get("scope") or {}).get("allow_general_knowledge"))
        ):
            packet = grounding.get("evidence_packet") or {}
            answer = format_cannot_verify(
                repository_ids=resolved_repos,
                reason=str(packet.get("summary") or "No usable project evidence from Hub tools."),
                errors=list(packet.get("errors") or []),
            )
            status = evaluate_answer_grounding(
                prompt,
                answer,
                repository_ids=resolved_repos,
                evidence=packet,
            )
            answer = apply_grounding_to_answer(answer, status)
            conversation_id = str(payload.get("conversation_id") or "").strip()
            if not conversation_id:
                conversation = self.store.create_conversation(
                    profile_id=profile.id, title=prompt[:80]
                )
                conversation_id = conversation["id"]
            run = self.store.create_run(
                {
                    "status": "completed",
                    "mode": mode,
                    "agent_id": agent_id,
                    "agent_label": adapter.descriptor.label,
                    "model": str(payload.get("model") or ""),
                    "repository_ids": resolved_repos,
                    "prompt": prompt,
                    "packed_prompt": "",
                    "answer": answer,
                    "context": {
                        "grounding": status,
                        "evidence_packet": {
                            "summary": packet.get("summary"),
                            "usable": False,
                            "sources": packet.get("sources") or [],
                            "errors": packet.get("errors") or [],
                            "hit_count": len(packet.get("hits") or []),
                        },
                        "included_sources": list(packet.get("sources") or [])
                        + [f"repository:{r}" for r in resolved_repos],
                        "tools": {"enabled": list(payload.get("tool_ids") or [])},
                    },
                    "referenced_files": [],
                    "profile_id": profile.id,
                    "conversation_id": conversation_id,
                    "error": "",
                    "finished_at": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                }
            )
            if self.audit:
                self.audit(
                    action="AGENT_RUN_UNGROUNDED_BLOCK",
                    detail={"run_id": run.get("id"), "agent_id": agent_id, "repos": resolved_repos},
                )
            return run

        connection = self.connections.get(agent_id) if agent_id in self.connections.adapters else None
        av = adapter.availability()
        if connection is not None and connection["state"] != "connected" or connection is None and av.status not in {"available", "degraded"}:
            unavailable_detail = connection["detail"] if connection else av.detail
            run = self.store.create_run(
                {
                    "status": "unavailable",
                    "mode": mode,
                    "agent_id": agent_id,
                    "agent_label": av.label,
                    "model": str(payload.get("model") or ""),
                    "repository_ids": list(payload.get("repository_ids") or []),
                    "prompt": prompt,
                    "packed_prompt": "",
                    "context": {"detail": unavailable_detail, "connection": connection or {}},
                    "referenced_files": [],
                    "profile_id": profile.id,
                    "conversation_id": "",
                }
            )
            self.store.update_run(
                run["id"],
                status="unavailable",
                error=unavailable_detail or "Agent unavailable",
                finished_at=run["created_at"],
            )
            if self.audit:
                self.audit(
                    action="AGENT_RUN_UNAVAILABLE",
                    detail={"run_id": run["id"], "agent_id": agent_id, "detail": unavailable_detail},
                )
            return self.store.get_run(run["id"]) or run

        if mode not in av.modes:
            raise AgentCenterError(f"Agent does not support mode {mode}", code="mode_unsupported")

        selected_model = str(payload.get("model") or "").strip()
        reasoning_effort_raw = str(payload.get("reasoning_effort") or "").strip()
        run_opts: dict[str, Any] = {}

        from hub.agent_center.model_selection import resolve_model_for_run

        resolution = resolve_model_for_run(
            adapter,
            agent_id=agent_id,
            mode=mode,
            selected_model=selected_model,
            force_refresh=True,
            provider_changed=bool(payload.get("provider_changed")),
            previous_provider=str(payload.get("previous_provider") or ""),
        )
        if not resolution.ok:
            raise AgentCenterError(
                resolution.error or "Model unavailable",
                code=resolution.code or "model_unavailable",
            )
        model = resolution.resolved_model
        details = resolution.details or {}
        if getattr(adapter, "is_api_adapter", False):
            from hub.agent_center.openai_catalog import normalize_reasoning_effort

            effort = normalize_reasoning_effort(
                reasoning_effort_raw,
                supported=bool(details.get("supports_reasoning_effort")),
            )
            if reasoning_effort_raw and details.get("supports_reasoning_effort") and effort is None:
                raise AgentCenterError(
                    f"Invalid reasoning_effort {reasoning_effort_raw!r}",
                    code="reasoning_effort_invalid",
                )
            run_opts = {
                "reasoning_effort": effort,
                "background": bool(details.get("background")),
                "is_pro": bool(details.get("is_pro")),
                "timeout_seconds": float(
                    details.get("timeout_seconds") or self.openai_settings.timeout_seconds
                ),
                "selection_reason": resolution.reason,
                "selected_model": resolution.selected_model,
                "fallback_reason": resolution.fallback_reason,
            }
        else:
            run_opts = {
                "selection_reason": resolution.reason,
                "selected_model": resolution.selected_model,
                "fallback_reason": resolution.fallback_reason,
            }

        preview = self.preview_context(payload)
        if not preview.get("ok"):
            errors = preview.get("scope_errors") or []
            missing = preview.get("missing_repository_ids") or []
            msg = "; ".join(errors + [f"missing:{m}" for m in missing]) or "Invalid repository scope"
            raise AgentCenterError(msg, code="scope_invalid")

        roots = preview["roots"]
        if isinstance(adapter, CodexAdapter):
            if not roots:
                raise AgentCenterError(
                    "Codex requires a selected connected repository. "
                    "Connect a repository in Work, then choose it in the assistant (or leave blank to use the first connected repo).",
                    code="repository_required",
                )
            approved = [Path(r["path"]) for r in roots]
            try:
                cwd = resolve_approved_repo_cwd(roots[0]["path"], approved)
            except ValueError as exc:
                raise AgentCenterError(str(exc), code="scope_invalid") from exc
        else:
            cwd = Path(roots[0]["path"]) if roots else ROOT_DIR
        packed = preview["packed_prompt"]
        referenced = [
            {"repo_id": f["repo_id"], "path": f["path"]} for f in preview.get("files") or []
        ]
        for item in preview.get("instructions") or []:
            referenced.append({"repo_id": item["repo_id"], "path": item["path"], "kind": "instruction"})

        conversation_id = str(payload.get("conversation_id") or "").strip()
        if conversation_id:
            if not self.store.get_conversation(conversation_id, profile_id=profile.id):
                raise AgentCenterError("Conversation not found", code="conversation_not_found")
        else:
            conversation = self.store.create_conversation(
                profile_id=profile.id, title=prompt[:80]
            )
            conversation_id = conversation["id"]
        self.store.update_conversation_summary(
            conversation_id, profile_id=profile.id, summary=prompt[:500]
        )

        run = self.store.create_run(
            {
                "status": "queued",
                "mode": mode,
                "agent_id": agent_id,
                "agent_label": av.label,
                "model": model,
                "repository_ids": preview["repository_ids"],
                "prompt": prompt,
                "packed_prompt": packed,
                "context": {
                    "roots": roots,
                    "files": preview.get("files") or [],
                    "excluded_secrets": preview.get("excluded_secrets") or [],
                    "included_sources": preview.get("included_sources") or [],
                    "excluded_sources": preview.get("excluded_sources") or [],
                    "packed_prompt_chars": preview.get("packed_prompt_chars"),
                    "tools": preview.get("tools"),
                    "grounding": preview.get("grounding") or {},
                    "evidence_packet": {
                        "summary": (preview.get("evidence_packet") or {}).get("summary"),
                        "usable": (preview.get("evidence_packet") or {}).get("usable"),
                        "sources": (preview.get("evidence_packet") or {}).get("sources") or [],
                        "hit_count": len((preview.get("evidence_packet") or {}).get("hits") or []),
                        "errors": (preview.get("evidence_packet") or {}).get("errors") or [],
                    },
                    "model_selection": run_opts.get("selection_reason"),
                    "selected_model": run_opts.get("selected_model"),
                    "resolved_model": model,
                    "fallback_reason": run_opts.get("fallback_reason") or "",
                    "reasoning_effort": run_opts.get("reasoning_effort"),
                    "background": run_opts.get("background"),
                    "is_pro": run_opts.get("is_pro"),
                    "connection": {
                        "state": (connection or {}).get("state", "connected"),
                        "provider": adapter.descriptor.provider,
                    },
                },
                "referenced_files": referenced,
                "profile_id": profile.id,
                "conversation_id": conversation_id,
            }
        )

        if self.audit:
            self.audit(
                action="AGENT_RUN_SUBMIT",
                detail={
                    "run_id": run["id"],
                    "agent_id": agent_id,
                    "mode": mode,
                    "selected_model": run_opts.get("selected_model") or "",
                    "model": model,
                    "model_selection": run_opts.get("selection_reason"),
                    "fallback_reason": run_opts.get("fallback_reason") or "",
                    "reasoning_effort": run_opts.get("reasoning_effort"),
                    "background": run_opts.get("background"),
                    "repository_ids": preview["repository_ids"],
                    "profile_id": profile.id,
                    "conversation_id": conversation_id,
                    "prompt_chars": len(prompt),
                },
            )

        if getattr(adapter, "is_api_adapter", False):
            tools_ctx = AgentToolsContext(
                registry=self.registry,
                repository_ids=list(preview["repository_ids"]),
                notebook=self.notebook,
                sql_store=self.sql_store,
                uid_index=self.uid_index,
                profile_id=profile.id,
                workspace=profile.workspace,
                allowed_tools=set(preview["tools"]["enabled"]),
                email=self.email,
                calendar=self.calendar,
                job_store=self.job_store,
                audit_store=self.audit_store,
                notepad_factory=self.notepad_factory,
                dhis2_reports=self.dhis2_reports,
                max_result_chars=self.openai_settings.max_tool_result_chars,
            )
            tools_ctx.referenced_files.extend(referenced)
            api_runner = self.api_runners.get(agent_id)
            if api_runner is None:
                raise AgentCenterError("API runner unavailable", code="runner_unavailable")
            api_runner.start(
                run_id=run["id"],
                model=model,
                mode=mode,
                user_prompt=prompt,
                packed_prompt=packed,
                tools_ctx=tools_ctx,
                timeout_seconds=float(
                    payload.get("timeout_seconds")
                    or run_opts.get("timeout_seconds")
                    or self.openai_settings.timeout_seconds
                    or self.timeout_seconds
                ),
                reasoning_effort=run_opts.get("reasoning_effort"),
                background=bool(run_opts.get("background")),
                agent_id=agent_id,
            )
            return self.store.get_run(run["id"]) or run

        # CLI adapters
        prompt_dir = ROOT_DIR / "data" / "agent_center" / "runs" / run["id"]
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / "prompt.txt"
        prompt_path.write_text(packed, encoding="utf-8")
        provider = getattr(adapter.descriptor, "provider", "")
        if profile.id == "aira" and not isinstance(adapter, CodexAdapter):
            cwd = prompt_dir

        try:
            argv = adapter.build_argv(
                mode=mode,
                prompt=packed,
                model=model,
                cwd=str(cwd),
                prompt_file=str(prompt_path),
            )
        except TypeError:
            argv = adapter.build_argv(mode=mode, prompt=packed, model=model, cwd=str(cwd))
        except ValueError as exc:
            self.store.update_run(
                run["id"],
                status="failed",
                error=str(exc),
                finished_at=run["created_at"],
            )
            raise AgentCenterError(str(exc), code="argv_invalid") from exc

        if not argv or any(not isinstance(x, str) for x in argv):
            self.store.update_run(run["id"], status="failed", error="Invalid agent argv", finished_at=run["created_at"])
            raise AgentCenterError("Invalid agent argv", code="argv_invalid")
        for part in argv:
            if part in {";", "&&", "||", "|", ">", "<", "`"}:
                self.store.update_run(
                    run["id"], status="failed", error="Rejected unsafe argv token", finished_at=run["created_at"]
                )
                raise AgentCenterError("Rejected unsafe argv token", code="argv_unsafe")
        if isinstance(adapter, CodexAdapter):
            try:
                assert_safe_codex_argv(argv)
            except ValueError as exc:
                self.store.update_run(
                    run["id"], status="failed", error=str(exc), finished_at=run["created_at"]
                )
                raise AgentCenterError(str(exc), code="argv_unsafe") from exc

        run_cwd = prompt_dir
        safety_repo = None
        stdin_path = None
        jsonl = bool(getattr(adapter, "uses_jsonl", False))
        if provider == "hub_simulator" or agent_id == "hub-simulator":
            run_cwd = ROOT_DIR
        elif isinstance(adapter, CodexAdapter):
            run_cwd = cwd
            safety_repo = str(cwd)
            if argv and argv[-1] == "-":
                stdin_path = str(prompt_path)

        self.runner.start(
            run_id=run["id"],
            argv=argv,
            cwd=run_cwd,
            timeout_seconds=float(payload.get("timeout_seconds") or self.timeout_seconds),
            stdin_path=stdin_path,
            jsonl=jsonl,
            safety_repo=safety_repo,
        )
        return self.store.get_run(run["id"]) or run

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        run = self.store.get_run(run_id, profile_id=profile_id)
        if run is None:
            raise AgentCenterError("Run not found", code="not_found")
        # Cooperative cancel for both CLI and API runners
        for api_runner in self.api_runners.values():
            api_runner.cancel(run_id)
        updated = self.runner.cancel(run_id) or self.store.get_run(run_id) or run
        if self.audit:
            self.audit(action="AGENT_RUN_CANCEL", detail={"run_id": run_id})
        return updated

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        run = self.store.get_run(run_id, profile_id=profile_id)
        if run is None:
            raise AgentCenterError("Run not found", code="not_found")
        return run

    def history(self, *, limit: int = 50, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return self.store.list_runs(limit=limit, profile_id=profile_id)

    def retry_run(self, run_id: str, *, profile_id: str) -> dict[str, Any]:
        prior = self.get_run(run_id, profile_id=profile_id)
        return self.start_run(
            {
                "profile_id": profile_id,
                "conversation_id": prior.get("conversation_id"),
                "mode": prior.get("mode"),
                "agent_id": prior.get("agent_id"),
                "model": prior.get("model"),
                "repository_ids": prior.get("repository_ids") or [],
                "tool_ids": ((prior.get("context") or {}).get("tools") or {}).get("enabled"),
                "prompt": prior.get("prompt"),
            }
        )

    def page_bootstrap(self, profile_id: str = "okarun") -> dict[str, Any]:
        profile = get_profile(profile_id)
        return {
            "profile": profile.public(),
            "profiles": [item.public() for item in PROFILES.values()],
            "modes": self.list_modes(),
            # Never probe providers on full-page render — use cached/placeholder status.
            "agents": self.list_agents(probe=False, profile_id=profile.id),
            "repositories": self.repositories(profile.id),
            "prompts": self.store.list_prompts(profile_id=profile.id),
            "history": self.history(limit=30, profile_id=profile.id),
            "conversations": self.store.list_conversations(profile_id=profile.id),
            "openai": self.openai_settings.public_status(),
            "safety": {
                "read_only": True,
                "edit_test": "Not yet available",
                "secret_exclusion": True,
                "output_untrusted": True,
                "profile_isolation": True,
                "tools_allowlist": [
                    "repo_search",
                    "read_file",
                    "uid_lookup",
                    "sql_lookup",
                    "notebook_lookup",
                ],
            },
        }
