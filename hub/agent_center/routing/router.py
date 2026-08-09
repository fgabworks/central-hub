"""Route recommender — cheapest capable tier likely to succeed (Phase 1)."""

from __future__ import annotations

from hub.agent_center.routing.models import (
    PromptClassification,
    RouteRecommendation,
    RoutingSettings,
)
from hub.agent_center.routing.providers import ProviderRegistry


def estimate_usage_band(classification: PromptClassification, tier: str) -> str:
    score = classification.complexity
    if tier == "T3":
        score += 20
    elif tier == "T2":
        score += 8
    elif tier == "T0":
        score -= 10
    if classification.context_size == "large":
        score += 15
    elif classification.context_size == "medium":
        score += 5
    if classification.estimated_scope_files >= 10:
        score += 10
    if score < 20:
        return "Very Low"
    if score < 40:
        return "Low"
    if score < 70:
        return "Moderate"
    return "High"


def _pick_available(
    registry: ProviderRegistry,
    preferred_ids: list[str],
    *,
    available_ids: set[str] | None,
) -> str | None:
    avail = available_ids
    for pid in preferred_ids:
        spec = registry.get(pid)
        if spec is None:
            continue
        if avail is None or pid in avail or (spec.adapter_id or pid) in avail:
            return pid
        # Deterministic is always selectable in Phase 1.
        if pid == "deterministic":
            return pid
    return None


def recommend_route(
    classification: PromptClassification,
    *,
    settings: RoutingSettings | None = None,
    registry: ProviderRegistry | None = None,
    available_provider_ids: set[str] | None = None,
) -> RouteRecommendation:
    """
    Choose cheapest capable option. Never auto-prefer Codex.

    available_provider_ids: optional live adapter ids; None = ignore live status
    (still recommend by capability for Phase 1 offline tests).
    """
    settings = settings or RoutingSettings()
    registry = registry or ProviderRegistry()
    c = classification
    considered: list[str] = []

    # Target tier from classification.
    if c.deterministic_capable and settings.prefer_deterministic:
        target = "T0"
        reason = "Simple lookup can be answered with free Hub tools."
    elif c.needs_architecture or c.complexity >= 75 or (
        c.task_type in {"architecture", "refactor"} and c.estimated_scope_files >= 8
    ):
        target = "T3"
        reason = "Large/cross-module or architecture work needs an advanced agent."
    elif c.task_type in {"sql_investigation", "dhis2_investigation"} or (
        c.needs_coding and c.complexity >= 40
    ):
        target = "T2"
        reason = "Investigation/coding fits Grok as the routine capable agent."
    elif c.task_type == "css_ui" or c.complexity < 35:
        target = "T1"
        reason = "Small UI/simple change fits a low-cost agent."
    else:
        target = "T2"
        reason = "Default routine coding/investigation on Grok."

    # Preference knobs.
    if (
        settings.prefer_grok_for_routine
        and target in {"T1", "T2"}
        and c.task_type not in {"lookup"}
        and c.complexity < 75
    ):
        if target == "T1" and c.task_type in {"coding", "css_ui", "general", "testing"}:
            # Keep T1 for trivial CSS unless mode says best quality.
            if settings.mode == "best_quality":
                target = "T2"
                reason = "Best-quality mode elevates routine work to Grok."
        elif target == "T1" and settings.mode == "balanced" and c.complexity >= 28:
            target = "T2"
            reason = "Prefer Grok for routine non-trivial work."

    if settings.mode == "cheapest":
        if c.deterministic_capable:
            target = "T0"
            reason = "Cheapest mode: deterministic tools first."
        elif target == "T3" and settings.allow_escalation:
            # Still allow T3 when clearly needed; otherwise step down one tier.
            pass
        elif target == "T2" and c.complexity < 50:
            target = "T1"
            reason = "Cheapest mode: try low-cost agent before Grok."
        elif target == "T3" and c.complexity < 80 and not c.needs_architecture:
            target = "T2"
            reason = "Cheapest mode: avoid Codex unless clearly required."
    elif settings.mode == "best_quality":
        if target in {"T0", "T1"} and not c.deterministic_capable:
            target = "T2"
            reason = "Best-quality mode prefers Grok over low-cost agents."
        if c.complexity >= 60 or c.needs_architecture:
            target = "T3"
            reason = "Best-quality mode selects Codex for hard work."
    elif settings.mode == "max_speed":
        if c.deterministic_capable:
            target = "T0"
            reason = "Maximum speed: free tools first."
        elif target == "T3":
            target = "T2"
            reason = "Maximum speed: prefer Grok over slower advanced agents."
        elif target == "T2" and c.complexity < 35:
            target = "T1"
            reason = "Maximum speed: low-cost agent for small tasks."

    # Map tiers → provider preference lists (never put Codex first for T0–T2).
    tier_prefs = {
        "T0": ["deterministic"],
        "T1": ["low-cost", "openai-api", "grok"],
        "T2": ["grok", "low-cost", "openai-api"],
        "T3": ["codex", "claude-code", "cursor-agent", "grok"],
    }
    preferred = list(tier_prefs.get(target, ["grok"]))
    considered.extend(preferred)

    chosen = _pick_available(registry, preferred, available_ids=available_provider_ids)
    if chosen is None and settings.allow_escalation:
        # Escalate upward only when needed; never leap to Codex for T0/T1 without need.
        escalate_order = {
            "T0": ["low-cost", "openai-api", "grok"],
            "T1": ["grok"],
            "T2": ["codex", "claude-code"],
            "T3": ["grok", "low-cost"],
        }
        for pid in escalate_order.get(target, []):
            considered.append(pid)
            chosen = _pick_available(registry, [pid], available_ids=available_provider_ids)
            if chosen:
                spec = registry.get(chosen)
                if spec and spec.tier == "T3" and target != "T3":
                    # Only accept Codex escalation when allow_escalation and complexity warrants.
                    if c.complexity < 70 and not c.needs_architecture:
                        chosen = None
                        continue
                reason = f"{reason} Escalated after preferred providers were unavailable."
                break

    if chosen is None:
        chosen = "deterministic" if c.deterministic_capable else "grok"
        reason = f"{reason} Fell back to {chosen}."

    spec = registry.get(chosen) or registry.get("grok")
    assert spec is not None
    tier = spec.tier

    # Alternative: next-best different provider.
    alt_prefs: list[str] = []
    if tier == "T0":
        alt_prefs = ["low-cost", "grok"]
    elif tier == "T1":
        alt_prefs = ["grok", "deterministic"] if c.deterministic_capable else ["grok"]
    elif tier == "T2":
        alt_prefs = ["codex", "low-cost"] if (c.complexity >= 55 or c.needs_architecture) else ["low-cost", "openai-api"]
    else:
        alt_prefs = ["grok", "claude-code"]

    alternative = None
    for pid in alt_prefs:
        if pid == chosen:
            continue
        hit = _pick_available(registry, [pid], available_ids=available_provider_ids)
        if hit and hit != chosen:
            alternative = hit
            break

    alt_spec = registry.get(alternative) if alternative else None

    approval = bool(spec.requires_approval)
    if chosen == "codex" and settings.require_approval_before_codex:
        approval = True

    confidence = 0.55
    if c.task_type in {"lookup", "css_ui", "architecture", "dhis2_investigation", "sql_investigation"}:
        confidence += 0.2
    if c.signals:
        confidence += min(0.15, 0.03 * len(c.signals))
    if available_provider_ids is not None and chosen in available_provider_ids:
        confidence += 0.05
    confidence = max(0.35, min(0.95, confidence))

    usage = estimate_usage_band(c, tier)

    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent=chosen,
        recommended_label=spec.label,
        recommended_tier=tier,
        alternative_agent=alternative,
        alternative_label=alt_spec.label if alt_spec else None,
        confidence=confidence,
        reason=reason.strip(),
        estimated_usage=usage,
        approval_required=approval,
        classification=c,
        providers_considered=list(dict.fromkeys(considered)),
    )
