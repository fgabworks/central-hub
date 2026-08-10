"""AiriX Smart Routing service — Phase 5 cost intelligence + RBAC + findings."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing.budget import (
    assert_budget_allows,
    band_to_tokens,
    budget_snapshot,
    check_budget_allows,
    expensive_escalation_warning,
)
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import (
    build_minimal_context_preview,
    select_minimal_tools,
    tools_for_repository_knowledge,
)
from hub.agent_center.routing.cost import cost_intelligence, estimate_cost_usd
from hub.agent_center.routing.execution import RouteExecutor, prompt_only_fingerprint
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import (
    ExecutionPlan,
    PromptClassification,
    RouteRecommendation,
    RoutingSettings,
)
from hub.agent_center.routing.lifecycle import (
    DEFAULT_STALE_SECONDS,
    is_stale,
    is_terminal,
    log_lifecycle,
    normalize_status,
    public_execution_fields,
)
from hub.agent_center.routing.orchestrate import (
    build_orchestration_plan,
    is_task_solved,
    plan_estimated_tokens,
)
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.rbac import (
    RoutingAclStore,
    assert_execution_allowed,
    assert_permission,
    check_execution_allowed,
    export_acl_public,
    filter_tools_for_permissions,
    list_rbac_roles,
    live_requested_from_prompt,
    permissions_for_role,
)
from hub.agent_center.routing.roles import detect_role, list_roles
from hub.agent_center.routing.router import estimate_usage_band, recommend_route
from hub.agent_center.routing.settings import (
    default_settings,
    load_routing_settings,
    save_routing_settings,
)
from hub.agent_center.service import AgentCenterError, AgentCenterService


class AgentRouterService:
    """
    Provider-agnostic router used by AiriX.

    Phase 5: cost intelligence, RBAC, relevance findings.
    Order: capability/risk → permissions → budget → history.
    Still executes through RouteExecutor / AgentCenter adapters only.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        availability_loader: Callable[[], dict[str, dict[str, Any]]] | None = None,
        db: Any | None = None,
        agent_center: AgentCenterService | None = None,
        history: RoutingHistoryStore | None = None,
        history_db: AgentCenterDb | None = None,
        acl: RoutingAclStore | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self._availability_loader = availability_loader
        self.db = db
        self.agent_center = agent_center
        if history is not None:
            self.history = history
        elif history_db is not None:
            self.history = RoutingHistoryStore(history_db)
        elif agent_center is not None and getattr(agent_center, "store", None) is not None:
            self.history = RoutingHistoryStore(agent_center.store.db)
        else:
            self.history = None
        if acl is not None:
            self.acl = acl
        elif self.history is not None:
            self.acl = RoutingAclStore(self.history.db)
        elif history_db is not None:
            self.acl = RoutingAclStore(history_db)
        else:
            self.acl = RoutingAclStore()
        self.executor: RouteExecutor | None = None
        if agent_center is not None:
            self.executor = RouteExecutor(
                agent_center,
                availability_loader=availability_loader,
                history=self.history,
            )

    def attach_agent_center(self, agent_center: AgentCenterService) -> None:
        self.agent_center = agent_center
        if self.history is None and getattr(agent_center, "store", None) is not None:
            self.history = RoutingHistoryStore(agent_center.store.db)
            self.acl = RoutingAclStore(self.history.db)
        self.executor = RouteExecutor(
            agent_center,
            availability_loader=self._availability_loader,
            history=self.history,
        )

    def classify_request(
        self,
        prompt: str,
        *,
        hints: list[str] | None = None,
        repository_ids: list[str] | None = None,
    ) -> PromptClassification:
        return classify_prompt(prompt, hints=hints, repository_ids=repository_ids)

    def get_settings(self, workspace: str = "work") -> RoutingSettings:
        if self.db is None:
            return default_settings()
        return load_routing_settings(self.db, workspace)

    def save_settings(
        self,
        payload: dict[str, Any] | None,
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> RoutingSettings:
        if self.db is None:
            raise RuntimeError("Routing settings require a notebook database")
        perms = self.permissions_for(actor, workspace=workspace)
        data = payload if isinstance(payload, dict) else {}
        budget_keys = {
            "daily_token_budget",
            "monthly_token_budget",
            "per_task_max_tokens",
            "price_per_mtok",
            "enable_cost_estimates",
        }
        if budget_keys & set(data.keys()):
            assert_permission(perms, "settings.budget", detail="Budget/settings changes require settings.budget")
        return save_routing_settings(self.db, workspace, payload)

    def permissions_for(self, actor: str, *, workspace: str = "work") -> frozenset[str]:
        role = self.acl.get_role(actor, workspace=workspace)
        return permissions_for_role(role)

    def rbac_snapshot(self, actor: str, *, workspace: str = "work") -> dict[str, Any]:
        role = self.acl.get_role(actor, workspace=workspace)
        return {
            "actor": actor,
            "workspace": workspace,
            "role_id": role,
            "permissions": sorted(permissions_for_role(role)),
            "roles": list_rbac_roles(),
        }

    def list_acl(self, *, workspace: str = "work", actor: str = "owner") -> list[dict[str, Any]]:
        perms = self.permissions_for(actor, workspace=workspace)
        assert_permission(perms, "settings.rbac", detail="Listing ACL requires settings.rbac")
        return export_acl_public(self.acl.list_assignments(workspace=workspace))

    def set_acl_role(
        self,
        target_actor: str,
        role_id: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> dict[str, Any]:
        perms = self.permissions_for(actor, workspace=workspace)
        assert_permission(perms, "settings.rbac", detail="Changing ACL requires settings.rbac")
        return self.acl.set_role(target_actor, role_id, workspace=workspace)

    def list_roles(self) -> list[dict[str, Any]]:
        return list_roles()

    def list_rbac_roles(self) -> list[dict[str, Any]]:
        return list_rbac_roles()

    def list_available_providers(
        self,
        *,
        probe: bool = False,
    ) -> list[dict[str, Any]]:
        availability: dict[str, dict[str, Any]] = {}
        if self._availability_loader is not None:
            try:
                availability = self._availability_loader() or {}
            except Exception:  # noqa: BLE001
                availability = {}
        if probe and not availability and self.agent_center is not None:
            try:
                agents = self.agent_center.list_agents(probe=True, profile_id="okarun")
                availability = {str(a.get("id")): a for a in agents if a.get("id")}
            except Exception:  # noqa: BLE001
                availability = {}
        return self.registry.list_public(availability=availability)

    def estimate_usage(
        self,
        classification: PromptClassification | None = None,
        *,
        prompt: str = "",
        tier: str | None = None,
    ) -> dict[str, Any]:
        c = classification or self.classify_request(prompt)
        t = tier or (
            "T0"
            if c.deterministic_capable
            else "T3"
            if c.needs_architecture or c.complexity >= 75
            else "T2"
            if c.complexity >= 40
            else "T1"
        )
        band = estimate_usage_band(c, t)
        return {
            "estimated_usage": band,
            "estimated_tokens": band_to_tokens(band),
            "tier": t,
            "complexity": c.complexity,
            "context_size": c.context_size,
            "estimated_scope_files": c.estimated_scope_files,
        }

    def analytics(self, *, workspace: str = "work", actor: str | None = None) -> dict[str, Any]:
        actor_n = actor or "owner"
        if self.history is None:
            return {
                "phase": 5,
                "workspace": workspace,
                "actor": actor_n,
                "executions_total": 0,
                "t0_llm_avoided": 0,
                "provider_stats": [],
                "findings_count": 0,
                "cost": {},
                "permissions": self.rbac_snapshot(actor_n, workspace=workspace),
            }
        data = self.history.analytics(workspace=workspace)
        cfg = self.get_settings(workspace)
        events = self.history.list_events(workspace=workspace, actor=actor, limit=500)
        snap = budget_snapshot(events, cfg)
        data["budget"] = snap
        data["cost"] = cost_intelligence(events, cfg, budget=snap)
        data["actor"] = actor_n
        data["roles"] = self.list_roles()
        data["permissions"] = self.rbac_snapshot(actor_n, workspace=workspace)
        data["phase"] = 5
        return data

    def _budget_for(
        self,
        *,
        workspace: str,
        actor: str,
        settings: RoutingSettings,
        task_estimated_tokens: int = 0,
    ) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        if self.history is not None:
            events = self.history.list_events(workspace=workspace, actor=actor, limit=500)
        return budget_snapshot(events, settings, task_estimated_tokens=task_estimated_tokens)

    def _record_permission_block(
        self,
        *,
        workspace: str,
        actor: str,
        provider_id: str,
        task_type: str,
        reason: str,
        rbac_role: str,
    ) -> None:
        if self.history is None:
            return
        try:
            self.history.record_event(
                {
                    "workspace": workspace,
                    "actor": actor,
                    "provider_id": provider_id or "unknown",
                    "tier": "",
                    "task_type": task_type or "general",
                    "status": "blocked",
                    "outcome": "permission_denied",
                    "error_code": "permission_denied",
                    "partial_summary": reason[:200],
                    "permission_denied": True,
                    "rbac_role": rbac_role,
                    "usage_source": "estimate",
                    "estimated_usage": "Very Low",
                    "estimated_tokens": 0,
                    "actual_tokens": 0,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _attach_recommended_model(self, rec: RouteRecommendation) -> None:
        """Recommend Provider + Model using discovered provider catalogs when available."""
        from hub.agent_center.codex_models import pick_model_for_complexity
        from hub.agent_center.routing.context import provider_to_adapter_id

        provider = str(rec.recommended_agent or "").strip()
        if provider in {"", "deterministic"}:
            rec.recommended_model = None
            rec.recommended_model_reason = "t0_no_model"
            return
        adapter_id = provider_to_adapter_id(provider) or provider
        models: list[str] = []
        source = ""
        if self.agent_center is not None:
            try:
                details = self.agent_center.list_models(adapter_id)
                models = [str(m) for m in (details.get("models") or []) if str(m).strip()]
                source = str(details.get("models_source") or "")
                recommended = str(details.get("recommended_model") or "").strip()
            except Exception:  # noqa: BLE001
                details = {}
                recommended = ""
        else:
            recommended = ""

        pick = pick_model_for_complexity(
            models,
            complexity=int(rec.complexity or 0),
            task_type=str(rec.task_type or ""),
        )
        if pick:
            rec.recommended_model = pick
            reason = "lower_cost_available" if int(rec.complexity or 0) < 45 else "strongest_appropriate"
            if int(rec.complexity or 0) >= 65 or rec.task_type in {"architecture", "refactor"}:
                reason = "strongest_appropriate"
            elif int(rec.complexity or 0) < 35:
                reason = "lower_cost_available"
            else:
                reason = "balanced_available"
            rec.recommended_model_reason = f"{reason};source={source or 'catalog'}"
            return
        if recommended and not recommended.startswith("__"):
            rec.recommended_model = recommended
            rec.recommended_model_reason = f"provider_recommended;source={source or 'catalog'}"
            return
        rec.recommended_model = recommended or None
        rec.recommended_model_reason = f"provider_default;source={source or 'none'}"

    def recommend_route(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
        settings: RoutingSettings | None = None,
        probe_providers: bool = False,
        hints: list[str] | None = None,
        session_id: str | None = None,
        repository_ids: list[str] | None = None,
    ) -> RouteRecommendation:
        knowledge: dict[str, Any] = {}
        if self.agent_center is not None and repository_ids:
            try:
                knowledge = self.agent_center.repository_intelligence.retrieve(
                    list(repository_ids), prompt
                )
            except Exception:  # noqa: BLE001
                knowledge = {}
        knowledge_hints = list(hints or [])
        for item in (knowledge.get("items") or [])[:4]:
            knowledge_hints.extend(
                [str(item.get("category") or ""), str(item.get("path") or "")]
            )
        classification = self.classify_request(
            prompt, hints=knowledge_hints, repository_ids=repository_ids
        )
        cfg = settings or self.get_settings(workspace)
        role = detect_role(prompt, classification)
        available_ids: set[str] | None = None
        # Always exclude providers that are not installed+authenticated+healthy when a
        # loader is configured. probe_providers forces a cache invalidate first.
        if self._availability_loader is not None:
            if probe_providers and self.agent_center is not None:
                try:
                    self.agent_center.connections.invalidate()
                except Exception:  # noqa: BLE001
                    pass
            try:
                raw = self._availability_loader() or {}
                if probe_providers and self.agent_center is not None:
                    # Live probe coding CLIs once so recommend does not target stale offline agents.
                    for pid in ("codex", "claude-code", "cursor-agent", "openai-api", "grok"):
                        if pid in self.agent_center.connections.adapters:
                            try:
                                live = self.agent_center.connections.get(pid, refresh=True, probe=True)
                                raw[pid] = {
                                    **(raw.get(pid) or {}),
                                    "runnable": live.get("state") == "connected",
                                    "status": live.get("state"),
                                    "detail": live.get("detail"),
                                }
                            except Exception:  # noqa: BLE001
                                continue
                available_ids = {
                    str(k)
                    for k, v in raw.items()
                    if isinstance(v, dict)
                    and (
                        v.get("runnable")
                        or str(v.get("status") or "") in {"available", "degraded", "connected"}
                    )
                }
                available_ids.add("deterministic")
                # Hub simulator remains a local demo fallback when configured/runnable.
                if "hub-simulator" in raw:
                    sim = raw.get("hub-simulator") or {}
                    if sim.get("runnable") or str(sim.get("status") or "") in {
                        "available",
                        "degraded",
                        "connected",
                    }:
                        available_ids.add("hub-simulator")
            except Exception:  # noqa: BLE001
                available_ids = None

        provider_stats: list[dict[str, Any]] = []
        recent_failures: list[dict[str, Any]] = []
        if self.history is not None and cfg.use_history:
            provider_stats = self.history.provider_stats(
                workspace=workspace, task_type=classification.task_type
            )
            recent_failures = self.history.recent_failures_for_fingerprint(
                prompt_only_fingerprint(prompt), workspace=workspace, limit=10
            )

        rec = recommend_route(
            classification,
            settings=cfg,
            registry=self.registry,
            available_provider_ids=available_ids,
            provider_stats=provider_stats,
            recent_failures=recent_failures,
        )
        self._attach_recommended_model(rec)

        completed: list[str] = []
        if self.history is not None and session_id:
            sess = self.history.get_session(session_id, workspace=workspace, actor=actor)
            if sess:
                completed = list(sess.get("completed_steps") or [])
        elif self.history is not None:
            sess = self.history.find_resumable_session(
                prompt_only_fingerprint(prompt), workspace=workspace, actor=actor
            )
            if sess:
                completed = list(sess.get("completed_steps") or [])

        orch = []
        if cfg.enable_orchestration:
            orch_steps = build_orchestration_plan(
                recommendation=rec,
                role=role,
                settings=cfg,
                resume_completed=completed,
            )
            orch = [s.public() for s in orch_steps]
            est_tokens = plan_estimated_tokens(orch_steps)
        else:
            est_tokens = band_to_tokens(rec.estimated_usage)

        budget = self._budget_for(
            workspace=workspace,
            actor=actor,
            settings=cfg,
            task_estimated_tokens=est_tokens,
        )
        ok, budget_reason = check_budget_allows(budget, additional_tokens=0)
        expensive = expensive_escalation_warning(
            provider_id=rec.recommended_agent,
            tier=rec.recommended_tier,
            settings=cfg,
            estimated_usage=rec.estimated_usage,
        )
        rbac_role = self.acl.get_role(actor, workspace=workspace)
        perms = permissions_for_role(rbac_role)
        tools = tools_for_repository_knowledge(select_minimal_tools(classification), knowledge)
        tools = filter_tools_for_permissions(tools, perms)
        live_req = live_requested_from_prompt(prompt, classification.signals)
        perm_ok, perm_reason = check_execution_allowed(
            perms=perms,
            provider_id=rec.recommended_agent,
            tool_ids=tools,
            approve_codex=False,
            live_requested=live_req,
        )
        # Recommend stays soft on permissions (viewer can still inspect plans).
        prior: list[dict[str, Any]] = []
        if self.history is not None and cfg.use_history:
            candidates = self.history.list_findings(
                workspace=workspace, task_type=rec.task_type, actor=actor, limit=40
            )
            preview = build_minimal_context_preview(
                prompt=prompt,
                classification=classification,
                recommendation=rec,
                candidate_findings=candidates,
            )
            prior = list(preview.get("prior_findings") or [])

        if rec.explanation:
            rec.explanation.role_id = role.id
            rec.explanation.budget_warning = None if ok else budget_reason
            rec.explanation.expensive_warning = expensive
            rec.explanation.permission_warning = None if perm_ok else perm_reason
            rec.explanation.rbac_role = rbac_role
        rec.role_id = role.id
        rec.orchestration = orch
        rec.budget = {**budget, "ok": ok, "blocked_reason": None if ok else budget_reason}
        rec.permissions = {
            "ok": perm_ok,
            "blocked_reason": None if perm_ok else perm_reason,
            "role_id": rbac_role,
            "permissions": sorted(perms),
            "allowed_tools": tools,
        }
        rec.prior_findings = prior
        rec.estimated_cost_usd = estimate_cost_usd(
            est_tokens, provider_id=rec.recommended_agent, settings=cfg
        )
        return rec

    def build_execution_plan(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
        recommendation: RouteRecommendation | None = None,
        settings: RoutingSettings | None = None,
        repository_ids: list[str] | None = None,
        agent_override: str | None = None,
        session_id: str | None = None,
        context_sources: list[str] | None = None,
    ) -> ExecutionPlan:
        cfg = settings or self.get_settings(workspace)
        rec = recommendation or self.recommend_route(
            prompt,
            workspace=workspace,
            actor=actor,
            settings=cfg,
            session_id=session_id,
            repository_ids=repository_ids,
        )
        findings: list[dict[str, Any]] = []
        if self.history is not None:
            findings = self.history.list_findings(
                workspace=workspace, task_type=rec.task_type, actor=actor, limit=40
            )
        context = build_minimal_context_preview(
            prompt=prompt,
            classification=rec.classification,
            recommendation=rec,
            repository_ids=repository_ids,
            agent_override=agent_override,
            candidate_findings=findings,
            context_sources=context_sources,
        )
        knowledge: dict[str, Any] = {}
        if self.agent_center is not None and repository_ids:
            try:
                knowledge = self.agent_center.repository_intelligence.retrieve(
                    list(repository_ids), prompt
                )
            except Exception:  # noqa: BLE001
                knowledge = {}
        context["repository_intelligence"] = knowledge
        context["tool_ids"] = tools_for_repository_knowledge(
            list(context.get("tool_ids") or []), knowledge
        )
        # Enforce tool permission filter on packed context.
        perms = self.permissions_for(actor, workspace=workspace)
        context["tool_ids"] = filter_tools_for_permissions(
            list(context.get("tool_ids") or []), perms
        )
        context["prior_findings"] = list(context.get("prior_findings") or [])[:3]
        steps = [
            "Classify prompt and detect specialized role (capability/risk).",
            "Check RBAC permissions (never overridden by history).",
            f"Role: {rec.role_id}; recommend {rec.recommended_label} ({rec.recommended_tier}).",
            f"Estimated usage: {rec.estimated_usage}.",
            "Check workspace/actor budgets (hard stop if exceeded).",
        ]
        if rec.prior_findings or context.get("prior_findings"):
            steps.append(
                f"Reuse {len(context.get('prior_findings') or rec.prior_findings)} relevant prior findings."
            )
        if rec.orchestration:
            steps.append(
                "Orchestrate: "
                + " → ".join(s.get("label") or s.get("id") for s in rec.orchestration[:6])
            )
        if rec.approval_required:
            steps.append("Require explicit approval before Codex/advanced provider.")
        steps.append("Preserve intermediate findings; resume skips completed expensive steps.")
        return ExecutionPlan(
            prompt=(prompt or "").strip(),
            recommended_agent=rec.recommended_agent,
            alternative_agent=rec.alternative_agent,
            tier=rec.recommended_tier,
            approval_required=rec.approval_required,
            estimated_usage=rec.estimated_usage,
            max_retries=cfg.max_retries,
            steps=steps,
            status="planned",
            context=context,
            explanation=rec.explanation.public() if rec.explanation else {},
            orchestration=list(rec.orchestration),
            budget=dict(rec.budget),
            role_id=rec.role_id,
            session_id=session_id,
            permissions=dict(rec.permissions),
            prior_findings=list(context.get("prior_findings") or []),
            estimated_cost_usd=rec.estimated_cost_usd,
        )

    def execute_route(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
        agent_override: str | None = None,
        repository_ids: list[str] | None = None,
        active_repository_id: str | None = None,
        selected_repository_id: str | None = None,
        approve_codex: bool = False,
        force: bool = False,
        recommendation: RouteRecommendation | None = None,
        attempt: int = 0,
        previous_partial: str = "",
        session_id: str | None = None,
        orchestrate: bool | None = None,
        model: str | None = None,
        routing_mode: str | None = None,
        conversation_id: str | None = None,
        context_fingerprint: str | None = None,
        interaction_mode: str | None = None,
        context_sources: list[str] | None = None,
        dhis2_environment: str | None = None,
    ) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("RouteExecutor requires AgentCenterService")
        from hub.agent_center.routing.context import (
            build_direct_agent_recommendation,
            normalize_context_sources,
            normalize_interaction_mode,
            normalize_routing_mode,
            provider_to_adapter_id,
        )

        cfg = self.get_settings(workspace)
        interaction = normalize_interaction_mode(interaction_mode or routing_mode)
        mode = normalize_routing_mode(interaction)
        sources = normalize_context_sources(context_sources)
        env = str(dhis2_environment or "").strip().lower()
        if env not in {"stage", "live"}:
            env = ""
        if interaction in {"inspect", "plan"}:
            cfg = replace(cfg, prefer_deterministic=True, enable_orchestration=True)
        # Smart owns provider/model resolution. Persisted manual selections are UI
        # state only and must never accidentally become a Smart override.
        if interaction == "smart" and interaction_mode is not None:
            agent_override = None
            model = None
        if interaction == "agent":
            provider = (agent_override or "").strip()
            if not provider:
                raise AgentCenterError(
                    "Direct Agent requires an explicitly selected provider",
                    code="agent_required",
                )
            if provider_to_adapter_id(provider) is None:
                raise AgentCenterError(
                    "Direct Agent cannot use T0/deterministic — select an AI provider",
                    code="agent_required",
                )
            try:
                rec = build_direct_agent_recommendation(
                    prompt,
                    provider_id=provider,
                    model=model,
                    repository_ids=repository_ids,
                )
            except ValueError as exc:
                raise AgentCenterError(str(exc), code="agent_required") from exc
            use_orch = False
            agent_override = provider
        else:
            use_orch = cfg.enable_orchestration if orchestrate is None else bool(orchestrate)
            if agent_override:
                use_orch = False
            if use_orch:
                return self._execute_orchestrated(
                    prompt,
                    workspace=workspace,
                    actor=actor,
                    repository_ids=repository_ids,
                    active_repository_id=active_repository_id,
                    selected_repository_id=selected_repository_id,
                    approve_codex=approve_codex,
                    force=force,
                    recommendation=recommendation,
                    session_id=session_id,
                    model=model,
                    interaction_mode=interaction,
                    context_sources=sources,
                    dhis2_environment=env,
                )
            rec = recommendation or self.recommend_route(
                prompt,
                workspace=workspace,
                actor=actor,
                settings=cfg,
                session_id=session_id,
                repository_ids=repository_ids,
            )

        if not (model or "").strip():
            model = (rec.recommended_model or "").strip() or None
        provider = (agent_override or rec.recommended_agent or "").strip()
        rbac_role = self.acl.get_role(actor, workspace=workspace)
        perms = permissions_for_role(rbac_role)
        tools = filter_tools_for_permissions(select_minimal_tools(rec.classification), perms)
        live_req = live_requested_from_prompt(prompt, rec.classification.signals)
        try:
            assert_execution_allowed(
                perms=perms,
                provider_id=provider,
                tool_ids=tools,
                approve_codex=approve_codex,
                live_requested=live_req,
            )
        except AgentCenterError as exc:
            self._record_permission_block(
                workspace=workspace,
                actor=actor,
                provider_id=provider,
                task_type=rec.task_type,
                reason=str(exc),
                rbac_role=rbac_role,
            )
            raise

        est = band_to_tokens(rec.estimated_usage)
        snap = self._budget_for(
            workspace=workspace, actor=actor, settings=cfg, task_estimated_tokens=est
        )
        assert_budget_allows(snap, additional_tokens=est if provider != "deterministic" else 0)
        if (
            provider in {"codex", "claude-code", "cursor-agent"}
            and cfg.require_approval_before_codex
            and not approve_codex
        ):
            raise AgentCenterError(
                "Codex/advanced agent requires explicit approval before execution",
                code="approval_required",
            )
        plan = self.build_execution_plan(
            prompt,
            workspace=workspace,
            actor=actor,
            recommendation=rec,
            settings=cfg,
            repository_ids=repository_ids,
            agent_override=agent_override,
            session_id=session_id,
            context_sources=sources,
        )
        findings: list[dict[str, Any]] = []
        if self.history is not None:
            findings = self.history.list_findings(
                workspace=workspace, task_type=rec.task_type, actor=actor, limit=40
            )
        result = self.executor.execute(
            prompt=prompt,
            recommendation=rec,
            settings=cfg,
            agent_override=agent_override,
            repository_ids=repository_ids,
            active_repository_id=active_repository_id,
            selected_repository_id=selected_repository_id,
            approve_codex=approve_codex,
            force=force,
            workspace=workspace,
            attempt=attempt,
            candidate_findings=findings,
            previous_partial=previous_partial,
            actor=actor,
            rbac_role=rbac_role,
            model=model,
            manual_override=bool(agent_override) or mode == "direct",
            routing_mode=mode,
            conversation_id=conversation_id,
            context_fingerprint=context_fingerprint,
            interaction_mode=interaction,
            context_sources=sources,
            dhis2_environment=env,
        )
        return {
            "ok": True,
            "phase": 5,
            "recommendation": rec.public(),
            "plan": plan.public(),
            "execution": result,
            "budget": snap,
            "permissions": plan.permissions,
            "prior_findings_reused": list((result.get("prior_findings") or plan.prior_findings)[:3]),
            "routing_mode": mode,
            "interaction_mode": interaction,
            "cost": {
                "estimated_tokens": est,
                "estimated_cost_usd": estimate_cost_usd(est, provider_id=provider, settings=cfg),
                "usage": result.get("usage"),
            },
        }

    def _execute_orchestrated(
        self,
        prompt: str,
        *,
        workspace: str,
        actor: str,
        repository_ids: list[str] | None,
        active_repository_id: str | None = None,
        selected_repository_id: str | None = None,
        approve_codex: bool,
        force: bool,
        recommendation: RouteRecommendation | None,
        session_id: str | None,
        model: str | None = None,
        interaction_mode: str = "smart",
        context_sources: list[str] | None = None,
        dhis2_environment: str | None = None,
    ) -> dict[str, Any]:
        assert self.executor is not None
        cfg = self.get_settings(workspace)
        rec = recommendation or self.recommend_route(
            prompt,
            workspace=workspace,
            actor=actor,
            settings=cfg,
            session_id=session_id,
            repository_ids=repository_ids,
        )
        role = detect_role(prompt, rec.classification)
        rbac_role = self.acl.get_role(actor, workspace=workspace)
        perms = permissions_for_role(rbac_role)
        live_req = live_requested_from_prompt(prompt, rec.classification.signals)
        # Entry gate: execution + live only. Provider/tool checks happen per step so
        # Analysts can still run tool/Grok steps before a Codex pause.
        try:
            assert_permission(perms, "ai.execute", detail="AI execution requires permission ai.execute")
            if live_req:
                assert_permission(perms, "live.access", detail="Live access requires permission live.access")
        except AgentCenterError as exc:
            self._record_permission_block(
                workspace=workspace,
                actor=actor,
                provider_id=rec.recommended_agent,
                task_type=rec.task_type,
                reason=str(exc),
                rbac_role=rbac_role,
            )
            raise

        fp = prompt_only_fingerprint(prompt)
        session = None
        if self.history is not None:
            if session_id:
                session = self.history.get_session(session_id, workspace=workspace, actor=actor)
            if session is None:
                session = self.history.find_resumable_session(fp, workspace=workspace, actor=actor)

        completed = list((session or {}).get("completed_steps") or [])
        intermediate = list((session or {}).get("findings") or [])
        partial = str((session or {}).get("partial_summary") or "")
        actual_tokens = int((session or {}).get("actual_tokens") or 0)

        orch_steps = build_orchestration_plan(
            recommendation=rec,
            role=role,
            settings=cfg,
            resume_completed=completed,
        )
        est_tokens = plan_estimated_tokens(orch_steps)
        snap = self._budget_for(
            workspace=workspace,
            actor=actor,
            settings=cfg,
            task_estimated_tokens=est_tokens + actual_tokens,
        )
        assert_budget_allows(snap)

        if self.history is not None:
            session = self.history.save_session(
                {
                    "id": (session or {}).get("id"),
                    "workspace": workspace,
                    "actor": actor,
                    "prompt_fingerprint": fp,
                    "prompt_preview": prompt[:160],
                    "role_id": role.id,
                    "status": "active",
                    "plan": [s.public() for s in orch_steps],
                    "completed_steps": completed,
                    "findings": intermediate,
                    "partial_summary": partial,
                    "estimated_tokens": est_tokens,
                    "actual_tokens": actual_tokens,
                    "created_at": (session or {}).get("created_at"),
                }
            )

        plan = self.build_execution_plan(
            prompt,
            workspace=workspace,
            actor=actor,
            recommendation=rec,
            settings=cfg,
            repository_ids=repository_ids,
            session_id=(session or {}).get("id"),
        )
        step_results: list[dict[str, Any]] = []
        final_answer = ""
        stopped_reason = ""
        status = "completed"

        for step in orch_steps:
            if step.status == "skipped":
                step_results.append({"step": step.public(), "status": "skipped"})
                continue

            # Budget gate before each non-T0 step.
            step_cost = 0 if step.provider_id == "deterministic" else int(step.estimated_tokens or 0)
            ok, reason = check_budget_allows(
                {**snap, "task_estimated_tokens": actual_tokens + step_cost},
                additional_tokens=step_cost,
            )
            if not ok:
                step.status = "blocked"
                step.skip_reason = reason
                step_results.append({"step": step.public(), "status": "blocked", "error": reason})
                status = "failed"
                stopped_reason = reason
                break

            if step.approval_required and cfg.require_approval_before_codex and not approve_codex:
                step.status = "blocked"
                step.skip_reason = "Codex approval required"
                step_results.append(
                    {
                        "step": step.public(),
                        "status": "paused_for_approval",
                        "error": "approval_required",
                        "expensive_warning": step.expensive_warning,
                    }
                )
                status = "paused_for_approval"
                stopped_reason = "Codex escalation requires explicit approval"
                break

            step_tools = filter_tools_for_permissions(list(step.tools or []), perms)
            ok_perm, perm_reason = check_execution_allowed(
                perms=perms,
                provider_id=step.provider_id,
                tool_ids=step_tools,
                approve_codex=approve_codex if step.approval_required else False,
                live_requested=live_req,
            )
            if not ok_perm:
                step.status = "blocked"
                step.skip_reason = perm_reason
                step_results.append(
                    {"step": step.public(), "status": "blocked", "error": perm_reason}
                )
                status = "failed"
                stopped_reason = perm_reason
                self._record_permission_block(
                    workspace=workspace,
                    actor=actor,
                    provider_id=step.provider_id,
                    task_type=rec.task_type,
                    reason=perm_reason,
                    rbac_role=rbac_role,
                )
                break

            findings = intermediate[:]
            if self.history is not None:
                findings.extend(
                    self.history.list_findings(
                        workspace=workspace, task_type=rec.task_type, actor=actor, limit=40
                    )
                )

            # Force provider for this step via override (deterministic / grok / codex).
            override = step.provider_id
            step_started = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            log_lifecycle(
                event="orch_step_start",
                status="running",
                step_id=step.id,
                provider_id=override,
                tool_ids=step_tools,
                started_at=step_started,
                session_id=str((session or {}).get("id") or ""),
            )
            try:
                result = self.executor.execute(
                    prompt=prompt,
                    recommendation=rec,
                    settings=cfg,
                    agent_override=override,
                    repository_ids=repository_ids,
                    active_repository_id=active_repository_id,
                    selected_repository_id=selected_repository_id,
                    approve_codex=approve_codex if step.approval_required else False,
                    force=force,
                    workspace=workspace,
                    candidate_findings=findings,
                    previous_partial=partial,
                    tool_ids_override=step_tools,
                    actor=actor,
                    rbac_role=rbac_role,
                    model=(
                        model
                        if model and override == rec.recommended_agent
                        else None
                    ),
                    interaction_mode=interaction_mode,
                    context_sources=context_sources,
                    dhis2_environment=dhis2_environment,
                )
            except AgentCenterError as exc:
                if exc.code == "approval_required":
                    status = "paused_for_approval"
                    stopped_reason = str(exc)
                    step.status = "blocked"
                    step_results.append(
                        {"step": step.public(), "status": "paused_for_approval", "error": str(exc)}
                    )
                    break
                if exc.code == "budget_exceeded":
                    status = "failed"
                    stopped_reason = str(exc)
                    break
                status = "failed"
                stopped_reason = str(exc)
                step.status = "failed"
                step_results.append({"step": step.public(), "status": "failed", "error": str(exc)})
                break
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                stopped_reason = str(exc)
                step.status = "failed"
                step_results.append({"step": step.public(), "status": "failed", "error": str(exc)})
                break

            child_status = normalize_status(
                str(result.get("status") or ""),
                error_code=str(result.get("error_code") or "") or None,
            )
            step_results.append({"step": step.public(), "execution": result})
            log_lifecycle(
                event="orch_step_end",
                status=child_status,
                step_id=step.id,
                provider_id=override,
                tool_ids=step_tools,
                started_at=step_started,
                finished_at=str(result.get("finished_at") or ""),
                failure_reason=str(result.get("error") or ""),
                execution_id=str(result.get("id") or ""),
                session_id=str((session or {}).get("id") or ""),
            )

            if child_status == "completed":
                step.status = "completed"
                completed.append(step.id)
                ans = str(result.get("answer") or "")
                if ans:
                    final_answer = ans
                    partial = (partial + "\n" + ans[:200]).strip()[:240]
                    intermediate.append(
                        {
                            "step_id": step.id,
                            "summary": ans[:200],
                            "provider_id": override,
                        }
                    )
                # Track rough actual tokens for session.
                usage = ((result.get("agent_run") or {}) if isinstance(result.get("agent_run"), dict) else {}).get(
                    "usage"
                ) or {}
                tok = usage.get("total_tokens")
                if tok is None and isinstance(result.get("usage"), dict):
                    tok = result["usage"].get("total_tokens")
                if tok is not None:
                    actual_tokens += int(tok)
                elif override != "deterministic":
                    actual_tokens += int(step.estimated_tokens or 0)

                if self.history is not None and session:
                    session = self.history.save_session(
                        {
                            **session,
                            "completed_steps": completed,
                            "findings": intermediate[-8:],
                            "partial_summary": partial,
                            "actual_tokens": actual_tokens,
                            "status": "active",
                            "plan": [s.public() for s in orch_steps],
                        }
                    )

                # Stop early for simple lookups / sufficient analysis.
                if step.id == "step_tool_lookup" and rec.classification.deterministic_capable:
                    if is_task_solved(result, step=step):
                        stopped_reason = "Solved by deterministic tools"
                        status = "completed"
                        break
                if step.id == "step_ai_analysis" and is_task_solved(result, step=step):
                    # Only stop before Codex if Codex wasn't the primary recommendation.
                    if rec.recommended_tier != "T3" and not rec.escalation_reason:
                        stopped_reason = "Solved by AI analysis step"
                        status = "completed"
                        break
            elif child_status == "paused_for_approval":
                status = "paused_for_approval"
                stopped_reason = str(result.get("error") or "Codex approval required")
                step.status = "paused_for_approval"
                break
            elif child_status == "cancelled":
                status = "cancelled"
                stopped_reason = str(result.get("error") or "Cancelled")
                step.status = "cancelled"
                break
            elif child_status == "timed_out":
                status = "timed_out"
                stopped_reason = str(result.get("error") or "Timed out")
                step.status = "timed_out"
                break
            else:
                # failed (includes unavailable/blocked)
                step.status = child_status or "failed"
                if step.id == "step_tool_lookup":
                    # Continue to next steps after tool failure.
                    continue
                status = "failed"
                stopped_reason = str(result.get("error") or step.status)
                break

        status = normalize_status(status)
        if not is_terminal(status):
            # Safety net: never leave parent orchestration non-terminal after the loop.
            status = "failed"
            stopped_reason = stopped_reason or "Orchestration ended without a terminal status"

        if self.history is not None and session:
            session = self.history.save_session(
                {
                    **session,
                    "completed_steps": completed,
                    "findings": intermediate[-8:],
                    "partial_summary": partial,
                    "actual_tokens": actual_tokens,
                    "status": status,
                    "plan": [s.public() for s in orch_steps],
                }
            )

        finished_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        log_lifecycle(
            event="orch_finished",
            status=status,
            step_id=(completed[-1] if completed else ""),
            provider_id=rec.recommended_agent,
            started_at=str((session or {}).get("created_at") or ""),
            finished_at=finished_at,
            failure_reason=stopped_reason,
            session_id=str((session or {}).get("id") or ""),
        )

        execution_payload = public_execution_fields(
            {
                "id": (session or {}).get("id"),
                "status": status,
                "answer": final_answer,
                "provider_id": rec.recommended_agent,
                "mode": "orchestrated",
                "interaction_mode": interaction_mode,
                "partial_summary": partial,
                "error": stopped_reason if status not in {"completed", "paused_for_approval"} else "",
                "error_code": (
                    "approval_required"
                    if status == "paused_for_approval"
                    else ("timed_out" if status == "timed_out" else "")
                ),
                "finished_at": finished_at,
                "current_step": (completed[-1] if completed else ""),
                "stopped_reason": stopped_reason,
                "session_id": (session or {}).get("id"),
            }
        )

        return {
            "ok": status in {"completed", "paused_for_approval"},
            "phase": 5,
            "recommendation": rec.public(),
            "plan": plan.public(),
            "orchestration": {
                "status": status,
                "stopped_reason": stopped_reason,
                "steps": step_results,
                "session_id": (session or {}).get("id"),
                "completed_steps": completed,
                "intermediate_findings": intermediate[-8:],
                "answer": final_answer,
                "actual_tokens": actual_tokens,
            },
            "execution": execution_payload,
            "interaction_mode": interaction_mode,
            "budget": self._budget_for(
                workspace=workspace,
                actor=actor,
                settings=cfg,
                task_estimated_tokens=actual_tokens,
            ),
            "permissions": {
                "role_id": rbac_role,
                "permissions": sorted(perms),
            },
            "prior_findings_reused": list(plan.prior_findings or [])[:3],
            "cost": {
                "actual_tokens": actual_tokens,
                "estimated_cost_usd": estimate_cost_usd(
                    actual_tokens, provider_id=rec.recommended_agent, settings=cfg
                ),
            },
        }

    def cancel_execution(self, execution_id: str) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("RouteExecutor requires AgentCenterService")
        finished: dict[str, Any] | None = None
        try:
            finished = self.executor.cancel(execution_id)
        except AgentCenterError:
            finished = None
        # Also cancel any other active steps and finalize parent session if this is a session id.
        self.executor.cancel_all_active(workspace="work")
        if self.history is not None:
            sess = self.history.get_session(execution_id, workspace="work", actor="owner")
            if sess is None:
                # Try common actors from the cancelled step.
                actor = str((finished or {}).get("actor") or "owner")
                sess = self.history.get_session(execution_id, workspace="work", actor=actor)
            if sess is not None and not is_terminal(str(sess.get("status") or "")):
                sess = self.history.save_session(
                    {
                        **sess,
                        "status": "cancelled",
                        "partial_summary": str(sess.get("partial_summary") or "Cancelled"),
                    }
                )
                log_lifecycle(
                    event="orch_cancelled",
                    status="cancelled",
                    session_id=execution_id,
                    failure_reason="Cancelled by user",
                )
                return public_execution_fields(
                    {
                        **sess,
                        "mode": "orchestrated",
                        "error": "Cancelled by user",
                        "error_code": "cancelled",
                    }
                )
        if finished is None:
            raise AgentCenterError("Execution not found", code="execution_not_found")
        return public_execution_fields(finished)

    def execution_status(self, execution_id: str) -> dict[str, Any] | None:
        if self.executor is None:
            return None
        # Prefer live executor status; fall back to resumable session.
        live = self.executor.refresh(execution_id)
        if live is not None:
            return public_execution_fields(live)
        if self.history is not None:
            sess = self.history.get_session(execution_id, workspace="work", actor="owner")
            if sess is None:
                return None
            # Recover stale "active"/running sessions left behind by crashes/restarts.
            if not is_terminal(str(sess.get("status") or "")) and is_stale(
                sess, stale_seconds=DEFAULT_STALE_SECONDS
            ):
                sess = self.history.save_session(
                    {
                        **sess,
                        "status": "timed_out",
                        "partial_summary": str(sess.get("partial_summary") or "")[:200]
                        or "Stale session recovered",
                    }
                )
                log_lifecycle(
                    event="orch_stale_recovered",
                    status="timed_out",
                    session_id=execution_id,
                    failure_reason="Stale session recovered on status poll",
                )
            # Map legacy session statuses for the UI.
            mapped = normalize_status(str(sess.get("status") or ""))
            return public_execution_fields(
                {
                    **sess,
                    "status": mapped,
                    "mode": "orchestrated",
                    "answer": sess.get("answer") or sess.get("partial_summary") or "",
                    "provider_id": sess.get("provider_id") or "",
                    "error": sess.get("error")
                    or (sess.get("partial_summary") if mapped == "timed_out" else ""),
                }
            )
        return None

    def get_session(
        self,
        session_id: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        if self.history is None:
            return None
        return self.history.get_session(session_id, workspace=workspace, actor=actor)
