"""Safe read-only SQL execution with timeout, row cap, and cancellation."""

from __future__ import annotations

import csv
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hub.settings import ROOT_DIR
from hub.sql_workspace.connections import SqlConnectionProfile, public_error
from hub.sql_workspace.safety import (
    SqlSafetyError,
    bind_named_params,
    validate_readonly_sql,
)
from hub.sql_workspace.store import SqlWorkspaceStore


DEFAULT_MAX_ROWS = 1000
DEFAULT_PAGE_SIZE = 100
DEFAULT_STATEMENT_TIMEOUT_MS = 15_000


@dataclass
class ExecuteResult:
    ok: bool
    run_id: str
    status: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0
    error: str | None = None
    kind: str = ""
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    total_rows: int = 0


class SqlExecutor:
    def __init__(
        self,
        store: SqlWorkspaceStore,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        results_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.max_rows = max(1, min(int(max_rows), 10_000))
        self.statement_timeout_ms = max(500, int(statement_timeout_ms))
        self.results_dir = results_dir or (ROOT_DIR / "data" / "sql_results")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._pg_lock = threading.Lock()
        self._active_pg: dict[str, Any] = {}

    def test_connection(self, profile: SqlConnectionProfile) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            if profile.driver == "sqlite":
                path = profile.sqlite_path or ""
                if path == ":memory:":
                    conn = sqlite3.connect(":memory:")
                else:
                    conn = sqlite3.connect(str(Path(path)), timeout=5)
                try:
                    conn.execute("SELECT 1").fetchone()
                finally:
                    conn.close()
            else:
                import psycopg

                conninfo = self._pg_conninfo(profile)
                with psycopg.connect(conninfo, connect_timeout=5) as conn:
                    conn.execute("SELECT 1")
            ms = (time.perf_counter() - started) * 1000
            return {"ok": True, "latency_ms": round(ms, 1), "detail": "Connection OK"}
        except Exception as exc:  # noqa: BLE001
            ms = (time.perf_counter() - started) * 1000
            return {
                "ok": False,
                "latency_ms": round(ms, 1),
                "detail": public_error(str(exc), profile),
            }

    def execute(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        *,
        params: dict[str, Any] | None = None,
        query_id: str = "",
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        explain: bool = False,
    ) -> ExecuteResult:
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), self.max_rows))

        dialect = "sqlite" if profile.driver == "sqlite" else "postgres"
        to_validate = sql
        if explain and not sql.strip().upper().startswith("EXPLAIN"):
            to_validate = f"EXPLAIN {sql}"

        try:
            validated = validate_readonly_sql(to_validate, dialect=dialect)
            bound_sql, bound_params = bind_named_params(validated.sql, params)
        except SqlSafetyError as exc:
            run = self.store.create_run(
                connection_id=profile.id,
                sql_text=sql,
                environment=profile.environment,
                query_id=query_id,
                params=params,
                status="error",
            )
            self.store.finish_run(
                run["id"], status="error", error=str(exc), duration_ms=0, row_count=0
            )
            return ExecuteResult(
                ok=False,
                run_id=run["id"],
                status="error",
                error=str(exc),
            )

        run = self.store.create_run(
            connection_id=profile.id,
            sql_text=bound_sql,
            environment=profile.environment,
            query_id=query_id,
            params=bound_params,
            status="running",
        )
        run_id = run["id"]
        started = time.perf_counter()
        try:
            columns, all_rows, truncated = self._run_query(
                profile, bound_sql, bound_params, run_id=run_id
            )
            duration_ms = (time.perf_counter() - started) * 1000
            if self.store.is_cancel_requested(run_id):
                self.store.finish_run(
                    run_id,
                    status="cancelled",
                    row_count=len(all_rows),
                    duration_ms=duration_ms,
                    columns=columns,
                    error="Cancelled",
                )
                return ExecuteResult(
                    ok=False,
                    run_id=run_id,
                    status="cancelled",
                    columns=columns,
                    rows=[],
                    row_count=len(all_rows),
                    duration_ms=duration_ms,
                    error="Cancelled",
                    kind=validated.kind,
                )

            result_path = self._write_csv(run_id, columns, all_rows)
            self.store.finish_run(
                run_id,
                status="ok",
                row_count=len(all_rows),
                duration_ms=duration_ms,
                columns=columns,
                result_path=str(result_path),
            )
            start = (page - 1) * page_size
            end = start + page_size
            page_rows = all_rows[start:end]
            return ExecuteResult(
                ok=True,
                run_id=run_id,
                status="ok",
                columns=columns,
                rows=page_rows,
                row_count=len(page_rows),
                truncated=truncated,
                duration_ms=duration_ms,
                kind=validated.kind,
                page=page,
                page_size=page_size,
                total_rows=len(all_rows),
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - started) * 1000
            message = public_error(str(exc), profile)
            status = "cancelled" if self.store.is_cancel_requested(run_id) else "error"
            self.store.finish_run(
                run_id,
                status=status,
                duration_ms=duration_ms,
                error=message,
            )
            return ExecuteResult(
                ok=False,
                run_id=run_id,
                status=status,
                duration_ms=duration_ms,
                error=message,
                kind=validated.kind,
            )

    def cancel(self, run_id: str) -> bool:
        ok = self.store.request_cancel(run_id)
        with self._pg_lock:
            conn = self._active_pg.get(run_id)
        if conn is not None:
            try:
                conn.cancel()
            except Exception:  # noqa: BLE001
                pass
        return ok

    def export_csv_path(self, run_id: str) -> Path | None:
        run = self.store.get_run(run_id)
        if not run:
            return None
        path = (run.get("result_path") or "").strip()
        if not path:
            return None
        p = Path(path)
        if not p.is_file():
            return None
        # Jail under results dir
        try:
            p.resolve().relative_to(self.results_dir.resolve())
        except ValueError:
            return None
        return p

    def _run_query(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        run_id: str,
    ) -> tuple[list[str], list[list[Any]], bool]:
        if profile.driver == "sqlite":
            return self._run_sqlite(profile, sql, params, run_id=run_id)
        return self._run_postgres(profile, sql, params, run_id=run_id)

    def _run_sqlite(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        run_id: str,
    ) -> tuple[list[str], list[list[Any]], bool]:
        path = profile.sqlite_path or ":memory:"
        uri = path.startswith("file:")
        if path == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            conn = sqlite3.connect(str(Path(path)), timeout=max(1, self.statement_timeout_ms / 1000))
        try:
            conn.row_factory = None
            conn.execute(f"PRAGMA busy_timeout = {int(self.statement_timeout_ms)}")
            # Read-only attempt when file path
            if path not in {":memory:"} and not uri:
                try:
                    conn.execute("PRAGMA query_only = ON")
                except sqlite3.Error:
                    pass
            cur = conn.execute(sql, params)
            columns = [d[0] for d in (cur.description or [])]
            rows: list[list[Any]] = []
            truncated = False
            while True:
                if self.store.is_cancel_requested(run_id):
                    break
                batch = cur.fetchmany(100)
                if not batch:
                    break
                for row in batch:
                    rows.append([_cell(v) for v in row])
                    if len(rows) >= self.max_rows:
                        truncated = True
                        break
                if truncated:
                    break
            return columns, rows, truncated
        finally:
            conn.close()

    def _run_postgres(
        self,
        profile: SqlConnectionProfile,
        sql: str,
        params: dict[str, Any],
        *,
        run_id: str,
    ) -> tuple[list[str], list[list[Any]], bool]:
        import psycopg

        conninfo = self._pg_conninfo(profile)
        conn = psycopg.connect(conninfo, connect_timeout=5, autocommit=False)
        with self._pg_lock:
            self._active_pg[run_id] = conn
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
            truncated = False
            while True:
                if self.store.is_cancel_requested(run_id):
                    break
                batch = cur.fetchmany(100)
                if not batch:
                    break
                for row in batch:
                    rows.append([_cell(v) for v in row])
                    if len(rows) >= self.max_rows:
                        truncated = True
                        break
                if truncated:
                    break
            conn.commit()
            return columns, rows, truncated
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            with self._pg_lock:
                self._active_pg.pop(run_id, None)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _pg_conninfo(self, profile: SqlConnectionProfile) -> str:
        # Build keyword conninfo without embedding secrets in logs elsewhere.
        parts = {
            "host": profile.host or "",
            "port": str(profile.port or 5432),
            "dbname": profile.database or "",
            "user": profile.user or "",
            "password": profile.password or "",
            "sslmode": profile.sslmode or "prefer",
            "application_name": "central-hub-sql-workspace",
            "options": "-c default_transaction_read_only=on",
        }
        return " ".join(f"{k}={_quote_conn(v)}" for k, v in parts.items() if v != "")

    def _write_csv(
        self, run_id: str, columns: list[str], rows: list[list[Any]]
    ) -> Path:
        path = self.results_dir / f"{run_id}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)
        return path


def _quote_conn(value: str) -> str:
    if any(ch in value for ch in " '\\"):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return value


def _cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
