"""Read-only Google Drive service reused by AiriX context (no Drive writes)."""

from __future__ import annotations

import re
from typing import Any, Callable

from hub.drive.api import DriveApiError, DriveClient, parse_file_summary
from hub.drive.models import (
    DEFAULT_PAGE_SIZE,
    EXPORTABLE_MIME,
    FORBIDDEN_DRIVE_ACTIONS,
    MAX_EXPORT_CHARS,
    MAX_PAGE_SIZE,
    normalize_workspace,
)
from hub.email.models import DRIVE_SCOPES, google_api_scopes_for_account, has_drive_scopes
from hub.email.service import EmailService, EmailServiceError
from hub.email.store import EmailStore

HttpGet = Callable[..., Any]

_TERM_RE = re.compile(r"[a-z0-9_./-]{2,}", re.IGNORECASE)


class DriveServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class DriveService:
    """Bounded Drive search/retrieve using shared Google account tokens."""

    def __init__(
        self,
        store: EmailStore,
        *,
        email_service: EmailService,
        drive_client: DriveClient | None = None,
        http_get: HttpGet | None = None,
    ) -> None:
        self.store = store
        self.email = email_service
        self.client = drive_client or DriveClient(http_get=http_get)

    def assert_not_write_action(self, action: str) -> None:
        if (action or "").strip().lower() in FORBIDDEN_DRIVE_ACTIONS:
            raise DriveServiceError(
                f"Drive write action '{action}' is not allowed (readonly mode)",
                code="forbidden",
            )

    def list_accounts(self, workspace: str) -> list[dict[str, Any]]:
        return self.store.list_accounts(workspace=normalize_workspace(workspace))

    def start_drive_oauth(
        self,
        *,
        workspace: str,
        account_id: str | None = None,
    ) -> dict[str, str]:
        """Incremental OAuth for Drive — re-request Gmail plus already-granted APIs."""
        acct = self.store.get_account(account_id) if account_id else None
        scopes = google_api_scopes_for_account(acct, extra=DRIVE_SCOPES)
        return self.email.start_oauth(
            workspace=workspace,
            account_id=account_id,
            scopes=scopes,
        )

    def connected_accounts(self, workspace: str) -> list[dict[str, Any]]:
        return [
            acct
            for acct in self.list_accounts(workspace)
            if acct.get("status") == "connected" and acct.get("has_drive")
        ]

    def search_files(
        self,
        workspace: str,
        *,
        q: str,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """Query-relevant Drive files only — never list a whole drive."""
        query = _drive_search_query(q)
        if not query:
            return []
        size = max(1, min(int(limit), MAX_PAGE_SIZE))
        out: list[dict[str, Any]] = []
        for acct in self.connected_accounts(workspace):
            try:
                access = self._access_token(acct["id"])
                raw = self.client.list_files(access, query=query, page_size=size)
            except (DriveApiError, DriveServiceError, EmailServiceError) as exc:
                if isinstance(exc, DriveApiError):
                    self._handle_api_error(acct["id"], exc)
                continue
            for item in list(raw.get("files") or [])[:size]:
                if not isinstance(item, dict):
                    continue
                parsed = parse_file_summary(item)
                if not parsed.get("id"):
                    continue
                out.append({
                    **parsed,
                    "account_id": acct["id"],
                    "account_email": acct.get("email"),
                })
                if len(out) >= size:
                    return out
        return out

    def get_file(
        self,
        account_id: str,
        file_id: str,
        *,
        include_export: bool = True,
        char_budget: int = MAX_EXPORT_CHARS,
    ) -> dict[str, Any]:
        acct = self._require_drive_account(account_id)
        access = self._access_token(account_id)
        try:
            raw = self.client.get_file(access, file_id)
        except DriveApiError as exc:
            self._handle_api_error(account_id, exc)
            raise DriveServiceError(str(exc), code="drive_api") from exc
        parsed = parse_file_summary(raw)
        export_text = ""
        mime = str(parsed.get("mime_type") or "")
        export_mime = EXPORTABLE_MIME.get(mime) if include_export else None
        budget = max(1, min(int(char_budget), MAX_EXPORT_CHARS))
        if export_mime:
            try:
                export_text = self.client.export_text(
                    access, file_id, mime_type=export_mime, max_chars=budget
                )
            except DriveApiError:
                export_text = ""
        return {
            "ok": True,
            "account": acct,
            "file": {
                **parsed,
                "export_text": export_text,
                "content_embedded": bool(export_text),
            },
        }

    def _require_drive_account(self, account_id: str) -> dict[str, Any]:
        try:
            acct = self.email._require_usable_account(account_id)  # noqa: SLF001
        except EmailServiceError as exc:
            raise DriveServiceError(str(exc), code=exc.code) from exc
        scopes = acct.get("scopes") or ""
        if not has_drive_scopes(scopes) and not acct.get("has_drive"):
            acct = self.store.get_account(account_id) or acct
        if not acct.get("has_drive"):
            raise DriveServiceError(
                "Drive scopes not granted — enable Drive on Google Connections",
                code="missing_scopes",
            )
        return acct

    def _access_token(self, account_id: str) -> str:
        try:
            return self.email._access_token(account_id)  # noqa: SLF001
        except EmailServiceError as exc:
            raise DriveServiceError(str(exc), code=exc.code) from exc

    def _handle_api_error(self, account_id: str, exc: DriveApiError) -> None:
        if exc.status_code in {401, 403}:
            self.store.set_account_status(
                account_id,
                "needs_reauth",
                last_error=str(exc)[:300],
            )
        elif exc.rate_limited:
            self.store.set_account_status(account_id, "error", last_error="Rate limited")


def _drive_search_query(query: str) -> str:
    terms = [match.group(0) for match in _TERM_RE.finditer(query or "")][:6]
    if not terms:
        return ""
    parts: list[str] = []
    for term in terms:
        safe = term.replace("'", r"\'")
        parts.append(f"(name contains '{safe}' or fullText contains '{safe}')")
    return " or ".join(parts)
