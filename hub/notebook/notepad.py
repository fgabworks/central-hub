"""Persistent Quick Notepad — one local scratchpad, separate from structured notes."""

from __future__ import annotations

import uuid
from typing import Any

from hub.notebook.db import NotebookDatabase, utcnow

NOTEPAD_ID = "default"
FORMATS = ("plain", "markdown")
DEFAULT_WIDTH = 320
MIN_WIDTH = 240
MAX_WIDTH = 560
MAX_REVISIONS = 20


def normalize_format(value: str | None) -> str:
    raw = (value or "plain").strip().lower()
    return raw if raw in FORMATS else "plain"


def clamp_width(value: Any) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


class QuickNotepadStore:
    """Singleton scratchpad + small revision history in the notebook SQLite DB."""

    def __init__(self, db: NotebookDatabase) -> None:
        self.db = db
        self.ensure_row()

    def ensure_row(self) -> None:
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO quick_notepad
                    (id, content, content_format, panel_open, panel_width, updated_at)
                VALUES (?, '', 'plain', 1, ?, ?)
                """,
                (NOTEPAD_ID, DEFAULT_WIDTH, now),
            )

    def get(self, *, include_revisions: bool = True) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM quick_notepad WHERE id = ?",
                (NOTEPAD_ID,),
            ).fetchone()
            if not row:
                self.ensure_row()
                row = conn.execute(
                    "SELECT * FROM quick_notepad WHERE id = ?",
                    (NOTEPAD_ID,),
                ).fetchone()
            payload = self._row_to_dict(row)
            if include_revisions:
                payload["revisions"] = self._list_revisions(conn)
            return payload

    def save(
        self,
        *,
        content: str | None = None,
        content_format: str | None = None,
        panel_open: bool | None = None,
        panel_width: int | None = None,
    ) -> dict[str, Any]:
        current = self.get(include_revisions=False)
        now = utcnow()
        next_content = current["content"] if content is None else str(content)
        next_format = (
            current["content_format"]
            if content_format is None
            else normalize_format(content_format)
        )
        next_open = current["panel_open"] if panel_open is None else bool(panel_open)
        next_width = (
            current["panel_width"] if panel_width is None else clamp_width(panel_width)
        )
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE quick_notepad
                SET content = ?, content_format = ?, panel_open = ?,
                    panel_width = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_content,
                    next_format,
                    1 if next_open else 0,
                    next_width,
                    now,
                    NOTEPAD_ID,
                ),
            )
        return self.get()

    def clear(self) -> dict[str, Any]:
        current = self.get(include_revisions=False)
        if (current["content"] or "").strip():
            self._add_revision(
                current["content"],
                current["content_format"],
                reason="clear",
            )
        return self.save(content="")

    def restore(self, revision_id: str) -> dict[str, Any] | None:
        rid = (revision_id or "").strip()
        if not rid:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM quick_notepad_revisions WHERE id = ?",
                (rid,),
            ).fetchone()
        if not row:
            return None
        current = self.get(include_revisions=False)
        if (current["content"] or "").strip():
            self._add_revision(
                current["content"],
                current["content_format"],
                reason="before_restore",
            )
        return self.save(
            content=str(row["content"] or ""),
            content_format=normalize_format(str(row["content_format"] or "plain")),
        )

    def convert_to_note(self, notes_store: Any) -> dict[str, Any] | None:
        """Create a structured notebook note from scratchpad content. Does not clear."""
        pad = self.get(include_revisions=False)
        body = pad["content"] or ""
        if not body.strip():
            return None
        self._add_revision(body, pad["content_format"], reason="convert")
        title = _title_from_content(body)
        note = notes_store.create(title=title, actor="owner")
        saved = notes_store.save(
            note["id"],
            title=title,
            body_md=body if pad["content_format"] == "markdown" else body,
            note_type="note",
            status="inbox",
            priority="medium",
            due_date=None,
            tags=["from-quick-notepad"],
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
            actor="owner",
        )
        return saved

    def _add_revision(self, content: str, content_format: str, *, reason: str) -> None:
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO quick_notepad_revisions
                    (id, content, content_format, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    content,
                    normalize_format(content_format),
                    (reason or "snapshot")[:40],
                    now,
                ),
            )
            rows = conn.execute(
                """
                SELECT id FROM quick_notepad_revisions
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
            for stale in rows[MAX_REVISIONS:]:
                conn.execute(
                    "DELETE FROM quick_notepad_revisions WHERE id = ?",
                    (stale["id"],),
                )

    def _list_revisions(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, content, content_format, reason, created_at
            FROM quick_notepad_revisions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (MAX_REVISIONS,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            text = str(row["content"] or "")
            preview = " ".join(text.split())
            if len(preview) > 72:
                preview = preview[:69] + "…"
            out.append(
                {
                    "id": str(row["id"]),
                    "content": text,
                    "content_format": normalize_format(str(row["content_format"])),
                    "reason": str(row["reason"] or "snapshot"),
                    "created_at": str(row["created_at"] or ""),
                    "preview": preview or "(empty)",
                }
            )
        return out

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": NOTEPAD_ID,
            "content": str(row["content"] or "") if row else "",
            "content_format": normalize_format(
                str(row["content_format"]) if row else "plain"
            ),
            "panel_open": bool(int(row["panel_open"])) if row else True,
            "panel_width": clamp_width(row["panel_width"] if row else DEFAULT_WIDTH),
            "updated_at": str(row["updated_at"] or "") if row else "",
        }


def _title_from_content(content: str) -> str:
    for line in (content or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:80]
    return "Quick note"
