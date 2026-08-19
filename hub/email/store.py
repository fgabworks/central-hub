"""Account + cache store for Email Center (no token exposure in public APIs)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from hub.email.crypto import decrypt_token_blob, encrypt_token_blob, redact_account_public
from hub.email.db import EmailDatabase, utcnow
from hub.email.models import (
    CACHE_TTL_SECONDS,
    OAUTH_STATE_TTL_SECONDS,
    has_calendar_scopes,
    has_drive_scopes,
    has_gmail_scopes,
    normalize_account_status,
    normalize_workspace,
)
from hub.email.oauth import state_expiry_iso


class EmailStore:
    def __init__(self, db: EmailDatabase | None = None, *, secret_key: str = "") -> None:
        self.db = db or EmailDatabase()
        self.secret_key = secret_key

    # --- OAuth state ---

    def create_oauth_state(
        self,
        *,
        workspace: str,
        account_id: str | None = None,
        ttl_seconds: int = OAUTH_STATE_TTL_SECONDS,
        state: str,
        requested_scopes: str = "",
    ) -> dict[str, str]:
        ws = normalize_workspace(workspace)
        now = utcnow()
        expires = state_expiry_iso(ttl_seconds=ttl_seconds)
        scopes = (requested_scopes or "").strip()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO gmail_oauth_states
                    (state, workspace, account_id, created_at, expires_at, requested_scopes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (state, ws, account_id or None, now, expires, scopes),
            )
        return {
            "state": state,
            "workspace": ws,
            "account_id": account_id or "",
            "expires_at": expires,
            "requested_scopes": scopes,
        }

    def get_oauth_state(self, state: str) -> dict[str, Any] | None:
        """Validate OAuth state without consuming it. Returns None if invalid/expired."""
        raw = (state or "").strip()
        if not raw:
            return None
        now = datetime.now(timezone.utc)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM gmail_oauth_states WHERE state = ?",
                (raw,),
            ).fetchone()
            if not row:
                return None
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            except ValueError:
                return None
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                conn.execute("DELETE FROM gmail_oauth_states WHERE state = ?", (raw,))
                return None
            keys = row.keys()
            requested = row["requested_scopes"] if "requested_scopes" in keys else ""
            return {
                "state": row["state"],
                "workspace": normalize_workspace(row["workspace"]),
                "account_id": row["account_id"] or "",
                "requested_scopes": requested or "",
            }

    def consume_oauth_state(self, state: str) -> dict[str, Any] | None:
        """Validate and delete one-time OAuth state. Returns None if invalid/expired."""
        saved = self.get_oauth_state(state)
        if not saved:
            return None
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gmail_oauth_states WHERE state = ?", (saved["state"],))
        return saved

    def delete_oauth_state(self, state: str) -> None:
        raw = (state or "").strip()
        if not raw:
            return
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gmail_oauth_states WHERE state = ?", (raw,))

    # --- Accounts ---

    def list_accounts(self, *, workspace: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            if workspace:
                rows = conn.execute(
                    """
                    SELECT * FROM gmail_accounts
                    WHERE workspace = ?
                    ORDER BY email COLLATE NOCASE, created_at
                    """,
                    (normalize_workspace(workspace),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM gmail_accounts ORDER BY workspace, email COLLATE NOCASE"
                ).fetchall()
        return [self._public_row(dict(r)) for r in rows]

    def get_account(self, account_id: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM gmail_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if include_secrets:
            return data
        return self._public_row(data)

    def find_account(
        self,
        *,
        email: str | None = None,
        google_sub: str | None = None,
        include_secrets: bool = False,
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = None
            if google_sub:
                row = conn.execute(
                    "SELECT * FROM gmail_accounts WHERE google_sub = ?",
                    (google_sub,),
                ).fetchone()
            if row is None and email:
                row = conn.execute(
                    "SELECT * FROM gmail_accounts WHERE lower(email) = lower(?)",
                    (email,),
                ).fetchone()
        if not row:
            return None
        data = dict(row)
        if include_secrets:
            return data
        return self._public_row(data)

    def get_token_payload(self, account_id: str) -> dict[str, Any] | None:
        acct = self.get_account(account_id, include_secrets=True)
        if not acct or not acct.get("token_encrypted"):
            return None
        try:
            return decrypt_token_blob(self.secret_key, acct["token_encrypted"])
        except ValueError:
            return None

    def upsert_connected_account(
        self,
        *,
        workspace: str,
        email: str,
        google_sub: str,
        token_payload: dict[str, Any],
        access_expires_at: str | None,
        scopes: str,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        now = utcnow()
        encrypted = encrypt_token_blob(self.secret_key, token_payload)
        with self.db.connect() as conn:
            existing = None
            if account_id:
                existing = conn.execute(
                    "SELECT * FROM gmail_accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
            if existing is None and google_sub:
                existing = conn.execute(
                    "SELECT * FROM gmail_accounts WHERE google_sub = ?",
                    (google_sub,),
                ).fetchone()
            if existing is None and email:
                existing = conn.execute(
                    "SELECT * FROM gmail_accounts WHERE lower(email) = lower(?)",
                    (email,),
                ).fetchone()

            if existing:
                aid = existing["id"]
                conn.execute(
                    """
                    UPDATE gmail_accounts SET
                        email = ?, google_sub = ?, workspace = ?, status = 'connected',
                        token_encrypted = ?, access_expires_at = ?, scopes = ?,
                        connected_at = COALESCE(connected_at, ?), last_error = '',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        email or existing["email"],
                        google_sub or existing["google_sub"],
                        ws,
                        encrypted,
                        access_expires_at,
                        scopes,
                        now,
                        now,
                        aid,
                    ),
                )
            else:
                aid = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO gmail_accounts (
                        id, email, google_sub, workspace, status, token_encrypted,
                        access_expires_at, scopes, connected_at, last_sync_at,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'connected', ?, ?, ?, ?, NULL, '', ?, ?)
                    """,
                    (
                        aid,
                        email,
                        google_sub,
                        ws,
                        encrypted,
                        access_expires_at,
                        scopes,
                        now,
                        now,
                        now,
                    ),
                )
        return self.get_account(aid) or {"id": aid}

    def update_tokens(
        self,
        account_id: str,
        token_payload: dict[str, Any],
        *,
        access_expires_at: str | None,
    ) -> None:
        encrypted = encrypt_token_blob(self.secret_key, token_payload)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE gmail_accounts SET
                    token_encrypted = ?, access_expires_at = ?,
                    status = 'connected', last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (encrypted, access_expires_at, utcnow(), account_id),
            )

    def set_account_status(
        self,
        account_id: str,
        status: str,
        *,
        last_error: str = "",
        clear_tokens: bool = False,
    ) -> dict[str, Any] | None:
        st = normalize_account_status(status)
        with self.db.connect() as conn:
            if clear_tokens:
                conn.execute(
                    """
                    UPDATE gmail_accounts SET
                        status = ?, last_error = ?, token_encrypted = '',
                        access_expires_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (st, (last_error or "")[:500], utcnow(), account_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE gmail_accounts SET
                        status = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (st, (last_error or "")[:500], utcnow(), account_id),
                )
        return self.get_account(account_id)

    def assign_workspace(self, account_id: str, workspace: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE gmail_accounts SET workspace = ?, updated_at = ? WHERE id = ?",
                (normalize_workspace(workspace), utcnow(), account_id),
            )
        return self.get_account(account_id)

    def delete_account(self, account_id: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM gmail_accounts WHERE id = ?", (account_id,))
            return cur.rowcount > 0

    def touch_sync(self, account_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE gmail_accounts SET last_sync_at = ?, updated_at = ? WHERE id = ?",
                (utcnow(), utcnow(), account_id),
            )

    # --- Cache ---

    def get_list_cache(self, cache_key: str, *, ttl_seconds: int = CACHE_TTL_SECONDS) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM gmail_list_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        if _is_stale(row["cached_at"], ttl_seconds):
            return None
        try:
            data = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def put_list_cache(self, cache_key: str, account_id: str, payload: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO gmail_list_cache (cache_key, account_id, payload_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    cached_at = excluded.cached_at,
                    account_id = excluded.account_id
                """,
                (cache_key, account_id, json.dumps(payload), utcnow()),
            )

    def invalidate_account_cache(self, account_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gmail_list_cache WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM gmail_message_cache WHERE account_id = ?", (account_id,))
        self.invalidate_calendar_cache(account_id)

    def get_message_cache(
        self, account_id: str, message_id: str, *, ttl_seconds: int = CACHE_TTL_SECONDS
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM gmail_message_cache
                WHERE account_id = ? AND message_id = ?
                """,
                (account_id, message_id),
            ).fetchone()
        if not row or _is_stale(row["cached_at"], ttl_seconds):
            return None
        data = dict(row)
        try:
            label_ids = json.loads(data.get("label_ids_json") or "[]")
        except json.JSONDecodeError:
            label_ids = []
        payload = {}
        if data.get("payload_json"):
            try:
                payload = json.loads(data["payload_json"])
            except json.JSONDecodeError:
                payload = {}
        return {
            "id": data["message_id"],
            "thread_id": data["thread_id"],
            "snippet": data["snippet"],
            "subject": data["subject"],
            "from_addr": data["from_addr"],
            "to_addr": data["to_addr"],
            "date_header": data["date_header"],
            "internal_date": data["internal_date"],
            "label_ids": label_ids,
            "is_unread": bool(data["is_unread"]),
            "is_starred": bool(data["is_starred"]),
            **(payload if isinstance(payload, dict) else {}),
            "cached": True,
        }

    def put_message_cache(self, account_id: str, message: dict[str, Any]) -> None:
        detail_keys = ("body_text", "body_html", "attachments", "size_estimate")
        payload = {k: message[k] for k in detail_keys if k in message}
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO gmail_message_cache (
                    account_id, message_id, thread_id, label_ids_json, snippet,
                    subject, from_addr, to_addr, date_header, internal_date,
                    is_unread, is_starred, payload_json, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, message_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    label_ids_json = excluded.label_ids_json,
                    snippet = excluded.snippet,
                    subject = excluded.subject,
                    from_addr = excluded.from_addr,
                    to_addr = excluded.to_addr,
                    date_header = excluded.date_header,
                    internal_date = excluded.internal_date,
                    is_unread = excluded.is_unread,
                    is_starred = excluded.is_starred,
                    payload_json = excluded.payload_json,
                    cached_at = excluded.cached_at
                """,
                (
                    account_id,
                    message.get("id") or "",
                    message.get("thread_id") or "",
                    json.dumps(message.get("label_ids") or []),
                    message.get("snippet") or "",
                    message.get("subject") or "",
                    message.get("from_addr") or "",
                    message.get("to_addr") or "",
                    message.get("date_header") or "",
                    message.get("internal_date") or "",
                    1 if message.get("is_unread") else 0,
                    1 if message.get("is_starred") else 0,
                    json.dumps(payload),
                    utcnow(),
                ),
            )

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        public = redact_account_public(row)
        scopes = str(row.get("scopes") or "")
        public["scopes"] = scopes
        public["has_gmail"] = has_gmail_scopes(scopes)
        public["has_calendar"] = has_calendar_scopes(scopes)
        public["has_drive"] = has_drive_scopes(scopes)
        return public

    def invalidate_calendar_cache(self, account_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM calendar_list_cache WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM calendar_event_cache WHERE account_id = ?", (account_id,))

    def get_calendar_list_cache(
        self, cache_key: str, *, ttl_seconds: int = CACHE_TTL_SECONDS
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM calendar_list_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row or _is_stale(row["cached_at"], ttl_seconds):
            return None
        try:
            data = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def put_calendar_list_cache(
        self, cache_key: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO calendar_list_cache (cache_key, account_id, payload_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    cached_at = excluded.cached_at,
                    account_id = excluded.account_id
                """,
                (cache_key, account_id, json.dumps(payload), utcnow()),
            )

    def get_calendar_event_cache(
        self,
        account_id: str,
        calendar_id: str,
        event_id: str,
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM calendar_event_cache
                WHERE account_id = ? AND calendar_id = ? AND event_id = ?
                """,
                (account_id, calendar_id, event_id),
            ).fetchone()
        if not row or _is_stale(row["cached_at"], ttl_seconds):
            return None
        try:
            data = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def put_calendar_event_cache(
        self, account_id: str, calendar_id: str, event: dict[str, Any]
    ) -> None:
        event_id = str(event.get("id") or "")
        if not event_id:
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO calendar_event_cache
                    (account_id, calendar_id, event_id, payload_json, cached_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, calendar_id, event_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    cached_at = excluded.cached_at
                """,
                (account_id, calendar_id, event_id, json.dumps(event), utcnow()),
            )


def _is_stale(cached_at: str | None, ttl_seconds: int) -> bool:
    if not cached_at:
        return True
    try:
        when = datetime.fromisoformat(str(cached_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > when + timedelta(seconds=ttl_seconds)
