"""DHIS2 period helpers for report controls (no duplicate Live Processing logic).

Reuses validation from hub.dhis2_reports.security.validate_period.
"""

from __future__ import annotations

from calendar import month_name
from datetime import date, datetime, timezone
from typing import Any

from hub.dhis2_reports.security import ReportSecurityError, validate_period

_MONTH_NAMES = list(month_name)  # index 1..12

# Relative DHIS2 keys we can resolve to a concrete absolute period for submit.
_RELATIVE_RESOLVERS: dict[str, str] = {
    "thismonth": "thisMonth",
    "lastmonth": "lastMonth",
    "thisquarter": "thisQuarter",
    "lastquarter": "lastQuarter",
    "thisyear": "thisYear",
    "lastyear": "lastYear",
    "monthsthisyear": "thisMonth",
    "quartersthisyear": "thisQuarter",
}


def period_label(period_id: str) -> str:
    """Friendly label for a canonical DHIS2 period id."""
    value = (period_id or "").strip()
    if not value:
        return ""
    if len(value) == 6 and value[4] == "Q" and value[5] in "1234":
        return f"{value[:4]} Q{value[5]}"
    if len(value) == 6 and value.isdigit():
        month = int(value[4:6])
        if 1 <= month <= 12:
            return f"{_MONTH_NAMES[month]} {value[:4]}"
        return f"{value[:4]}-{value[4:6]}"
    if len(value) == 4 and value.isdigit():
        return value
    return value


def list_quarters(*, years_back: int = 4, years_forward: int = 1) -> list[dict[str, str]]:
    """Return searchable quarter options as canonical DHIS2 ids (YYYYQn)."""
    today = datetime.now(timezone.utc).date()
    start_year = today.year - max(0, int(years_back))
    end_year = today.year + max(0, int(years_forward))
    rows: list[dict[str, str]] = []
    for year in range(end_year, start_year - 1, -1):
        for q in (4, 3, 2, 1):
            pid = f"{year}Q{q}"
            rows.append({"id": pid, "label": period_label(pid), "type": "quarterly"})
    return rows


def list_months(*, years_back: int = 3, years_forward: int = 0) -> list[dict[str, str]]:
    """Return searchable month options as canonical DHIS2 ids (YYYYMM)."""
    today = datetime.now(timezone.utc).date()
    end_year = today.year + max(0, int(years_forward))
    end_month = 12 if years_forward > 0 else today.month
    start_year = today.year - max(0, int(years_back))
    rows: list[dict[str, str]] = []
    year, month = end_year, end_month
    while (year, month) >= (start_year, 1):
        pid = f"{year}{month:02d}"
        rows.append({"id": pid, "label": period_label(pid), "type": "monthly"})
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return rows


def list_years(*, years_back: int = 8, years_forward: int = 0) -> list[dict[str, str]]:
    """Return searchable year options as canonical DHIS2 ids (YYYY)."""
    today = datetime.now(timezone.utc).date()
    end_year = today.year + max(0, int(years_forward))
    start_year = today.year - max(0, int(years_back))
    rows: list[dict[str, str]] = []
    for year in range(end_year, start_year - 1, -1):
        pid = str(year)
        rows.append({"id": pid, "label": period_label(pid), "type": "yearly"})
    return rows


def default_completed_quarter(*, as_of: date | None = None) -> str:
    """Latest fully completed calendar quarter (canonical YYYYQn)."""
    day = as_of or datetime.now(timezone.utc).date()
    q = (day.month - 1) // 3 + 1
    if q == 1:
        return f"{day.year - 1}Q4"
    return f"{day.year}Q{q - 1}"


def default_completed_month(*, as_of: date | None = None) -> str:
    """Latest fully completed calendar month (canonical YYYYMM)."""
    day = as_of or datetime.now(timezone.utc).date()
    if day.month == 1:
        return f"{day.year - 1}12"
    return f"{day.year}{day.month - 1:02d}"


def default_year(*, as_of: date | None = None) -> str:
    day = as_of or datetime.now(timezone.utc).date()
    # Prefer prior completed year when still in January; otherwise current year.
    if day.month == 1:
        return str(day.year - 1)
    return str(day.year)


def resolve_relative_period(key: str, *, as_of: date | None = None) -> str | None:
    """Map a DHIS2 relative-period key to a concrete absolute period id."""
    day = as_of or datetime.now(timezone.utc).date()
    canon = _RELATIVE_RESOLVERS.get((key or "").strip().lower())
    if not canon:
        return None
    if canon == "thisMonth":
        return f"{day.year}{day.month:02d}"
    if canon == "lastMonth":
        return default_completed_month(as_of=day)
    if canon == "thisQuarter":
        q = (day.month - 1) // 3 + 1
        return f"{day.year}Q{q}"
    if canon == "lastQuarter":
        return default_completed_quarter(as_of=day)
    if canon == "thisYear":
        return str(day.year)
    if canon == "lastYear":
        return str(day.year - 1)
    return None


def relative_period_options(
    keys: list[str] | None = None,
    *,
    as_of: date | None = None,
) -> list[dict[str, str]]:
    """Selectable relative presets that submit absolute canonical period ids."""
    day = as_of or datetime.now(timezone.utc).date()
    wanted = [str(k).strip() for k in (keys or []) if str(k).strip()]
    if not wanted:
        wanted = ["thisMonth", "lastMonth", "thisQuarter", "lastQuarter", "thisYear", "lastYear"]
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in wanted:
        absolute = resolve_relative_period(key, as_of=day)
        if not absolute or absolute in seen:
            continue
        seen.add(absolute)
        friendly = period_label(absolute)
        rows.append(
            {
                "id": absolute,
                "label": f"{key} · {friendly}",
                "type": "relative",
                "relative_key": key,
            }
        )
    return rows


def normalize_period_selection(raw: str, *, period_type: str = "") -> str:
    """Accept UI values and return a validated canonical period id when possible."""
    value = (raw or "").strip()
    if not value:
        return ""
    # Friendly "2026 Q2" → 2026Q2
    if " Q" in value.upper():
        parts = value.upper().replace(" ", "").split("Q")
        if len(parts) == 2 and parts[0].isdigit() and parts[1] in {"1", "2", "3", "4"}:
            value = f"{parts[0]}Q{parts[1]}"
    # Friendly "August 2026" → 202608
    parts = value.split()
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
        month_lookup = {name.lower(): idx for idx, name in enumerate(_MONTH_NAMES) if name}
        month_idx = month_lookup.get(parts[0].lower())
        if month_idx:
            value = f"{parts[1]}{month_idx:02d}"
    if period_type == "quarterly" and value.isdigit() and len(value) == 5:
        pass
    try:
        return validate_period(value, required=False)
    except ReportSecurityError:
        return ""


def infer_period_type(
    *,
    preferred: str = "",
    relative_keys: list[str] | None = None,
) -> str:
    pref = (preferred or "").strip().lower()
    if pref in {"quarterly", "monthly", "yearly", "relative"}:
        return pref
    rel = [str(k).lower() for k in (relative_keys or [])]
    if any("quarter" in k for k in rel):
        return "quarterly"
    if any(k.endswith("year") or "year" in k for k in rel) and not any(
        "month" in k for k in rel
    ):
        return "yearly"
    if rel:
        return "relative"
    return "monthly"


def periods_for_type(
    period_type: str,
    *,
    relative_keys: list[str] | None = None,
) -> list[dict[str, str]]:
    ptype = infer_period_type(preferred=period_type, relative_keys=relative_keys)
    if ptype == "quarterly":
        return list_quarters()
    if ptype == "yearly":
        return list_years()
    if ptype == "relative":
        return relative_period_options(relative_keys)
    return list_months()


def default_period_for_type(
    period_type: str,
    *,
    relative_keys: list[str] | None = None,
) -> str:
    ptype = infer_period_type(preferred=period_type, relative_keys=relative_keys)
    if ptype == "quarterly":
        return default_completed_quarter()
    if ptype == "yearly":
        return default_year()
    if ptype == "relative":
        opts = relative_period_options(relative_keys)
        return opts[0]["id"] if opts else default_completed_month()
    return default_completed_month()


def periods_payload(
    *,
    remembered: str = "",
    period_type: str = "quarterly",
    relative_keys: list[str] | None = None,
) -> dict[str, Any]:
    ptype = infer_period_type(preferred=period_type, relative_keys=relative_keys)
    periods = periods_for_type(ptype, relative_keys=relative_keys)
    # When relative is supported alongside a base type, append relative presets.
    relative_opts: list[dict[str, str]] = []
    if relative_keys and ptype != "relative":
        relative_opts = relative_period_options(relative_keys)

    default_id = normalize_period_selection(remembered, period_type=ptype) or default_period_for_type(
        ptype, relative_keys=relative_keys
    )
    ids = {p["id"] for p in periods}
    if default_id and default_id not in ids:
        periods.insert(
            0,
            {
                "id": default_id,
                "label": period_label(default_id),
                "type": ptype if ptype != "relative" else "relative",
            },
        )

    period_types = [
        {"id": "quarterly", "label": "Quarterly"},
        {"id": "monthly", "label": "Monthly"},
        {"id": "yearly", "label": "Yearly"},
    ]
    if relative_keys:
        period_types.append({"id": "relative", "label": "Relative"})

    return {
        "ok": True,
        "period_type": ptype,
        "default_period": default_id,
        "default_label": period_label(default_id),
        "periods": periods,
        "quarters": [p for p in periods if p.get("type") == "quarterly"] or list_quarters(),
        "relative_periods": relative_opts,
        "period_types": period_types,
    }
