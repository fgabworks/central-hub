"""SQLite persistence for Agent Center runs and saved prompts."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_agent_center_initial",
        """
        CREATE TABLE IF NOT EXISTS agent_prompts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Untitled prompt',
            body TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'ask',
            tags_json TEXT NOT NULL DEFAULT '[]',
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_prompts_updated ON agent_prompts(updated_at DESC);

        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            agent_label TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            repository_ids_json TEXT NOT NULL DEFAULT '[]',
            prompt TEXT NOT NULL DEFAULT '',
            packed_prompt TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            answer TEXT NOT NULL DEFAULT '',
            logs TEXT NOT NULL DEFAULT '',
            referenced_files_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            pid INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
        """,
    ),
    (
        "002_agent_center_openai_fields",
        """
        ALTER TABLE agent_runs ADD COLUMN tool_activity_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE agent_runs ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        "003_assistant_profiles",
        """
        ALTER TABLE agent_prompts ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'okarun';
        ALTER TABLE agent_runs ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'okarun';
        ALTER TABLE agent_runs ADD COLUMN conversation_id TEXT NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_agent_runs_profile_created
            ON agent_runs(profile_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS agent_conversations (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New conversation',
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_conversations_profile_updated
            ON agent_conversations(profile_id, updated_at DESC);
        """,
    ),
    (
        "004_ai_connections",
        """
        CREATE TABLE IF NOT EXISTS agent_connections (
            agent_id TEXT PRIMARY KEY,
            disconnected INTEGER NOT NULL DEFAULT 0,
            last_check TEXT NOT NULL DEFAULT '',
            last_successful_check TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """,
    ),
]


def default_agent_db_path() -> Path:
    return ROOT_DIR / "data" / "agent_center.db"


class AgentCenterDb:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_agent_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            for mid, sql in _MIGRATIONS:
                if mid in applied:
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(id, applied_at) VALUES (?, ?)", (mid, now))
