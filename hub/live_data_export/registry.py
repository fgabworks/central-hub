"""Load and validate the approved Live Data Export registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hub.settings import ROOT_DIR

IDENT_RE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class RegistryError(ValueError):
    """Invalid or missing registry configuration."""


@dataclass(frozen=True)
class SortSpec:
    column: str
    direction: str = "asc"


@dataclass
class ExportSource:
    source_key: str
    display_name: str
    source_type: str  # table | view | saved_query
    status: str  # verified | unverified | disabled
    enabled: bool
    description: str
    source_owner: str
    repository: str
    connection_id: str
    schema: str
    object_name: str
    saved_query_id: str
    allowed_columns: list[str]
    default_columns: list[str]
    sensitive_columns: list[str]
    excluded_columns: list[str]
    required_filters: list[str]
    filters_supported: list[str]
    quarter_column: str
    organisation_unit_column: str
    date_column: str
    status_column: str
    ip_column: str
    maximum_rows: int
    supported_formats: list[str]
    default_sort: list[SortSpec]
    enabled_environments: list[str]
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.enabled) and self.status == "verified" and bool(self.allowed_columns)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "display_name": self.display_name,
            "source_type": self.source_type,
            "status": self.status,
            "enabled": self.enabled,
            "available": self.available,
            "description": self.description,
            "source_owner": self.source_owner,
            "repository": self.repository,
            "allowed_columns": list(self.allowed_columns),
            "default_columns": list(self.default_columns),
            "sensitive_columns": list(self.sensitive_columns),
            "excluded_columns": list(self.excluded_columns),
            "required_filters": list(self.required_filters),
            "filters_supported": list(self.filters_supported),
            "quarter_column": self.quarter_column,
            "organisation_unit_column": self.organisation_unit_column,
            "date_column": self.date_column,
            "status_column": self.status_column,
            "ip_column": self.ip_column,
            "maximum_rows": self.maximum_rows,
            "supported_formats": list(self.supported_formats),
            "default_sort": [
                {"column": s.column, "direction": s.direction} for s in self.default_sort
            ],
            "enabled_environments": list(self.enabled_environments),
            "unavailable_reason": self.unavailable_reason,
            # Never expose connection credentials; connection_id is ok (profile key).
            "connection_id": self.connection_id if self.available else "",
            "schema": self.schema if self.available else "",
            "object_name": self.object_name if self.available else "",
            "saved_query_id": self.saved_query_id if self.available else "",
        }


@dataclass
class ExportDefaults:
    max_rows_sync: int = 5000
    max_rows_hard: int = 100000
    preview_rows: int = 25
    download_ttl_seconds: int = 86400
    large_export_rows: int = 5000
    formats: list[str] = field(default_factory=lambda: ["csv", "xlsx", "csv_gz"])
    connection_by_environment: dict[str, str] = field(
        default_factory=lambda: {"live": "live-ro", "stage": "stage-ro", "dev": "local-demo"}
    )


class LiveExportRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (ROOT_DIR / "config" / "live_data_exports.yaml")
        self.defaults = ExportDefaults()
        self.sources: dict[str, ExportSource] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.is_file():
            raise RegistryError(f"Export registry not found: {self.path}")
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        d = raw.get("defaults") or {}
        self.defaults = ExportDefaults(
            max_rows_sync=int(d.get("max_rows_sync", 5000)),
            max_rows_hard=int(d.get("max_rows_hard", 100000)),
            preview_rows=int(d.get("preview_rows", 25)),
            download_ttl_seconds=int(d.get("download_ttl_seconds", 86400)),
            large_export_rows=int(d.get("large_export_rows", 5000)),
            formats=list(d.get("formats") or ["csv", "xlsx", "csv_gz"]),
            connection_by_environment=dict(
                d.get("connection_by_environment")
                or {"live": "live-ro", "stage": "stage-ro", "dev": "local-demo"}
            ),
        )
        sources: dict[str, ExportSource] = {}
        for item in raw.get("sources") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("source_key") or "").strip()
            if not key:
                continue
            sort_specs: list[SortSpec] = []
            for s in item.get("default_sort") or []:
                if isinstance(s, dict) and s.get("column"):
                    direction = str(s.get("direction") or "asc").lower()
                    if direction not in ("asc", "desc"):
                        direction = "asc"
                    sort_specs.append(SortSpec(column=str(s["column"]), direction=direction))
            src = ExportSource(
                source_key=key,
                display_name=str(item.get("display_name") or key),
                source_type=str(item.get("source_type") or "table").lower(),
                status=str(item.get("status") or "unverified").lower(),
                enabled=bool(item.get("enabled", False)),
                description=str(item.get("description") or ""),
                source_owner=str(item.get("source_owner") or ""),
                repository=str(item.get("repository") or ""),
                connection_id=str(item.get("connection_id") or ""),
                schema=str(item.get("schema") or ""),
                object_name=str(item.get("object_name") or ""),
                saved_query_id=str(item.get("saved_query_id") or ""),
                allowed_columns=[str(c) for c in (item.get("allowed_columns") or [])],
                default_columns=[str(c) for c in (item.get("default_columns") or [])],
                sensitive_columns=[str(c) for c in (item.get("sensitive_columns") or [])],
                excluded_columns=[str(c) for c in (item.get("excluded_columns") or [])],
                required_filters=[str(f) for f in (item.get("required_filters") or [])],
                filters_supported=[str(f) for f in (item.get("filters_supported") or [])],
                quarter_column=str(item.get("quarter_column") or ""),
                organisation_unit_column=str(item.get("organisation_unit_column") or ""),
                date_column=str(item.get("date_column") or ""),
                status_column=str(item.get("status_column") or ""),
                ip_column=str(item.get("ip_column") or ""),
                maximum_rows=int(item.get("maximum_rows") or self.defaults.max_rows_hard),
                supported_formats=[
                    str(f) for f in (item.get("supported_formats") or self.defaults.formats)
                ],
                default_sort=sort_specs,
                enabled_environments=[
                    str(e).lower() for e in (item.get("enabled_environments") or ["live"])
                ],
                unavailable_reason=str(item.get("unavailable_reason") or ""),
            )
            if src.source_type not in ("table", "view", "saved_query"):
                raise RegistryError(f"{key}: invalid source_type {src.source_type}")
            sources[key] = src
        self.sources = sources

    def list_sources(self, *, environment: str | None = None) -> list[ExportSource]:
        env = (environment or "").lower().strip() or None
        out = list(self.sources.values())
        if env:
            out = [s for s in out if env in s.enabled_environments or not s.enabled_environments]
        return sorted(out, key=lambda s: (not s.available, s.display_name.lower()))

    def get(self, source_key: str) -> ExportSource | None:
        return self.sources.get(str(source_key or "").strip())

    def require_available(self, source_key: str, *, environment: str) -> ExportSource:
        src = self.get(source_key)
        if src is None:
            raise RegistryError(f"Unknown export source: {source_key}")
        if not src.available:
            reason = src.unavailable_reason or f"Source '{source_key}' is not available"
            raise RegistryError(reason)
        env = environment.lower().strip()
        if env and env not in src.enabled_environments:
            raise RegistryError(f"Source '{source_key}' is not enabled for environment '{env}'")
        return src


@lru_cache(maxsize=1)
def get_registry() -> LiveExportRegistry:
    return LiveExportRegistry()


def clear_registry_cache() -> None:
    get_registry.cache_clear()
