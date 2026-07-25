"""Gmail REST API client (readonly). Injectable HTTP for tests."""

from __future__ import annotations

import base64
import email.utils
from typing import Any, Callable

import requests

from hub.email.models import DEFAULT_PAGE_SIZE, MAX_ATTACHMENT_BYTES, MAX_PAGE_SIZE

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

HttpGet = Callable[..., Any]


class GmailApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limited = rate_limited


class GmailClient:
    """Thin Gmail API wrapper. Never persists tokens; caller supplies access_token."""

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        timeout: float = 25.0,
    ) -> None:
        self._get = http_get or requests.get
        self.timeout = timeout

    def list_messages(
        self,
        access_token: str,
        *,
        query: str = "",
        label_ids: list[str] | None = None,
        page_token: str | None = None,
        max_results: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results), MAX_PAGE_SIZE)),
        }
        if query:
            params["q"] = query
        if label_ids:
            params["labelIds"] = label_ids
        if page_token:
            params["pageToken"] = page_token
        return self._request(access_token, "/users/me/messages", params=params)

    def get_message(
        self,
        access_token: str,
        message_id: str,
        *,
        fmt: str = "full",
        metadata_headers: list[str] | None = None,
    ) -> dict[str, Any]:
        mid = _safe_id(message_id)
        params: dict[str, Any] = {"format": fmt}
        if metadata_headers:
            params["metadataHeaders"] = metadata_headers
        return self._request(
            access_token,
            f"/users/me/messages/{mid}",
            params=params,
        )

    def get_thread(self, access_token: str, thread_id: str) -> dict[str, Any]:
        tid = _safe_id(thread_id)
        return self._request(
            access_token,
            f"/users/me/threads/{tid}",
            params={"format": "full"},
        )

    def list_labels(self, access_token: str) -> list[dict[str, Any]]:
        data = self._request(access_token, "/users/me/labels")
        labels = data.get("labels") or []
        return [lbl for lbl in labels if isinstance(lbl, dict)]

    def get_attachment(
        self,
        access_token: str,
        message_id: str,
        attachment_id: str,
    ) -> bytes:
        mid = _safe_id(message_id)
        aid = _safe_id(attachment_id)
        data = self._request(
            access_token,
            f"/users/me/messages/{mid}/attachments/{aid}",
        )
        raw_b64 = data.get("data") or ""
        if not isinstance(raw_b64, str) or not raw_b64:
            raise GmailApiError("Empty attachment payload")
        try:
            content = base64.urlsafe_b64decode(raw_b64 + "===")
        except Exception as exc:  # noqa: BLE001
            raise GmailApiError("Invalid attachment encoding") from exc
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise GmailApiError("Attachment exceeds download size limit")
        return content

    def _request(
        self,
        access_token: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not access_token:
            raise GmailApiError("Missing access token", status_code=401)
        url = f"{GMAIL_API}{path}"
        try:
            resp = self._get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GmailApiError("Gmail API network error") from exc
        try:
            payload = resp.json() if resp.content else {}
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if resp.status_code == 429:
            raise GmailApiError(
                "Gmail API rate limit exceeded — try again shortly",
                status_code=429,
                rate_limited=True,
            )
        if resp.status_code in {401, 403}:
            raise GmailApiError(
                "Gmail access denied — reconnect the account",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            msg = str(payload.get("error", {}).get("message") or "Gmail API error")
            # Avoid leaking request URLs with tokens.
            raise GmailApiError(msg[:200], status_code=resp.status_code)
        return payload


def parse_message_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Gmail message resource into a hub-safe summary (no tokens)."""
    headers = _header_map(raw)
    label_ids = [str(x) for x in (raw.get("labelIds") or []) if x]
    subject = headers.get("subject") or "(no subject)"
    from_addr = headers.get("from") or ""
    to_addr = headers.get("to") or ""
    date_header = headers.get("date") or ""
    return {
        "id": str(raw.get("id") or ""),
        "thread_id": str(raw.get("threadId") or ""),
        "snippet": str(raw.get("snippet") or ""),
        "subject": subject,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "date_header": date_header,
        "internal_date": str(raw.get("internalDate") or ""),
        "label_ids": label_ids,
        "is_unread": "UNREAD" in label_ids,
        "is_starred": "STARRED" in label_ids,
    }


def parse_message_detail(raw: dict[str, Any]) -> dict[str, Any]:
    summary = parse_message_summary(raw)
    body_text, body_html = _extract_bodies(raw.get("payload") or {})
    attachments = _extract_attachments(raw.get("payload") or {})
    summary.update(
        {
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
            "size_estimate": raw.get("sizeEstimate"),
        }
    )
    return summary


def _header_map(raw: dict[str, Any]) -> dict[str, str]:
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    out: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name:
            out[name] = str(item.get("value") or "")
    return out


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        data = body.get("data")
        if data and mime == "text/plain":
            text_parts.append(_b64url_decode_str(data))
        elif data and mime == "text/html":
            html_parts.append(_b64url_decode_str(data))
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(payload, dict):
        walk(payload)
    return ("\n".join(text_parts).strip(), "\n".join(html_parts).strip())


def _extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        att_id = body.get("attachmentId")
        if filename and att_id:
            size = body.get("size")
            try:
                size_i = int(size) if size is not None else None
            except (TypeError, ValueError):
                size_i = None
            found.append(
                {
                    "filename": _safe_filename(filename),
                    "mime_type": str(part.get("mimeType") or "application/octet-stream"),
                    "attachment_id": str(att_id),
                    "size": size_i,
                }
            )
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    if isinstance(payload, dict):
        walk(payload)
    return found


def _b64url_decode_str(data: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(data + "===")
        return raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _safe_id(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or any(c in cleaned for c in "/?#&"):
        raise GmailApiError("Invalid Gmail id")
    return cleaned


def _safe_filename(name: str) -> str:
    base = (name or "attachment").replace("\\", "_").replace("/", "_").strip()
    return base[:180] or "attachment"


def format_internal_date(ms: str | None) -> str:
    if not ms:
        return ""
    try:
        seconds = int(ms) / 1000.0
        return email.utils.formatdate(seconds, localtime=False, usegmt=True)
    except (TypeError, ValueError, OSError):
        return ""
