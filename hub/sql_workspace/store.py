"""Query library, versions, and run history for SQL Workspace."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hub.sql_workspace.db import SqlWorkspaceDatabase, utcnow


def _parse_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        parts = [t.strip() for t in tags.replace(";", ",").split(",")]
        return [p for p in parts if p]
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        s = str(t).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


class SqlWorkspaceStore:
    def __init__(self, db: SqlWorkspaceDatabase | None = None) -> None:
        self.db = db or SqlWorkspaceDatabase()

    # --- folders ---
    def list_folders(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sql_folders
                ORDER BY sort_order ASC, name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def create_folder(self, name: str, *, parent_id: str | None = None) -> dict[str, Any]:
        folder_id = uuid.uuid4().hex
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_folders (id, name, parent_id, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (folder_id, (name or "Folder").strip() or "Folder", parent_id or None, now, now),
            )
        return self.get_folder(folder_id) or {"id": folder_id}

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sql_folders WHERE id = ?", (folder_id,)
            ).fetchone()
        return dict(row) if row else None

    # --- queries ---
    def list_queries(
        self,
        *,
        q: str = "",
        folder_id: str = "",
        tag: str = "",
        favorites_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if folder_id:
            clauses.append("folder_id = ?")
            params.append(folder_id)
        if favorites_only:
            clauses.append("favorite = 1")
        if tag:
            clauses.append("LOWER(tags_json) LIKE ?")
            params.append(f"%{tag.lower()}%")
        if q:
            clauses.append(
                "(LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(sql_text) LIKE ? OR LOWER(tags_json) LIKE ?)"
            )
            like = f"%{q.lower()}%"
            params.extend([like, like, like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT * FROM sql_queries
            {where}
            ORDER BY favorite DESC, updated_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._hydrate_query(dict(r)) for r in rows]

    def get_query(self, query_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sql_queries WHERE id = ?", (query_id,)
            ).fetchone()
            if not row:
                return None
            data = self._hydrate_query(dict(row))
            versions = conn.execute(
                """
                SELECT id, query_id, version, title, note, created_at,
                       substr(sql_text, 1, 200) AS sql_preview
                FROM sql_query_versions
                WHERE query_id = ?
                ORDER BY version DESC
                LIMIT 50
                """,
                (query_id,),
            ).fetchall()
            data["versions"] = [dict(v) for v in versions]
            return data

    def create_query(
        self,
        *,
        title: str = "Untitled query",
        sql_text: str = "",
        description: str = "",
        folder_id: str | None = None,
        connection_id: str = "",
        tags: list[str] | str | None = None,
        favorite: bool = False,
        repository_id: str = "",
        notebook_note_id: str = "",
    ) -> dict[str, Any]:
        query_id = uuid.uuid4().hex
        now = utcnow()
        tag_list = _parse_tags(tags)
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_queries (
                    id, folder_id, title, description, sql_text, connection_id,
                    favorite, tags_json, repository_id, notebook_note_id,
                    current_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    query_id,
                    folder_id or None,
                    (title or "Untitled query").strip() or "Untitled query",
                    description or "",
                    sql_text or "",
                    connection_id or "",
                    1 if favorite else 0,
                    json.dumps(tag_list, ensure_ascii=True),
                    repository_id or "",
                    notebook_note_id or "",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO sql_query_versions
                    (id, query_id, version, sql_text, title, note, created_at)
                VALUES (?, ?, 1, ?, ?, 'created', ?)
                """,
                (
                    uuid.uuid4().hex,
                    query_id,
                    sql_text or "",
                    (title or "Untitled query").strip() or "Untitled query",
                    now,
                ),
            )
        return self.get_query(query_id) or {"id": query_id}

    def save_query(
        self,
        query_id: str,
        *,
        title: str | None = None,
        sql_text: str | None = None,
        description: str | None = None,
        folder_id: str | None = None,
        connection_id: str | None = None,
        tags: list[str] | str | None = None,
        favorite: bool | None = None,
        repository_id: str | None = None,
        notebook_note_id: str | None = None,
        new_version: bool = True,
        version_note: str = "",
    ) -> dict[str, Any] | None:
        existing = self.get_query(query_id)
        if not existing:
            return None
        now = utcnow()
        next_title = existing["title"] if title is None else (
            (title or "").strip() or "Untitled query"
        )
        next_sql = existing["sql_text"] if sql_text is None else (sql_text or "")
        next_desc = existing["description"] if description is None else (description or "")
        next_folder = existing.get("folder_id") if folder_id is None else (folder_id or None)
        next_conn = existing["connection_id"] if connection_id is None else (connection_id or "")
        next_tags = existing["tags"] if tags is None else _parse_tags(tags)
        next_fav = existing["favorite"] if favorite is None else bool(favorite)
        next_repo = (
            existing.get("repository_id") or ""
            if repository_id is None
            else (repository_id or "")
        )
        next_note = (
            existing.get("notebook_note_id") or ""
            if notebook_note_id is None
            else (notebook_note_id or "")
        )
        sql_changed = next_sql != (existing.get("sql_text") or "")
        version = int(existing.get("current_version") or 1)
        if new_version and sql_changed:
            version += 1
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE sql_queries SET
                    folder_id = ?, title = ?, description = ?, sql_text = ?,
                    connection_id = ?, favorite = ?, tags_json = ?,
                    repository_id = ?, notebook_note_id = ?,
                    current_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_folder,
                    next_title,
                    next_desc,
                    next_sql,
                    next_conn,
                    1 if next_fav else 0,
                    json.dumps(next_tags, ensure_ascii=True),
                    next_repo,
                    next_note,
                    version,
                    now,
                    query_id,
                ),
            )
            if new_version and sql_changed:
                conn.execute(
                    """
                    INSERT INTO sql_query_versions
                        (id, query_id, version, sql_text, title, note, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        query_id,
                        version,
                        next_sql,
                        next_title,
                        (version_note or "saved").strip() or "saved",
                        now,
                    ),
                )
        return self.get_query(query_id)

    def delete_query(self, query_id: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM sql_queries WHERE id = ?", (query_id,))
            return cur.rowcount > 0

    # --- runs ---
    def create_run(
        self,
        *,
        connection_id: str,
        sql_text: str,
        environment: str = "",
        query_id: str = "",
        params: dict[str, Any] | None = None,
        status: str = "running",
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sql_runs (
                    id, query_id, connection_id, environment, sql_text, params_json,
                    status, row_count, duration_ms, error, columns_json, result_path,
                    cancel_requested, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, '[]', '', 0, ?, NULL)
                """,
                (
                    run_id,
                    query_id or "",
                    connection_id,
                    environment or "",
                    sql_text,
                    json.dumps(params or {}, ensure_ascii=True),
                    status,
                    now,
                ),
            )
        return self.get_run(run_id) or {"id": run_id}

    def request_cancel(self, run_id: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                UPDATE sql_runs SET cancel_requested = 1
                WHERE id = ? AND status = 'running'
                """,
                (run_id,),
            )
            return cur.rowcount > 0

    def is_cancel_requested(self, run_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM sql_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return bool(row and int(row["cancel_requested"] or 0))

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        row_count: int | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
        columns: list[str] | None = None,
        result_path: str = "",
    ) -> dict[str, Any] | None:
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE sql_runs SET
                    status = ?, row_count = ?, duration_ms = ?, error = ?,
                    columns_json = ?, result_path = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    row_count,
                    duration_ms,
                    error,
                    json.dumps(columns or [], ensure_ascii=True),
                    result_path or "",
                    now,
                    run_id,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sql_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["params"] = json.loads(data.get("params_json") or "{}")
        except json.JSONDecodeError:
            data["params"] = {}
        try:
            data["columns"] = json.loads(data.get("columns_json") or "[]")
        except json.JSONDecodeError:
            data["columns"] = []
        data["cancel_requested"] = bool(int(data.get("cancel_requested") or 0))
        return data

    def list_runs(self, *, query_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if query_id:
            where = "WHERE query_id = ?"
            params.append(query_id)
        params.append(max(1, min(int(limit), 200)))
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, query_id, connection_id, environment, status, row_count,
                       duration_ms, error, created_at, finished_at,
                       substr(sql_text, 1, 160) AS sql_preview
                FROM sql_runs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _hydrate_query(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["tags"] = json.loads(row.get("tags_json") or "[]")
        except json.JSONDecodeError:
            row["tags"] = []
        row["favorite"] = bool(int(row.get("favorite") or 0))
        return row
