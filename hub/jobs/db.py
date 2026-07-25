"""SQLite persistence for Central Hub jobs (Phase 2+)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hub.settings import ROOT_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 1,
    confirmed INTEGER NOT NULL DEFAULT 0,
    phase TEXT,
    message TEXT,
    percent REAL DEFAULT 0,
    resumable INTEGER DEFAULT 0,
    cancel_requested INTEGER DEFAULT 0,
    pause_requested INTEGER DEFAULT 0,
    actor TEXT DEFAULT 'owner',
    input_path TEXT,
    result_path TEXT,
    log_path TEXT,
    checkpoint_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

_LOCK = threading.RLock()


def default_db_path() -> Path:
    return ROOT_DIR / "data" / "hub.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()


def row_to_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["dry_run"] = bool(data.get("dry_run"))
    data["confirmed"] = bool(data.get("confirmed"))
    data["resumable"] = bool(data.get("resumable"))
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    data["pause_requested"] = bool(data.get("pause_requested"))
    meta = data.get("metadata_json")
    try:
        data["metadata"] = json.loads(meta) if meta else {}
    except json.JSONDecodeError:
        data["metadata"] = {}
    checkpoint = data.get("checkpoint_json")
    try:
        data["checkpoint"] = json.loads(checkpoint) if checkpoint else {}
    except json.JSONDecodeError:
        data["checkpoint"] = {}
    return data
