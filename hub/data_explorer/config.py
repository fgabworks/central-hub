"""Load Data Explorer configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hub.settings import ROOT_DIR


@dataclass
class ColumnPolicy:
    pattern: re.Pattern[str]
    action: str  # hide | mask | export_prohibit | preview_only


@dataclass
class ClassPattern:
    group: str
    pattern: re.Pattern[str]
    likely_role: str


@dataclass
class ExplorerDefaults:
    page_size: int = 100
    max_page_size: int = 500
    max_export_rows: int = 50000
    max_rows_sync: int = 5000
    metadata_cache_ttl_seconds: int = 300
    approximate_count_threshold: int = 100000
    connection_by_environment: dict[str, str] = field(
        default_factory=lambda: {"live": "live-ro", "stage": "stage-ro", "dev": "local-demo"}
    )
    excluded_schemas: list[str] = field(
        default_factory=lambda: ["pg_catalog", "information_schema", "pg_toast"]
    )
    formats: list[str] = field(default_factory=lambda: ["csv", "xlsx", "csv_gz"])


@dataclass
class GroupPolicy:
    sensitivity: str
    browse: str
    export: str


@dataclass
class ExplorerConfig:
    defaults: ExplorerDefaults
    column_policies: list[ColumnPolicy]
    classification_patterns: list[ClassPattern]
    group_policies: dict[str, GroupPolicy]
    object_overrides: dict[str, dict[str, Any]]


def load_explorer_config(path: Path | None = None) -> ExplorerConfig:
    cfg_path = path or (ROOT_DIR / "config" / "data_explorer.yaml")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    d = raw.get("defaults") or {}
    defaults = ExplorerDefaults(
        page_size=int(d.get("page_size", 100)),
        max_page_size=int(d.get("max_page_size", 500)),
        max_export_rows=int(d.get("max_export_rows", 50000)),
        max_rows_sync=int(d.get("max_rows_sync", 5000)),
        metadata_cache_ttl_seconds=int(d.get("metadata_cache_ttl_seconds", 300)),
        approximate_count_threshold=int(d.get("approximate_count_threshold", 100000)),
        connection_by_environment=dict(
            d.get("connection_by_environment")
            or {"live": "live-ro", "stage": "stage-ro", "dev": "local-demo"}
        ),
        excluded_schemas=[str(s) for s in (d.get("excluded_schemas") or [])],
        formats=[str(f) for f in (d.get("formats") or ["csv", "xlsx", "csv_gz"])],
    )
    col_pols = [
        ColumnPolicy(pattern=re.compile(str(p["match"])), action=str(p.get("action") or "hide"))
        for p in (raw.get("column_policies") or [])
        if isinstance(p, dict) and p.get("match")
    ]
    class_pats = [
        ClassPattern(
            group=str(p["group"]),
            pattern=re.compile(str(p["pattern"])),
            likely_role=str(p.get("likely_role") or ""),
        )
        for p in (raw.get("classification_patterns") or [])
        if isinstance(p, dict) and p.get("group") and p.get("pattern")
    ]
    group_pols: dict[str, GroupPolicy] = {}
    for name, gp in (raw.get("group_policies") or {}).items():
        if not isinstance(gp, dict):
            continue
        group_pols[str(name)] = GroupPolicy(
            sensitivity=str(gp.get("sensitivity") or "unresolved"),
            browse=str(gp.get("browse") or "preview_only"),
            export=str(gp.get("export") or "deny"),
        )
    overrides: dict[str, dict[str, Any]] = {}
    for item in raw.get("object_overrides") or []:
        if isinstance(item, dict) and item.get("object"):
            overrides[str(item["object"]).lower()] = item
    return ExplorerConfig(
        defaults=defaults,
        column_policies=col_pols,
        classification_patterns=class_pats,
        group_policies=group_pols,
        object_overrides=overrides,
    )


@lru_cache(maxsize=1)
def get_explorer_config() -> ExplorerConfig:
    return load_explorer_config()


def clear_explorer_config_cache() -> None:
    get_explorer_config.cache_clear()
