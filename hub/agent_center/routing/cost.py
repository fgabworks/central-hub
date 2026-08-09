"""Cost intelligence for AiriX Smart Routing Phase 5.

Token budgets remain authoritative. Monetary figures are optional estimates
from configured public rates (USD / 1M tokens) — never provider secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hub.agent_center.routing.budget import USAGE_BAND_TOKENS, band_to_tokens, event_tokens
from hub.agent_center.routing.models import RoutingSettings

# Rough tokens assumed avoided when a successful T0 run replaces a Low-cost LLM call.
T0_AVOIDED_TOKEN_BAND = "Low"


def parse_usage(usage: Any) -> dict[str, Any]:
    """Normalize provider usage dict → input/output/total + source."""
    if not isinstance(usage, dict) or not usage:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "usage_source": "estimate",
            "raw_keys": [],
        }
    inp = _int_or_none(
        usage.get("input_tokens")
        if usage.get("input_tokens") is not None
        else usage.get("prompt_tokens")
    )
    out = _int_or_none(
        usage.get("output_tokens")
        if usage.get("output_tokens") is not None
        else usage.get("completion_tokens")
    )
    total = _int_or_none(
        usage.get("total_tokens")
        if usage.get("total_tokens") is not None
        else usage.get("total")
    )
    if total is None and (inp is not None or out is not None):
        total = int(inp or 0) + int(out or 0)
    if total is None and out is not None:
        total = out
    source = "actual" if total is not None else "estimate"
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
        "usage_source": source,
        "raw_keys": sorted(str(k) for k in usage.keys())[:12],
    }


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def price_per_mtok(settings: RoutingSettings, provider_id: str) -> float:
    rates = dict(settings.price_per_mtok or {})
    pid = (provider_id or "").strip().lower()
    if pid in rates:
        try:
            return max(0.0, float(rates[pid]))
        except (TypeError, ValueError):
            return 0.0
    if "default" in rates:
        try:
            return max(0.0, float(rates["default"]))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def estimate_cost_usd(
    tokens: int | None,
    *,
    provider_id: str,
    settings: RoutingSettings,
) -> float | None:
    if not settings.enable_cost_estimates:
        return None
    if tokens is None:
        return None
    rate = price_per_mtok(settings, provider_id)
    if rate <= 0:
        return None
    return round((max(0, int(tokens)) / 1_000_000.0) * rate, 6)


def usage_variance(estimated_tokens: int, actual_tokens: int | None) -> dict[str, Any]:
    est = max(0, int(estimated_tokens or 0))
    if actual_tokens is None:
        return {
            "estimated_tokens": est,
            "actual_tokens": None,
            "delta_tokens": None,
            "variance_ratio": None,
            "source": "estimate_only",
        }
    act = max(0, int(actual_tokens))
    delta = act - est
    ratio = None if est <= 0 else round(delta / est, 3)
    return {
        "estimated_tokens": est,
        "actual_tokens": act,
        "delta_tokens": delta,
        "variance_ratio": ratio,
        "source": "actual",
    }


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_period(events: list[dict[str, Any]], *, since: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        created = _parse_iso(str(ev.get("created_at") or ""))
        if created is None or created < since:
            continue
        out.append(ev)
    return out


def cost_intelligence(
    events: list[dict[str, Any]],
    settings: RoutingSettings,
    *,
    budget: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dashboard metrics: periods, providers, savings, variance."""
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = _in_period(events, since=day_start)
    month = _in_period(events, since=month_start)

    def _period_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        tokens_actual = 0
        tokens_estimated = 0
        cost_actual = 0.0
        cost_estimated = 0.0
        cost_actual_n = 0
        cost_est_n = 0
        by_provider: dict[str, dict[str, Any]] = {}
        task_tokens: list[int] = []
        for ev in rows:
            pid = str(ev.get("provider_id") or "unknown")
            bucket = by_provider.setdefault(
                pid,
                {"runs": 0, "tokens": 0, "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0},
            )
            bucket["runs"] += 1
            toks = event_tokens(ev)
            task_tokens.append(toks)
            tokens_actual += int(ev["actual_tokens"]) if ev.get("actual_tokens") is not None else 0
            est = ev.get("estimated_tokens")
            if est is None:
                est = band_to_tokens(str(ev.get("estimated_usage") or ""))
            tokens_estimated += int(est or 0)
            bucket["tokens"] += toks

            ac = ev.get("actual_cost_usd")
            if ac is None:
                ac = estimate_cost_usd(ev.get("actual_tokens"), provider_id=pid, settings=settings)
            if ac is not None:
                cost_actual += float(ac)
                cost_actual_n += 1
                bucket["actual_cost_usd"] += float(ac)

            ec = ev.get("estimated_cost_usd")
            if ec is None:
                ec = estimate_cost_usd(int(est or 0), provider_id=pid, settings=settings)
            if ec is not None:
                cost_estimated += float(ec)
                cost_est_n += 1
                bucket["estimated_cost_usd"] += float(ec)

        avg = int(sum(task_tokens) / len(task_tokens)) if task_tokens else None
        return {
            "runs": len(rows),
            "tokens_actual": tokens_actual,
            "tokens_estimated": tokens_estimated,
            "tokens_authoritative": sum(event_tokens(e) for e in rows),
            "avg_tokens_per_task": avg,
            "estimated_cost_usd": round(cost_estimated, 6) if cost_est_n else None,
            "actual_cost_usd": round(cost_actual, 6) if cost_actual_n else None,
            "by_provider": {
                k: {
                    **v,
                    "estimated_cost_usd": round(v["estimated_cost_usd"], 6),
                    "actual_cost_usd": round(v["actual_cost_usd"], 6),
                }
                for k, v in by_provider.items()
            },
        }

    t0_avoided = sum(1 for e in events if e.get("t0_llm_avoided"))
    avoided_tokens = t0_avoided * band_to_tokens(T0_AVOIDED_TOKEN_BAND)
    avoided_cost = estimate_cost_usd(avoided_tokens, provider_id="grok", settings=settings)

    # Variance across events that have both estimate and actual.
    deltas: list[int] = []
    for ev in events:
        if ev.get("actual_tokens") is None:
            continue
        est = ev.get("estimated_tokens")
        if est is None:
            est = band_to_tokens(str(ev.get("estimated_usage") or ""))
        deltas.append(int(ev["actual_tokens"]) - int(est or 0))
    avg_delta = int(sum(deltas) / len(deltas)) if deltas else None

    budget = budget or {}
    return {
        "today": _period_stats(today),
        "month": _period_stats(month),
        "all": _period_stats(events),
        "budget_remaining": {
            "daily": budget.get("daily_remaining"),
            "monthly": budget.get("monthly_remaining"),
            "per_task_max_tokens": budget.get("per_task_max_tokens"),
        },
        "t0_savings": {
            "runs_avoided": t0_avoided,
            "estimated_tokens_avoided": avoided_tokens,
            "estimated_cost_usd_avoided": avoided_cost,
            "band_assumed": T0_AVOIDED_TOKEN_BAND,
            "band_tokens": USAGE_BAND_TOKENS.get(T0_AVOIDED_TOKEN_BAND, 800),
        },
        "estimated_vs_actual": {
            "sample_size": len(deltas),
            "avg_delta_tokens": avg_delta,
            "note": "Positive delta means actual exceeded estimate",
        },
        "pricing_configured": bool(
            settings.enable_cost_estimates and any(float(v or 0) > 0 for v in (settings.price_per_mtok or {}).values())
        ),
        "token_budgets_authoritative": True,
    }
