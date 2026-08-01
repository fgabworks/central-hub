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
        "ratio",
        "derived_status",
        "status",
        "disaggregation",
        # Display labels also accepted
        "Count",
        "Percentage",
        "Ratio",
        "Status",
        "Disaggregation",
    }
)
SOURCE_OWNERS = frozenset({"DHIS2", "Live Processing", "data_scripts"})

SECTIONS = (
    "eligible_beneficiaries",
    "maternal_health",
    "child_nutrition_health",
    "household_wash_sbc",
    "food_security",
    "convergence",
    "data_mapping",
    "validation",
)

SECTION_LABELS = {
    "eligible_beneficiaries": "Eligible Beneficiaries",
    "maternal_health": "Maternal Health",
    "child_nutrition_health": "Child Nutrition & Health",
    "household_wash_sbc": "Household / WASH / SBC",
    "food_security": "Food Security",
    "convergence": "Convergence",
    "data_mapping": "Data Mapping",
    "validation": "Validation",
}

_CATEGORY_TO_SECTION = {
    "household": "convergence",
    "maternal": "maternal_health",
    "child": "child_nutrition_health",
    "demographics": "eligible_beneficiaries",
    "wash": "household_wash_sbc",
    "sbc": "household_wash_sbc",
    "food_security": "food_security",
    "convergence": "convergence",
}


class RegistryError(ValueError):
    """Invalid indicator registry."""


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _canonical_result_type(raw: str, *, has_num_den: bool) -> str:
    """Store a stable internal result_type while accepting UI labels."""
    key = (raw or "").strip()
    lower = key.lower()
    if lower in {"numerator_denominator_percentage"} or (
        lower in {"percentage"} and has_num_den
    ):
        return "numerator_denominator_percentage"
    if lower in {"percentage"}:
        return "percentage"
    if lower in {"ratio"}:
        return "ratio"
    if lower in {"derived_status"}:
        return "derived_status"
    if lower in {"status"}:
        return "status"
    if lower in {"disaggregation"}:
        return "disaggregation"
    if lower in {"count"}:
        return "count"
    # Display labels
    from hub.hcsc_indicators.presentation import normalize_result_type

    display = normalize_result_type(key)
    mapped = {
        "Count": "count",
        "Percentage": "numerator_denominator_percentage" if has_num_den else "percentage",
        "Ratio": "ratio",
        "Status": "status",
        "Disaggregation": "disaggregation",
    }.get(display, "count")
    return mapped


def _normalize_indicator(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RegistryError("Each indicator must be a mapping.")
    key = _as_str(raw.get("key"))
    if not key:
        raise RegistryError("Indicator key is required.")
    result_raw = _as_str(raw.get("result_type")) or ""
    allowed = {r.lower() for r in RESULT_TYPES}
    if result_raw.lower() not in allowed:
        raise RegistryError(f"Invalid result_type for {key}: {result_raw}")
    uids_raw = raw.get("dhis2_uids") or {}
    if uids_raw is None:
        uids_raw = {}
    if not isinstance(uids_raw, dict):
        raise RegistryError(f"dhis2_uids must be a mapping for {key}")
    uids = {str(k): str(v).strip() for k, v in uids_raw.items() if v}
    has_num_den = bool(uids.get("numerator") or uids.get("denominator") or raw.get("numerator_label"))
    result_type = _canonical_result_type(result_raw, has_num_den=has_num_den)
    owner = _as_str(raw.get("source_owner")) or ""
    if owner not in SOURCE_OWNERS:
        raise RegistryError(f"Invalid source_owner for {key}: {owner}")
    unresolved = bool(raw.get("unresolved"))
    value_uid = uids.get("value")
    if not unresolved and not value_uid and result_type not in {"derived_status", "status"}:
        raise RegistryError(f"Resolved indicator {key} requires dhis2_uids.value")
    category = _as_str(raw.get("category")) or "other"
    section = _as_str(raw.get("section")) or _CATEGORY_TO_SECTION.get(category, "eligible_beneficiaries")
    if section not in SECTION_LABELS:
        raise RegistryError(f"Invalid section for {key}: {section}")
    # Explicit phase wins; overview indicators default to 1, everything else to 2.
    if raw.get("phase") is not None:
        phase = int(raw.get("phase"))
    elif raw.get("overview"):
        phase = 1
    else:
        phase = 2
    adapter = _as_str(raw.get("adapter")) or (
        "dhis2_analytics"
        if value_uid
        else (
            "approved_sql"
            if raw.get("approved_sql_reference") or raw.get("approved_sql_query_id")
            else ("connected_capability" if raw.get("capability_reference") else "unresolved")
        )
    )
    return {
        "key": key,
        "display_name": _as_str(raw.get("display_name")) or key,
        "category": category,
        "section": section,
        "section_label": SECTION_LABELS[section],
        "phase": phase,
        "adapter": adapter,
        "result_type": result_type,
        "source_owner": owner,
        "source_type": _as_str(raw.get("source_type")) or "",
        "dhis2_uids": uids,
        "numerator_label": _as_str(raw.get("numerator_label")),
        "denominator_label": _as_str(raw.get("denominator_label")),
        "percentage_formula_reference": _as_str(raw.get("percentage_formula_reference")),
        "population_definition_reference": _as_str(raw.get("population_definition_reference")),
        "source_table_view_reference": _as_str(raw.get("source_table_view_reference")),
        "source_columns_reference": _as_str(raw.get("source_columns_reference")),
        "repository_file_reference": _as_str(raw.get("repository_file_reference")),
        "quarter_rule_reference": _as_str(raw.get("quarter_rule_reference")),
        "organisation_unit_rule": _as_str(raw.get("organisation_unit_rule")),
        "status_filters_reference": _as_str(raw.get("status_filters_reference")),
        "ip_non_ip_rule": _as_str(raw.get("ip_non_ip_rule")),
        "lineage_reference": _as_str(raw.get("lineage_reference")),
        "definition": _as_str(raw.get("definition")) or _as_str(raw.get("population_definition_reference")),
        "confidence": _as_str(raw.get("confidence")) or "medium",
        "validation_source": _as_str(raw.get("validation_source")),
        "validation_parity_note": _as_str(raw.get("validation_parity_note")),
        "notes": _as_str(raw.get("notes")),
        "overview": bool(raw.get("overview", False)),
        "unresolved": unresolved,
        "age_range": _as_str(raw.get("age_range")),
        "approved_sql_reference": _as_str(raw.get("approved_sql_reference")),
        "approved_sql_query_id": _as_str(raw.get("approved_sql_query_id")),
        "capability_reference": _as_str(raw.get("capability_reference")),
        "hcsc_excel_key": _as_str(raw.get("hcsc_excel_key")),
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
    by_section: dict[str, list[dict[str, Any]]] = {s: [] for s in SECTIONS}
    for row in indicators:
        by_section.setdefault(row["section"], []).append(row)
    payload = {
        "ok": True,
        "path": str(registry_path),
        "npmo_report_uid": _as_str(data.get("npmo_report_uid")) or "qTQD08sNuzZ",
        "npmo_report_name": _as_str(data.get("npmo_report_name"))
        or "HCSC Summary Tables (NPMO)",
        "indicators": indicators,
        "overview_indicators": [row for row in indicators if row.get("overview")],
        "unresolved_keys": [row["key"] for row in indicators if row.get("unresolved")],
        "sections": [
            {
                "id": sid,
                "label": SECTION_LABELS[sid],
                "indicator_keys": [r["key"] for r in by_section.get(sid) or []],
                "count": len(by_section.get(sid) or []),
            }
            for sid in SECTIONS
            if sid not in {"data_mapping", "validation"} or by_section.get(sid)
        ],
        "by_section": {k: v for k, v in by_section.items() if v},
        "phase2_keys": [row["key"] for row in indicators if int(row.get("phase") or 1) >= 2],
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
