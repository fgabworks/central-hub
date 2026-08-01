"""DHIS2 period helpers for report controls (no duplicate Live Processing logic).

Reuses validation from hub.dhis2_reports.security.validate_period.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from hub.dhis2_reports.security import validate_period


def period_label(period_id: str) -> str:
    """Friendly label for a canonical DHIS2 period id."""
    value = (period_id or "").strip()
    if not value:
        return ""
    if len(value) == 6 and value[4] == "Q" and value[5] in "1234":
        return f"{value[:4]} Q{value[5]}"
    if len(value) == 6 and value.isdigit():
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


def default_completed_quarter(*, as_of: date | None = None) -> str:
    """Latest fully completed calendar quarter (canonical YYYYQn)."""
    day = as_of or datetime.now(timezone.utc).date()
    q = (day.month - 1) // 3 + 1
    if q == 1:
        return f"{day.year - 1}Q4"
    return f"{day.year}Q{q - 1}"


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
    if period_type == "quarterly" and value.isdigit() and len(value) == 5:
        # Rare typo 20262 → reject via validate
        pass
    return validate_period(value, required=False)


def periods_payload(*, remembered: str = "") -> dict[str, Any]:
    quarters = list_quarters()
    default_id = normalize_period_selection(remembered) or default_completed_quarter()
    ids = {q["id"] for q in quarters}
    if default_id and default_id not in ids and default_id.endswith(("Q1", "Q2", "Q3", "Q4")):
        quarters.insert(0, {"id": default_id, "label": period_label(default_id), "type": "quarterly"})
    return {
        "ok": True,
        "default_period": default_id,
        "default_label": period_label(default_id),
        "quarters": quarters,
        "period_types": [
            {"id": "quarterly", "label": "Quarterly"},
            {"id": "monthly", "label": "Monthly"},
            {"id": "yearly", "label": "Yearly"},
            {"id": "custom", "label": "Custom"},
        ],
    }
