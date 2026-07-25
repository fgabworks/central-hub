"""Convert Gmail messages to Notebook notes/tasks and link repositories."""

from __future__ import annotations

from typing import Any

from hub.notebook.workspace import notebook_endpoint


def message_to_note_body(message: dict[str, Any], *, account_email: str = "") -> str:
    lines = [
        f"**From:** {message.get('from_addr') or '—'}",
        f"**To:** {message.get('to_addr') or '—'}",
        f"**Date:** {message.get('date_header') or message.get('internal_date') or '—'}",
    ]
    if account_email:
        lines.append(f"**Account:** {account_email}")
    lines.append("")
    body = (message.get("body_text") or "").strip()
    if not body:
        body = (message.get("snippet") or "").strip()
    lines.append(body or "_(no body text)_")
    mid = message.get("id") or ""
    tid = message.get("thread_id") or ""
    if mid:
        lines.extend(["", "---", f"_Gmail message id:_ `{mid}`"])
    if tid:
        lines.append(f"_Gmail thread id:_ `{tid}`")
    return "\n".join(lines)


def convert_message_to_notebook(
    notes_store: Any,
    *,
    message: dict[str, Any],
    workspace: str,
    account_email: str = "",
    note_type: str = "note",
    repository_id: str = "",
    repository_label: str = "",
    actor: str = "owner",
) -> dict[str, Any]:
    """Create a scoped notebook note/task from a Gmail message. Does not mutate Gmail."""
    title = (message.get("subject") or "Email").strip() or "Email"
    if len(title) > 180:
        title = title[:177] + "..."
    body = message_to_note_body(message, account_email=account_email)
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
    note = notes_store.create(
        title=title,
        actor=actor,
        scope=scope,
        note_type=note_type if note_type in ("note", "task") else "note",
        repository_id=repository_id if scope == "work" else "",
        repository_label=repository_label if scope == "work" else "",
    )
    gmail_url = ""
    mid = message.get("id") or ""
    if mid:
        gmail_url = f"https://mail.google.com/mail/u/0/#all/{mid}"
    links = []
    if gmail_url:
        links.append({"label": "Open in Gmail", "url": gmail_url})
    saved = notes_store.save(
        note["id"],
        title=title,
        body_md=body,
        note_type=note_type if note_type in ("note", "task") else "note",
        status="inbox",
        priority="medium",
        due_date=None,
        tags=["from-email", "gmail"],
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
    # Prefer endpoint helper when Flask context exists; fall back to paths.
    try:
        from flask import has_request_context, url_for

        if has_request_context():
            return url_for(notebook_endpoint(scope))
    except Exception:  # noqa: BLE001
        pass
    return "/personal/notebook" if scope == "personal" else "/work/notebook"
