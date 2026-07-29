"""SQLite store for repository-specific run profiles (overrides YAML templates)."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hub.settings import ROOT_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_profiles (
    repo_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    approved INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'user',
    description TEXT NOT NULL DEFAULT '',
    environments_json TEXT NOT NULL,
    executable TEXT NOT NULL,
    args_json TEXT NOT NULL,
    working_directory TEXT NOT NULL DEFAULT '{repository_path}',
    startup_timeout_seconds REAL NOT NULL DEFAULT 30,
    local_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:{port}/',
    health_url TEXT,
    provides_api INTEGER NOT NULL DEFAULT 0,
    live_profile INTEGER NOT NULL DEFAULT 0,
    write_capable INTEGER NOT NULL DEFAULT 0,
    port_mode TEXT NOT NULL DEFAULT 'none',
    fixed_port INTEGER,
    default_port INTEGER,
    port_arg TEXT,
    port_env TEXT,
    allowed_env_names_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repo_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_run_profiles_repo
    ON run_profiles(repo_id, enabled, approved);
"""


def default_profile_db_path() -> Path:
    configured = (os.environ.get("REPO_WS_PROFILE_DATABASE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "data" / "repository_workspace.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunProfileStore:
    """Per-repository run profiles stored in Central Hub SQLite."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_profile_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
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
            conn.executescript(SCHEMA)
            applied = {
                str(r["name"])
                for r in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            if "001_run_profiles" not in applied:
                conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    ("001_run_profiles", utcnow()),
                )

    def list_for_repo(
        self,
        repo_id: str,
        *,
        include_unapproved: bool = True,
    ) -> list[dict[str, Any]]:
        rid = (repo_id or "").strip()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_profiles
                WHERE repo_id = ?
                ORDER BY name COLLATE NOCASE, profile_id
                """,
                (rid,),
            ).fetchall()
        out = [self._row_to_dict(r) for r in rows]
        if not include_unapproved:
            out = [p for p in out if p.get("approved")]
        return out

    def get(self, repo_id: str, profile_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM run_profiles
                WHERE repo_id = ? AND profile_id = ?
                """,
                ((repo_id or "").strip(), (profile_id or "").strip()),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def upsert(self, repo_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        rid = (repo_id or "").strip()
        pid = str(payload.get("id") or payload.get("profile_id") or "").strip()
        if not rid or not pid:
            raise ValueError("repo_id and profile_id are required")
        now = utcnow()
        existing = self.get(rid, pid)
        created = existing["created_at"] if existing else now
        record = {
            "repo_id": rid,
            "profile_id": pid,
            "name": str(payload.get("name") or pid).strip(),
            "enabled": 1 if payload.get("enabled", True) else 0,
            "approved": 1 if payload.get("approved", True) else 0,
            "source": str(payload.get("source") or "user").strip() or "user",
            "description": str(payload.get("description") or "").strip(),
            "environments_json": json.dumps(
                list(payload.get("environments") or ["development"]),
                ensure_ascii=False,
            ),
            "executable": str(payload.get("executable") or "").strip(),
            "args_json": json.dumps(list(payload.get("args") or []), ensure_ascii=False),
            "working_directory": str(
                payload.get("working_directory") or "{repository_path}"
            ).strip(),
            "startup_timeout_seconds": float(payload.get("startup_timeout_seconds") or 30),
            "local_url": str(
                payload.get("local_url") or "http://127.0.0.1:{port}/"
            ).strip(),
            "health_url": (
                str(payload["health_url"]).strip()
                if payload.get("health_url") not in (None, "")
                else None
            ),
            "provides_api": 1 if payload.get("provides_api") else 0,
            "live_profile": 1 if payload.get("live_profile") else 0,
            "write_capable": 1 if payload.get("write_capable") else 0,
            "port_mode": str(payload.get("port_mode") or "none").strip().lower(),
            "fixed_port": payload.get("fixed_port"),
            "default_port": payload.get("default_port"),
            "port_arg": (
                str(payload["port_arg"]).strip() if payload.get("port_arg") else None
            ),
            "port_env": (
                str(payload["port_env"]).strip() if payload.get("port_env") else None
            ),
            "allowed_env_names_json": json.dumps(
                list(payload.get("allowed_env_names") or []),
                ensure_ascii=False,
            ),
            "created_at": created,
            "updated_at": now,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO run_profiles (
                    repo_id, profile_id, name, enabled, approved, source, description,
                    environments_json, executable, args_json, working_directory,
                    startup_timeout_seconds, local_url, health_url, provides_api,
                    live_profile, write_capable, port_mode, fixed_port, default_port,
                    port_arg, port_env, allowed_env_names_json, created_at, updated_at
                ) VALUES (
                    :repo_id, :profile_id, :name, :enabled, :approved, :source, :description,
                    :environments_json, :executable, :args_json, :working_directory,
                    :startup_timeout_seconds, :local_url, :health_url, :provides_api,
                    :live_profile, :write_capable, :port_mode, :fixed_port, :default_port,
                    :port_arg, :port_env, :allowed_env_names_json, :created_at, :updated_at
                )
                ON CONFLICT(repo_id, profile_id) DO UPDATE SET
                    name=excluded.name,
                    enabled=excluded.enabled,
                    approved=excluded.approved,
                    source=excluded.source,
                    description=excluded.description,
                    environments_json=excluded.environments_json,
                    executable=excluded.executable,
                    args_json=excluded.args_json,
                    working_directory=excluded.working_directory,
                    startup_timeout_seconds=excluded.startup_timeout_seconds,
                    local_url=excluded.local_url,
                    health_url=excluded.health_url,
                    provides_api=excluded.provides_api,
                    live_profile=excluded.live_profile,
                    write_capable=excluded.write_capable,
                    port_mode=excluded.port_mode,
                    fixed_port=excluded.fixed_port,
                    default_port=excluded.default_port,
                    port_arg=excluded.port_arg,
                    port_env=excluded.port_env,
                    allowed_env_names_json=excluded.allowed_env_names_json,
                    updated_at=excluded.updated_at
                """,
                record,
            )
        got = self.get(rid, pid)
        assert got is not None
        return got

    def delete(self, repo_id: str, profile_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM run_profiles WHERE repo_id = ? AND profile_id = ?",
                ((repo_id or "").strip(), (profile_id or "").strip()),
            )
            return cur.rowcount > 0

    def set_enabled(self, repo_id: str, profile_id: str, enabled: bool) -> dict[str, Any] | None:
        row = self.get(repo_id, profile_id)
        if not row:
            return None
        row["enabled"] = bool(enabled)
        return self.upsert(repo_id, row)

    def set_approved(self, repo_id: str, profile_id: str, approved: bool) -> dict[str, Any] | None:
        row = self.get(repo_id, profile_id)
        if not row:
            return None
        row["approved"] = bool(approved)
        if approved and row.get("source") == "suggestion":
            row["source"] = "user"
        return self.upsert(repo_id, row)

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        envs = json.loads(str(row["environments_json"] or "[]"))
        args = json.loads(str(row["args_json"] or "[]"))
        env_names = json.loads(str(row["allowed_env_names_json"] or "[]"))
        return {
            "id": str(row["profile_id"]),
            "profile_id": str(row["profile_id"]),
            "repo_id": str(row["repo_id"]),
            "name": str(row["name"] or ""),
            "enabled": bool(row["enabled"]),
            "approved": bool(row["approved"]),
            "source": str(row["source"] or "user"),
            "description": str(row["description"] or ""),
            "environments": list(envs),
            "executable": str(row["executable"] or ""),
            "args": list(args),
            "working_directory": str(row["working_directory"] or "{repository_path}"),
            "startup_timeout_seconds": float(row["startup_timeout_seconds"] or 30),
            "local_url": str(row["local_url"] or ""),
            "health_url": str(row["health_url"]) if row["health_url"] else None,
            "provides_api": bool(row["provides_api"]),
            "live_profile": bool(row["live_profile"]),
            "write_capable": bool(row["write_capable"]),
            "port_mode": str(row["port_mode"] or "none"),
            "fixed_port": int(row["fixed_port"]) if row["fixed_port"] is not None else None,
            "default_port": int(row["default_port"]) if row["default_port"] is not None else None,
            "port_arg": str(row["port_arg"]) if row["port_arg"] else None,
            "port_env": str(row["port_env"]) if row["port_env"] else None,
            "allowed_env_names": list(env_names),
            "repository_ids": [str(row["repo_id"])],
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
