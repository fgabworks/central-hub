"""Geographic breakdown rules for HCSC–RF (OU-level scoped children).

Does not implement indicator formulas — only validates breakdown targets and
resolves child organisation units from the hub OU cache.
"""

from __future__ import annotations

import os
from typing import Any

from hub.dhis2_reports.security import ReportSecurityError

# DHIS2 org-unit levels used by the HCSC cascade picker.
LEVEL_REGION = 2
LEVEL_PROVINCE = 3
LEVEL_MUNICIPALITY = 4
LEVEL_BARANGAY = 5

BREAKDOWN_NONE = "none"
BREAKDOWN_REGION = "region"
BREAKDOWN_PROVINCE = "province"
BREAKDOWN_MUNICIPALITY = "municipality_city"
BREAKDOWN_BARANGAY = "barangay"

BREAKDOWN_LEVELS: dict[str, int] = {
    BREAKDOWN_REGION: LEVEL_REGION,
    BREAKDOWN_PROVINCE: LEVEL_PROVINCE,
    BREAKDOWN_MUNICIPALITY: LEVEL_MUNICIPALITY,
    BREAKDOWN_BARANGAY: LEVEL_BARANGAY,
}

BREAKDOWN_LABELS: dict[str, str] = {
    BREAKDOWN_NONE: "None (selected area total)",
    BREAKDOWN_REGION: "By Region",
    BREAKDOWN_PROVINCE: "By Province",
    BREAKDOWN_MUNICIPALITY: "By Municipality/City",
    BREAKDOWN_BARANGAY: "By Barangay",
}

LEVEL_NAME: dict[int, str] = {
    LEVEL_REGION: "region",
    LEVEL_PROVINCE: "province",
    LEVEL_MUNICIPALITY: "municipality_city",
    LEVEL_BARANGAY: "barangay",
}

LEVEL_ESTIMATE_LABEL: dict[str, str] = {
    BREAKDOWN_REGION: "regions",
    BREAKDOWN_PROVINCE: "provinces",
    BREAKDOWN_MUNICIPALITY: "municipalities/cities",
    BREAKDOWN_BARANGAY: "barangays",
}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def breakdown_thresholds() -> dict[str, int]:
    """Configurable large-breakdown safeguards."""
    return {
        "warn_at": _env_int("HCSC_BREAKDOWN_WARN_AT", 50),
        "confirm_at": _env_int("HCSC_BREAKDOWN_CONFIRM_AT", 200),
        "max_children": _env_int("HCSC_BREAKDOWN_MAX_CHILDREN", 2500),
        "analytics_ou_chunk": _env_int("HCSC_BREAKDOWN_OU_CHUNK", 40),
    }


def normalize_breakdown(raw: str | None) -> str:
    value = (raw or BREAKDOWN_NONE).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "none": BREAKDOWN_NONE,
        "selected_area_total": BREAKDOWN_NONE,
        "municipality": BREAKDOWN_MUNICIPALITY,
        "city": BREAKDOWN_MUNICIPALITY,
        "municipality_city": BREAKDOWN_MUNICIPALITY,
        "mun": BREAKDOWN_MUNICIPALITY,
    }
    return aliases.get(value, value)


def options_for_parent_level(parent_level: int | None) -> list[dict[str, Any]]:
    """Breakdown choices strictly below the selected OU level."""
    options = [
        {
            "id": BREAKDOWN_NONE,
            "label": BREAKDOWN_LABELS[BREAKDOWN_NONE],
            "target_level": None,
        }
    ]
    if parent_level is None:
        # Unknown level — only None until OU metadata resolves.
        return options
    for bid, level in (
        (BREAKDOWN_REGION, LEVEL_REGION),
        (BREAKDOWN_PROVINCE, LEVEL_PROVINCE),
        (BREAKDOWN_MUNICIPALITY, LEVEL_MUNICIPALITY),
        (BREAKDOWN_BARANGAY, LEVEL_BARANGAY),
    ):
        if level > int(parent_level):
            options.append(
                {
                    "id": bid,
                    "label": BREAKDOWN_LABELS[bid],
                    "target_level": level,
                }
            )
    return options


def validate_breakdown_for_parent(
    *,
    parent_level: int | None,
    geographic_breakdown: str | None,
) -> str:
    """Reject breakdowns at or above the selected OU level."""
    bid = normalize_breakdown(geographic_breakdown)
    if bid == BREAKDOWN_NONE:
        return BREAKDOWN_NONE
    if bid not in BREAKDOWN_LEVELS:
        raise ReportSecurityError(
            f"Unsupported geographic breakdown: {geographic_breakdown}",
            code="invalid_geographic_breakdown",
        )
    allowed = {o["id"] for o in options_for_parent_level(parent_level)}
    if bid not in allowed:
        raise ReportSecurityError(
            "Geographic breakdown must be below the selected organisation unit level.",
            code="invalid_geographic_breakdown",
        )
    return bid


def target_level_for_breakdown(breakdown_id: str) -> int | None:
    bid = normalize_breakdown(breakdown_id)
    if bid == BREAKDOWN_NONE:
        return None
    return BREAKDOWN_LEVELS.get(bid)


def format_estimate(count: int, breakdown_id: str) -> str:
    label = LEVEL_ESTIMATE_LABEL.get(normalize_breakdown(breakdown_id), "units")
    return f"{count:,} {label}"


def bootstrap_breakdown_meta() -> dict[str, Any]:
    return {
        "levels": LEVEL_NAME,
        "labels": BREAKDOWN_LABELS,
        "thresholds": breakdown_thresholds(),
        "help": {
            "region": "Region → Province / Municipality/City / Barangay",
            "province": "Province → Municipality/City / Barangay",
            "municipality_city": "Municipality/City → Barangay",
            "barangay": "Barangay → no lower breakdown",
        },
    }
