"""SQLite persistence for Gmail accounts, OAuth state, and limited message cache."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_email_gmail_initial",
        """
        CREATE TABLE IF NOT EXISTS gmail_accounts (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL DEFAULT '',
            google_sub TEXT NOT NULL DEFAULT '',
            workspace TEXT NOT NULL DEFAULT 'work',
            status TEXT NOT NULL DEFAULT 'connected',
            token_encrypted TEXT NOT NULL DEFAULT '',
            access_expires_at TEXT,
            scopes TEXT NOT NULL DEFAULT '',
            connected_at TEXT,
            last_sync_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gmail_accounts_workspace
            ON gmail_accounts(workspace);
        CREATE INDEX IF NOT EXISTS idx_gmail_accounts_email
            ON gmail_accounts(email);

        CREATE TABLE IF NOT EXISTS gmail_oauth_states (
            state TEXT PRIMARY KEY,
            workspace TEXT NOT NULL,
            account_id TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gmail_message_cache (
            account_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            thread_id TEXT NOT NULL DEFAULT '',
            label_ids_json TEXT NOT NULL DEFAULT '[]',
            snippet TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            from_addr TEXT NOT NULL DEFAULT '',
            to_addr TEXT NOT NULL DEFAULT '',
            date_header TEXT NOT NULL DEFAULT '',
            internal_date TEXT NOT NULL DEFAULT '',
            is_unread INTEGER NOT NULL DEFAULT 0,
            is_starred INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '',
            cached_at TEXT NOT NULL,
            PRIMARY KEY (account_id, message_id),
            FOREIGN KEY(account_id) REFERENCES gmail_accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_gmail_msg_thread
            ON gmail_message_cache(account_id, thread_id);

        CREATE TABLE IF NOT EXISTS gmail_list_cache (
            cache_key TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES gmail_accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_gmail_list_account
            ON gmail_list_cache(account_id);
        """,
    ),
    (
        "002_google_calendar_scopes_cache",
        """
        ALTER TABLE gmail_oauth_states ADD COLUMN requested_scopes TEXT NOT NULL DEFAULT '';

        CREATE TABLE IF NOT EXISTS calendar_list_cache (
            cache_key TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES gmail_accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cal_list_account
            ON calendar_list_cache(account_id);

        CREATE TABLE IF NOT EXISTS calendar_event_cache (
            account_id TEXT NOT NULL,
            calendar_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (account_id, calendar_id, event_id),
            FOREIGN KEY(account_id) REFERENCES gmail_accounts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_cal_event_account
            ON calendar_event_cache(account_id);
        """,
    ),
]


def default_email_db_path() -> Path:
    return ROOT_DIR / "data" / "email.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmailDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_email_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["name"]
                for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for name, script in _MIGRATIONS:
                if name in applied:
                    continue
                conn.executescript(script)
                conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (name, utcnow()),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
