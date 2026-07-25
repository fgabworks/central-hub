"""Calendar Center constants."""

from __future__ import annotations

from hub.email.models import CALENDAR_SCOPES, DEFAULT_WORKSPACE, WORKSPACES, normalize_workspace

CALENDAR_VIEWS = ("month", "week", "day", "agenda", "upcoming")
DEFAULT_VIEW = "month"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
UPCOMING_LIMIT = 8
CACHE_TTL_SECONDS = 300

FORBIDDEN_CALENDAR_ACTIONS = (
    "create",
    "update",
    "delete",
    "rsvp",
    "patch",
    "insert",
    "move",
)


def normalize_calendar_view(value: str | None, *, default: str = DEFAULT_VIEW) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in CALENDAR_VIEWS else default


__all__ = [
    "CACHE_TTL_SECONDS",
    "CALENDAR_SCOPES",
    "CALENDAR_VIEWS",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_VIEW",
    "DEFAULT_WORKSPACE",
    "FORBIDDEN_CALENDAR_ACTIONS",
    "MAX_PAGE_SIZE",
    "UPCOMING_LIMIT",
    "WORKSPACES",
    "normalize_calendar_view",
    "normalize_workspace",
]
