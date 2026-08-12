"""Route recommender — capability rules authoritative; history may bias (Phase 3)."""

from __future__ import annotations

from typing import Any

from hub.agent_center.routing.history import MIN_HISTORY_SAMPLES
from hub.agent_center.routing.models import (
    PromptClassification,
    RouteExplanation,
    RouteRecommendation,
    RoutingSettings,
)
from hub.agent_center.routing.providers import ProviderRegistry

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
_ESCALATE_NEXT = {
    "deterministic": "low-cost",
    "low-cost": "grok",
    "openai-api": "grok",
    "grok": "codex",
    "codex": "claude-code",
    "claude-code": "cursor-agent",
}


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
        if pid == "deterministic":
            return pid
    return None


def _success_rate(stats_by_provider: dict[str, dict[str, Any]], provider_id: str) -> tuple[float | None, int]:
    row = stats_by_provider.get(provider_id) or {}
    samples = int(row.get("samples") or 0)
    rate = row.get("success_rate")
    if samples < MIN_HISTORY_SAMPLES:
        return None, samples
    if rate is None:
        return None, samples
    return float(rate), samples


def _reorder_by_history(
    preferred: list[str],
    *,
    stats_by_provider: dict[str, dict[str, Any]],
    use_history: bool,
) -> tuple[list[str], bool, str | None]:
    """Demote chronically failing providers within the same preference list."""
    if not use_history or not stats_by_provider:
        return preferred, False, None
    scored: list[tuple[float, str]] = []
    influenced = False
    note = None
    for pid in preferred:
        rate, samples = _success_rate(stats_by_provider, pid)
        # Higher score = preferred. Unknown history keeps mid score by list order.
        if rate is None:
            score = 0.55 - (0.01 * len(scored))
        else:
            score = rate
            if rate < 0.4 and samples >= MIN_HISTORY_SAMPLES:
                score -= 0.35
                influenced = True
                note = f"History demoted {pid} (success rate {rate:.0%} over {samples} runs)."
            elif rate >= 0.75 and samples >= MIN_HISTORY_SAMPLES:
                score += 0.08
                influenced = True
        scored.append((score, pid))
    scored.sort(key=lambda x: -x[0])
    return [p for _s, p in scored], influenced, note


def recommend_route(
    classification: PromptClassification,
    *,
    settings: RoutingSettings | None = None,
    registry: ProviderRegistry | None = None,
    available_provider_ids: set[str] | None = None,
    provider_stats: list[dict[str, Any]] | None = None,
    recent_failures: list[dict[str, Any]] | None = None,
) -> RouteRecommendation:
    """
    Choose cheapest capable option. Capability/risk rules stay authoritative.
    History may reorder within-tier preferences or escalate after repeated failure.
    Never auto-prefer Codex; Codex escalation always sets approval_required.
    """
    settings = settings or RoutingSettings()
    registry = registry or ProviderRegistry()
    c = classification
    considered: list[str] = []
    history_influenced = False
    escalation_reason: str | None = None
    base_target_reason = ""

    # Target tier from classification (authoritative).
    signals = set(c.signals or [])
    simple_gk = "simple_general_knowledge" in signals
    authoritative_data = (
        "authoritative_data_query" in signals
        or "data_query" in signals
        or "structured_data_lookup" in signals
    )
    if c.deterministic_capable and settings.prefer_deterministic:
        target = "T0"
        reason = (
            "Structured data/DHIS2 lookup — Hub tools first (never Hub Simulator)."
            if authoritative_data
            else "Simple lookup can be answered with free Hub tools."
        )
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
    elif authoritative_data:
        # Safety: never treat structured data as cheap simulator GK.
        target = "T0" if c.deterministic_capable else "T2"
        reason = (
            "Authoritative data question — deterministic tools first; escalate to real AI only if needed."
            if c.deterministic_capable
            else "Authoritative data question — prefer capable AI over Hub Simulator."
        )
    elif simple_gk or (
        c.task_type == "general" and c.complexity < 30 and not c.needs_coding
    ):
        target = "T1"
        reason = "Simple general-knowledge question fits the lowest-tier available model."
    elif c.task_type == "css_ui" or c.complexity < 35:
        target = "T1"
        reason = "Small UI/simple change fits a low-cost agent."
    else:
        target = "T2"
        reason = "Default routine coding/investigation on Grok."
    base_target_reason = reason

    # Preference knobs.
    if (
        settings.prefer_grok_for_routine
        and target in {"T1", "T2"}
        and c.task_type not in {"lookup"}
        and not simple_gk
        and c.complexity < 75
    ):
        if target == "T1" and c.task_type in {"coding", "css_ui", "general", "testing"}:
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
        elif authoritative_data:
            # Still never Hub Simulator for authoritative counts/indicators.
            target = "T2"
            reason = "Cheapest mode: authoritative data uses capable AI, not Hub Simulator."
        elif simple_gk:
            target = "T1"
            reason = "Cheapest mode: lowest-tier model for general knowledge."
        elif target == "T3" and settings.allow_escalation:
            pass
        elif target == "T2" and c.complexity < 50:
            target = "T1"
            reason = "Cheapest mode: try low-cost agent before Grok."
        elif target == "T3" and c.complexity < 80 and not c.needs_architecture:
            target = "T2"
            reason = "Cheapest mode: avoid Codex unless clearly required."
    elif settings.mode == "best_quality":
        if target in {"T0", "T1"} and not c.deterministic_capable and not simple_gk:
            target = "T2"
            reason = "Best-quality mode prefers Grok over low-cost agents."
        if c.complexity >= 60 or c.needs_architecture:
            target = "T3"
            reason = "Best-quality mode selects Codex for hard work."
    elif settings.mode == "max_speed":
        if c.deterministic_capable:
            target = "T0"
            reason = "Maximum speed: free tools first."
        elif authoritative_data:
            target = "T2"
            reason = "Maximum speed: authoritative data skips Hub Simulator."
        elif simple_gk:
            target = "T1"
            reason = "Maximum speed: lowest-tier model for simple general knowledge."
        elif target == "T3":
            target = "T2"
            reason = "Maximum speed: prefer Grok over slower advanced agents."
        elif target == "T2" and c.complexity < 35:
            target = "T1"
            reason = "Maximum speed: low-cost agent for small tasks."

    # Never route pure general knowledge to Codex / coding CLIs.
    if simple_gk and target == "T3":
        target = "T1"
        reason = "Simple general knowledge must not use Codex; using lowest-tier model."
    # Never recommend Hub Simulator (low-cost) for authoritative structured data.
    if authoritative_data and target == "T1":
        target = "T0" if c.deterministic_capable else "T2"
        reason = f"{reason} Authoritative data must not use Hub Simulator.".strip()
    # Repeated failures for this prompt fingerprint → recommend stronger route.
    failures = list(recent_failures or [])
    expected_retries = 0
    if settings.use_history and settings.allow_escalation and failures:
        fail_count = len(failures)
        expected_retries = min(settings.max_retries, max(0, fail_count))
        last_provider = str(failures[0].get("provider_id") or "")
        if fail_count >= max(1, settings.max_retries) or (
            fail_count >= 2 and last_provider
        ):
            stronger = _ESCALATE_NEXT.get(last_provider)
            if stronger:
                strong_spec = registry.get(stronger)
                if strong_spec is not None:
                    # Never escalate below capability floor; only upward.
                    if _TIER_RANK.get(strong_spec.tier, 0) > _TIER_RANK.get(target, 0) or fail_count >= 2:
                        # Capability guard: don't jump to T3 unless complexity/risk warrant OR repeated failure.
                        if strong_spec.tier == "T3" and not (
                            c.needs_architecture or c.complexity >= 55 or fail_count >= settings.max_retries
                        ):
                            stronger = "grok"
                            strong_spec = registry.get("grok")
                        if strong_spec is not None:
                            target = strong_spec.tier
                            escalation_reason = (
                                f"Escalating after {fail_count} failure(s) on {last_provider} "
                                f"→ recommend {stronger}."
                            )
                            reason = f"{base_target_reason} {escalation_reason}"
                            history_influenced = True
                            # Force preferred list to start with stronger.
                            preferred_override = [stronger]
                        else:
                            preferred_override = None
                    else:
                        preferred_override = None
                else:
                    preferred_override = None
            else:
                preferred_override = None
        else:
            preferred_override = None
    else:
        preferred_override = None

    tier_prefs = {
        "T0": ["deterministic"],
        "T1": ["low-cost", "openai-api", "grok"],
        "T2": ["grok", "low-cost", "openai-api"],
        "T3": ["codex", "claude-code", "cursor-agent", "grok"],
    }
    if authoritative_data:
        # Prefer real providers over Hub Simulator for structured/project data.
        tier_prefs = {
            **tier_prefs,
            "T0": ["deterministic"],
            "T1": ["openai-api", "grok"],
            "T2": ["grok", "openai-api"],
        }
    preferred = list(preferred_override or tier_prefs.get(target, ["grok"]))
    if authoritative_data:
        preferred = [p for p in preferred if p not in {"low-cost", "hub-simulator"}] or [
            "deterministic" if c.deterministic_capable else "grok"
        ]
    stats_map = {
        str(s.get("provider_id")): s
        for s in (provider_stats or [])
        if s.get("provider_id")
    }
    preferred, hist_reorder, hist_note = _reorder_by_history(
        preferred,
        stats_by_provider=stats_map,
        use_history=bool(settings.use_history),
    )
    if hist_reorder:
        history_influenced = True
        if hist_note:
            reason = f"{reason} {hist_note}".strip()
    considered.extend(preferred)

    chosen = _pick_available(registry, preferred, available_ids=available_provider_ids)
    if chosen is None and settings.allow_escalation:
        escalate_order = {
            "T0": (
                ["openai-api", "grok"]
                if authoritative_data
                else ["low-cost", "openai-api", "grok"]
            ),
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
                    if c.complexity < 70 and not c.needs_architecture and not escalation_reason:
                        chosen = None
                        continue
                reason = f"{reason} Escalated after preferred providers were unavailable."
                if spec and spec.tier == "T3":
                    escalation_reason = escalation_reason or "Preferred providers unavailable; Codex needs approval."
                break

    if chosen is None:
        chosen = "deterministic" if c.deterministic_capable else "grok"
        reason = f"{reason} Fell back to {chosen}."

    spec = registry.get(chosen) or registry.get("grok")
    assert spec is not None
    tier = spec.tier

    alt_prefs: list[str] = []
    if tier == "T0":
        alt_prefs = ["openai-api", "grok"] if authoritative_data else ["low-cost", "grok"]
    elif tier == "T1":
        alt_prefs = ["grok", "deterministic"] if c.deterministic_capable else ["grok"]
    elif tier == "T2":
        if authoritative_data:
            alt_prefs = ["openai-api", "codex"] if (
                c.complexity >= 55 or c.needs_architecture or escalation_reason
            ) else ["openai-api"]
        else:
            alt_prefs = (
                ["codex", "low-cost"]
                if (c.complexity >= 55 or c.needs_architecture or escalation_reason)
                else ["low-cost", "openai-api"]
            )
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

    # Approval belongs to the ACTION (tool/write policy), never to provider identity.
    approval = False

    rate, samples = _success_rate(stats_map, chosen)
    confidence = 0.55
    if c.task_type in {"lookup", "css_ui", "architecture", "dhis2_investigation", "sql_investigation"}:
        confidence += 0.2
    if c.signals:
        confidence += min(0.15, 0.03 * len(c.signals))
    if available_provider_ids is not None and chosen in available_provider_ids:
        confidence += 0.05
    if rate is not None:
        confidence = 0.4 * confidence + 0.6 * (0.35 + 0.6 * rate)
        history_influenced = True
    confidence = max(0.35, min(0.95, confidence))

    usage = estimate_usage_band(c, tier)
    # Expected retries: low when history is strong, higher when weak/escalating.
    if rate is not None and rate >= 0.8:
        expected_retries = 0
    elif rate is not None and rate < 0.5:
        expected_retries = min(settings.max_retries, max(expected_retries, 1))
    elif escalation_reason:
        expected_retries = min(settings.max_retries, max(expected_retries, 1))

    explanation = RouteExplanation(
        recommended_provider=chosen,
        historical_success_rate=rate,
        sample_size=samples,
        expected_retries=expected_retries,
        estimated_usage=usage,
        escalation_reason=escalation_reason,
        history_influenced=history_influenced,
        reason=reason.strip(),
    )

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
        explanation=explanation,
        expected_retries=expected_retries,
        history_influenced=history_influenced,
        escalation_reason=escalation_reason,
    )
