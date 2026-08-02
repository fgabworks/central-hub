"""Seed demo export table into the local SQL Workspace demo SQLite DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hub.settings import ROOT_DIR


def ensure_export_demo_table(path: Path | None = None) -> Path:
    db_path = path or (ROOT_DIR / "data" / "sql_workspace_demo.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS export_demo_household (
                household_id TEXT PRIMARY KEY,
                org_unit_uid TEXT NOT NULL,
                org_unit_name TEXT NOT NULL,
                quarter TEXT NOT NULL,
                status TEXT NOT NULL,
                ip_flag TEXT NOT NULL,
                created_at TEXT NOT NULL,
                internal_notes TEXT
            );
            INSERT OR IGNORE INTO export_demo_household (
                household_id, org_unit_uid, org_unit_name, quarter, status,
                ip_flag, created_at, internal_notes
            ) VALUES
                ('HH-001', 'OuRegion01', 'Demo Region', '2025Q1', 'Active', 'IP', '2025-01-15', 'secret-a'),
                ('HH-002', 'OuRegion01', 'Demo Region', '2025Q1', 'Active', 'Non-IP', '2025-01-20', 'secret-b'),
                ('HH-003', 'OuProv001', 'Demo Province', '2025Q1', 'Inactive', 'IP', '2025-02-01', NULL),
                ('HH-004', 'OuProv001', 'Demo Province', '2025Q2', 'Active', 'Non-IP', '2025-04-10', NULL),
                ('HH-005', 'OuMuni001', 'Demo City', '2025Q2', 'Active', 'IP', '2025-05-01', NULL),
                ('HH-006', 'OuBrgy001', 'Demo Barangay', '2025Q2', 'Active', 'IP', '2025-05-12', NULL),
                ('HH-007', 'OuBrgy001', 'Demo Barangay', '2025Q3', 'Active', 'Non-IP', '2025-07-01', NULL),
                ('HH-008', 'OuBrgy001', 'Demo Barangay', '2025Q3', 'Inactive', 'IP', '2025-08-01', NULL);
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path
