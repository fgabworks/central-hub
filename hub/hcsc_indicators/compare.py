"""Phase 3 read-only validation comparisons (no formula engine, no HTML scrape)."""

from __future__ import annotations

from typing import Any

from hub.hcsc_indicators.branding import (
    COMPARE_SOURCES,
    REVIEW_DIFFERENCES,
    SOURCE_DHIS2_ANALYTICS,
    comparison_source_label,
)
from hub.hcsc_indicators.validation import VALIDATION_STATUSES, compare_percentage

STATUS_UNAVAILABLE = "Comparison Source Unavailable"

ALL_STATUSES = VALIDATION_STATUSES + (STATUS_UNAVAILABLE,)


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _primary_value(row: dict[str, Any]) -> float | None:
    display = (row.get("display_result_type") or row.get("result_type") or "").lower()
    if "percent" in display or row.get("result_type") in {
        "percentage",
        "numerator_denominator_percentage",
        "ratio",
    }:
        return _as_float(row.get("percentage"))
    return _as_float(row.get("count"))


def definitions_compatible(primary: dict[str, Any], comparison: dict[str, Any]) -> tuple[bool, str]:
    """Return whether scopes are compatible for Exact Match classification."""
    checks = [
        ("period", "period"),
        ("org_unit", "org_unit"),
        ("population_definition_reference", "population_definition_reference"),
        ("age_range", "age_range"),
        ("ip_non_ip_rule", "ip_non_ip_rule"),
        ("numerator_label", "numerator_label"),
        ("denominator_label", "denominator_label"),
    ]
    mismatches: list[str] = []
    for a, b in checks:
        pa = (primary.get(a) or "").strip()
        cb = (comparison.get(b) or "").strip()
        if pa and cb and pa != cb:
            mismatches.append(a)
    if primary.get("validation_parity_note") or comparison.get("validation_parity_note"):
        return False, primary.get("validation_parity_note") or comparison.get("validation_parity_note") or (
            "Known definition parity gap."
        )
    if mismatches:
        return False, "Incompatible filters/definitions: " + ", ".join(mismatches)
    return True, "Compatible period/OU/population/N-D labels where both sides declare them."


def build_comparison_row(
    *,
    primary_row: dict[str, Any],
    comparison_source: str,
    comparison_payload: dict[str, Any] | None,
    scope: dict[str, Any],
) -> dict[str, Any]:
    """Build one validation comparison from registry + retrieved values only."""
    primary_source = SOURCE_DHIS2_ANALYTICS
    key = primary_row.get("indicator_key")
    note = ""
    status = "Not Yet Validated"
    comp = comparison_payload or {}
    unavailable = bool(comp.get("unavailable")) or comparison_payload is None

    primary_value = _primary_value(primary_row)
    primary_num = _as_float(primary_row.get("numerator"))
    primary_den = _as_float(primary_row.get("denominator"))

    if primary_row.get("unresolved"):
        status = "Not Yet Validated"
        note = primary_row.get("unresolved_notes") or primary_row.get("notes") or "Unresolved indicator."
        unavailable = True

    if unavailable and not primary_row.get("validation_parity_note"):
        status = STATUS_UNAVAILABLE if comparison_payload is not None or comparison_source else STATUS_UNAVAILABLE
        note = note or comp.get("reason") or "Comparison source not available (read-only; not invented)."

    compatible, compat_note = definitions_compatible(
        {
            **primary_row,
            "period": scope.get("period"),
            "org_unit": scope.get("org_unit"),
        },
        {
            **comp,
            "period": comp.get("period") or scope.get("period"),
            "org_unit": comp.get("org_unit") or scope.get("org_unit"),
        },
    )

    comparison_value = _as_float(comp.get("value") if "value" in comp else comp.get("percentage") or comp.get("count"))
    comparison_num = _as_float(comp.get("numerator"))
    comparison_den = _as_float(comp.get("denominator"))

    value_diff = None
    pp_diff = None
    num_diff = None
    den_diff = None
    if primary_value is not None and comparison_value is not None:
        value_diff = primary_value - comparison_value
        if primary_row.get("result_type") in {
            "percentage",
            "numerator_denominator_percentage",
            "ratio",
        } or (primary_row.get("display_result_type") or "") == "Percentage":
            pp_diff = value_diff
    if primary_num is not None and comparison_num is not None:
        num_diff = primary_num - comparison_num
    if primary_den is not None and comparison_den is not None:
        den_diff = primary_den - comparison_den

    if primary_row.get("validation_parity_note") or not compatible:
        status = "Expected Logic Difference"
        note = primary_row.get("validation_parity_note") or compat_note
    elif unavailable:
        status = STATUS_UNAVAILABLE
        note = note or compat_note
    elif comparison_source == "analytics_num_den" and primary_num is not None and primary_den not in (None, 0):
        recomputed = (primary_num / primary_den) * 100.0
        status = compare_percentage(primary_value, recomputed)
        note = f"{REVIEW_DIFFERENCES}: analytics value vs N/D from the same batched response."
        comparison_value = recomputed
        comparison_num = primary_num
        comparison_den = primary_den
        if primary_value is not None:
            pp_diff = primary_value - recomputed
            value_diff = pp_diff
    elif comparison_value is not None and primary_value is not None:
        status = compare_percentage(primary_value, comparison_value)
        note = note or compat_note
    elif primary_row.get("source_uid") and not unavailable:
        status = "Not Yet Validated"
        note = note or "Awaiting comparable second source values."

    evidence = {
        "primary_request": (primary_row.get("retrieval_method") or primary_source),
        "comparison_ref": comp.get("reference") or comparison_source,
        "approved_sql_query_id": primary_row.get("approved_sql_query_id"),
        "capability_reference": primary_row.get("capability_reference"),
        "sanitized": True,
        "invented": False,
        "sql_executed": False,
        "dhis2_writes": 0,
    }

    return {
        "indicator_key": key,
        "display_name": primary_row.get("display_name"),
        "section": primary_row.get("section"),
        "section_label": primary_row.get("section_label"),
        "display_group": primary_row.get("display_group"),
        "display_group_label": primary_row.get("display_group_label"),
        "classification": primary_row.get("classification"),
        "category": primary_row.get("category"),
        "primary_source": primary_source,
        "primary_source_label": primary_source,
        "comparison_source": comparison_source,
        "comparison_source_label": comparison_source_label(comparison_source),
        "compare_sources_label": COMPARE_SOURCES,
        "review_differences_label": REVIEW_DIFFERENCES,
        "primary_value": primary_value,
        "comparison_value": comparison_value,
        "numerator": primary_num,
        "denominator": primary_den,
        "comparison_numerator": comparison_num,
        "comparison_denominator": comparison_den,
        "numerator_label": primary_row.get("numerator_label"),
        "denominator_label": primary_row.get("denominator_label"),
        "value_diff": value_diff,
        "pp_diff": pp_diff,
        "numerator_diff": num_diff,
        "denominator_diff": den_diff,
        "population_compatible": compatible,
        "compatibility_note": compat_note,
        "validation_status": status,
        "note": note,
        "freshness": primary_row.get("freshness"),
        "comparison_freshness": comp.get("freshness"),
        "period": scope.get("period"),
        "org_unit": scope.get("org_unit"),
        "environment": scope.get("environment"),
        "unresolved": bool(primary_row.get("unresolved")),
        "evidence": evidence,
        "source_uid": primary_row.get("source_uid"),
        "open_mapping_url": (
            f"/dhis2/uid-explorer/{primary_row.get('source_uid')}"
            if primary_row.get("source_uid")
            else "/dhis2/uid-explorer"
        ),
        "open_sql_workspace_url": (
            f"/sql?query={primary_row.get('approved_sql_query_id')}"
            if primary_row.get("approved_sql_query_id")
            else ("/sql" if primary_row.get("approved_sql_reference") else None)
        ),
    }


def summarize_comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {s: 0 for s in ALL_STATUSES}
    for row in rows:
        st = row.get("validation_status") or "Not Yet Validated"
        counts[st] = counts.get(st, 0) + 1
    return {
        "total": len(rows),
        "by_status": counts,
        "comparable": sum(
            1
            for r in rows
            if r.get("validation_status")
            not in {STATUS_UNAVAILABLE, "Not Yet Validated"}
            or (
                r.get("comparison_value") is not None
                and not r.get("unresolved")
            )
        ),
        "not_comparable": sum(
            1
            for r in rows
            if r.get("unresolved")
            or r.get("validation_status") == STATUS_UNAVAILABLE
            or not r.get("population_compatible")
        ),
    }
