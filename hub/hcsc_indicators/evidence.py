"""Local read-only evidence snapshots for HCSC validation (never writes to DHIS2)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.dhis2.redact import redact_text
from hub.hcsc_indicators.branding import EXPORT_EVIDENCE, PAGE_TITLE, export_package_meta
from hub.settings import ROOT_DIR

DEFAULT_DB = ROOT_DIR / "data" / "hcsc_validation_evidence.db"

_LOCK = threading.RLock()
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "authorization",
        "cookie",
        "cookies",
        "auth",
        "username",
        "credential",
    }
)


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if str(key).lower() in _SECRET_KEYS:
                out[key] = "[REDACTED]"
            else:
                out[key] = _scrub(value)
        return out
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, secrets=[])
    return obj


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(path: Path | None = None) -> Path:
    db_path = Path(path) if path else DEFAULT_DB
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_snapshots (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  environment TEXT NOT NULL,
                  period TEXT NOT NULL,
                  org_unit TEXT NOT NULL,
                  disaggregation TEXT NOT NULL,
                  note TEXT,
                  payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_notes (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  indicator_key TEXT,
                  environment TEXT,
                  period TEXT,
                  org_unit TEXT,
                  note TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
    return db_path


def save_snapshot(
    *,
    environment: str,
    period: str,
    org_unit: str,
    disaggregation: str,
    comparisons: list[dict[str, Any]],
    report_meta: dict[str, Any] | None = None,
    note: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    db_path = ensure_schema(path)
    snap_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    package = export_package_meta(
        kind="evidence",
        environment=environment,
        period=period,
        org_unit=org_unit,
        generated_at=created,
        source_versions=(report_meta or {}).get("source_versions")
        or {"module": PAGE_TITLE, "package": EXPORT_EVIDENCE},
    )
    payload = {
        "id": snap_id,
        "created_at": created,
        "environment": environment,
        "period": period,
        "org_unit": org_unit,
        "disaggregation": disaggregation,
        "note": note,
        "package": package,
        "report_meta": report_meta or {},
        "comparisons": comparisons,
        "dhis2_writes": 0,
        "sql_executed": False,
        "invented": False,
    }
    scrubbed = _scrub(payload)
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO evidence_snapshots
                (id, created_at, environment, period, org_unit, disaggregation, note, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap_id,
                    created,
                    environment,
                    period,
                    org_unit,
                    disaggregation,
                    note,
                    json.dumps(scrubbed),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "id": snap_id, "created_at": created, "path": str(db_path)}


def list_snapshots(
    *,
    environment: str | None = None,
    period: str | None = None,
    org_unit: str | None = None,
    limit: int = 20,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    db_path = ensure_schema(path)
    clauses = []
    args: list[Any] = []
    if environment:
        clauses.append("environment = ?")
        args.append(environment)
    if period:
        clauses.append("period = ?")
        args.append(period)
    if org_unit:
        clauses.append("org_unit = ?")
        args.append(org_unit)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        f"SELECT id, created_at, environment, period, org_unit, disaggregation, note "
        f"FROM evidence_snapshots{where} ORDER BY created_at DESC LIMIT ?"
    )
    args.append(max(1, min(int(limit), 100)))
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def get_snapshot(snapshot_id: str, *, path: Path | None = None) -> dict[str, Any] | None:
    db_path = ensure_schema(path)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT payload_json FROM evidence_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return json.loads(row["payload_json"])


def add_investigation_note(
    *,
    note: str,
    indicator_key: str | None = None,
    environment: str | None = None,
    period: str | None = None,
    org_unit: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    text = (note or "").strip()
    if not text:
        raise ValueError("Investigation note is required.")
    text = redact_text(text, secrets=[])
    if not text:
        raise ValueError("Investigation note is required.")
    db_path = ensure_schema(path)
    note_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO investigation_notes
                (id, created_at, indicator_key, environment, period, org_unit, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (note_id, created, indicator_key, environment, period, org_unit, text),
            )
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "id": note_id, "created_at": created, "note": text}


def latest_snapshot_comparisons(
    *,
    environment: str,
    period: str,
    org_unit: str,
    path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Map indicator_key → comparison payload from newest matching snapshot."""
    snaps = list_snapshots(
        environment=environment, period=period, org_unit=org_unit, limit=1, path=path
    )
    if not snaps:
        return {}
    full = get_snapshot(snaps[0]["id"], path=path) or {}
    out: dict[str, dict[str, Any]] = {}
    for row in full.get("comparisons") or []:
        key = row.get("indicator_key")
        if not key:
            continue
        out[key] = {
            "value": row.get("primary_value"),
            "numerator": row.get("numerator"),
            "denominator": row.get("denominator"),
            "period": row.get("period") or period,
            "org_unit": row.get("org_unit") or org_unit,
            "freshness": row.get("freshness") or full.get("created_at"),
            "reference": f"evidence_snapshot:{snaps[0]['id']}",
            "population_definition_reference": None,
            "numerator_label": row.get("numerator_label"),
            "denominator_label": row.get("denominator_label"),
            "age_range": None,
            "ip_non_ip_rule": None,
        }
    return out
