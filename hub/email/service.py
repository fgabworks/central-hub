"""Shared Email Center service (one implementation for Personal and Work)."""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from hub.email.crypto import decrypt_token_blob
from hub.email.gmail_api import GmailApiError, GmailClient, parse_message_detail, parse_message_summary
from hub.email.models import (
    DEFAULT_PAGE_SIZE,
    FORBIDDEN_GMAIL_ACTIONS,
    GMAIL_SCOPES,
    MAILBOX_VIEWS,
    MAX_PAGE_SIZE,
    merge_scope_strings,
    normalize_mailbox_view,
    normalize_workspace,
)
from hub.email.oauth import (
    OAuthError,
    access_expires_at_iso,
    build_authorization_url,
    exchange_code,
    fetch_userinfo,
    generate_state,
    is_expired,
    refresh_access_token,
    revoke_token,
)
from hub.email.settings_gmail import GmailOAuthSettings, load_gmail_oauth_settings
from hub.email.store import EmailStore

HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]


class EmailServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class EmailService:
    """Single Gmail service used by both Personal and Work Email Center routes."""

    def __init__(
        self,
        store: EmailStore,
        *,
        oauth_settings: GmailOAuthSettings | None = None,
        gmail_client: GmailClient | None = None,
        http_get: HttpGet | None = None,
        http_post: HttpPost | None = None,
    ) -> None:
        self.store = store
        self.oauth = oauth_settings or load_gmail_oauth_settings()
        self.gmail = gmail_client or GmailClient(http_get=http_get)
        self._http_get = http_get
        self._http_post = http_post

    def oauth_public(self) -> dict[str, object]:
        return self.oauth.public_dict()

    def assert_not_write_action(self, action: str) -> None:
        if (action or "").strip().lower() in FORBIDDEN_GMAIL_ACTIONS:
            raise EmailServiceError(
                f"Gmail write action '{action}' is not allowed (readonly mode)",
                code="forbidden",
            )

    # --- Accounts ---

    def list_accounts(self, workspace: str) -> list[dict[str, Any]]:
        return self.store.list_accounts(workspace=normalize_workspace(workspace))

    def start_oauth(
        self,
        *,
        workspace: str,
        account_id: str | None = None,
        login_hint: str | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, str]:
        if not self.oauth.enabled:
            raise EmailServiceError("Gmail integration is disabled", code="disabled")
        if not self.oauth.is_configured:
            raise EmailServiceError(
                "Gmail OAuth is not configured — set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET",
                code="not_configured",
            )
        ws = normalize_workspace(workspace)
        hint = login_hint
        if account_id:
            acct = self.store.get_account(account_id)
            if not acct:
                raise EmailServiceError("Account not found", code="not_found")
            hint = hint or acct.get("email") or None
        scope_tuple = tuple(scopes) if scopes else GMAIL_SCOPES
        state = generate_state()
        self.store.create_oauth_state(
            workspace=ws,
            account_id=account_id,
            state=state,
            requested_scopes=" ".join(scope_tuple),
        )
        url = build_authorization_url(
            self.oauth,
            state=state,
            login_hint=hint,
            prompt="consent select_account",
            scopes=scope_tuple,
        )
        return {"authorization_url": url, "state": state}

    def complete_oauth(self, *, state: str, code: str) -> dict[str, Any]:
        if not code or not state:
            raise EmailServiceError("Missing OAuth code or state", code="invalid_callback")
        saved = self.store.consume_oauth_state(state)
        if not saved:
            raise EmailServiceError("Invalid or expired OAuth state", code="invalid_state")
        try:
            tokens = exchange_code(self.oauth, code, http_post=self._http_post)
        except OAuthError as exc:
            raise EmailServiceError(str(exc), code="oauth_exchange") from exc
        access = str(tokens.get("access_token") or "")
        if not access:
            raise EmailServiceError("No access token returned", code="oauth_exchange")
        # Require refresh token for durable server-side storage.
        if not tokens.get("refresh_token") and not saved.get("account_id"):
            raise EmailServiceError(
                "Google did not return a refresh token — try reconnect with consent",
                code="oauth_exchange",
            )
        try:
            profile = fetch_userinfo(access, http_get=self._http_get)
        except OAuthError as exc:
            raise EmailServiceError(str(exc), code="oauth_profile") from exc
        email = str(profile.get("email") or "").strip()
        sub = str(profile.get("sub") or "").strip()
        prior_scopes = ""
        if saved.get("account_id"):
            existing_acct = self.store.get_account(saved["account_id"], include_secrets=True)
            if existing_acct:
                prior_scopes = str(existing_acct.get("scopes") or "")
            existing = self.store.get_token_payload(saved["account_id"]) or {}
            if not tokens.get("refresh_token") and existing.get("refresh_token"):
                tokens = {**tokens, "refresh_token": existing["refresh_token"]}
            if existing.get("scope"):
                prior_scopes = merge_scope_strings(prior_scopes, str(existing.get("scope") or ""))
        if not tokens.get("refresh_token"):
            raise EmailServiceError(
                "Refresh token unavailable — disconnect and reconnect with consent",
                code="oauth_exchange",
            )
        requested = saved.get("requested_scopes") or ""
        scope = merge_scope_strings(
            prior_scopes,
            str(tokens.get("scope") or ""),
            requested,
        )
        if not scope:
            scope = " ".join(GMAIL_SCOPES)
        account = self.store.upsert_connected_account(
            workspace=saved["workspace"],
            email=email,
            google_sub=sub,
            token_payload={
                "refresh_token": tokens["refresh_token"],
                "token_type": tokens.get("token_type") or "Bearer",
                "scope": scope,
            },
            access_expires_at=None,
            scopes=scope,
            account_id=saved.get("account_id") or None,
        )
        # Store access token alongside for immediate use (still encrypted).
        payload = self.store.get_token_payload(account["id"]) or {}
        payload["access_token"] = access
        payload["scope"] = scope
        self.store.update_tokens(
            account["id"],
            payload,
            access_expires_at=access_expires_at_iso(tokens),
        )
        return self.store.get_account(account["id"]) or account

    def assign_workspace(self, account_id: str, workspace: str) -> dict[str, Any]:
        acct = self.store.assign_workspace(account_id, workspace)
        if not acct:
            raise EmailServiceError("Account not found", code="not_found")
        return acct

    def disconnect(
        self,
        account_id: str,
        *,
        revoke: bool = True,
    ) -> dict[str, Any]:
        acct = self.store.get_account(account_id, include_secrets=True)
        if not acct:
            raise EmailServiceError("Account not found", code="not_found")
        revoked = False
        if revoke and acct.get("token_encrypted"):
            try:
                payload = decrypt_token_blob(self.store.secret_key, acct["token_encrypted"])
            except ValueError:
                payload = {}
            token = str(payload.get("refresh_token") or payload.get("access_token") or "")
            if token:
                revoked = revoke_token(token, http_post=self._http_post)
        self.store.invalidate_account_cache(account_id)
        self.store.set_account_status(
            account_id,
            "revoked" if revoke else "unavailable",
            last_error="Disconnected by owner",
            clear_tokens=True,
        )
        self.store.delete_account(account_id)
        return {"ok": True, "revoked": revoked, "account_id": account_id}

    # --- Mailbox ---

    def list_messages(
        self,
        account_id: str,
        *,
        view: str = "inbox",
        q: str = "",
        label: str = "",
        page_token: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        acct = self._require_usable_account(account_id)
        view_n = normalize_mailbox_view(view)
        base_q = MAILBOX_VIEWS[view_n]["query"]
        parts = [base_q]
        if label.strip():
            # label:name — escape quotes in label name lightly
            safe_label = label.strip().replace('"', "")
            parts.append(f'label:"{safe_label}"' if " " in safe_label else f"label:{safe_label}")
        if q.strip():
            parts.append(q.strip())
        query = " ".join(parts)
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        cache_key = _list_cache_key(account_id, query, page_token or "", size)
        if not force_refresh:
            cached = self.store.get_list_cache(cache_key)
            if cached is not None:
                cached = {**cached, "from_cache": True, "account": acct}
                return cached
        access = self._access_token(account_id)
        try:
            raw = self.gmail.list_messages(
                access,
                query=query,
                page_token=page_token,
                max_results=size,
            )
        except GmailApiError as exc:
            self._handle_api_error(account_id, exc)
            raise EmailServiceError(str(exc), code="gmail_api") from exc

        messages: list[dict[str, Any]] = []
        for item in raw.get("messages") or []:
            mid = str((item or {}).get("id") or "")
            if not mid:
                continue
            cached_msg = self.store.get_message_cache(account_id, mid)
            if cached_msg and "body_text" not in cached_msg:
                messages.append(cached_msg)
                continue
            try:
                full = self.gmail.get_message(
                    access,
                    mid,
                    fmt="metadata",
                    metadata_headers=["From", "To", "Subject", "Date"],
                )
                summary = parse_message_summary(full)
            except GmailApiError:
                summary = {
                    "id": mid,
                    "thread_id": str((item or {}).get("threadId") or ""),
                    "snippet": "",
                    "subject": "(unavailable)",
                    "from_addr": "",
                    "to_addr": "",
                    "date_header": "",
                    "internal_date": "",
                    "label_ids": [],
                    "is_unread": False,
                    "is_starred": False,
                }
            self.store.put_message_cache(account_id, summary)
            messages.append(summary)

        result = {
            "ok": True,
            "account": acct,
            "view": view_n,
            "query": query,
            "q": q,
            "label": label,
            "messages": messages,
            "next_page_token": raw.get("nextPageToken") or "",
            "result_size_estimate": raw.get("resultSizeEstimate"),
            "from_cache": False,
        }
        self.store.put_list_cache(cache_key, account_id, {k: v for k, v in result.items() if k != "account"})
        self.store.touch_sync(account_id)
        result["account"] = acct
        return result

    def get_message(
        self,
        account_id: str,
        message_id: str,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        acct = self._require_usable_account(account_id)
        if not force_refresh:
            cached = self.store.get_message_cache(account_id, message_id)
            if cached and ("body_text" in cached or "body_html" in cached):
                return {"ok": True, "account": acct, "message": cached, "from_cache": True}
        access = self._access_token(account_id)
        try:
            raw = self.gmail.get_message(access, message_id, fmt="full")
            detail = parse_message_detail(raw)
        except GmailApiError as exc:
            self._handle_api_error(account_id, exc)
            raise EmailServiceError(str(exc), code="gmail_api") from exc
        self.store.put_message_cache(account_id, detail)
        return {"ok": True, "account": acct, "message": detail, "from_cache": False}

    def get_thread(self, account_id: str, thread_id: str) -> dict[str, Any]:
        acct = self._require_usable_account(account_id)
        access = self._access_token(account_id)
        try:
            raw = self.gmail.get_thread(access, thread_id)
        except GmailApiError as exc:
            self._handle_api_error(account_id, exc)
            raise EmailServiceError(str(exc), code="gmail_api") from exc
        messages = []
        for item in raw.get("messages") or []:
            if isinstance(item, dict):
                detail = parse_message_detail(item)
                self.store.put_message_cache(account_id, detail)
                messages.append(detail)
        return {
            "ok": True,
            "account": acct,
            "thread_id": thread_id,
            "messages": messages,
        }

    def list_labels(self, account_id: str) -> list[dict[str, Any]]:
        self._require_usable_account(account_id)
        access = self._access_token(account_id)
        try:
            labels = self.gmail.list_labels(access)
        except GmailApiError as exc:
            self._handle_api_error(account_id, exc)
            raise EmailServiceError(str(exc), code="gmail_api") from exc
        # Prefer user labels + system, drop noisy ids if needed
        out = []
        for lbl in labels:
            out.append(
                {
                    "id": lbl.get("id"),
                    "name": lbl.get("name"),
                    "type": lbl.get("type"),
                    "messages_total": lbl.get("messagesTotal"),
                    "messages_unread": lbl.get("messagesUnread"),
                }
            )
        out.sort(key=lambda x: (str(x.get("type") or ""), str(x.get("name") or "").lower()))
        return out

    def download_attachment(
        self,
        account_id: str,
        message_id: str,
        attachment_id: str,
        *,
        expected_filename: str = "attachment",
    ) -> tuple[bytes, str, str]:
        """Return (content, filename, mime_type). Validates attachment belongs to message."""
        detail = self.get_message(account_id, message_id)
        message = detail["message"]
        attachments = message.get("attachments") or []
        match = None
        for att in attachments:
            if str(att.get("attachment_id")) == str(attachment_id):
                match = att
                break
        if match is None:
            raise EmailServiceError("Attachment not found on message", code="not_found")
        access = self._access_token(account_id)
        try:
            content = self.gmail.get_attachment(access, message_id, attachment_id)
        except GmailApiError as exc:
            self._handle_api_error(account_id, exc)
            raise EmailServiceError(str(exc), code="gmail_api") from exc
        filename = str(match.get("filename") or expected_filename or "attachment")
        mime = str(match.get("mime_type") or "application/octet-stream")
        return content, filename, mime

    def refresh_account_cache(self, account_id: str) -> None:
        self._require_usable_account(account_id)
        self.store.invalidate_account_cache(account_id)

    # --- Internals ---

    def _require_usable_account(self, account_id: str) -> dict[str, Any]:
        acct = self.store.get_account(account_id)
        if not acct:
            raise EmailServiceError("Account not found", code="not_found")
        status = acct.get("status")
        if status in {"revoked", "unavailable"}:
            raise EmailServiceError(
                f"Account is {status} — reconnect to continue",
                code="account_unavailable",
            )
        if status == "needs_reauth":
            raise EmailServiceError(
                "Account needs reconnect (token expired or revoked)",
                code="needs_reauth",
            )
        if not acct.get("token_stored"):
            raise EmailServiceError("Account has no stored credentials", code="needs_reauth")
        return acct

    def _access_token(self, account_id: str) -> str:
        raw = self.store.get_account(account_id, include_secrets=True)
        if not raw or not raw.get("token_encrypted"):
            self.store.set_account_status(account_id, "needs_reauth", last_error="Missing tokens")
            raise EmailServiceError("Missing credentials — reconnect", code="needs_reauth")
        try:
            payload = decrypt_token_blob(self.store.secret_key, raw["token_encrypted"])
        except ValueError as exc:
            self.store.set_account_status(account_id, "error", last_error="Token decrypt failed")
            raise EmailServiceError("Stored token could not be decrypted", code="token_error") from exc

        access = str(payload.get("access_token") or "")
        expires = raw.get("access_expires_at")
        if access and not is_expired(expires):
            return access

        refresh = str(payload.get("refresh_token") or "")
        try:
            tokens = refresh_access_token(self.oauth, refresh, http_post=self._http_post)
        except OAuthError as exc:
            self.store.set_account_status(
                account_id,
                "needs_reauth",
                last_error=str(exc)[:300],
            )
            raise EmailServiceError(str(exc), code="needs_reauth") from exc

        new_access = str(tokens.get("access_token") or "")
        if not new_access:
            self.store.set_account_status(account_id, "needs_reauth", last_error="Empty access token")
            raise EmailServiceError("Token refresh returned no access token", code="needs_reauth")
        payload["access_token"] = new_access
        if tokens.get("refresh_token"):
            payload["refresh_token"] = tokens["refresh_token"]
        self.store.update_tokens(
            account_id,
            payload,
            access_expires_at=access_expires_at_iso(tokens),
        )
        return new_access

    def _handle_api_error(self, account_id: str, exc: GmailApiError) -> None:
        if exc.status_code in {401, 403}:
            self.store.set_account_status(
                account_id,
                "needs_reauth",
                last_error=str(exc)[:300],
            )
        elif exc.rate_limited:
            self.store.set_account_status(
                account_id,
                "error",
                last_error="Rate limited",
            )


def _list_cache_key(account_id: str, query: str, page_token: str, size: int) -> str:
    raw = f"{account_id}|{query}|{page_token}|{size}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
