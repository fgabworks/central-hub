"""Persistent VS Code-style assistant dock preferences (per workspace)."""

from __future__ import annotations

import json
from typing import Any

from hub.agent_center.profiles import get_profile, profile_for_workspace
from hub.notebook.models import normalize_workspace
from hub.notebook.workspace import get_pref, set_pref

DEFAULT_WIDTH = 400
MIN_WIDTH = 300
MAX_WIDTH = 560
PREF_PREFIX = "assistant_dock"


def clamp_width(value: Any) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def _pref_key(workspace: str) -> str:
    return f"{PREF_PREFIX}:{normalize_workspace(workspace)}"


def default_dock_state(workspace: str) -> dict[str, Any]:
    ws = normalize_workspace(workspace)
    profile = profile_for_workspace(ws)
    return {
        "workspace": ws,
        "profile_id": profile.id,
        "profile_name": profile.name,
        "profile_title": profile.title,
        "open": False,
        "pinned": True,
        "minimized": False,
        "width": DEFAULT_WIDTH,
        "min_width": MIN_WIDTH,
        "max_width": MAX_WIDTH,
        "selected_repository_id": "",
    }


def load_dock_prefs(db: Any, workspace: str) -> dict[str, Any]:
    state = default_dock_state(workspace)
    raw = get_pref(db, _pref_key(workspace), "")
    if not raw:
        return state
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return state
    if not isinstance(data, dict):
        return state
    if "open" in data:
        state["open"] = bool(data.get("open"))
    if "pinned" in data:
        state["pinned"] = bool(data.get("pinned"))
    if "minimized" in data:
        state["minimized"] = bool(data.get("minimized"))
    if "width" in data:
        state["width"] = clamp_width(data.get("width"))
    if "selected_repository_id" in data:
        state["selected_repository_id"] = str(data.get("selected_repository_id") or "")[:128]
    return state


def save_dock_prefs(db: Any, workspace: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    current = load_dock_prefs(db, workspace)
    data = payload if isinstance(payload, dict) else {}
    if "open" in data:
        current["open"] = bool(data.get("open"))
    if "pinned" in data:
        current["pinned"] = bool(data.get("pinned"))
    if "minimized" in data:
        current["minimized"] = bool(data.get("minimized"))
    if "width" in data:
        current["width"] = clamp_width(data.get("width"))
    if "selected_repository_id" in data:
        current["selected_repository_id"] = str(data.get("selected_repository_id") or "")[:128]
    # Minimized implies closed chrome but host stays mounted; keep open flag for restore.
    set_pref(
        db,
        _pref_key(workspace),
        json.dumps(
            {
                "open": current["open"],
                "pinned": current["pinned"],
                "minimized": current["minimized"],
                "width": current["width"],
                "selected_repository_id": current["selected_repository_id"],
            }
        ),
    )
    return current


def page_aware_suggestions(profile_id: str, endpoint: str | None) -> list[dict[str, str]]:
    """Lightweight suggestion chips — no provider checks."""
    ep = (endpoint or "").strip()
    profile = get_profile(profile_id)
    if profile.id == "aira":
        base = [
            {"id": "personal-notes", "label": "Summarize my recent personal notes"},
            {"id": "personal-tasks", "label": "What personal tasks are open?"},
            {"id": "personal-email", "label": "Search personal email for unread threads"},
            {"id": "personal-calendar", "label": "What is on my personal calendar soon?"},
        ]
        if "email" in ep:
            return [
                {"id": "email-unread", "label": "List recent personal email threads"},
                {"id": "email-search", "label": "Find emails about a keyword"},
            ] + base[:2]
        if "calendar" in ep:
            return [
                {"id": "cal-upcoming", "label": "Show upcoming personal events"},
                {"id": "cal-today", "label": "What is on my calendar today?"},
            ] + base[:2]
        if "notebook" in ep or "tasks" in ep:
            return [
                {"id": "notes-open", "label": "List open personal notes"},
                {"id": "notes-summary", "label": "Summarize the latest notepad content"},
            ] + base[2:]
        return base

    base = [
        {"id": "dhis2-jobs", "label": "Show recent DHIS2 Jobs and statuses"},
        {"id": "audit-week", "label": "Summarize audit logs for last 7 days"},
        {"id": "repo-health", "label": "Check repository health overview"},
        {"id": "sql-safe", "label": "List saved SQL queries (read-only)"},
    ]
    if "dhis2" in ep:
        return [
            {"id": "dhis2-jobs", "label": "Show recent DHIS2 Jobs and statuses"},
            {"id": "dhis2-reports", "label": "List recent DHIS2 report syncs"},
            {"id": "uid-lookup", "label": "Look up a UID in the local index"},
        ]
    if "sql" in ep:
        return [
            {"id": "sql-safe", "label": "List saved SQL queries (read-only)"},
            {"id": "sql-explain", "label": "Explain a saved read-only SQL query"},
        ]
    if "repositor" in ep or "health" in ep:
        return [
            {"id": "repo-health", "label": "Check repository health overview"},
            {"id": "repo-runs", "label": "Summarize recent repository runs"},
        ]
    if "audit" in ep or "job" in ep:
        return [
            {"id": "audit-week", "label": "Summarize audit logs for last 7 days"},
            {"id": "jobs-recent", "label": "List recent hub jobs and statuses"},
        ]
    return base


def dock_shell_bootstrap(
    db: Any,
    *,
    workspace: str,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Minimal bootstrap for every page — no adapter/provider probing."""
    prefs = load_dock_prefs(db, workspace)
    profile = get_profile(prefs["profile_id"])
    return {
        "ok": True,
        "workspace": prefs["workspace"],
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "title": profile.title,
            "tone": profile.tone,
            "default_mode": profile.default_mode,
            "default_tools": list(profile.default_tools),
            "repositories_allowed": profile.repositories_allowed,
        },
        "prefs": {
            "open": prefs["open"],
            "pinned": prefs["pinned"],
            "minimized": prefs["minimized"],
            "width": prefs["width"],
            "min_width": prefs["min_width"],
            "max_width": prefs["max_width"],
            "selected_repository_id": prefs.get("selected_repository_id") or "",
        },
        "suggestions": page_aware_suggestions(profile.id, endpoint),
        "safety": {
            "read_only": True,
            "message": "Read-only mode. No actions are executed.",
            "voice_disabled": True,
        },
        "center_url": "/personal/aira" if profile.id == "aira" else "/work/airix",
        "api_base": f"/api/assistants/{profile.id}",
        "prefs_url": "/api/assistant-dock/prefs",
        "lazy_agents_url": f"/api/assistants/{profile.id}/agents",
        "lazy_repositories_url": f"/api/assistants/{profile.id}/repositories",
        "smart_routing": {
            "enabled": profile.id == "okarun",
            "phase": 5,
            # Canonical AiriX routing APIs (legacy /okarun/... still accepted).
            "recommend_url": "/api/assistants/airix/routing/recommend",
            "execute_url": "/api/assistants/airix/routing/execute",
            "cancel_url": "/api/assistants/airix/routing/cancel",
            "status_url": "/api/assistants/airix/routing/status",
            "settings_url": "/api/assistants/airix/routing/settings",
            "providers_url": "/api/assistants/airix/routing/providers",
            "analytics_url": "/api/assistants/airix/routing/analytics",
            "roles_url": "/api/assistants/airix/routing/roles",
            "permissions_url": "/api/assistants/airix/routing/permissions",
            "execute": True,
        },
    }
