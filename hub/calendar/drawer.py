"""Shared Calendar event-detail drawer view helpers (Personal + Work)."""

from __future__ import annotations

from typing import Any

from hub.calendar.sanitize import sanitize_html


def format_event_when(event: dict[str, Any]) -> str:
    """Human-readable when line for all-day and timed events."""
    start = event.get("start") or {}
    end = event.get("end") or {}
    if event.get("all_day"):
        s = str(start.get("date") or "")
        e = str(end.get("date") or s)
        if e and e != s:
            return f"{s} → {e} (all day)"
        return f"{s} (all day)" if s else "All day"
    st = str(start.get("date_time") or start.get("date") or "")
    en = str(end.get("date_time") or end.get("date") or "")
    if st and en:
        return f"{st} → {en}"
    return st or en or "—"


def drawer_sections(event: dict[str, Any], *, display_time_zone: str = "") -> list[dict[str, Any]]:
    """Structured sections for the right-side event drawer."""
    start = event.get("start") or {}
    tz = str(start.get("time_zone") or display_time_zone or "").strip() or "—"
    location = str(event.get("location") or "").strip()
    meet = str(event.get("hangout_link") or "").strip()
    attendees = event.get("attendees") or []
    desc_html = event.get("description_html")
    if desc_html is None:
        desc_html = sanitize_html(event.get("description"))
    sections: list[dict[str, Any]] = [
        {
            "id": "when",
            "title": "Date & time",
            "kind": "text",
            "value": format_event_when(event),
            "empty": False,
        },
        {
            "id": "calendar",
            "title": "Calendar",
            "kind": "text",
            "value": str(event.get("calendar_summary") or event.get("calendar_id") or "—"),
            "empty": False,
        },
        {
            "id": "timezone",
            "title": "Time zone",
            "kind": "text",
            "value": tz,
            "empty": False,
        },
        {
            "id": "location",
            "title": "Location",
            "kind": "text",
            "value": location or "No location",
            "empty": not bool(location),
        },
        {
            "id": "attendees",
            "title": "Attendees",
            "kind": "attendees",
            "value": attendees,
            "empty": not bool(attendees),
        },
        {
            "id": "description",
            "title": "Description",
            "kind": "html",
            "value": desc_html or "",
            "empty": not bool(desc_html),
        },
        {
            "id": "meet",
            "title": "Meet",
            "kind": "link",
            "value": meet,
            "label": "Join meeting",
            "empty": not bool(meet),
        },
    ]
    if event.get("recurring_event_id"):
        sections.append(
            {
                "id": "recurring",
                "title": "Recurring",
                "kind": "text",
                "value": str(event.get("recurring_event_id")),
                "empty": False,
            }
        )
    return sections


def drawer_actions(
    event: dict[str, Any],
    *,
    workspace: str,
    registry_repos: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Available vs hidden/disabled actions for the drawer footer."""
    ws = (workspace or "").strip().lower()
    repos = registry_repos or []
    return {
        "convert_note": True,
        "create_task": True,
        "link_repository": ws == "work" and bool(repos),
        "open_in_google": bool(event.get("html_link")),
        "registry_repos": repos if ws == "work" else [],
        "readonly_hidden": ["create", "edit", "delete", "drag", "resize", "rsvp"],
    }
