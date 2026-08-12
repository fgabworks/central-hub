"""SQLite persistence for Repository Notebook with versioned migrations."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_initial_notebook",
        """
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            body_md TEXT NOT NULL DEFAULT '',
            note_type TEXT NOT NULL DEFAULT 'note',
            status TEXT NOT NULL DEFAULT 'inbox',
            priority TEXT NOT NULL DEFAULT 'medium',
            due_date TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
        CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(note_type);
        CREATE INDEX IF NOT EXISTS idx_notes_priority ON notes(priority);

        CREATE TABLE IF NOT EXISTS note_repositories (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            repository_label TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'related',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_note_repos_note ON note_repositories(note_id);
        CREATE INDEX IF NOT EXISTS idx_note_repos_repo ON note_repositories(repository_id);

        CREATE TABLE IF NOT EXISTS note_checklist (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_note_check_note ON note_checklist(note_id);

        CREATE TABLE IF NOT EXISTS note_links (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_note_links_note ON note_links(note_id);

        CREATE TABLE IF NOT EXISTS note_activity (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'owner',
            created_at TEXT NOT NULL,
            FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_note_activity_note ON note_activity(note_id, created_at DESC);
        """,
    ),
    (
        "002_note_pinned",
        """
        ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_notes_pinned ON notes(pinned);
        CREATE INDEX IF NOT EXISTS idx_notes_due ON notes(due_date);
        """,
    ),
    (
        "003_quick_notepad",
        """
        CREATE TABLE IF NOT EXISTS quick_notepad (
            id TEXT PRIMARY KEY CHECK (id = 'default'),
            content TEXT NOT NULL DEFAULT '',
            content_format TEXT NOT NULL DEFAULT 'plain',
            panel_open INTEGER NOT NULL DEFAULT 1,
            panel_width INTEGER NOT NULL DEFAULT 320,
            updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO quick_notepad
            (id, content, content_format, panel_open, panel_width, updated_at)
        VALUES ('default', '', 'plain', 1, 320, datetime('now'));

        CREATE TABLE IF NOT EXISTS quick_notepad_revisions (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_format TEXT NOT NULL DEFAULT 'plain',
            reason TEXT NOT NULL DEFAULT 'snapshot',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_qn_rev_created
            ON quick_notepad_revisions(created_at DESC);
        """,
    ),
    (
        "004_note_scope_workspace",
        """
        ALTER TABLE notes ADD COLUMN scope TEXT NOT NULL DEFAULT 'work';
        UPDATE notes SET scope = 'work' WHERE scope IS NULL OR TRIM(scope) = '';
        CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes(scope);

        CREATE TABLE IF NOT EXISTS hub_prefs (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO hub_prefs (key, value, updated_at)
        VALUES ('workspace', 'work', datetime('now'));
        """,
    ),
    (
        "005_notepad_personal_work",
        """
        CREATE TABLE IF NOT EXISTS quick_notepad_v2 (
            id TEXT PRIMARY KEY CHECK (id IN ('personal', 'work')),
            content TEXT NOT NULL DEFAULT '',
            content_format TEXT NOT NULL DEFAULT 'plain',
            panel_open INTEGER NOT NULL DEFAULT 1,
            panel_width INTEGER NOT NULL DEFAULT 320,
            updated_at TEXT NOT NULL
        );
        INSERT INTO quick_notepad_v2
            (id, content, content_format, panel_open, panel_width, updated_at)
        SELECT 'personal', content, content_format, panel_open, panel_width, updated_at
        FROM quick_notepad WHERE id = 'default';
        INSERT OR IGNORE INTO quick_notepad_v2
            (id, content, content_format, panel_open, panel_width, updated_at)
        VALUES ('personal', '', 'plain', 1, 320, datetime('now'));
        INSERT OR IGNORE INTO quick_notepad_v2
            (id, content, content_format, panel_open, panel_width, updated_at)
        VALUES ('work', '', 'plain', 1, 320, datetime('now'));
        DROP TABLE quick_notepad;
        ALTER TABLE quick_notepad_v2 RENAME TO quick_notepad;

        CREATE TABLE IF NOT EXISTS quick_notepad_revisions_v2 (
            id TEXT PRIMARY KEY,
            notepad_id TEXT NOT NULL DEFAULT 'personal',
            content TEXT NOT NULL,
            content_format TEXT NOT NULL DEFAULT 'plain',
            reason TEXT NOT NULL DEFAULT 'snapshot',
            created_at TEXT NOT NULL
        );
        INSERT INTO quick_notepad_revisions_v2
            (id, notepad_id, content, content_format, reason, created_at)
        SELECT id, 'personal', content, content_format, reason, created_at
        FROM quick_notepad_revisions;
        DROP TABLE quick_notepad_revisions;
        ALTER TABLE quick_notepad_revisions_v2 RENAME TO quick_notepad_revisions;
        CREATE INDEX IF NOT EXISTS idx_qn_rev_pad_created
            ON quick_notepad_revisions(notepad_id, created_at DESC);
        """,
    ),
    (
        "006_notepad_panel_size",
        """
        ALTER TABLE quick_notepad ADD COLUMN panel_size TEXT NOT NULL DEFAULT 'normal';
        UPDATE quick_notepad
           SET panel_size = 'normal'
         WHERE panel_size IS NULL
            OR TRIM(panel_size) = ''
            OR lower(panel_size) NOT IN ('normal', 'expanded', 'maximized');
        """,
    ),
    (
        "007_perf_indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_notes_scope_status ON notes(scope, status);
        """,
    ),
    (
        "008_today_missions",
        """
        ALTER TABLE notes ADD COLUMN completed_at TEXT;
        ALTER TABLE notes ADD COLUMN reminder_status TEXT NOT NULL DEFAULT 'none';
        ALTER TABLE notes ADD COLUMN carry_over INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE notes ADD COLUMN original_due_date TEXT;
        CREATE INDEX IF NOT EXISTS idx_notes_mission_due
            ON notes(scope, note_type, due_date, status);
        CREATE INDEX IF NOT EXISTS idx_notes_carry_over
            ON notes(scope, carry_over, status);
        """,
    ),
    (
        "009_official_references",
        """
        CREATE TABLE IF NOT EXISTS official_references (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            ref_type TEXT NOT NULL DEFAULT 'other',
            year INTEGER NOT NULL,
            short_note TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            external_url TEXT NOT NULL DEFAULT '',
            storage_kind TEXT NOT NULL DEFAULT 'file',
            original_filename TEXT NOT NULL DEFAULT '',
            stored_filename TEXT NOT NULL DEFAULT '',
            relative_path TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'owner'
        );
        CREATE INDEX IF NOT EXISTS idx_official_refs_year
            ON official_references(year DESC);
        CREATE INDEX IF NOT EXISTS idx_official_refs_type
            ON official_references(ref_type);
        CREATE INDEX IF NOT EXISTS idx_official_refs_year_type
            ON official_references(year DESC, ref_type);
        """,
    ),
    (
        "010_official_references_subject",
        """
        ALTER TABLE official_references ADD COLUMN subject TEXT;
        ALTER TABLE official_references ADD COLUMN subject_source TEXT NOT NULL DEFAULT '';
        """,
    ),
]


def default_notebook_db_path() -> Path:
    return ROOT_DIR / "data" / "notebook.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotebookDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_notebook_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for name, sql in _MIGRATIONS:
                if name in applied:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (name, utcnow()),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def applied_migrations(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM schema_migrations ORDER BY id"
            ).fetchall()
        return [str(r["name"]) for r in rows]
