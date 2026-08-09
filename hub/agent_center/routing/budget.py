"""AI usage budget controls for AiriX Smart Routing Phase 4."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.service import AgentCenterError

USAGE_BAND_TOKENS = {
    "Very Low": 200,
    "Low": 800,
    "Moderate": 2500,
    "High": 8000,
}

EXPENSIVE_TIERS = frozenset({"T3"})
EXPENSIVE_PROVIDERS = frozenset({"codex", "claude-code", "cursor-agent"})


def band_to_tokens(band: str | None) -> int:
    return int(USAGE_BAND_TOKENS.get(str(band or ""), 1000))


def event_tokens(event: dict[str, Any]) -> int:
    actual = event.get("actual_tokens")
    if actual is not None:
        try:
            return max(0, int(actual))
        except (TypeError, ValueError):
            pass
    return band_to_tokens(str(event.get("estimated_usage") or ""))


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def sum_tokens_in_period(
    events: list[dict[str, Any]],
    *,
    since: datetime,
) -> int:
    total = 0
    for ev in events:
        created = _parse_iso(str(ev.get("created_at") or ""))
        if created is None or created < since:
            continue
        total += event_tokens(ev)
    return total


def budget_snapshot(
    events: list[dict[str, Any]],
    settings: RoutingSettings,
    *,
    now: datetime | None = None,
    task_estimated_tokens: int = 0,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    daily_used = sum_tokens_in_period(events, since=day_start)
    monthly_used = sum_tokens_in_period(events, since=month_start)
    daily_limit = int(settings.daily_token_budget or 0)
    monthly_limit = int(settings.monthly_token_budget or 0)
    per_task = int(settings.per_task_max_tokens or 0)
    return {
        "daily_used": daily_used,
        "monthly_used": monthly_used,
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
        "per_task_max_tokens": per_task,
        "task_estimated_tokens": max(0, int(task_estimated_tokens)),
        "daily_remaining": None if daily_limit <= 0 else max(0, daily_limit - daily_used),
        "monthly_remaining": None if monthly_limit <= 0 else max(0, monthly_limit - monthly_used),
        "unlimited": daily_limit <= 0 and monthly_limit <= 0 and per_task <= 0,
    }


def check_budget_allows(
    snapshot: dict[str, Any],
    *,
    additional_tokens: int = 0,
) -> tuple[bool, str]:
    add = max(0, int(additional_tokens))
    daily_limit = int(snapshot.get("daily_limit") or 0)
    monthly_limit = int(snapshot.get("monthly_limit") or 0)
    per_task = int(snapshot.get("per_task_max_tokens") or 0)
    daily_used = int(snapshot.get("daily_used") or 0)
    monthly_used = int(snapshot.get("monthly_used") or 0)
    task_est = int(snapshot.get("task_estimated_tokens") or 0) + add

    if per_task > 0 and task_est > per_task:
        return False, f"Per-task budget exceeded ({task_est} > {per_task} tokens)"
    if daily_limit > 0 and daily_used + add > daily_limit:
        return False, f"Daily AI budget exceeded ({daily_used + add} > {daily_limit} tokens)"
    if monthly_limit > 0 and monthly_used + add > monthly_limit:
        return False, f"Monthly AI budget exceeded ({monthly_used + add} > {monthly_limit} tokens)"
    return True, ""


def assert_budget_allows(snapshot: dict[str, Any], *, additional_tokens: int = 0) -> None:
    ok, reason = check_budget_allows(snapshot, additional_tokens=additional_tokens)
    if not ok:
        raise AgentCenterError(reason, code="budget_exceeded")


def expensive_escalation_warning(
    *,
    provider_id: str,
    tier: str,
    settings: RoutingSettings,
    estimated_usage: str,
) -> str | None:
    if not settings.warn_before_expensive_escalation:
        return None
    if provider_id in EXPENSIVE_PROVIDERS or tier in EXPENSIVE_TIERS:
        tokens = band_to_tokens(estimated_usage)
        return (
            f"Expensive escalation to {provider_id} (~{tokens} tokens estimated). "
            "Codex/advanced agents still require explicit approval."
        )
    return None
