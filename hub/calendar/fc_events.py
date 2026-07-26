"""Map hub Calendar events to FullCalendar event objects."""

from __future__ import annotations

from typing import Any

from hub.calendar.sanitize import description_plain, sanitize_html


def to_fullcalendar_event(
    event: dict[str, Any],
    *,
    calendar_colors: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Convert a hub event dict into a FullCalendar event input object."""
    colors = (calendar_colors or {}).get(str(event.get("calendar_id") or ""), {})
    bg = colors.get("background") or event.get("calendar_color") or "#8b0000"
    fg = colors.get("foreground") or "#ffffff"
    all_day = bool(event.get("all_day"))
    start = event.get("start") or {}
    end = event.get("end") or {}
    if all_day:
        start_val = start.get("date") or ""
        end_val = end.get("date") or start_val
    else:
        start_val = start.get("date_time") or start.get("date") or ""
        end_val = end.get("date_time") or end.get("date") or start_val

    return {
        "id": f"{event.get('calendar_id')}:{event.get('id')}",
        "title": event.get("summary") or "(no title)",
        "start": start_val,
        "end": end_val,
        "allDay": all_day,
        "backgroundColor": bg,
        "borderColor": bg,
        "textColor": fg,
        "editable": False,
        "startEditable": False,
        "durationEditable": False,
        "resourceEditable": False,
        "extendedProps": {
            "event_id": event.get("id"),
            "calendar_id": event.get("calendar_id"),
            "calendar_summary": event.get("calendar_summary"),
            "location": event.get("location") or "",
            "hangout_link": event.get("hangout_link") or "",
            "html_link": event.get("html_link") or "",
            "attendees": event.get("attendees") or [],
            "recurring_event_id": event.get("recurring_event_id") or "",
            "description_plain": description_plain(event.get("description")),
            "description_html": sanitize_html(event.get("description")),
            "time_zone": start.get("time_zone") or "",
        },
    }


def calendar_color_map(calendars: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    fallback = [
        "#8b0000",
        "#0D5561",
        "#9a3412",
        "#1d4ed8",
        "#166534",
        "#7c2d12",
        "#6b21a8",
    ]
    for idx, cal in enumerate(calendars):
        cid = str(cal.get("id") or "")
        if not cid:
            continue
        bg = (cal.get("background_color") or "").strip() or fallback[idx % len(fallback)]
        fg = (cal.get("foreground_color") or "").strip() or "#ffffff"
        out[cid] = {"background": bg, "foreground": fg}
    return out


def event_detail_payload(
    event: dict[str, Any],
    *,
    account: dict[str, Any] | None = None,
    display_time_zone: str = "",
    registry_repos: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """JSON-safe event detail for the read-only drawer."""
    from hub.calendar.drawer import drawer_actions, drawer_sections, format_event_when

    description_html = sanitize_html(event.get("description"))
    enriched = {**event, "description_html": description_html}
    acct = account or {}
    workspace = str(acct.get("workspace") or "work")
    return {
        "id": event.get("id"),
        "calendar_id": event.get("calendar_id"),
        "calendar_summary": event.get("calendar_summary"),
        "summary": event.get("summary"),
        "location": event.get("location") or "",
        "hangout_link": event.get("hangout_link") or "",
        "html_link": event.get("html_link") or "",
        "all_day": bool(event.get("all_day")),
        "start": event.get("start") or {},
        "end": event.get("end") or {},
        "attendees": event.get("attendees") or [],
        "recurring_event_id": event.get("recurring_event_id") or "",
        "description_html": description_html,
        "description_plain": description_plain(event.get("description")),
        "when": format_event_when(enriched),
        "sections": drawer_sections(enriched, display_time_zone=display_time_zone),
        "actions": drawer_actions(
            enriched, workspace=workspace, registry_repos=registry_repos
        ),
        "account_email": acct.get("email") or "",
        "account_id": acct.get("id") or "",
        "workspace": workspace,
        "readonly": True,
        "actions_disabled": {
            "create": True,
            "edit": True,
            "delete": True,
            "drag": True,
            "resize": True,
            "rsvp": True,
        },
    }
