"""Persistent organisation-unit cache (SQLite), Stage/Live isolated."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

# Hierarchy metadata changes slowly; serve local rows immediately and refresh in background.
DEFAULT_STALE_SECONDS = 6 * 60 * 60


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_org_unit_db_path() -> Path:
    return Path(ROOT_DIR) / "data" / "dhis2_org_units.db"


class OrgUnitStore:
    """Environment-scoped OU rows for cascade/search. Never mixes Stage and Live."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_org_unit_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS org_units (
                    environment TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT '',
                    level INTEGER,
                    parent_uid TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    path_label TEXT NOT NULL DEFAULT '',
                    has_children INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL DEFAULT '',
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (environment, uid)
                );
                CREATE INDEX IF NOT EXISTS idx_ou_env_parent
                    ON org_units(environment, parent_uid, name);
                CREATE INDEX IF NOT EXISTS idx_ou_env_level
                    ON org_units(environment, level, name);
                CREATE INDEX IF NOT EXISTS idx_ou_env_name
                    ON org_units(environment, name);
                CREATE INDEX IF NOT EXISTS idx_ou_env_code
                    ON org_units(environment, code);

                CREATE TABLE IF NOT EXISTS org_unit_scopes (
                    environment TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    unit_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (environment, scope_key)
                );

                CREATE TABLE IF NOT EXISTS org_unit_env_meta (
                    environment TEXT PRIMARY KEY,
                    last_sync_at TEXT NOT NULL DEFAULT '',
                    last_sync_status TEXT NOT NULL DEFAULT '',
                    last_sync_error TEXT NOT NULL DEFAULT '',
                    unit_count INTEGER NOT NULL DEFAULT 0,
                    sync_started_at TEXT NOT NULL DEFAULT ''
                );
                """
            )

    @staticmethod
    def scope_key(*, level: int | None = None, parent_uid: str = "", q: str = "") -> str:
        needle = (q or "").strip().lower()
        parent = (parent_uid or "").strip()
        if parent:
            return f"parent:{parent}"
        if level is not None and not needle:
            return f"level:{int(level)}"
        if needle:
            return f"q:{needle}"
        return "unknown"

    def get_scope(self, environment: str, scope_key: str) -> dict[str, Any] | None:
        env = (environment or "").strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT environment, scope_key, synced_at, unit_count "
                "FROM org_unit_scopes WHERE environment = ? AND scope_key = ?",
                (env, scope_key),
            ).fetchone()
        return dict(row) if row else None

    def is_scope_stale(
        self,
        environment: str,
        scope_key: str,
        *,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
    ) -> bool:
        meta = self.get_scope(environment, scope_key)
        if not meta or not meta.get("synced_at"):
            return True
        try:
            stamp = datetime.strptime(meta["synced_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return age > float(stale_seconds)

    def mark_scope(
        self,
        environment: str,
        scope_key: str,
        *,
        unit_count: int,
        synced_at: str | None = None,
    ) -> str:
        env = (environment or "").strip().lower()
        stamp = (synced_at or "").strip() or utcnow_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO org_unit_scopes(environment, scope_key, synced_at, unit_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(environment, scope_key) DO UPDATE SET
                    synced_at = excluded.synced_at,
                    unit_count = excluded.unit_count
                """,
                (env, scope_key, stamp, int(unit_count)),
            )
        return stamp

    def clear_scope(self, environment: str, scope_key: str | None = None) -> int:
        env = (environment or "").strip().lower()
        with self.connect() as conn:
            if scope_key:
                cur = conn.execute(
                    "DELETE FROM org_unit_scopes WHERE environment = ? AND scope_key = ?",
                    (env, scope_key),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM org_unit_scopes WHERE environment = ?",
                    (env,),
                )
            return int(cur.rowcount or 0)

    def upsert_rows(
        self,
        environment: str,
        rows: list[dict[str, Any]],
        *,
        parent_uid: str = "",
        synced_at: str | None = None,
    ) -> str:
        env = (environment or "").strip().lower()
        stamp = (synced_at or "").strip() or utcnow_iso()
        parent = (parent_uid or "").strip()
        with self.connect() as conn:
            for row in rows:
                uid = str(row.get("id") or row.get("uid") or "").strip()
                if not uid:
                    continue
                level = row.get("level")
                try:
                    level_i = int(level) if level is not None else None
                except (TypeError, ValueError):
                    level_i = None
                has_children = 1 if row.get("has_children") else 0
                path_label = str(row.get("path_label") or row.get("path") or row.get("name") or "")
                conn.execute(
                    """
                    INSERT INTO org_units(
                        environment, uid, name, code, level, parent_uid, path, path_label,
                        has_children, last_updated, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(environment, uid) DO UPDATE SET
                        name = excluded.name,
                        code = COALESCE(NULLIF(excluded.code, ''), org_units.code),
                        level = COALESCE(excluded.level, org_units.level),
                        parent_uid = CASE
                            WHEN excluded.parent_uid != '' THEN excluded.parent_uid
                            ELSE org_units.parent_uid
                        END,
                        path = COALESCE(NULLIF(excluded.path, ''), org_units.path),
                        path_label = COALESCE(NULLIF(excluded.path_label, ''), org_units.path_label),
                        has_children = excluded.has_children,
                        last_updated = COALESCE(NULLIF(excluded.last_updated, ''), org_units.last_updated),
                        synced_at = excluded.synced_at
                    """,
                    (
                        env,
                        uid,
                        str(row.get("name") or uid),
                        str(row.get("code") or ""),
                        level_i,
                        parent or str(row.get("parent_uid") or row.get("parent_id") or ""),
                        str(row.get("path") or ""),
                        path_label,
                        has_children,
                        str(row.get("last_updated") or ""),
                        stamp,
                    ),
                )
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM org_units WHERE environment = ?",
                (env,),
            ).fetchone()["c"]
            conn.execute(
                """
                INSERT INTO org_unit_env_meta(
                    environment, last_sync_at, last_sync_status, last_sync_error, unit_count, sync_started_at
                ) VALUES (?, ?, 'ok', '', ?, '')
                ON CONFLICT(environment) DO UPDATE SET
                    last_sync_at = excluded.last_sync_at,
                    last_sync_status = 'ok',
                    last_sync_error = '',
                    unit_count = excluded.unit_count
                """,
                (env, stamp, int(count)),
            )
        return stamp

    def set_env_sync_state(
        self,
        environment: str,
        *,
        status: str,
        error: str = "",
        started: bool = False,
    ) -> None:
        env = (environment or "").strip().lower()
        now = utcnow_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT unit_count FROM org_unit_env_meta WHERE environment = ?",
                (env,),
            ).fetchone()
            count = int(row["unit_count"]) if row else 0
            if started:
                conn.execute(
                    """
                    INSERT INTO org_unit_env_meta(
                        environment, last_sync_at, last_sync_status, last_sync_error,
                        unit_count, sync_started_at
                    ) VALUES (?, '', ?, ?, ?, ?)
                    ON CONFLICT(environment) DO UPDATE SET
                        last_sync_status = excluded.last_sync_status,
                        last_sync_error = excluded.last_sync_error,
                        sync_started_at = excluded.sync_started_at
                    """,
                    (env, status, error[:400], count, now),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO org_unit_env_meta(
                        environment, last_sync_at, last_sync_status, last_sync_error,
                        unit_count, sync_started_at
                    ) VALUES (?, ?, ?, ?, ?, '')
                    ON CONFLICT(environment) DO UPDATE SET
                        last_sync_at = excluded.last_sync_at,
                        last_sync_status = excluded.last_sync_status,
                        last_sync_error = excluded.last_sync_error,
                        unit_count = excluded.unit_count,
                        sync_started_at = ''
                    """,
                    (env, now, status, error[:400], count),
                )

    def env_meta(self, environment: str) -> dict[str, Any]:
        env = (environment or "").strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM org_unit_env_meta WHERE environment = ?",
                (env,),
            ).fetchone()
        return dict(row) if row else {
            "environment": env,
            "last_sync_at": "",
            "last_sync_status": "",
            "last_sync_error": "",
            "unit_count": 0,
            "sync_started_at": "",
        }

    def get(self, environment: str, uid: str) -> dict[str, Any] | None:
        env = (environment or "").strip().lower()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM org_units WHERE environment = ? AND uid = ?",
                (env, (uid or "").strip()),
            ).fetchone()
        return self._to_api_row(row) if row else None

    def list_children(
        self, environment: str, parent_uid: str, *, limit: int = 300
    ) -> list[dict[str, Any]]:
        env = (environment or "").strip().lower()
        parent = (parent_uid or "").strip()
        lim = max(1, min(int(limit or 300), 500))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM org_units
                WHERE environment = ? AND parent_uid = ?
                ORDER BY name COLLATE NOCASE
                LIMIT ?
                """,
                (env, parent, lim),
            ).fetchall()
        return [self._to_api_row(r) for r in rows]

    def list_level(
        self, environment: str, level: int, *, limit: int = 80
    ) -> list[dict[str, Any]]:
        env = (environment or "").strip().lower()
        lim = max(1, min(int(limit or 80), 200))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM org_units
                WHERE environment = ? AND level = ?
                ORDER BY name COLLATE NOCASE
                LIMIT ?
                """,
                (env, int(level), lim),
            ).fetchall()
        return [self._to_api_row(r) for r in rows]

    def search(
        self, environment: str, q: str, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        env = (environment or "").strip().lower()
        needle = (q or "").strip()
        lim = max(1, min(int(limit or 25), 50))
        if not needle:
            return []
        if len(needle) == 11 and needle.isalnum():
            hit = self.get(env, needle)
            return [hit] if hit else []
        like = f"%{needle}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM org_units
                WHERE environment = ?
                  AND (
                    name LIKE ? ESCAPE '\\'
                    OR code LIKE ? ESCAPE '\\'
                    OR uid LIKE ? ESCAPE '\\'
                    OR path_label LIKE ? ESCAPE '\\'
                  )
                ORDER BY
                  CASE WHEN lower(name) = lower(?) THEN 0
                       WHEN lower(code) = lower(?) THEN 1
                       ELSE 2 END,
                  name COLLATE NOCASE
                LIMIT ?
                """,
                (env, like, like, like, like, needle, needle, lim),
            ).fetchall()
        return [self._to_api_row(r) for r in rows]

    @staticmethod
    def _to_api_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        uid = data.get("uid") or ""
        level = data.get("level")
        try:
            level_i = int(level) if level is not None else None
        except (TypeError, ValueError):
            level_i = None
        level_labels = {
            2: "region",
            3: "province",
            4: "municipality_city",
            5: "barangay",
        }
        return {
            "id": uid,
            "uid": uid,
            "name": data.get("name") or uid,
            "code": data.get("code") or "",
            "path": data.get("path") or "",
            "path_label": data.get("path_label") or data.get("path") or data.get("name") or "",
            "level": level_i,
            "level_label": level_labels.get(level_i or -1, ""),
            "parent_uid": data.get("parent_uid") or "",
            "has_children": bool(data.get("has_children")),
            "last_updated": data.get("last_updated") or "",
            "synced_at": data.get("synced_at") or "",
        }
