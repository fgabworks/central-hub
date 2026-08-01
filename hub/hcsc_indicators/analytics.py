"""Batched DHIS2 analytics retrieval (GET-only)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2.redact import redact_url


def analytics_request_description(
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str,
) -> dict[str, Any]:
    """Registry/adapter-owned request description (not model free-text)."""
    params_list = [
        ("dimension", f"dx:{';'.join(dx_uids)}"),
        ("dimension", f"pe:{period}"),
        ("dimension", f"ou:{org_unit}"),
        ("displayProperty", "NAME"),
        ("skipMeta", "true"),
    ]
    query = urlencode(params_list)
    return {
        "retrieval_method": "DHIS2 Analytics",
        "endpoint": "/api/analytics.json",
        "method": "GET",
        "parameters": {
            "dx": list(dx_uids),
            "pe": period,
            "ou": org_unit,
            "displayProperty": "NAME",
            "skipMeta": True,
        },
        "query_string": query,
        "readable": (
            f"GET /api/analytics.json with dx=[{len(dx_uids)} UIDs], "
            f"pe={period}, ou={org_unit} (single batched request)."
        ),
        "aggregation_request": "DHIS2 analytics default aggregation for indicator/programIndicator dx",
    }


def parse_analytics_rows(payload: dict[str, Any]) -> dict[str, float | None]:
    """Map dx UID → numeric value from analytics.json rows."""
    values: dict[str, float | None] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        # With dx, pe, ou dimensions, common shapes:
        # [dx, pe, ou, value] or [dx, value] depending on skipMeta / headers.
        dx = None
        value_raw = None
        if len(row) >= 4:
            dx = str(row[0])
            value_raw = row[-1]
        elif len(row) == 3:
            dx = str(row[0])
            value_raw = row[-1]
        elif len(row) == 2:
            dx = str(row[0])
            value_raw = row[1]
        if not dx:
            continue
        try:
            values[dx] = float(value_raw) if value_raw not in (None, "") else None
        except (TypeError, ValueError):
            values[dx] = None
    return values


def fetch_analytics_batch(
    client: Dhis2Client,
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str,
) -> dict[str, Any]:
    """One GET /api/analytics.json for all UIDs."""
    if not dx_uids:
        return {
            "ok": True,
            "values": {},
            "latency_ms": 0,
            "request": analytics_request_description(dx_uids=[], period=period, org_unit=org_unit),
        }
    started = time.perf_counter()
    # Build params with repeated dimension keys via list of tuples for requests.
    params = [
        ("dimension", f"dx:{';'.join(dx_uids)}"),
        ("dimension", f"pe:{period}"),
        ("dimension", f"ou:{org_unit}"),
        ("displayProperty", "NAME"),
        ("skipMeta", "true"),
    ]
    # Dhis2Client._get_json expects dict — add a dedicated method that accepts Sequence.
    payload = client.get_analytics(params)
    latency_ms = int((time.perf_counter() - started) * 1000)
    values = parse_analytics_rows(payload if isinstance(payload, dict) else {})
    request_meta = analytics_request_description(
        dx_uids=dx_uids, period=period, org_unit=org_unit
    )
    request_meta["base_url"] = redact_url(getattr(client.settings, "base_url", "") or "")
    return {
        "ok": True,
        "values": values,
        "latency_ms": latency_ms,
        "raw_row_count": len(payload.get("rows") or []) if isinstance(payload, dict) else 0,
        "request": request_meta,
        "dhis2_writes": 0,
    }


def map_indicator_values(
    indicator: dict[str, Any],
    values: dict[str, float | None],
) -> dict[str, Any]:
    """Map analytics values onto a registry indicator without computing HCSC formulas."""
    uids = indicator.get("dhis2_uids") or {}
    value_uid = uids.get("value")
    num_uid = uids.get("numerator")
    den_uid = uids.get("denominator")
    result_type = indicator.get("result_type")
    count = None
    numerator = None
    denominator = None
    percentage = None

    if indicator.get("unresolved") or not value_uid:
        return {
            "count": None,
            "numerator": None,
            "denominator": None,
            "percentage": None,
            "source_uid": None,
        }

    raw = values.get(value_uid)
    if result_type == "count":
        count = raw
    elif result_type in {"percentage", "numerator_denominator_percentage"}:
        percentage = raw
        if num_uid:
            numerator = values.get(num_uid)
        if den_uid:
            denominator = values.get(den_uid)
    else:
        # derived_status / disaggregation without UID stay empty
        count = raw

    return {
        "count": count,
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
        "source_uid": value_uid,
    }
