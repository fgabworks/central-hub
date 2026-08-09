"""AiriX Smart Routing service — Phase 1 recommend / plan only."""

from __future__ import annotations

from typing import Any, Callable

from hub.agent_center.routing.classifier import classify_prompt
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


class AgentRouterService:
    """
    Provider-agnostic router used by AiriX before any agent run.

    Phase 1 never starts adapters, Codex, retries, or context dispatch.
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        availability_loader: Callable[[], dict[str, dict[str, Any]]] | None = None,
        db: Any | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self._availability_loader = availability_loader
        self.db = db

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
        if probe and self._availability_loader is not None:
            try:
                availability = self._availability_loader() or {}
            except Exception:  # noqa: BLE001 — Phase 1 must not fail listing
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
        return recommend_route(
            classification,
            settings=cfg,
            registry=self.registry,
            available_provider_ids=available_ids,
        )

    def build_execution_plan(
        self,
        prompt: str,
        *,
        workspace: str = "work",
        recommendation: RouteRecommendation | None = None,
        settings: RoutingSettings | None = None,
    ) -> ExecutionPlan:
        cfg = settings or self.get_settings(workspace)
        rec = recommendation or self.recommend_route(prompt, workspace=workspace, settings=cfg)
        steps = [
            "Classify prompt (task type, complexity, risk, scope).",
            f"Recommend {rec.recommended_label} ({rec.recommended_tier}).",
            "Await user confirmation (Use Recommended / Choose Agent / Cancel).",
        ]
        if rec.approval_required:
            steps.append("Require explicit approval before Codex/advanced provider.")
        steps.append("Phase 2+: dispatch context and execute selected agent (not implemented).")
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
        )
