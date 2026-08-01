"""Validation status helpers for HCSC Overview (no formula engine)."""

from __future__ import annotations

from typing import Any

VALIDATION_STATUSES = (
    "Exact Match",
    "Rounding Difference",
    "Expected Logic Difference",
    "Unexplained Difference",
    "Not Yet Validated",
)


def status_for_unresolved() -> str:
    return "Not Yet Validated"


def compare_percentage(
    analytics_pct: float | None,
    recomputed_pct: float | None,
    *,
    rounding_tolerance: float = 0.15,
) -> str:
    """Compare DHIS2 indicator % vs N/D from the same analytics batch."""
    if analytics_pct is None or recomputed_pct is None:
        return "Not Yet Validated"
    diff = abs(float(analytics_pct) - float(recomputed_pct))
    if diff <= 1e-9:
        return "Exact Match"
    if diff <= rounding_tolerance:
        return "Rounding Difference"
    return "Unexplained Difference"


def validate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach validation_status without inventing external comparisons."""
    out = dict(row)
    parity = (out.get("validation_parity_note") or "").strip()
    if out.get("unresolved") or not out.get("source_uid"):
        out["validation_status"] = status_for_unresolved()
        out["validation_note"] = out.get("notes") or "Unresolved or missing UID — not validated."
        return out

    if parity:
        # Known HH vs member / Excel vs DHIS2 definition gaps — do not force Exact Match.
        out["validation_status"] = "Expected Logic Difference"
        out["validation_note"] = parity
        return out

    result_type = out.get("result_type")
    if result_type in {"numerator_denominator_percentage", "percentage", "ratio"}:
        num = out.get("numerator")
        den = out.get("denominator")
        pct = out.get("percentage")
        if num is not None and den not in (None, 0) and pct is not None:
            recomputed = (float(num) / float(den)) * 100.0
            out["validation_status"] = compare_percentage(float(pct), recomputed)
            out["validation_note"] = (
                "Compared analytics indicator value to N/D from the same batched response."
            )
        else:
            out["validation_status"] = "Not Yet Validated"
            out["validation_note"] = "Waiting for numerator/denominator companions in batch."
        return out

    # Count PIs retrieved via the same analytics path used by NPMO design.
    out["validation_status"] = "Not Yet Validated"
    out["validation_note"] = (
        "Retrieved via DHIS2 analytics (same dx UIDs as NPMO design). "
        "HTML report value compare is not scraped; mark Exact Match after manual/NPMO check."
    )
    return out
