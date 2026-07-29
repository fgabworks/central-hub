"""Persistent Quick Notepad — separate personal and work scratchpads."""

from __future__ import annotations

import uuid
from typing import Any

from hub.notebook.db import NotebookDatabase, utcnow
from hub.notebook.models import normalize_scope

NOTEPAD_IDS = ("personal", "work")
# Legacy alias kept for imports/tests that referenced the old singleton id.
NOTEPAD_ID = "personal"
FORMATS = ("plain", "markdown")
PANEL_SIZES = ("normal", "expanded", "maximized")
DEFAULT_WIDTH = 320
MIN_WIDTH = 240
MAX_WIDTH = 560
MAX_REVISIONS = 3


def normalize_format(value: str | None) -> str:
    raw = (value or "plain").strip().lower()
    return raw if raw in FORMATS else "plain"


def normalize_panel_size(value: str | None) -> str:
    raw = (value or "normal").strip().lower()
    return raw if raw in PANEL_SIZES else "normal"


def clamp_width(value: Any) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def normalize_notepad_id(value: str | None, *, default: str = "personal") -> str:
    """Map scope / notepad id; treat legacy 'default' as personal."""
    raw = (value or "").strip().lower()
    if raw == "default":
        return "personal"
    return normalize_scope(raw, default=default)


class QuickNotepadStore:
    """Scoped scratchpad + revision history in the notebook SQLite DB."""

    def __init__(
        self, db: NotebookDatabase, *, scope: str = "personal"
    ) -> None:
        self.db = db
        self.notepad_id = normalize_notepad_id(scope)
        self.ensure_row()

    def ensure_row(self) -> None:
        now = utcnow()
        with self.db.connect() as conn:
            for pad_id in NOTEPAD_IDS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quick_notepad
                        (id, content, content_format, panel_open, panel_width, updated_at)
                    VALUES (?, '', 'plain', 1, ?, ?)
                    """,
                    (pad_id, DEFAULT_WIDTH, now),
                )

    def get(self, *, include_revisions: bool = True) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM quick_notepad WHERE id = ?",
                (self.notepad_id,),
            ).fetchone()
            if not row:
                self.ensure_row()
                row = conn.execute(
                    "SELECT * FROM quick_notepad WHERE id = ?",
                    (self.notepad_id,),
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
        panel_size: str | None = None,
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
        next_size = (
            current["panel_size"]
            if panel_size is None
            else normalize_panel_size(panel_size)
        )
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE quick_notepad
                SET content = ?, content_format = ?, panel_open = ?,
                    panel_width = ?, panel_size = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_content,
                    next_format,
                    1 if next_open else 0,
                    next_width,
                    next_size,
                    now,
                    self.notepad_id,
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

    def snapshot(
        self,
        *,
        content: str | None = None,
        content_format: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Persist pad content and store a revision snapshot (reason=save).

        Returns None when the pad would be empty after applying updates.
        """
        kwargs: dict[str, Any] = {}
        if content is not None:
            kwargs["content"] = str(content)
        if content_format is not None:
            kwargs["content_format"] = content_format
        if kwargs:
            self.save(**kwargs)
        current = self.get(include_revisions=False)
        body = current["content"] or ""
        if not body.strip():
            return None
        self._add_revision(body, current["content_format"], reason="save")
        return self.get()

    def restore(self, revision_id: str) -> dict[str, Any] | None:
        rid = (revision_id or "").strip()
        if not rid:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM quick_notepad_revisions
                WHERE id = ? AND notepad_id = ?
                """,
                (rid, self.notepad_id),
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
        """Create a structured notebook note from this pad. Does not clear."""
        pad = self.get(include_revisions=False)
        body = pad["content"] or ""
        if not body.strip():
            return None
        self._add_revision(body, pad["content_format"], reason="convert")
        title = _title_from_content(body)
        scope = self.notepad_id
        note = notes_store.create(title=title, actor="owner", scope=scope)
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
            scope=scope,
        )
        return saved

    def _add_revision(self, content: str, content_format: str, *, reason: str) -> None:
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO quick_notepad_revisions
                    (id, notepad_id, content, content_format, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    self.notepad_id,
                    content,
                    normalize_format(content_format),
                    (reason or "snapshot")[:40],
                    now,
                ),
            )
            rows = conn.execute(
                """
                SELECT id FROM quick_notepad_revisions
                WHERE notepad_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (self.notepad_id,),
            ).fetchall()
            for stale in rows[MAX_REVISIONS:]:
                conn.execute(
                    "DELETE FROM quick_notepad_revisions WHERE id = ?",
                    (stale["id"],),
                )

    def _list_revisions(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT id, notepad_id, content, content_format, reason, created_at
            FROM quick_notepad_revisions
            WHERE notepad_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (self.notepad_id, MAX_REVISIONS),
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
                    "notepad_id": str(row["notepad_id"] or self.notepad_id),
                    "content": text,
                    "content_format": normalize_format(str(row["content_format"])),
                    "reason": str(row["reason"] or "snapshot"),
                    "created_at": str(row["created_at"] or ""),
                    "preview": preview or "(empty)",
                }
            )
        return out

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        size_raw = "normal"
        if row is not None:
            keys = set(row.keys()) if hasattr(row, "keys") else set()
            if "panel_size" in keys:
                size_raw = str(row["panel_size"] or "normal")
        return {
            "id": self.notepad_id,
            "scope": self.notepad_id,
            "content": str(row["content"] or "") if row else "",
            "content_format": normalize_format(
                str(row["content_format"]) if row else "plain"
            ),
            "panel_open": bool(int(row["panel_open"])) if row else True,
            "panel_width": clamp_width(row["panel_width"] if row else DEFAULT_WIDTH),
            "panel_size": normalize_panel_size(size_raw),
            "updated_at": str(row["updated_at"] or "") if row else "",
        }


def _title_from_content(content: str) -> str:
    for line in (content or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:80]
    return "Quick note"
