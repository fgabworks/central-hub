"""Read-only SELECT runner for Data Explorer."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from hub.data_explorer.security import ExplorerSafetyError
from hub.sql_workspace.connections import SqlConnectionProfile
from hub.sql_workspace.safety import SqlSafetyError, validate_readonly_sql

_NAMED = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
CancelCheck = Callable[[], bool]


def _to_postgres(sql: str) -> str:
    return _NAMED.sub(r"%(\1)s", sql)


class ExplorerRunner:
    def __init__(self, *, statement_timeout_ms: int = 30_000) -> None:
        self.statement_timeout_ms = int(statement_timeout_ms)

    def fetch(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        dialect: str,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        self._assert_readonly(sql, dialect)
        if profile.driver == "sqlite":
            return self._sqlite(profile, sql, params, cancel_check=cancel_check)
        return self._postgres(profile, _to_postgres(sql), params, cancel_check=cancel_check)

    def fetch_count(
        self, profile: SqlConnectionProfile, sql: str, params: dict[str, Any], *, dialect: str
    ) -> int:
        count_params = {k: v for k, v in params.items() if k not in ("row_limit", "row_offset")}
        _cols, rows = self.fetch(profile, sql, count_params, dialect=dialect)
        if not rows:
            return 0
        return int(rows[0][0] or 0)

    def _assert_readonly(self, sql: str, dialect: str) -> None:
        try:
            validated = validate_readonly_sql(sql, dialect=dialect)
        except SqlSafetyError as exc:
            raise ExplorerSafetyError(str(exc)) from exc
        if validated.kind not in {"select", "with", "explain"}:
            raise ExplorerSafetyError("Only SELECT/EXPLAIN permitted in Data Explorer")

    def _sqlite(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        path = profile.sqlite_path or ":memory:"
        conn = sqlite3.connect(str(Path(path)) if path != ":memory:" else ":memory:")
        try:
            try:
                conn.execute("PRAGMA query_only = ON")
            except sqlite3.Error:
                pass
            cur = conn.execute(sql, params)
            columns = [d[0] for d in (cur.description or [])]
            rows: list[list[Any]] = []
            while True:
                if cancel_check and cancel_check():
                    break
                batch = cur.fetchmany(200)
                if not batch:
                    break
                for row in batch:
                    rows.append(list(row))
            return columns, rows
        finally:
            conn.close()

    def _postgres(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        import psycopg

        conninfo = (
            f"host={profile.host} port={profile.port or 5432} dbname={profile.database} "
            f"user={profile.user} password={profile.password or ''} "
            f"sslmode={profile.sslmode or 'prefer'}"
        )
        conn = psycopg.connect(conninfo, connect_timeout=5, autocommit=False)
        try:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(int(self.statement_timeout_ms)),),
            )
            conn.execute("BEGIN READ ONLY")
            cur = conn.execute(sql, params)
            columns = [d.name for d in (cur.description or [])]
            rows: list[list[Any]] = []
            while True:
                if cancel_check and cancel_check():
                    break
                batch = cur.fetchmany(200)
                if not batch:
                    break
                for row in batch:
                    rows.append(list(row))
            conn.commit()
            return columns, rows
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            conn.close()
