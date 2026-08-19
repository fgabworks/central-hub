"""Google Drive REST API client (readonly GET). Injectable HTTP for tests."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

import requests

from hub.drive.models import DEFAULT_PAGE_SIZE, MAX_EXPORT_CHARS, MAX_PAGE_SIZE

DRIVE_API = "https://www.googleapis.com/drive/v3"

HttpGet = Callable[..., Any]


class DriveApiError(Exception):
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


class DriveClient:
    """Thin Drive wrapper. Never persists tokens; caller supplies access_token."""

    def __init__(self, *, http_get: HttpGet | None = None, timeout: float = 25.0) -> None:
        self._get = http_get or requests.get
        self.timeout = timeout

    def list_files(
        self,
        access_token: str,
        *,
        query: str = "",
        page_size: int = DEFAULT_PAGE_SIZE,
        page_token: str | None = None,
        fields: str = (
            "nextPageToken,files(id,name,mimeType,modifiedTime,owners,description,"
            "webViewLink,size)"
        ),
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(page_size), MAX_PAGE_SIZE)),
            "fields": fields,
            "spaces": "drive",
            "corpora": "user",
        }
        if query.strip():
            params["q"] = query.strip()
        if page_token:
            params["pageToken"] = page_token
        return self._request(access_token, "/files", params=params)

    def get_file(self, access_token: str, file_id: str) -> dict[str, Any]:
        fid = quote(_safe_id(file_id), safe="")
        return self._request(
            access_token,
            f"/files/{fid}",
            params={
                "fields": (
                    "id,name,mimeType,modifiedTime,owners,description,webViewLink,"
                    "size,md5Checksum"
                ),
            },
        )

    def export_text(
        self,
        access_token: str,
        file_id: str,
        *,
        mime_type: str = "text/plain",
        max_chars: int = MAX_EXPORT_CHARS,
    ) -> str:
        """Bounded Google Docs/Sheets/Slides export. Never a full-file media download."""
        fid = quote(_safe_id(file_id), safe="")
        url = f"{DRIVE_API}/files/{fid}/export"
        resp = self._raw_get(
            access_token,
            url,
            params={"mimeType": mime_type},
        )
        if resp.status_code == 429:
            raise DriveApiError(
                "Drive API rate limit exceeded — try again shortly",
                status_code=429,
                rate_limited=True,
            )
        if resp.status_code in {401, 403}:
            raise DriveApiError(
                "Drive access denied — reconnect and approve Drive readonly scope",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            payload = _json_payload(resp)
            msg = str((payload.get("error") or {}).get("message") or "Drive export error")
            raise DriveApiError(msg[:200], status_code=resp.status_code)
        text = resp.content.decode("utf-8", errors="replace")
        return text[: max(1, min(int(max_chars), MAX_EXPORT_CHARS))]

    def _request(
        self,
        access_token: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._raw_get(access_token, f"{DRIVE_API}{path}", params=params or {})
        payload = _json_payload(resp)
        if resp.status_code == 429:
            raise DriveApiError(
                "Drive API rate limit exceeded — try again shortly",
                status_code=429,
                rate_limited=True,
            )
        if resp.status_code in {401, 403}:
            raise DriveApiError(
                "Drive access denied — reconnect and approve Drive readonly scope "
                "(and enable Google Drive API in Cloud Console)",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            msg = str((payload.get("error") or {}).get("message") or "Drive API error")
            raise DriveApiError(msg[:200], status_code=resp.status_code)
        return payload

    def _raw_get(
        self,
        access_token: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not access_token:
            raise DriveApiError("Missing access token", status_code=401)
        try:
            return self._get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DriveApiError("Drive API network error") from exc


def parse_file_summary(raw: dict[str, Any]) -> dict[str, Any]:
    owners = []
    for owner in raw.get("owners") or []:
        if isinstance(owner, dict):
            owners.append(
                str(owner.get("displayName") or owner.get("emailAddress") or "")
            )
    return {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or "(untitled)"),
        "mime_type": str(raw.get("mimeType") or ""),
        "modified_time": str(raw.get("modifiedTime") or ""),
        "description": str(raw.get("description") or ""),
        "web_view_link": str(raw.get("webViewLink") or ""),
        "size": raw.get("size"),
        "owners": [name for name in owners if name][:6],
    }


def _safe_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise DriveApiError("Invalid Drive file id")
    return raw


def _json_payload(resp: Any) -> dict[str, Any]:
    try:
        payload = resp.json() if getattr(resp, "content", None) else {}
    except Exception:  # noqa: BLE001
        payload = {}
    return payload if isinstance(payload, dict) else {}
