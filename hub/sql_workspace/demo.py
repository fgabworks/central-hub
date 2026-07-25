"""Ensure local demo SQLite DB exists for SQL Workspace."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hub.settings import ROOT_DIR


def ensure_demo_database(path: Path | None = None) -> Path:
    db_path = path or (ROOT_DIR / "data" / "sql_workspace_demo.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS demo_people (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT OR IGNORE INTO demo_people (id, name, role, active) VALUES
                (1, 'Ada', 'analyst', 1),
                (2, 'Grace', 'engineer', 1),
                (3, 'Alan', 'researcher', 0);
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path
