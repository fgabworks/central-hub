"""VS Code-style Workspace Console preferences (per workspace)."""

from __future__ import annotations

import json
from typing import Any

from hub.notebook.models import normalize_workspace
from hub.notebook.workspace import get_pref, set_pref

DEFAULT_HEIGHT = 280
MIN_HEIGHT = 160
MAX_HEIGHT = 640
MINIMIZED_HEIGHT = 36
TABS = ("problems", "output", "debug", "terminal", "ports")
PREF_PREFIX = "workspace_console"


def clamp_height(value: Any) -> int:
    try:
        height = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HEIGHT
    return max(MIN_HEIGHT, min(MAX_HEIGHT, height))


def normalize_tab(value: Any) -> str:
    tab = str(value or "").strip().lower()
    return tab if tab in TABS else "problems"


def _pref_key(workspace: str) -> str:
    return f"{PREF_PREFIX}:{normalize_workspace(workspace)}"


def default_console_state(workspace: str) -> dict[str, Any]:
    ws = normalize_workspace(workspace)
    return {
        "workspace": ws,
        "open": False,
        "minimized": False,
        "maximized": False,
        "height": DEFAULT_HEIGHT,
        "min_height": MIN_HEIGHT,
        "max_height": MAX_HEIGHT,
        "tab": "problems",
        "tabs": list(TABS),
        "terminal_session_id": "",
        "terminal_split": False,
    }


def load_console_prefs(db: Any, workspace: str) -> dict[str, Any]:
    state = default_console_state(workspace)
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
    if "minimized" in data:
        state["minimized"] = bool(data.get("minimized"))
    if "maximized" in data:
        state["maximized"] = bool(data.get("maximized"))
    if "height" in data:
        state["height"] = clamp_height(data.get("height"))
    if "tab" in data:
        state["tab"] = normalize_tab(data.get("tab"))
    if "terminal_session_id" in data:
        state["terminal_session_id"] = str(data.get("terminal_session_id") or "")[:64]
    if "terminal_split" in data:
        state["terminal_split"] = bool(data.get("terminal_split"))
    return state


def save_console_prefs(db: Any, workspace: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    current = load_console_prefs(db, workspace)
    data = payload if isinstance(payload, dict) else {}
    if "open" in data:
        current["open"] = bool(data.get("open"))
    if "minimized" in data:
        current["minimized"] = bool(data.get("minimized"))
    if "maximized" in data:
        current["maximized"] = bool(data.get("maximized"))
    if "height" in data:
        current["height"] = clamp_height(data.get("height"))
    if "tab" in data:
        current["tab"] = normalize_tab(data.get("tab"))
    if "terminal_session_id" in data:
        # Remember selected session id only — never command text or scrollback.
        current["terminal_session_id"] = str(data.get("terminal_session_id") or "")[:64]
    if "terminal_split" in data:
        current["terminal_split"] = bool(data.get("terminal_split"))
    if current["maximized"]:
        current["minimized"] = False
    if current["minimized"]:
        current["maximized"] = False
    set_pref(
        db,
        _pref_key(workspace),
        json.dumps(
            {
                "open": current["open"],
                "minimized": current["minimized"],
                "maximized": current["maximized"],
                "height": current["height"],
                "tab": current["tab"],
                "terminal_session_id": current.get("terminal_session_id") or "",
                "terminal_split": bool(current.get("terminal_split")),
            }
        ),
    )
    return current


def console_shell_bootstrap(db: Any, *, workspace: str) -> dict[str, Any]:
    """Lightweight page payload — never scans processes or streams logs."""
    prefs = load_console_prefs(db, workspace)
    return {
        "ok": True,
        "workspace": prefs["workspace"],
        "prefs": {
            "open": prefs["open"],
            "minimized": prefs["minimized"],
            "maximized": prefs["maximized"],
            "height": prefs["height"],
            "min_height": prefs["min_height"],
            "max_height": prefs["max_height"],
            "tab": prefs["tab"],
            "terminal_session_id": prefs.get("terminal_session_id") or "",
            "terminal_split": bool(prefs.get("terminal_split")),
        },
        "tabs": list(TABS),
        "prefs_url": "/api/workspace-console/prefs",
        "bootstrap_url": "/api/workspace-console/bootstrap",
        "problems_url": "/api/workspace-console/problems",
        "output_url": "/api/workspace-console/output",
        "debug_url": "/api/workspace-console/debug",
        "terminal_url": "/api/workspace-console/terminal",
        "terminal_sessions_url": "/api/workspace-console/terminal/sessions",
        "ports_url": "/api/workspace-console/ports",
        "interactive_terminal": True,
        "safety": {
            "controlled_terminal": True,
            "interactive_pty": True,
            "free_shell": False,
            "message": (
                "Interactive terminal is jailed to connected repository paths. "
                "AI assistants cannot execute commands."
            ),
        },
    }
