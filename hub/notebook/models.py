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
)

NOTE_TYPE_LABELS = {
    "note": "Note",
    "task": "Task",
    "bug": "Bug",
    "decision": "Decision",
    "idea": "Idea",
    "follow-up": "Follow-up",
}

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


def normalize_status(value: str | None, *, default: str = "inbox") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in STATUSES else default


def normalize_type(value: str | None, *, default: str = "note") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in NOTE_TYPES else default


def normalize_priority(value: str | None, *, default: str = "medium") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in PRIORITIES else default


def normalize_role(value: str | None, *, default: str = "related") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in REPO_ROLES else default


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
