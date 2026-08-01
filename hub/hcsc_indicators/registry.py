"""Load and cache the HCSC indicator registry YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hub.hcsc_indicators.cache import REGISTRY_CACHE
from hub.settings import ROOT_DIR

DEFAULT_REGISTRY_PATH = ROOT_DIR / "config" / "hcsc_indicators.yaml"

RESULT_TYPES = frozenset(
    {
        "count",
        "numerator_denominator_percentage",
        "percentage",
        "derived_status",
        "disaggregation",
    }
)
SOURCE_OWNERS = frozenset({"DHIS2", "Live Processing", "data_scripts"})


class RegistryError(ValueError):
    """Invalid indicator registry."""


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_indicator(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RegistryError("Each indicator must be a mapping.")
    key = _as_str(raw.get("key"))
    if not key:
        raise RegistryError("Indicator key is required.")
    result_type = _as_str(raw.get("result_type")) or ""
    if result_type not in RESULT_TYPES:
        raise RegistryError(f"Invalid result_type for {key}: {result_type}")
    owner = _as_str(raw.get("source_owner")) or ""
    if owner not in SOURCE_OWNERS:
        raise RegistryError(f"Invalid source_owner for {key}: {owner}")
    uids_raw = raw.get("dhis2_uids") or {}
    if uids_raw is None:
        uids_raw = {}
    if not isinstance(uids_raw, dict):
        raise RegistryError(f"dhis2_uids must be a mapping for {key}")
    uids = {str(k): str(v).strip() for k, v in uids_raw.items() if v}
    unresolved = bool(raw.get("unresolved"))
    value_uid = uids.get("value")
    if not unresolved and not value_uid and result_type != "derived_status":
        raise RegistryError(f"Resolved indicator {key} requires dhis2_uids.value")
    return {
        "key": key,
        "display_name": _as_str(raw.get("display_name")) or key,
        "category": _as_str(raw.get("category")) or "other",
        "result_type": result_type,
        "source_owner": owner,
        "source_type": _as_str(raw.get("source_type")) or "",
        "dhis2_uids": uids,
        "numerator_label": _as_str(raw.get("numerator_label")),
        "denominator_label": _as_str(raw.get("denominator_label")),
        "percentage_formula_reference": _as_str(raw.get("percentage_formula_reference")),
        "population_definition_reference": _as_str(raw.get("population_definition_reference")),
        "source_table_view_reference": _as_str(raw.get("source_table_view_reference")),
        "repository_file_reference": _as_str(raw.get("repository_file_reference")),
        "quarter_rule_reference": _as_str(raw.get("quarter_rule_reference")),
        "organisation_unit_rule": _as_str(raw.get("organisation_unit_rule")),
        "ip_non_ip_rule": _as_str(raw.get("ip_non_ip_rule")),
        "confidence": _as_str(raw.get("confidence")) or "medium",
        "validation_source": _as_str(raw.get("validation_source")),
        "notes": _as_str(raw.get("notes")),
        "overview": bool(raw.get("overview", True)),
        "unresolved": unresolved,
        "age_range": _as_str(raw.get("age_range")),
    }


def load_registry(path: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_REGISTRY_PATH
    cache_key = f"registry:{registry_path.resolve()}"
    if not force:
        cached = REGISTRY_CACHE.get(cache_key)
        if cached is not None:
            return cached
    if not registry_path.is_file():
        raise RegistryError(f"Registry file not found: {registry_path}")
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RegistryError("Registry root must be a mapping.")
    indicators = [_normalize_indicator(row) for row in (data.get("indicators") or [])]
    keys = [row["key"] for row in indicators]
    if len(keys) != len(set(keys)):
        raise RegistryError("Duplicate indicator keys in registry.")
    payload = {
        "ok": True,
        "path": str(registry_path),
        "npmo_report_uid": _as_str(data.get("npmo_report_uid")) or "qTQD08sNuzZ",
        "npmo_report_name": _as_str(data.get("npmo_report_name"))
        or "HCSC Summary Tables (NPMO)",
        "indicators": indicators,
        "overview_indicators": [row for row in indicators if row.get("overview")],
        "unresolved_keys": [row["key"] for row in indicators if row.get("unresolved")],
    }
    REGISTRY_CACHE.set(cache_key, payload)
    return payload


def indicator_by_key(registry: dict[str, Any], key: str) -> dict[str, Any] | None:
    for row in registry.get("indicators") or []:
        if row.get("key") == key:
            return row
    return None


def collect_analytics_uids(indicators: list[dict[str, Any]]) -> list[str]:
    """Unique value/numerator/denominator UIDs for a single batched analytics call."""
    seen: list[str] = []
    for row in indicators:
        if row.get("unresolved"):
            continue
        for role in ("value", "numerator", "denominator"):
            uid = (row.get("dhis2_uids") or {}).get(role)
            if uid and uid not in seen:
                seen.append(uid)
    return seen
