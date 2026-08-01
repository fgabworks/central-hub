"""HCSC–RF reporting-cycle quarter allowlist (configurable; no UI hardcoding)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from hub.dhis2_reports.periods import period_label
from hub.dhis2_reports.security import ReportSecurityError, validate_period

# Defaults match Cycle 1 until config/hcsc_indicators.yaml overrides them.
DEFAULT_QUARTER_START = "2025Q3"
DEFAULT_QUARTER_END = "2026Q4"


def _parse_quarter(value: str) -> tuple[int, int]:
    text = validate_period((value or "").strip(), required=True)
    if len(text) != 6 or text[4] != "Q" or text[5] not in "1234":
        raise ReportSecurityError(
            "Reporting cycle bounds must be YYYYQn quarters.",
            code="invalid_period",
        )
    return int(text[:4]), int(text[5])


def _quarter_tuple_to_id(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def _iter_quarter_ids(start: str, end: str) -> list[str]:
    y0, q0 = _parse_quarter(start)
    y1, q1 = _parse_quarter(end)
    if (y0, q0) > (y1, q1):
        raise ReportSecurityError(
            "reporting_cycle.quarter_start must be on or before quarter_end.",
            code="invalid_period",
        )
    out: list[str] = []
    y, q = y0, q0
    while (y, q) <= (y1, q1):
        out.append(_quarter_tuple_to_id(y, q))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def cycle_config_from_registry(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (registry or {}).get("reporting_cycle") or {}
    start = str(raw.get("quarter_start") or DEFAULT_QUARTER_START).strip()
    end = str(raw.get("quarter_end") or DEFAULT_QUARTER_END).strip()
    return {
        "id": raw.get("id") or 1,
        "label": raw.get("label") or "Cycle 1",
        "quarter_start": start,
        "quarter_end": end,
        "quarters": _iter_quarter_ids(start, end),
    }


def allowed_quarter_ids(registry: dict[str, Any] | None = None) -> list[str]:
    return list(cycle_config_from_registry(registry)["quarters"])


def current_calendar_quarter(*, as_of: date | None = None) -> str:
    day = as_of or datetime.now(timezone.utc).date()
    q = (day.month - 1) // 3 + 1
    return _quarter_tuple_to_id(day.year, q)


def default_allowed_quarter(
    allowed: list[str],
    *,
    remembered: str = "",
    as_of: date | None = None,
) -> str:
    """Latest allowed quarter not later than the current calendar quarter."""
    ids = list(allowed or [])
    if not ids:
        return ""
    remembered_id = validate_period((remembered or "").strip(), required=False)
    if remembered_id in ids:
        return remembered_id
    current = current_calendar_quarter(as_of=as_of)
    eligible = [qid for qid in ids if qid <= current]
    if eligible:
        return eligible[-1]
    return ids[0]


def assert_allowed_quarter(period: str, allowed: list[str]) -> str:
    pe = validate_period(period, required=True)
    if pe not in set(allowed or []):
        raise ReportSecurityError(
            f"Period {pe} is outside the configured reporting cycle.",
            code="invalid_period",
        )
    return pe


def cycle_periods_payload(
    registry: dict[str, Any] | None = None,
    *,
    remembered: str = "",
    as_of: date | None = None,
) -> dict[str, Any]:
    cycle = cycle_config_from_registry(registry)
    quarters = [
        {"id": qid, "label": period_label(qid), "type": "quarterly"}
        for qid in cycle["quarters"]
    ]
    default_id = default_allowed_quarter(
        cycle["quarters"], remembered=remembered, as_of=as_of
    )
    return {
        "ok": True,
        "period_type": "quarterly",
        "default_period": default_id,
        "default_label": period_label(default_id),
        "quarters": quarters,
        "periods": quarters,
        "cycle": {
            "id": cycle["id"],
            "label": cycle["label"],
            "quarter_start": cycle["quarter_start"],
            "quarter_end": cycle["quarter_end"],
        },
        "period_types": [{"id": "quarterly", "label": "Quarterly"}],
    }
