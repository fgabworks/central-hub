"""Repository Notebook constants and helpers."""

from __future__ import annotations

STATUSES = (
    "inbox",
    "pending",
    "ongoing",
    "blocked",
    "done",
    "archived",
)

STATUS_LABELS = {
    "inbox": "Inbox",
    "pending": "Pending",
    "ongoing": "Ongoing",
    "blocked": "Blocked",
    "done": "Done",
    "archived": "Archived",
}

NOTE_TYPES = (
    "note",
    "task",
    "bug",
    "decision",
    "idea",
    "follow-up",
    "mission",
)

NOTE_TYPE_LABELS = {
    "note": "Note",
    "task": "Task",
    "bug": "Bug",
    "decision": "Decision",
    "idea": "Idea",
    "follow-up": "Follow-up",
    "mission": "Mission",
}

# TODAY Mission Control reminder lifecycle (notebook missions only).
REMINDER_STATUSES = ("none", "pending", "sent", "skipped")
REMINDER_STATUS_LABELS = {
    "none": "None",
    "pending": "Pending",
    "sent": "Sent",
    "skipped": "Skipped",
}
# Local-time cutoff: unfinished TODAY missions should be reminded before 17:00.
MISSION_REMINDER_BEFORE_HOUR = 17

PRIORITIES = ("low", "medium", "high", "urgent")

PRIORITY_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "urgent": "Urgent",
}

REPO_ROLES = (
    "primary",
    "related",
    "depends-on",
    "blocks",
    "references",
)

REPO_ROLE_LABELS = {
    "primary": "Primary",
    "related": "Related",
    "depends-on": "Depends on",
    "blocks": "Blocks",
    "references": "References",
}

# Notebook / dashboard workspace scope (single note system, filtered views).
SCOPES = ("personal", "work")
SCOPE_LABELS = {
    "personal": "Personal",
    "work": "Work",
}
DEFAULT_SCOPE = "work"
WORKSPACES = SCOPES  # Personal | Work switcher values
DEFAULT_WORKSPACE = "work"


def normalize_status(value: str | None, *, default: str = "inbox") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in STATUSES else default


def normalize_type(value: str | None, *, default: str = "note") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in NOTE_TYPES else default


def normalize_reminder_status(value: str | None, *, default: str = "none") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in REMINDER_STATUSES else default


def normalize_priority(value: str | None, *, default: str = "medium") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in PRIORITIES else default


def normalize_role(value: str | None, *, default: str = "related") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in REPO_ROLES else default


def normalize_scope(value: str | None, *, default: str = DEFAULT_SCOPE) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in SCOPES else default


def normalize_workspace(value: str | None, *, default: str = DEFAULT_WORKSPACE) -> str:
    """Alias for UI workspace switcher (personal | work)."""
    return normalize_scope(value, default=default)


def parse_tags(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = (value or "").replace(";", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = str(part or "").strip().lstrip("#")
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out[:40]
