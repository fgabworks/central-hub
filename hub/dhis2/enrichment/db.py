"""SQLite persistence for enriched DHIS2 metadata + relationships."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hub.settings import ROOT_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_objects (
    uid TEXT NOT NULL,
    object_type TEXT NOT NULL,
    environment TEXT NOT NULL DEFAULT '',
    name TEXT,
    short_name TEXT,
    code TEXT,
    description TEXT,
    form_name TEXT,
    domain_type TEXT,
    value_type TEXT,
    aggregation_type TEXT,
    answer_type TEXT,
    zero_is_significant INTEGER,
    option_set_value INTEGER,
    option_set_uid TEXT,
    option_set_name TEXT,
    category_combo_uid TEXT,
    category_combo_name TEXT,
    analytics_type TEXT,
    decimals TEXT,
    expression TEXT,
    filter TEXT,
    program_uid TEXT,
    program_name TEXT,
    checksum TEXT,
    fetched_at TEXT,
    audit_statuses TEXT,
    summary_json TEXT,
    raw_json TEXT,
    snapshot_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, uid)
);
CREATE INDEX IF NOT EXISTS idx_meta_obj_type ON metadata_objects(object_type);
CREATE INDEX IF NOT EXISTS idx_meta_obj_answer ON metadata_objects(answer_type);
CREATE INDEX IF NOT EXISTS idx_meta_obj_program ON metadata_objects(program_uid);
CREATE INDEX IF NOT EXISTS idx_meta_obj_os ON metadata_objects(option_set_uid);
CREATE INDEX IF NOT EXISTS idx_meta_obj_latest ON metadata_objects(snapshot_id);

CREATE TABLE IF NOT EXISTS metadata_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    from_uid TEXT NOT NULL,
    from_type TEXT NOT NULL,
    to_uid TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_name TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON metadata_relationships(snapshot_id, from_uid);
CREATE INDEX IF NOT EXISTS idx_rel_to ON metadata_relationships(snapshot_id, to_uid);
CREATE INDEX IF NOT EXISTS idx_rel_type ON metadata_relationships(snapshot_id, rel_type);

CREATE TABLE IF NOT EXISTS option_set_options (
    snapshot_id TEXT NOT NULL,
    option_set_uid TEXT NOT NULL,
    option_uid TEXT NOT NULL,
    name TEXT,
    code TEXT,
    sort_order INTEGER,
    color TEXT,
    icon TEXT,
    PRIMARY KEY (snapshot_id, option_set_uid, option_uid)
);

CREATE TABLE IF NOT EXISTS enrichment_snapshots (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    environment TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    object_count INTEGER DEFAULT 0,
    relationship_count INTEGER DEFAULT 0,
    stats_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS enrichment_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    phase TEXT,
    message TEXT,
    percent REAL DEFAULT 0,
    cancel_requested INTEGER DEFAULT 0,
    environment TEXT,
    preview_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
"""

_LOCK = threading.RLock()


def default_enrichment_db_path() -> Path:
    return ROOT_DIR / "data" / "dhis2" / "enrichment.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnrichmentDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_enrichment_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
