"""Persist AiriX Smart Routing settings (notebook prefs JSON)."""

from __future__ import annotations

import json
from typing import Any

from hub.agent_center.routing.models import ROUTING_MODES, RoutingSettings
from hub.notebook.models import normalize_workspace
from hub.notebook.workspace import get_pref, set_pref

PREF_PREFIX = "airix_routing"


def _key(workspace: str) -> str:
    return f"{PREF_PREFIX}:{normalize_workspace(workspace)}"


def default_settings() -> RoutingSettings:
    return RoutingSettings()


def _int(data: dict[str, Any], key: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def load_routing_settings(db: Any, workspace: str = "work") -> RoutingSettings:
    raw = get_pref(db, _key(workspace), "")
    base = default_settings()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return base
    if not isinstance(data, dict):
        return base
    mode = str(data.get("mode") or base.mode).strip().lower()
    if mode not in ROUTING_MODES:
        mode = base.mode
    return RoutingSettings(
        mode=mode,
        prefer_deterministic=bool(data.get("prefer_deterministic", base.prefer_deterministic)),
        prefer_grok_for_routine=bool(
            data.get("prefer_grok_for_routine", base.prefer_grok_for_routine)
        ),
        require_approval_before_codex=bool(
            data.get("require_approval_before_codex", base.require_approval_before_codex)
        ),
        allow_escalation=bool(data.get("allow_escalation", base.allow_escalation)),
        max_retries=_int(data, "max_retries", base.max_retries, lo=0, hi=5),
        use_history=bool(data.get("use_history", base.use_history)),
        enable_orchestration=bool(data.get("enable_orchestration", base.enable_orchestration)),
        max_orchestration_steps=_int(
            data, "max_orchestration_steps", base.max_orchestration_steps, lo=1, hi=8
        ),
        daily_token_budget=_int(
            data, "daily_token_budget", base.daily_token_budget, lo=0, hi=50_000_000
        ),
        monthly_token_budget=_int(
            data, "monthly_token_budget", base.monthly_token_budget, lo=0, hi=500_000_000
        ),
        per_task_max_tokens=_int(
            data, "per_task_max_tokens", base.per_task_max_tokens, lo=0, hi=5_000_000
        ),
        warn_before_expensive_escalation=bool(
            data.get("warn_before_expensive_escalation", base.warn_before_expensive_escalation)
        ),
        enable_cost_estimates=bool(
            data.get("enable_cost_estimates", base.enable_cost_estimates)
        ),
        price_per_mtok=_parse_price_map(data.get("price_per_mtok")),
    )


def _parse_price_map(raw: Any) -> dict[str, float]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in raw.items():
        try:
            out[str(key).strip().lower()] = max(0.0, float(val))
        except (TypeError, ValueError):
            continue
    return out


def save_routing_settings(
    db: Any,
    workspace: str,
    payload: dict[str, Any] | None,
) -> RoutingSettings:
    current = load_routing_settings(db, workspace)
    data = payload if isinstance(payload, dict) else {}
    merged = current.public()
    for key in merged:
        if key in data:
            merged[key] = data[key]
    set_pref(db, _key(workspace), json.dumps(merged))
    normalized = load_routing_settings(db, workspace)
    set_pref(db, _key(workspace), json.dumps(normalized.public()))
    return normalized
