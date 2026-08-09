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
    try:
        max_retries = int(data.get("max_retries", base.max_retries))
    except (TypeError, ValueError):
        max_retries = base.max_retries
    max_retries = max(0, min(5, max_retries))
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
        max_retries=max_retries,
        use_history=bool(data.get("use_history", base.use_history)),
    )


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
    # Re-validate through loader path.
    set_pref(db, _key(workspace), json.dumps(merged))
    # Write normalized values.
    normalized = load_routing_settings(db, workspace)
    set_pref(db, _key(workspace), json.dumps(normalized.public()))
    return normalized
