"""Email Center constants."""

from __future__ import annotations

from hub.notebook.models import DEFAULT_WORKSPACE, WORKSPACES, normalize_workspace

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SCOPES = (GMAIL_READONLY_SCOPE,)

# Required so OAuth can resolve email/sub via OpenID userinfo.
IDENTITY_SCOPES = ("openid", "email", "profile")

# Calendar readonly (incremental — request with include_granted_scopes).
CALENDAR_CALENDARLIST_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
)
CALENDAR_EVENTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/calendar.events.readonly"
)
CALENDAR_SCOPES = (
    CALENDAR_CALENDARLIST_READONLY_SCOPE,
    CALENDAR_EVENTS_READONLY_SCOPE,
)


def with_identity_scopes(scopes: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """Always include OpenID identity scopes alongside API scopes."""
    base = tuple(scopes) if scopes else GMAIL_SCOPES
    merged = merge_scope_strings(" ".join(IDENTITY_SCOPES), " ".join(base))
    return tuple(merged.split())


def scopes_include(granted: str | None, required: tuple[str, ...] | list[str]) -> bool:
    """True when every required scope appears in a space-delimited granted string."""
    parts = set((granted or "").split())
    return all(scope in parts for scope in required)


def has_gmail_scopes(granted: str | None) -> bool:
    return scopes_include(granted, GMAIL_SCOPES)


def has_calendar_scopes(granted: str | None) -> bool:
    return scopes_include(granted, CALENDAR_SCOPES)


def merge_scope_strings(*parts: str | None) -> str:
    seen: list[str] = []
    for part in parts:
        for scope in (part or "").split():
            if scope and scope not in seen:
                seen.append(scope)
    return " ".join(seen)
ACCOUNT_STATUSES = (
    "connected",
    "needs_reauth",
    "revoked",
    "error",
    "unavailable",
)

ACCOUNT_STATUS_LABELS = {
    "connected": "Connected",
    "needs_reauth": "Needs reconnect",
    "revoked": "Revoked",
    "error": "Error",
    "unavailable": "Unavailable",
}

# Built-in mailbox views (Gmail search queries).
MAILBOX_VIEWS = {
    "inbox": {"label": "Inbox", "query": "in:inbox"},
    "unread": {"label": "Unread", "query": "is:unread"},
    "starred": {"label": "Starred", "query": "is:starred"},
    "sent": {"label": "Sent", "query": "in:sent"},
}

DEFAULT_VIEW = "inbox"
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50
CACHE_TTL_SECONDS = 300  # limited local cache; manual refresh invalidates
OAUTH_STATE_TTL_SECONDS = 1800  # 30 minutes — enough for consent + retries
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MiB passthrough cap

# Forbidden write operations (documented + guarded in service).
FORBIDDEN_GMAIL_ACTIONS = (
    "send",
    "reply",
    "delete",
    "modify_labels",
    "mark_read",
    "trash",
)


def normalize_mailbox_view(value: str | None, *, default: str = DEFAULT_VIEW) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in MAILBOX_VIEWS else default


def normalize_account_status(value: str | None, *, default: str = "error") -> str:
    raw = (value or "").strip().lower()
    return raw if raw in ACCOUNT_STATUSES else default


__all__ = [
    "ACCOUNT_STATUSES",
    "ACCOUNT_STATUS_LABELS",
    "CACHE_TTL_SECONDS",
    "CALENDAR_CALENDARLIST_READONLY_SCOPE",
    "CALENDAR_EVENTS_READONLY_SCOPE",
    "CALENDAR_SCOPES",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_VIEW",
    "DEFAULT_WORKSPACE",
    "FORBIDDEN_GMAIL_ACTIONS",
    "GMAIL_READONLY_SCOPE",
    "GMAIL_SCOPES",
    "IDENTITY_SCOPES",
    "MAILBOX_VIEWS",
    "MAX_ATTACHMENT_BYTES",
    "MAX_PAGE_SIZE",
    "OAUTH_STATE_TTL_SECONDS",
    "WORKSPACES",
    "has_calendar_scopes",
    "has_gmail_scopes",
    "merge_scope_strings",
    "normalize_account_status",
    "normalize_mailbox_view",
    "normalize_workspace",
    "scopes_include",
    "with_identity_scopes",
]
