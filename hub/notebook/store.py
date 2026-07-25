"""High-level Repository Notebook store operations."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hub.notebook.db import NotebookDatabase, utcnow
from hub.notebook.models import (
    NOTE_TYPES,
    PRIORITIES,
    STATUSES,
    normalize_priority,
    normalize_role,
    normalize_status,
    normalize_type,
    parse_tags,
)


class NotebookStore:
    def __init__(self, db: NotebookDatabase | None = None) -> None:
        self.db = db or NotebookDatabase()

    def status_counts(self) -> dict[str, int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM notes GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
        counts = {s: 0 for s in STATUSES}
        for row in rows:
            status = str(row["status"])
            if status in counts:
                counts[status] = int(row["n"] or 0)
        counts["all"] = int((total or {"n": 0})["n"] or 0)
        counts["open"] = sum(
            counts[s] for s in ("inbox", "pending", "ongoing", "blocked")
        )
        return counts

    def list_tags(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT tags_json FROM notes").fetchall()
        tags: set[str] = set()
        for row in rows:
            try:
                for tag in json.loads(row["tags_json"] or "[]"):
                    if tag:
                        tags.add(str(tag))
            except json.JSONDecodeError:
                continue
        return sorted(tags, key=str.lower)

    def list_open(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Open notes for dashboard (excludes done + archived)."""
        sql = """
            SELECT n.*
            FROM notes n
            WHERE n.status NOT IN ('done', 'archived')
            ORDER BY n.pinned DESC, n.due_date IS NULL, n.due_date ASC, n.updated_at DESC
            LIMIT ?
        """
        with self.db.connect() as conn:
            rows = conn.execute(sql, (max(1, min(int(limit), 2000)),)).fetchall()
            return [self._hydrate_note(dict(row), conn, light=True) for row in rows]

    def search(
        self,
        *,
        status: str = "",
        repository_id: str = "",
        note_type: str = "",
        priority: str = "",
        tag: str = "",
        q: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        status = (status or "").strip().lower()
        if status and status != "all":
            if status in {"open", "active"}:
                clauses.append("n.status NOT IN ('done', 'archived')")
            elif status == "archived":
                clauses.append("n.status = 'archived'")
            else:
                clauses.append("n.status = ?")
                params.append(normalize_status(status))

        if note_type:
            clauses.append("n.note_type = ?")
            params.append(normalize_type(note_type))
        if priority:
            clauses.append("n.priority = ?")
            params.append(normalize_priority(priority))
        if tag:
            clauses.append("LOWER(n.tags_json) LIKE ?")
            params.append(f"%{tag.strip().lower()}%")
        if q:
            like = f"%{q.strip().lower()}%"
            clauses.append(
                "(LOWER(n.title) LIKE ? OR LOWER(n.body_md) LIKE ? OR LOWER(n.tags_json) LIKE ?)"
            )
            params.extend([like, like, like])
        if repository_id:
            clauses.append(
                """EXISTS (
                    SELECT 1 FROM note_repositories nr
                    WHERE nr.note_id = n.id AND nr.repository_id = ?
                )"""
            )
            params.append(repository_id)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT n.*
            FROM notes n
            {where}
            ORDER BY n.updated_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 1000)))
        with self.db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            out = []
            for row in rows:
                note = self._hydrate_note(dict(row), conn, light=True)
                out.append(note)
        return out

    def get(self, note_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if not row:
                return None
            return self._hydrate_note(dict(row), conn, light=False)

    def create(
        self,
        *,
        title: str = "Untitled note",
        actor: str = "owner",
        repository_id: str = "",
        repository_label: str = "",
    ) -> dict[str, Any]:
        note_id = uuid.uuid4().hex
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    id, title, body_md, note_type, status, priority, due_date,
                    tags_json, created_at, updated_at, archived_at, pinned
                ) VALUES (?, ?, '', 'note', 'inbox', 'medium', NULL, '[]', ?, ?, NULL, 0)
                """,
                (note_id, (title or "Untitled note").strip() or "Untitled note", now, now),
            )
            if repository_id:
                conn.execute(
                    """
                    INSERT INTO note_repositories
                    (id, note_id, repository_id, repository_label, role, sort_order)
                    VALUES (?, ?, ?, ?, 'primary', 0)
                    """,
                    (
                        uuid.uuid4().hex,
                        note_id,
                        repository_id,
                        repository_label or repository_id,
                    ),
                )
            self._add_activity(
                conn,
                note_id,
                action="created",
                detail="Note created",
                actor=actor,
                when=now,
            )
        return self.get(note_id) or {"id": note_id}

    def save(
        self,
        note_id: str,
        *,
        title: str,
        body_md: str,
        note_type: str,
        status: str,
        priority: str,
        due_date: str | None,
        tags: list[str] | str | None,
        repositories: list[dict[str, str]],
        checklist: list[dict[str, Any]],
        links: list[dict[str, str]],
        pinned: bool = False,
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        existing = self.get(note_id)
        if not existing:
            return None

        now = utcnow()
        status_n = normalize_status(status)
        archived_at = existing.get("archived_at")
        if status_n == "archived" and not archived_at:
            archived_at = now
        if status_n != "archived":
            archived_at = None

        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET
                    title = ?, body_md = ?, note_type = ?, status = ?, priority = ?,
                    due_date = ?, tags_json = ?, updated_at = ?, archived_at = ?, pinned = ?
                WHERE id = ?
                """,
                (
                    (title or "").strip() or "Untitled note",
                    body_md or "",
                    normalize_type(note_type),
                    status_n,
                    normalize_priority(priority),
                    (due_date or "").strip() or None,
                    json.dumps(parse_tags(tags), ensure_ascii=True),
                    now,
                    archived_at,
                    1 if pinned else 0,
                    note_id,
                ),
            )
            conn.execute("DELETE FROM note_repositories WHERE note_id = ?", (note_id,))
            for idx, repo in enumerate(repositories):
                rid = str(repo.get("repository_id") or "").strip()
                if not rid:
                    continue
                conn.execute(
                    """
                    INSERT INTO note_repositories
                    (id, note_id, repository_id, repository_label, role, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        note_id,
                        rid,
                        str(repo.get("repository_label") or rid).strip(),
                        normalize_role(repo.get("role")),
                        idx,
                    ),
                )
            conn.execute("DELETE FROM note_checklist WHERE note_id = ?", (note_id,))
            for idx, item in enumerate(checklist):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                conn.execute(
                    """
                    INSERT INTO note_checklist (id, note_id, text, done, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        note_id,
                        text,
                        1 if item.get("done") else 0,
                        idx,
                    ),
                )
            conn.execute("DELETE FROM note_links WHERE note_id = ?", (note_id,))
            for idx, link in enumerate(links):
                url = str(link.get("url") or "").strip()
                if not url:
                    continue
                conn.execute(
                    """
                    INSERT INTO note_links (id, note_id, label, url, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        note_id,
                        str(link.get("label") or url).strip(),
                        url,
                        idx,
                    ),
                )
            detail_bits = []
            if existing.get("status") != status_n:
                detail_bits.append(f"status → {status_n}")
            if existing.get("priority") != normalize_priority(priority):
                detail_bits.append(f"priority → {normalize_priority(priority)}")
            self._add_activity(
                conn,
                note_id,
                action="updated",
                detail="; ".join(detail_bits) if detail_bits else "Note saved",
                actor=actor,
                when=now,
            )
        return self.get(note_id)

    def archive(self, note_id: str, *, actor: str = "owner") -> dict[str, Any] | None:
        note = self.get(note_id)
        if not note:
            return None
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET status = 'archived', archived_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, note_id),
            )
            self._add_activity(
                conn, note_id, action="archived", detail="Note archived", actor=actor, when=now
            )
        return self.get(note_id)

    def restore(self, note_id: str, *, actor: str = "owner") -> dict[str, Any] | None:
        note = self.get(note_id)
        if not note:
            return None
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET status = 'inbox', archived_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, note_id),
            )
            self._add_activity(
                conn, note_id, action="restored", detail="Note restored to Inbox", actor=actor, when=now
            )
        return self.get(note_id)

    def delete(self, note_id: str, *, actor: str = "owner") -> bool:
        """Permanently delete a note and its related rows (CASCADE)."""
        note = self.get(note_id)
        if not note:
            return False
        with self.db.connect() as conn:
            # Child rows cascade via FK when foreign_keys pragma is on.
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return True

    def export_payload(self, note_id: str) -> dict[str, Any] | None:
        note = self.get(note_id)
        if not note:
            return None
        return {
            "format": "central-hub-notebook-v1",
            "exported_at": utcnow(),
            "note": note,
        }

    def _hydrate_note(
        self, row: dict[str, Any], conn, *, light: bool
    ) -> dict[str, Any]:
        note = dict(row)
        note["pinned"] = bool(note.get("pinned"))
        try:
            note["tags"] = json.loads(note.get("tags_json") or "[]")
        except json.JSONDecodeError:
            note["tags"] = []
        repos = conn.execute(
            """
            SELECT repository_id, repository_label, role, sort_order
            FROM note_repositories WHERE note_id = ? ORDER BY sort_order, repository_id
            """,
            (note["id"],),
        ).fetchall()
        note["repositories"] = [dict(r) for r in repos]
        note["repository_labels"] = [
            r["repository_label"] or r["repository_id"] for r in note["repositories"]
        ]
        note["primary_repository"] = (
            note["repository_labels"][0] if note["repository_labels"] else "—"
        )
        check_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END), 0) AS done_count
            FROM note_checklist WHERE note_id = ?
            """,
            (note["id"],),
        ).fetchone()
        stats = dict(check_stats) if check_stats is not None else {}
        total = int(stats.get("total") or 0)
        done_count = int(stats.get("done_count") or 0)
        note["checklist_total"] = total
        note["checklist_done"] = done_count
        note["checklist_progress"] = f"{done_count}/{total}" if total else "0/0"
        if light:
            note["checklist"] = []
            note["links"] = []
            note["activity"] = []
            return note
        checklist = conn.execute(
            """
            SELECT id, text, done, sort_order FROM note_checklist
            WHERE note_id = ? ORDER BY sort_order
            """,
            (note["id"],),
        ).fetchall()
        note["checklist"] = [
            {"id": r["id"], "text": r["text"], "done": bool(r["done"]), "sort_order": r["sort_order"]}
            for r in checklist
        ]
        links = conn.execute(
            """
            SELECT id, label, url, sort_order FROM note_links
            WHERE note_id = ? ORDER BY sort_order
            """,
            (note["id"],),
        ).fetchall()
        note["links"] = [dict(r) for r in links]
        activity = conn.execute(
            """
            SELECT id, action, detail, actor, created_at FROM note_activity
            WHERE note_id = ? ORDER BY created_at DESC LIMIT 40
            """,
            (note["id"],),
        ).fetchall()
        note["activity"] = [dict(r) for r in activity]
        return note

    @staticmethod
    def _add_activity(
        conn,
        note_id: str,
        *,
        action: str,
        detail: str,
        actor: str,
        when: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO note_activity (id, note_id, action, detail, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, note_id, action, detail, actor, when or utcnow()),
        )


# Re-export for templates / routes
__all__ = ["NotebookStore", "NOTE_TYPES", "PRIORITIES", "STATUSES"]
