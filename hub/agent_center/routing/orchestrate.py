"""Multi-step orchestration planner for AiriX Smart Routing Phase 4."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from hub.agent_center.routing.budget import band_to_tokens, expensive_escalation_warning
from hub.agent_center.routing.models import RouteRecommendation, RoutingSettings
from hub.agent_center.routing.roles import RoleProfile, tools_for_role
from hub.agent_center.routing.router import estimate_usage_band


@dataclass
class OrchestrationStep:
    id: str
    kind: str  # tool | agent
    label: str
    provider_id: str
    role_id: str
    tools: list[str] = field(default_factory=list)
    estimated_usage: str = "Very Low"
    estimated_tokens: int = 0
    approval_required: bool = False
    expensive_warning: str | None = None
    status: str = "pending"  # pending|completed|skipped|failed|blocked
    skip_reason: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


def build_orchestration_plan(
    *,
    recommendation: RouteRecommendation,
    role: RoleProfile,
    settings: RoutingSettings,
    resume_completed: list[str] | None = None,
) -> list[OrchestrationStep]:
    """
    Build a small deterministic-first plan:
    tool lookup → repo search → Grok analysis → optional Codex.
    """
    c = recommendation.classification
    completed = set(resume_completed or [])
    max_steps = max(1, min(8, int(settings.max_orchestration_steps or 4)))
    role_tools = tools_for_role(role, c)
    steps: list[OrchestrationStep] = []

    # 1) Deterministic tool lookup
    lookup_tools = [
        t
        for t in role_tools
        if t.endswith("_lookup") or t in {"notebook_lookup", "uid_lookup"}
    ]
    if not lookup_tools and c.deterministic_capable:
        lookup_tools = ["notebook_lookup", "uid_lookup", "jobs_lookup"]
    if lookup_tools or c.deterministic_capable or settings.prefer_deterministic:
        if not lookup_tools:
            lookup_tools = list(role_tools)[:3] or ["notebook_lookup"]
        steps.append(
            OrchestrationStep(
                id="step_tool_lookup",
                kind="tool",
                label="Deterministic tool lookup",
                provider_id="deterministic",
                role_id=role.id,
                tools=lookup_tools[:4],
                estimated_usage="Very Low",
                estimated_tokens=band_to_tokens("Very Low"),
            )
        )

    # 2) Repo search (still T0 tools when possible)
    needs_repo = c.needs_coding or c.task_type in {
        "coding",
        "refactor",
        "architecture",
        "testing",
        "css_ui",
        "sql_investigation",
        "dhis2_investigation",
    } or role.id in {"repository", "ui_playwright"}
    if needs_repo and (not c.deterministic_capable or c.complexity >= 25):
        steps.append(
            OrchestrationStep(
                id="step_repo_search",
                kind="tool",
                label="Repository search / read",
                provider_id="deterministic",
                role_id=role.id,
                tools=["repo_search", "read_file"],
                estimated_usage="Very Low",
                estimated_tokens=band_to_tokens("Very Low"),
            )
        )

    # 3) Grok / low-cost analysis when lookup alone is unlikely enough
    needs_ai = (not c.deterministic_capable) or c.complexity >= 35 or recommendation.recommended_tier in {
        "T1",
        "T2",
        "T3",
    }
    if needs_ai and recommendation.recommended_agent != "deterministic":
        provider = recommendation.recommended_agent
        if provider in {"codex", "claude-code", "cursor-agent"}:
            # Keep Codex as optional final step; use Grok for analysis first.
            provider = "grok"
        elif provider == "deterministic":
            provider = "grok"
        usage = estimate_usage_band(c, "T2" if provider == "grok" else "T1")
        steps.append(
            OrchestrationStep(
                id="step_ai_analysis",
                kind="agent",
                label=f"{provider} analysis",
                provider_id=provider,
                role_id=role.id,
                tools=role_tools[:6],
                estimated_usage=usage,
                estimated_tokens=band_to_tokens(usage),
                approval_required=False,
            )
        )

    # 4) Optional Codex escalation (never autonomous)
    if (
        settings.allow_escalation
        and (
            recommendation.recommended_tier == "T3"
            or recommendation.escalation_reason
            or c.needs_architecture
            or c.complexity >= 70
        )
    ):
        usage = estimate_usage_band(c, "T3")
        warn = expensive_escalation_warning(
            provider_id="codex",
            tier="T3",
            settings=settings,
            estimated_usage=usage,
        )
        steps.append(
            OrchestrationStep(
                id="step_codex_escalation",
                kind="agent",
                label="Codex escalation",
                provider_id="codex",
                role_id=role.id,
                tools=role_tools[:6],
                estimated_usage=usage,
                estimated_tokens=band_to_tokens(usage),
                # Provider identity never requires interactive approval; budget may still block.
                approval_required=False,
                expensive_warning=warn,
            )
        )

    # Cap and mark already-completed resume steps.
    trimmed = steps[:max_steps]
    for step in trimmed:
        if step.id in completed:
            step.status = "skipped"
            step.skip_reason = "Already completed in prior session"
    return trimmed


def plan_estimated_tokens(steps: list[OrchestrationStep]) -> int:
    return sum(int(s.estimated_tokens or 0) for s in steps if s.status == "pending")


def is_task_solved(step_result: dict[str, Any], *, step: OrchestrationStep) -> bool:
    """Stop early only when the completion contract is satisfied (or honest empty cannot-verify)."""
    status = str(step_result.get("status") or "")
    if status != "completed":
        return False
    if step_result.get("t0_fallthrough"):
        return False
    from hub.agent_center.routing.execution import extract_provider_answer

    answer = extract_provider_answer(step_result)
    grounding = step_result.get("grounding") or {}
    if step.kind == "tool" and step.id == "step_tool_lookup":
        # Fully solved + grounded → stop (includes successful T0→AI synthesis).
        if grounding.get("task_solved") and grounding.get("answer_grounded") and answer:
            return True
        # Evidence ≠ completion — continue to next capable route when the plan allows it.
        if grounding.get("evidence_found") and not grounding.get("task_solved"):
            return False
        if step_result.get("t0_unsolved") and not answer:
            return False
        if not answer:
            return False
        # Honest empty cannot-verify (no usable evidence) is a terminal T0 outcome.
        if grounding.get("cannot_verify") and not grounding.get("task_solved"):
            return True
        if "task_solved" in grounding:
            return False
        evidence = step_result.get("evidence_packet") or {}
        # Legacy fallback: only stop when usable evidence AND grounded answer (not discovery).
        if evidence.get("usable") and grounding.get("grounded") and not grounding.get("cannot_verify"):
            return True
        return False
    if not answer:
        return False
    if step.id == "step_ai_analysis" and len(answer) >= 40:
        return True
    return False
