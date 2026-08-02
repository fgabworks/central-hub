"""Read-only query runner for Live Data Export (dedicated RO profiles only)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from hub.live_data_export.query import BuiltQuery
from hub.live_data_export.security import ExportSafetyError
from hub.sql_workspace.connections import SqlConnectionProfile
from hub.sql_workspace.safety import SqlSafetyError, validate_readonly_sql

_NAMED = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
CancelCheck = Callable[[], bool]


def _to_postgres(sql: str) -> str:
    return _NAMED.sub(r"%(\1)s", sql)


class ExportRunner:
    """Execute allowlisted SELECT/COUNT with parameterized binds. SELECT only."""

    def __init__(self, *, statement_timeout_ms: int = 60_000) -> None:
        self.statement_timeout_ms = int(statement_timeout_ms)

    def fetch_all(
        self,
        profile: SqlConnectionProfile,
        built: BuiltQuery,
        *,
        cancel_check: CancelCheck | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        self._assert_readonly(built.sql, built.dialect)
        params = {k: v for k, v in built.params.items()}
        if profile.driver == "sqlite":
            return self._sqlite(profile, built.sql, params, cancel_check=cancel_check)
        return self._postgres(profile, _to_postgres(built.sql), params, cancel_check=cancel_check)

    def fetch_count(self, profile: SqlConnectionProfile, built: BuiltQuery) -> int:
        self._assert_readonly(built.count_sql, built.dialect)
        count_params = built.filters.get("_count_params") or {
            k: v for k, v in built.params.items() if k != "row_limit"
        }
        if profile.driver == "sqlite":
            cols, rows = self._sqlite(profile, built.count_sql, count_params)
        else:
            cols, rows = self._postgres(profile, _to_postgres(built.count_sql), count_params)
        _ = cols
        if not rows:
            return 0
        return int(rows[0][0] or 0)

    def _assert_readonly(self, sql: str, dialect: str) -> None:
        try:
            validated = validate_readonly_sql(sql, dialect=dialect)
        except SqlSafetyError as exc:
            raise ExportSafetyError(str(exc)) from exc
        if validated.kind not in {"select", "with"}:
            raise ExportSafetyError("Only SELECT queries are permitted for export")

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
                    rows.append([_cell(v) for v in row])
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
                    rows.append([_cell(v) for v in row])
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


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value
