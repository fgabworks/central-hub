"""Convert Calendar events to Notebook notes/tasks and link repositories."""

from __future__ import annotations

from typing import Any

from hub.notebook.workspace import notebook_endpoint


def event_to_note_body(event: dict[str, Any], *, account_email: str = "") -> str:
    start = event.get("start") or {}
    end = event.get("end") or {}
    when = start.get("date_time") or start.get("date") or "—"
    until = end.get("date_time") or end.get("date") or ""
    lines = [
        f"**When:** {when}" + (f" → {until}" if until else ""),
        f"**All-day:** {'yes' if event.get('all_day') else 'no'}",
        f"**Calendar:** {event.get('calendar_summary') or event.get('calendar_id') or '—'}",
        f"**Location:** {event.get('location') or '—'}",
    ]
    if account_email:
        lines.append(f"**Account:** {account_email}")
    if event.get("hangout_link"):
        lines.append(f"**Meet:** {event['hangout_link']}")
    attendees = event.get("attendees") or []
    if attendees:
        names = []
        for att in attendees[:20]:
            label = att.get("display_name") or att.get("email") or "attendee"
            status = att.get("response_status") or ""
            names.append(f"{label}" + (f" ({status})" if status else ""))
        lines.append("**Attendees:** " + ", ".join(names))
    lines.append("")
    desc = (event.get("description") or "").strip()
    lines.append(desc or "_(no description)_")
    eid = event.get("id") or ""
    if eid:
        lines.extend(["", "---", f"_Google Calendar event id:_ `{eid}`"])
    if event.get("recurring_event_id"):
        lines.append(f"_Recurring event id:_ `{event['recurring_event_id']}`")
    return "\n".join(lines)


def convert_event_to_notebook(
    notes_store: Any,
    *,
    event: dict[str, Any],
    workspace: str,
    account_email: str = "",
    note_type: str = "note",
    repository_id: str = "",
    repository_label: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    """Create a scoped notebook note/task from a Calendar event. Does not mutate Calendar."""
    title = (event.get("summary") or "Calendar event").strip() or "Calendar event"
    if len(title) > 180:
        title = title[:177] + "..."
    body = event_to_note_body(event, account_email=account_email)
    scope = workspace
    repos: list[dict[str, str]] = []
    if scope == "work" and repository_id:
        repos = [
            {
                "repository_id": repository_id,
                "repository_label": repository_label or repository_id,
                "role": "references",
            }
        ]
    due = None
    start = event.get("start") or {}
    if note_type == "task":
        due = (start.get("date") or str(start.get("date_time") or "")[:10] or None)

    note = notes_store.create(
        title=title,
        actor=actor,
        scope=scope,
        note_type=note_type if note_type in ("note", "task") else "note",
        repository_id=repository_id if scope == "work" else "",
        repository_label=repository_label if scope == "work" else "",
    )
    links = []
    if event.get("html_link"):
        links.append({"label": "Open in Google Calendar", "url": event["html_link"]})
    if event.get("hangout_link"):
        links.append({"label": "Meet link", "url": event["hangout_link"]})
    saved = notes_store.save(
        note["id"],
        title=title,
        body_md=body,
        note_type=note_type if note_type in ("note", "task") else "note",
        status="inbox",
        priority="medium",
        due_date=due,
        tags=["from-calendar", "google-calendar"],
        repositories=repos,
        checklist=[],
        links=links,
        pinned=False,
        actor=actor,
        scope=scope,
    )
    result = saved or note
    result["redirect"] = f"{_notebook_path(scope)}?note={result['id']}"
    return result


def _notebook_path(scope: str) -> str:
    try:
        from flask import has_request_context, url_for

        if has_request_context():
            return url_for(notebook_endpoint(scope))
    except Exception:  # noqa: BLE001
        pass
    return "/personal/notebook" if scope == "personal" else "/work/notebook"
