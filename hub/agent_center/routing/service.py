"""AiriX Smart Routing service — Phase 3 recommend + execute + history."""

from __future__ import annotations

from typing import Any, Callable

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import build_minimal_context_preview
from hub.agent_center.routing.execution import RouteExecutor, prompt_only_fingerprint
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import (
    ExecutionPlan,
    PromptClassification,
    RouteRecommendation,
    RoutingSettings,
)
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.router import estimate_usage_band, recommend_route
from hub.agent_center.routing.settings import (
    default_settings,
    load_routing_settings,
    save_routing_settings,
)
from hub.agent_center.service import AgentCenterService


class AgentRouterService:
    """
    Provider-agnostic router used by AiriX.

    Phase 3: recommend (history-aware), execute via RouteExecutor, record metrics,
    inject relevant prior findings, support retry/escalation recommendations.
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
    ) -> PromptClassification:
        return classify_prompt(prompt, hints=hints)

    def get_settings(self, workspace: str = "work") -> RoutingSettings:
        if self.db is None:
            return default_settings()
        return load_routing_settings(self.db, workspace)

    def save_settings(
        self,
        payload: dict[str, Any] | None,
        *,
        workspace: str = "work",
    ) -> RoutingSettings:
        if self.db is None:
            raise RuntimeError("Routing settings require a notebook database")
        return save_routing_settings(self.db, workspace, payload)

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
            "tier": t,
            "complexity": c.complexity,
            "context_size": c.context_size,
            "estimated_scope_files": c.estimated_scope_files,
        }

    def analytics(self, *, workspace: str = "work") -> dict[str, Any]:
        if self.history is None:
            return {
                "phase": 3,
                "workspace": workspace,
                "executions_total": 0,
                "executions_by_tier": {},
                "executions_by_provider": {},
                "success_rate": None,
                "t0_llm_avoided": 0,
                "provider_stats": [],
                "findings_count": 0,
                "note": "History store unavailable",
            }
        return self.history.analytics(workspace=workspace)

    def recommend_route(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        settings: RoutingSettings | None = None,
        probe_providers: bool = False,
        hints: list[str] | None = None,
    ) -> RouteRecommendation:
        classification = self.classify_request(prompt, hints=hints)
        cfg = settings or self.get_settings(workspace)
        available_ids: set[str] | None = None
        if probe_providers and self._availability_loader is not None:
            try:
                raw = self._availability_loader() or {}
                available_ids = {
                    str(k)
                    for k, v in raw.items()
                    if isinstance(v, dict)
                    and (
                        v.get("runnable")
                        or str(v.get("status") or "") in {"available", "degraded"}
                    )
                }
                available_ids.add("deterministic")
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

        return recommend_route(
            classification,
            settings=cfg,
            registry=self.registry,
            available_provider_ids=available_ids,
            provider_stats=provider_stats,
            recent_failures=recent_failures,
        )

    def build_execution_plan(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        recommendation: RouteRecommendation | None = None,
        settings: RoutingSettings | None = None,
        repository_ids: list[str] | None = None,
        agent_override: str | None = None,
    ) -> ExecutionPlan:
        cfg = settings or self.get_settings(workspace)
        rec = recommendation or self.recommend_route(prompt, workspace=workspace, settings=cfg)
        findings: list[dict[str, Any]] = []
        if self.history is not None:
            findings = self.history.list_findings(
                workspace=workspace, task_type=rec.task_type, limit=40
            )
        context = build_minimal_context_preview(
            prompt=prompt,
            classification=rec.classification,
            recommendation=rec,
            repository_ids=repository_ids,
            agent_override=agent_override,
            candidate_findings=findings,
        )
        steps = [
            "Classify prompt (task type, complexity, risk, scope).",
            f"Recommend {rec.recommended_label} ({rec.recommended_tier}).",
            f"Estimated usage: {rec.estimated_usage}.",
        ]
        if rec.history_influenced:
            steps.append("Apply performance history bias (capability rules remain authoritative).")
        if rec.escalation_reason:
            steps.append(f"Escalation: {rec.escalation_reason}")
        steps.append("Build minimal context (tools/files + matching prior findings only).")
        steps.append("Check provider availability.")
        if rec.approval_required:
            steps.append("Require explicit approval before Codex/advanced provider.")
        steps.append("Execute route (T0 tools or selected adapter).")
        steps.append("Record metrics; allow cancel; block identical blind retries.")
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
        )

    def execute_route(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        agent_override: str | None = None,
        repository_ids: list[str] | None = None,
        approve_codex: bool = False,
        force: bool = False,
        recommendation: RouteRecommendation | None = None,
        attempt: int = 0,
        previous_partial: str = "",
    ) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("RouteExecutor requires AgentCenterService")
        cfg = self.get_settings(workspace)
        rec = recommendation or self.recommend_route(prompt, workspace=workspace, settings=cfg)
        plan = self.build_execution_plan(
            prompt,
            workspace=workspace,
            recommendation=rec,
            settings=cfg,
            repository_ids=repository_ids,
            agent_override=agent_override,
        )
        findings: list[dict[str, Any]] = []
        if self.history is not None:
            findings = self.history.list_findings(
                workspace=workspace, task_type=rec.task_type, limit=40
            )
        result = self.executor.execute(
            prompt=prompt,
            recommendation=rec,
            settings=cfg,
            agent_override=agent_override,
            repository_ids=repository_ids,
            approve_codex=approve_codex,
            force=force,
            workspace=workspace,
            attempt=attempt,
            candidate_findings=findings,
            previous_partial=previous_partial,
        )
        # Mark findings that were packed as used.
        if self.history is not None:
            for finding in (result.get("context") or {}).get("prior_findings") or []:
                fid = finding.get("id")
                if fid:
                    try:
                        self.history.mark_finding_hit(str(fid))
                    except Exception:  # noqa: BLE001
                        pass
        return {
            "ok": True,
            "phase": 3,
            "recommendation": rec.public(),
            "plan": plan.public(),
            "execution": result,
        }

    def cancel_execution(self, execution_id: str) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("RouteExecutor requires AgentCenterService")
        return self.executor.cancel(execution_id)

    def execution_status(self, execution_id: str) -> dict[str, Any] | None:
        if self.executor is None:
            return None
        return self.executor.refresh(execution_id)
