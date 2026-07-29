"""Scan configured repository mapping files into normalized UID records."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from hub.dhis2.uid_mapping.audit_profile import enrich_record_mapping_fields
from hub.dhis2.uid_mapping.models import (
    SOURCE_CSV,
    SOURCE_DHIS2_IMPORT,
    SOURCE_MANUAL,
    NormalizedUidRecord,
)
from hub.settings import ROOT_DIR

_DEFAULT_CONFIG = ROOT_DIR / "config" / "uid_mapping_sources.yaml"

# Common column aliases when no explicit column_map is provided.
_DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "uid": ("uid", "id", "UID", "Id"),
    "name": ("name", "displayName", "Name"),
    "code": ("code", "Code"),
    "object_type": ("object_type", "kind", "type", "resource_type", "metadata_type"),
    "value_type": ("value_type", "valueType"),
    "domain_type": ("domain_type", "domainType"),
    "program_uid": ("program_uid", "program", "programId"),
    "program_stage_uid": ("program_stage_uid", "programStage", "program_stage"),
    "option_set_uid": ("option_set_uid", "optionSet", "option_set"),
    "category_combo_uid": ("category_combo_uid", "categoryCombo", "category_combo"),
    "source_environment": ("source_environment", "dhis2_environment", "environment", "env"),
}


def load_sources_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _DEFAULT_CONFIG
    if not cfg_path.is_file():
        return {"sources": [], "defaults": {"index_path": "data/dhis2/uid_index/latest.json"}}
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"sources": [], "defaults": {}}
    data.setdefault("sources", [])
    data.setdefault("defaults", {})
    return data


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _pick(row: dict[str, Any], field: str, column_map: dict[str, str]) -> str:
    if field in column_map:
        key = column_map[field]
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    for alias in _DEFAULT_ALIASES.get(field, ()):
        if alias in row and row[alias] not in (None, ""):
            return str(row[alias]).strip()
    return ""


def normalize_row(
    row: dict[str, Any],
    *,
    source: dict[str, Any],
    source_file: str,
    column_map: dict[str, str] | None = None,
) -> NormalizedUidRecord | None:
    column_map = column_map or {}
    uid = _pick(row, "uid", column_map)
    if not uid:
        return None
    env = _pick(row, "source_environment", column_map) or str(source.get("environment") or "")
    origin = str(source.get("source_origin") or row.get("source_origin") or SOURCE_CSV).strip()
    if origin not in {SOURCE_CSV, SOURCE_DHIS2_IMPORT, SOURCE_MANUAL}:
        origin = SOURCE_CSV
    if "csv_synced" in row:
        csv_synced = str(row.get("csv_synced")).strip().lower() in {"1", "true", "yes"}
    else:
        csv_synced = origin == SOURCE_CSV
    record = NormalizedUidRecord.from_mapping(
        {
            "uid": uid,
            "name": _pick(row, "name", column_map),
            "code": _pick(row, "code", column_map),
            "object_type": _pick(row, "object_type", column_map),
            "value_type": _pick(row, "value_type", column_map),
            "domain_type": _pick(row, "domain_type", column_map),
            "source_repository": str(source.get("repository_id") or source.get("id") or ""),
            "source_file": source_file,
            "source_environment": env,
            "program_uid": _pick(row, "program_uid", column_map),
            "program_stage_uid": _pick(row, "program_stage_uid", column_map),
            "option_set_uid": _pick(row, "option_set_uid", column_map),
            "category_combo_uid": _pick(row, "category_combo_uid", column_map),
            "source_origin": origin,
            "csv_synced": csv_synced,
            "extras": {
                k: v
                for k, v in row.items()
                if k
                not in {
                    column_map.get("uid"),
                    column_map.get("name"),
                    column_map.get("code"),
                    "uid",
                    "id",
                    "name",
                    "code",
                    "source_origin",
                    "csv_synced",
                }
                and v not in (None, "")
            },
        }
    )
    return enrich_record_mapping_fields(record)


def parse_csv_text(
    text: str,
    *,
    source: dict[str, Any],
    source_file: str,
) -> list[NormalizedUidRecord]:
    column_map = dict(source.get("column_map") or {})
    reader = csv.DictReader(text.splitlines())
    records: list[NormalizedUidRecord] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        cleaned = {str(k): (v if v is not None else "") for k, v in row.items() if k}
        rec = normalize_row(cleaned, source=source, source_file=source_file, column_map=column_map)
        if rec:
            records.append(rec)
    return records


def parse_json_text(
    text: str,
    *,
    source: dict[str, Any],
    source_file: str,
) -> list[NormalizedUidRecord]:
    column_map = dict(source.get("column_map") or {})
    data = json.loads(text)
    rows: list[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("records", "items", "mappings", "data"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        else:
            rows = [data]
    else:
        return []

    records: list[NormalizedUidRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rec = normalize_row(row, source=source, source_file=source_file, column_map=column_map)
        if rec:
            records.append(rec)
    return records


def scan_source(source: dict[str, Any]) -> dict[str, Any]:
    """Scan one configured source file. Does not write the index."""
    source_id = str(source.get("id") or "unknown")
    if not source.get("enabled", True):
        return {
            "ok": False,
            "source_id": source_id,
            "error": "Source disabled",
            "records": [],
            "count": 0,
        }
    raw_path = source.get("path")
    if not raw_path:
        return {
            "ok": False,
            "source_id": source_id,
            "error": "No path configured",
            "records": [],
            "count": 0,
        }
    path = _resolve_path(str(raw_path))
    if not path.is_file():
        return {
            "ok": False,
            "source_id": source_id,
            "error": f"File not found: {path}",
            "records": [],
            "count": 0,
            "path": str(path),
        }

    fmt = str(source.get("format") or path.suffix.lstrip(".")).lower()
    text = path.read_text(encoding="utf-8-sig")
    rel = str(path)
    try:
        if fmt == "csv":
            records = parse_csv_text(text, source=source, source_file=rel)
        elif fmt in {"json", "jsonl"}:
            if fmt == "jsonl":
                records = []
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    records.extend(parse_json_text(line, source=source, source_file=rel))
            else:
                records = parse_json_text(text, source=source, source_file=rel)
        else:
            return {
                "ok": False,
                "source_id": source_id,
                "error": f"Unsupported format: {fmt}",
                "records": [],
                "count": 0,
                "path": str(path),
            }
    except (OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        return {
            "ok": False,
            "source_id": source_id,
            "error": str(exc),
            "records": [],
            "count": 0,
            "path": str(path),
        }

    return {
        "ok": True,
        "source_id": source_id,
        "label": source.get("label") or source_id,
        "path": str(path),
        "format": fmt,
        "records": records,
        "count": len(records),
    }


def scan_all_sources(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_sources_config()
    sources = list(cfg.get("sources") or [])
    results = [scan_source(src) for src in sources if isinstance(src, dict)]
    records: list[NormalizedUidRecord] = []
    for item in results:
        records.extend(item.get("records") or [])
    return {
        "ok": all(item.get("ok") for item in results) if results else False,
        "sources": [
            {k: v for k, v in item.items() if k != "records"} | {"count": item.get("count", 0)}
            for item in results
        ],
        "records": records,
        "count": len(records),
    }
