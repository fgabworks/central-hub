"""SQLite store for Data Explorer favorites and browse/export audit trail."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.settings import ROOT_DIR


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ExplorerStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (ROOT_DIR / "data" / "data_explorer.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS explorer_favorites (
                    id TEXT PRIMARY KEY,
                    environment TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    object_name TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_explorer_fav_uniq
                  ON explorer_favorites(environment, schema_name, object_name, actor);

                CREATE TABLE IF NOT EXISTS explorer_audit (
                    id TEXT PRIMARY KEY,
                    event TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    object_ref TEXT,
                    detail_json TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_explorer_audit_created
                  ON explorer_audit(created_at DESC);
                """
            )

    def add_favorite(
        self,
        *,
        environment: str,
        schema: str,
        object_name: str,
        object_type: str,
        actor: str,
    ) -> dict[str, Any]:
        fid = f"exf_{uuid.uuid4().hex[:10]}"
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO explorer_favorites
                (id, environment, schema_name, object_name, object_type, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (fid, environment, schema, object_name, object_type, actor, now),
            )
        return {
            "id": fid,
            "environment": environment,
            "schema": schema,
            "object_name": object_name,
            "object_type": object_type,
            "actor": actor,
            "created_at": now,
        }

    def list_favorites(self, *, environment: str | None = None, actor: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            sql = "SELECT * FROM explorer_favorites WHERE 1=1"
            params: list[Any] = []
            if environment:
                sql += " AND environment = ?"
                params.append(environment)
            if actor:
                sql += " AND actor = ?"
                params.append(actor)
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "environment": r["environment"],
                "schema": r["schema_name"],
                "object_name": r["object_name"],
                "object_type": r["object_type"],
                "actor": r["actor"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def remove_favorite(self, favorite_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM explorer_favorites WHERE id = ?", (favorite_id,))
            return cur.rowcount > 0

    def audit(
        self,
        *,
        event: str,
        actor: str,
        environment: str,
        object_ref: str | None,
        detail: dict[str, Any],
        ok: bool = True,
    ) -> None:
        # Never store row values
        safe = {k: v for k, v in (detail or {}).items() if k not in {"rows", "sample", "data"}}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO explorer_audit
                (id, event, actor, environment, object_ref, detail_json, ok, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"exa_{uuid.uuid4().hex[:12]}",
                    event,
                    actor,
                    environment,
                    object_ref,
                    json.dumps(safe, ensure_ascii=True, sort_keys=True, default=str),
                    1 if ok else 0,
                    utcnow(),
                ),
            )

    def list_audit(self, *, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM explorer_audit ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "event": r["event"],
                "actor": r["actor"],
                "environment": r["environment"],
                "object_ref": r["object_ref"],
                "detail": json.loads(r["detail_json"] or "{}"),
                "ok": bool(r["ok"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
