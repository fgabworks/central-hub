"""Presentation helpers for HCSC Indicator Summary (no formula engine).

Maps registry metadata into UI-facing labels, badges, and calculation-basis text.
Never invents SQL or HCSC formulas — only formats registry + analytics values.
"""

from __future__ import annotations

from typing import Any

# Canonical UI result types (mockup).
DISPLAY_RESULT_TYPES = ("Count", "Percentage", "Ratio", "Status", "Disaggregation")

# Internal registry aliases → display type.
_RESULT_TYPE_MAP = {
    "count": "Count",
    "percentage": "Percentage",
    "numerator_denominator_percentage": "Percentage",
    "ratio": "Ratio",
    "status": "Status",
    "derived_status": "Status",
    "disaggregation": "Disaggregation",
}

# source_type → badge code
_SOURCE_BADGE_MAP = {
    "program_indicator": "PI",
    "indicator": "IND",
    "aggregate_indicator": "IND",
    "data_element": "DE",
    "approved_sql": "SQL",
    "sql": "SQL",
    "live_processing": "LP",
    "live_processing_capability": "LP",
    "report_client_computed": "PI",  # unresolved NPMO chrome; still DHIS2-side
}

_SOURCE_BADGE_LABELS = {
    "PI": "Program Indicator",
    "IND": "Aggregate Indicator",
    "DE": "Data Element",
    "SQL": "Approved query",
    "LP": "Live Processing capability",
}


def normalize_result_type(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    if key in _RESULT_TYPE_MAP:
        return _RESULT_TYPE_MAP[key]
    # Already a display label?
    for label in DISPLAY_RESULT_TYPES:
        if key == label.lower():
            return label
    return "Count"


def source_badge(source_type: str | None, *, source_owner: str | None = None) -> dict[str, str]:
    st = (source_type or "").strip().lower()
    code = _SOURCE_BADGE_MAP.get(st)
    if not code:
        owner = (source_owner or "").strip().lower()
        if "live processing" in owner:
            code = "LP"
        elif "data_scripts" in owner or "sql" in st:
            code = "SQL"
        else:
            code = "PI"
    return {
        "code": code,
        "label": _SOURCE_BADGE_LABELS.get(code, code),
    }


def format_number(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}"


def format_percentage(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return f"{n:.2f}%"


def calculation_basis(row: dict[str, Any]) -> str | None:
    """Human-readable N/D basis under percentage values — from registry labels + values only."""
    display_type = normalize_result_type(row.get("result_type") or row.get("display_result_type"))
    if display_type not in {"Percentage", "Ratio"}:
        return None
    num = row.get("numerator")
    den = row.get("denominator")
    num_label = (row.get("numerator_label") or "numerator").strip()
    den_label = (row.get("denominator_label") or "denominator").strip()
    num_s = format_number(num)
    den_s = format_number(den)
    if num_s is None or den_s is None:
        # Fall back to formula reference when companions missing — do not invent counts.
        formula = row.get("percentage_formula_reference")
        return str(formula) if formula else None
    if display_type == "Ratio":
        return f"{num_s} {num_label} per {den_s} {den_label}"
    return f"{num_s} {num_label} out of {den_s} {den_label}"


def primary_value_display(row: dict[str, Any]) -> dict[str, Any]:
    display_type = normalize_result_type(row.get("result_type") or row.get("display_result_type"))
    if row.get("unresolved"):
        return {
            "display_result_type": display_type,
            "value_text": "Unresolved",
            "value_subtext": row.get("notes"),
            "calculation_basis": None,
        }
    if display_type == "Count":
        return {
            "display_result_type": display_type,
            "value_text": format_number(row.get("count")) or "—",
            "value_subtext": None,
            "calculation_basis": None,
        }
    if display_type in {"Percentage", "Ratio"}:
        pct = format_percentage(row.get("percentage"))
        basis = calculation_basis(row)
        return {
            "display_result_type": display_type,
            "value_text": pct or "—",
            "value_subtext": None,
            "calculation_basis": basis,
        }
    if display_type == "Status":
        return {
            "display_result_type": display_type,
            "value_text": format_number(row.get("count")) or row.get("notes") or "—",
            "value_subtext": None,
            "calculation_basis": None,
        }
    return {
        "display_result_type": display_type,
        "value_text": format_number(row.get("count")) or "—",
        "value_subtext": None,
        "calculation_basis": None,
    }


def classification_badge(classification: str | None, *, unresolved: bool = False) -> dict[str, str]:
    raw = (classification or "").strip()
    if unresolved or raw.lower() == "unresolved" or not raw:
        return {"code": "unresolved", "label": "Unresolved"}
    if raw == "HCSC + RF":
        return {"code": "hcsc-rf", "label": "HCSC + RF"}
    if raw == "HCSC":
        return {"code": "hcsc", "label": "HCSC"}
    if raw == "RF":
        return {"code": "rf", "label": "RF"}
    return {"code": "unresolved", "label": "Unresolved"}


def enrich_result_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach display-only fields for the Indicator Summary table."""
    out = dict(row)
    badge = source_badge(out.get("source_type"), source_owner=out.get("source_owner"))
    class_badge = classification_badge(
        out.get("classification"),
        unresolved=bool(out.get("classification_unresolved") or out.get("unresolved")),
    )
    display = primary_value_display(out)
    out["display_result_type"] = display["display_result_type"]
    out["value_text"] = display["value_text"]
    out["value_subtext"] = display["value_subtext"]
    out["calculation_basis"] = display["calculation_basis"]
    out["source_badge"] = badge["code"]
    out["source_badge_label"] = badge["label"]
    out["classification_badge"] = class_badge["code"]
    out["classification_badge_label"] = class_badge["label"]
    out["population_scope"] = out.get("population_definition_reference") or "—"
    if out.get("age_range"):
        out["population_scope"] = f"{out['population_scope']} · Age: {out['age_range']}"
    out["source_display"] = out.get("source_badge_label") or out.get("source_type") or "—"
    if out.get("source_owner"):
        out["source_display"] = f"{out['source_display']} · {out['source_owner']}"
    out["uid_tooltip"] = {
        "source_type": out.get("source_badge_label") or out.get("source_type"),
        "uid": out.get("source_uid") or (out.get("dhis2_uids") or {}).get("value"),
        "source_name": out.get("display_name"),
        "source_owner": out.get("source_owner"),
        "source_object": out.get("source_table_view_reference"),
        "definition": out.get("population_definition_reference") or out.get("notes"),
        "classification": out.get("classification_badge_label"),
    }
    return out
