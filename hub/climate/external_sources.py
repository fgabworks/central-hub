"""External read-only Google sources for the AiriX context registry.

Gmail, Drive, and Calendar implement the same source contract as internal
stores. Providers still receive one plain bounded packet. Direct mode never
calls the resolver; repository/All scopes keep these sources unavailable.
"""

from __future__ import annotations

from typing import Any

from hub.climate.context_registry import (
    ContextCandidate,
    ContextEvidence,
    ContextRequest,
    _BaseSource,
    _rank,
    _text,
)
from hub.climate.context_scope import GENERAL
from hub.drive.models import MAX_EXPORT_CHARS


def _external_allowed(request: ContextRequest) -> bool:
    return request.scope == GENERAL


class GmailContextSource(_BaseSource):
    id = "gmail"
    type = "gmail"

    def __init__(self, email_service: Any = None) -> None:
        self.email = email_service

    def source_metadata(self) -> dict[str, Any]:
        return {"bounded": True, "read_only": True, "external": True}

    def _accounts(self, request: ContextRequest) -> list[dict[str, Any]]:
        if self.email is None:
            return []
        return [
            acct
            for acct in list(self.email.list_accounts(request.workspace) or [])
            if acct.get("status") == "connected" and acct.get("has_gmail", True)
        ]

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _external_allowed(request):
            return {
                "available": False,
                "detail": "Gmail is General-scope only",
            }
        if self.email is None:
            return {"available": False, "detail": "Email service is not configured"}
        accounts = self._accounts(request)
        if not accounts:
            return {"available": False, "detail": "Gmail is disconnected or unavailable"}
        return {"available": True, "detail": f"{len(accounts)} connected Gmail account(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        query = (request.query or "").strip()
        if not query or self.email is None:
            return []
        size = max(1, min(limit, 8))
        out: list[ContextCandidate] = []
        for acct in self._accounts(request)[:2]:
            result = self.email.search_messages(acct["id"], q=query, page_size=size)
            for item in list(result.get("messages") or [])[:size]:
                message_id = str(item.get("id") or "")
                if not message_id:
                    continue
                subject = _text(item.get("subject"), 300) or "(no subject)"
                snippet = _text(item.get("snippet"), 800)
                out.append(ContextCandidate(
                    self.id,
                    f"gmail:{acct['id']}:{message_id}",
                    subject,
                    " ".join(filter(None, [
                        _text(item.get("from_addr"), 200),
                        _text(item.get("date_header") or item.get("internal_date"), 80),
                        snippet,
                    ])),
                    score=1.0,
                    metadata={
                        "account_id": acct["id"],
                        "account_email": acct.get("email") or "",
                        "message_id": message_id,
                        "thread_id": str(item.get("thread_id") or ""),
                        "from_addr": _text(item.get("from_addr"), 200),
                        "date": _text(item.get("date_header") or item.get("internal_date"), 80),
                        "subject": subject,
                        "snippet": snippet,
                    },
                ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            body = str(meta.get("snippet") or "")
            account_id = str(meta.get("account_id") or "")
            message_id = str(meta.get("message_id") or "")
            if self.email is not None and account_id and message_id:
                try:
                    detail = self.email.get_message(account_id, message_id)
                    message = dict(detail.get("message") or {})
                    body = str(message.get("body_text") or message.get("snippet") or body)
                except Exception:  # isolate retrieve; snippet is enough
                    body = str(meta.get("snippet") or body)
            content = "\n".join(filter(None, [
                f"From: {meta.get('from_addr') or '(unknown)'}",
                f"Date: {meta.get('date') or '(unknown)'}",
                f"Subject: {meta.get('subject') or item.title}",
                _text(body, remaining),
            ]))[:remaining]
            if content:
                public = {
                    key: value for key, value in meta.items() if key != "snippet"
                }
                out.append(self._evidence(item, content, **public))
                remaining -= len(content)
        return out


class DriveContextSource(_BaseSource):
    id = "google_drive"
    type = "google_drive"

    def __init__(self, drive_service: Any = None) -> None:
        self.drive = drive_service

    def source_metadata(self) -> dict[str, Any]:
        return {"bounded": True, "read_only": True, "external": True}

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _external_allowed(request):
            return {
                "available": False,
                "detail": "Google Drive is General-scope only",
            }
        if self.drive is None:
            return {"available": False, "detail": "Drive service is not configured"}
        accounts = list(self.drive.connected_accounts(request.workspace) or [])
        if not accounts:
            return {"available": False, "detail": "Google Drive is disconnected or unavailable"}
        return {"available": True, "detail": f"{len(accounts)} connected Drive account(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        if self.drive is None:
            return []
        rows = list(self.drive.search_files(
            request.workspace, q=request.query, limit=max(1, min(limit, 8))
        ) or [])
        out = []
        for item in rows:
            file_id = str(item.get("id") or "")
            if not file_id:
                continue
            title = _text(item.get("name"), 300) or "(untitled)"
            snippet = " ".join(filter(None, [
                _text(item.get("mime_type"), 120),
                _text(item.get("modified_time"), 80),
                " ".join(str(owner) for owner in list(item.get("owners") or [])[:3]),
                _text(item.get("description"), 400),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"drive:{item.get('account_id')}:{file_id}",
                title,
                snippet,
                score=1.0,
                metadata={
                    "account_id": str(item.get("account_id") or ""),
                    "account_email": str(item.get("account_email") or ""),
                    "file_id": file_id,
                    "mime_type": _text(item.get("mime_type"), 120),
                    "modified_time": _text(item.get("modified_time"), 80),
                    "web_view_link": _text(item.get("web_view_link"), 400),
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            export_text = ""
            account_id = str(meta.get("account_id") or "")
            file_id = str(meta.get("file_id") or "")
            if self.drive is not None and account_id and file_id:
                try:
                    detail = self.drive.get_file(
                        account_id,
                        file_id,
                        include_export=True,
                        char_budget=min(remaining, MAX_EXPORT_CHARS),
                    )
                    file_row = dict(detail.get("file") or {})
                    export_text = str(file_row.get("export_text") or "")
                    if file_row.get("description") and not export_text:
                        export_text = str(file_row.get("description") or "")
                except Exception:
                    export_text = item.snippet
            content = "\n".join(filter(None, [
                f"File: {item.title}",
                f"Type: {meta.get('mime_type') or '(unknown)'}",
                f"Modified: {meta.get('modified_time') or '(unknown)'}",
                _text(export_text or item.snippet, remaining),
            ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


class CalendarContextSource(_BaseSource):
    id = "google_calendar"
    type = "google_calendar"

    def __init__(self, calendar_service: Any = None) -> None:
        self.calendar = calendar_service

    def source_metadata(self) -> dict[str, Any]:
        return {"bounded": True, "read_only": True, "external": True}

    def _accounts(self, request: ContextRequest) -> list[dict[str, Any]]:
        if self.calendar is None:
            return []
        return [
            acct
            for acct in list(self.calendar.list_accounts(request.workspace) or [])
            if acct.get("status") == "connected" and acct.get("has_calendar")
        ]

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _external_allowed(request):
            return {
                "available": False,
                "detail": "Google Calendar is General-scope only",
            }
        if self.calendar is None:
            return {"available": False, "detail": "Calendar service is not configured"}
        accounts = self._accounts(request)
        if not accounts:
            return {"available": False, "detail": "Google Calendar is disconnected or unavailable"}
        return {"available": True, "detail": f"{len(accounts)} connected Calendar account(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        if self.calendar is None or not (request.query or "").strip():
            return []
        rows = list(self.calendar.search_events(
            request.workspace, q=request.query, limit=max(1, min(limit, 8))
        ) or [])
        out = []
        for item in rows:
            event_id = str(item.get("id") or "")
            if not event_id:
                continue
            title = _text(item.get("summary"), 300) or "(no title)"
            start = _event_time(item.get("start"))
            attendees = [
                str(att.get("email") or att.get("display_name") or "")
                for att in list(item.get("attendees") or [])
                if isinstance(att, dict)
            ][:8]
            snippet = " ".join(filter(None, [
                start,
                _text(item.get("location"), 200),
                " ".join(attendees),
                _text(item.get("description"), 400),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"calendar:{item.get('account_id')}:{item.get('calendar_id')}:{event_id}",
                title,
                snippet,
                score=1.0,
                metadata={
                    "account_id": str(item.get("account_id") or ""),
                    "account_email": str(item.get("account_email") or ""),
                    "calendar_id": str(item.get("calendar_id") or ""),
                    "event_id": event_id,
                    "start": start,
                    "end": _event_time(item.get("end")),
                    "location": _text(item.get("location"), 200),
                    "attendees": attendees,
                    "description": _text(item.get("description"), 800),
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            content = "\n".join(filter(None, [
                f"Title: {item.title}",
                f"When: {meta.get('start') or '(unknown)'} – {meta.get('end') or ''}".strip(),
                f"Location: {meta.get('location') or '(none)'}",
                "Attendees: " + (", ".join(list(meta.get("attendees") or [])[:8]) or "(none)"),
                _text(meta.get("description"), remaining),
            ]))[:remaining]
            if content:
                public = {key: value for key, value in meta.items() if key != "description"}
                out.append(self._evidence(item, content, **public))
                remaining -= len(content)
        return out


def _event_time(value: Any) -> str:
    if not isinstance(value, dict):
        return _text(value, 80)
    return _text(value.get("date_time") or value.get("date"), 80)
