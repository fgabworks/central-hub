"""Personal / Work workspace preference (cookie + hub_prefs)."""

from __future__ import annotations

from typing import Any

from flask import Request, Response

from hub.notebook.db import NotebookDatabase, utcnow
from hub.notebook.models import DEFAULT_WORKSPACE, normalize_workspace

COOKIE_NAME = "hub_workspace"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
PREF_KEY = "workspace"


def get_pref(db: NotebookDatabase, key: str, default: str = "") -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM hub_prefs WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return default
    return str(row["value"] or default)


def set_pref(db: NotebookDatabase, key: str, value: str) -> None:
    now = utcnow()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO hub_prefs (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def read_workspace(
    request: Request,
    db: NotebookDatabase | None = None,
    *,
    default: str = DEFAULT_WORKSPACE,
) -> str:
    """Resolve workspace: cookie → persisted pref → default."""
    cookie = normalize_workspace(request.cookies.get(COOKIE_NAME), default="")
    if cookie:
        return cookie
    if db is not None:
        stored = normalize_workspace(get_pref(db, PREF_KEY, ""), default="")
        if stored:
            return stored
    return normalize_workspace(default)


def persist_workspace(db: NotebookDatabase, workspace: str) -> str:
    value = normalize_workspace(workspace)
    set_pref(db, PREF_KEY, value)
    return value


def apply_workspace_cookie(response: Response, workspace: str) -> Response:
    response.set_cookie(
        COOKIE_NAME,
        normalize_workspace(workspace),
        max_age=COOKIE_MAX_AGE,
        samesite="Lax",
        httponly=False,
        path="/",
    )
    return response


def dashboard_endpoint(workspace: str) -> str:
    """Actual dashboard route for a workspace (not the primary landing)."""
    return (
        "personal_dashboard"
        if normalize_workspace(workspace) == "personal"
        else "work_dashboard"
    )


def primary_endpoint(workspace: str) -> str:
    """VANTA lands on Code Workspace; ARCTIC lands on the personal dashboard."""
    return (
        "personal_dashboard"
        if normalize_workspace(workspace) == "personal"
        else "work_climate"
    )


def chat_endpoint(workspace: str) -> str:
    return (
        "personal_climate_chat"
        if normalize_workspace(workspace) == "personal"
        else "work_climate_chat"
    )


def tasks_endpoint(workspace: str) -> str:
    return (
        "personal_tasks"
        if normalize_workspace(workspace) == "personal"
        else "work_tasks"
    )


def _nav_item(
    endpoint: str,
    label: str,
    icon: str,
    *,
    active_prefix: str | None = None,
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "label": label,
        "icon": icon,
        "active_prefix": active_prefix,
    }


def climate_nav_sections(workspace: str) -> list[dict[str, Any]]:
    """One CLIMATE shell: shared items plus VANTA- or ARCTIC-only tools."""
    ws = normalize_workspace(workspace)
    personal = ws == "personal"
    shared = [
        _nav_item(dashboard_endpoint(ws), "Dashboard", "⌂"),
        _nav_item(chat_endpoint(ws), "CLIMATE Chat", "✦"),
        _nav_item(tasks_endpoint(ws), "Tasks", "☑"),
        _nav_item(
            notebook_endpoint(ws),
            "Notebook",
            "✎",
            active_prefix="personal_notebook" if personal else "work_notebook",
        ),
        _nav_item("settings_page", "Settings", "⚙", active_prefix="settings"),
    ]
    sections: list[dict[str, Any]] = [
        {"id": "climate", "label": "CLIMATE", "entries": shared},
    ]
    if personal:
        sections.append(
            {
                "id": "arctic",
                "label": "ARCTIC",
                "entries": [
                    _nav_item("arctic_dashboard", "Personal Files", "◈", active_prefix="arctic_"),
                    _nav_item("personal_aira", "Aira", "AI", active_prefix="personal_aira"),
                    _nav_item("personal_email", "Email", "✉", active_prefix="personal_email"),
                    _nav_item("personal_calendar", "Calendar", "📅", active_prefix="personal_calendar"),
                ],
            }
        )
    else:
        sections.append(
            {
                "id": "vanta",
                "label": "VANTA",
                "entries": [
                    _nav_item("work_climate", "Code Workspace", "C"),
                    _nav_item("repositories", "Repositories", "▣", active_prefix="repository"),
                    _nav_item("sql_workspace", "SQL Workspace", "▦", active_prefix="sql_workspace"),
                    _nav_item("data_explorer", "Data Explorer", "▤", active_prefix="data_explorer"),
                    _nav_item(
                        "work_airix",
                        "Workspace Assistant",
                        "AI",
                        active_prefix="work_airix",
                    ),
                    _nav_item("work_email", "Email", "✉", active_prefix="work_email"),
                    _nav_item("work_calendar", "Calendar", "📅", active_prefix="work_calendar"),
                ],
            }
        )
        sections.append(
            {
                "id": "dhis2",
                "label": "DHIS2",
                "icon": "⬡",
                "expandable": True,
                "expand_prefix": "dhis2",
                "entries": [
                    _nav_item("dhis2", "Overview", "⬡"),
                    _nav_item("dhis2_reports_library", "DHIS2 Reports", "▤", active_prefix="dhis2_reports"),
                    _nav_item("dhis2_hcsc_indicators", "HCSC–RF", "▣", active_prefix="dhis2_hcsc"),
                    _nav_item(
                        "dhis2_hcsc_progress_compare",
                        "Report Comparison",
                        "⇄",
                        active_prefix="dhis2_hcsc_progress",
                    ),
                ],
            }
        )
    sections.append(
        {
            "id": "system",
            "label": "System",
            "entries": [
                _nav_item("jobs", "Jobs", "▶", active_prefix="job"),
                _nav_item("health", "Health", "♡"),
                _nav_item("ai_connections", "Connections", "AI", active_prefix="ai_connections"),
                _nav_item("audit", "Audit", "☰"),
            ],
        }
    )
    return sections


# Preserve equivalent sections when switching VANTA ↔ ARCTIC.
# Code Workspace and Repositories are VANTA-only, so they are omitted here.
_WORKSPACE_SECTION_PAIRS = {
    "work_climate_chat": "personal_climate_chat",
    "personal_climate_chat": "work_climate_chat",
    "work_dashboard": "personal_dashboard",
    "personal_dashboard": "work_dashboard",
    "work_notebook": "personal_notebook",
    "personal_notebook": "work_notebook",
    "work_tasks": "personal_tasks",
    "personal_tasks": "work_tasks",
    "work_email": "personal_email",
    "personal_email": "work_email",
    "work_calendar": "personal_calendar",
    "personal_calendar": "work_calendar",
}

_SHARED_STAY_PREFIXES = ("settings", "api_settings")
_SHARED_STAY_ENDPOINTS = {
    "settings_page",
    "settings_ai_providers",
    "jobs",
    "job_detail",
    "health",
    "audit",
    "ai_connections",
}


def counterpart_endpoint(endpoint: str | None, target_workspace: str) -> str:
    """Map current section to the other workspace; else fall back to primary."""
    target = normalize_workspace(target_workspace)
    ep = endpoint or ""
    if ep in _SHARED_STAY_ENDPOINTS or ep.startswith(_SHARED_STAY_PREFIXES):
        return ep
    mapped = _WORKSPACE_SECTION_PAIRS.get(ep)
    if mapped:
        if target == "personal" and mapped.startswith("personal"):
            return mapped
        if target == "work" and mapped.startswith("work"):
            return mapped
    return primary_endpoint(target)


def notebook_endpoint(workspace: str) -> str:
    return (
        "personal_notebook"
        if normalize_workspace(workspace) == "personal"
        else "work_notebook"
    )


def scope_for_workspace(workspace: str) -> str:
    return normalize_workspace(workspace)
