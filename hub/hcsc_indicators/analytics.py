"""Batched DHIS2 analytics retrieval (GET-only)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from hub.dhis2.client import Dhis2Client
from hub.dhis2.redact import redact_url


def analytics_request_description(
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str,
    include_num_den: bool = True,
) -> dict[str, Any]:
    """Registry/adapter-owned request description (not model free-text)."""
    params_list = [
        ("dimension", f"dx:{';'.join(dx_uids)}"),
        ("dimension", f"pe:{period}"),
        ("dimension", f"ou:{org_unit}"),
        ("displayProperty", "NAME"),
        ("skipMeta", "false"),
        ("includeNumDen", "true" if include_num_den else "false"),
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
            "skipMeta": False,
            "includeNumDen": bool(include_num_den),
        },
        "query_string": query,
        "readable": (
            f"GET /api/analytics.json with dx=[{len(dx_uids)} UIDs], "
            f"pe={period}, ou={org_unit}, includeNumDen={bool(include_num_den)} "
            f"(single batched request)."
        ),
        "aggregation_request": "DHIS2 analytics default aggregation for indicator/programIndicator dx",
    }


def _header_index(payload: dict[str, Any]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, header in enumerate(payload.get("headers") or []):
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or header.get("column") or "").strip().lower()
        if name:
            idx[name] = i
    return idx


def _as_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_analytics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse analytics.json into values + optional numerator/denominator by dx."""
    values: dict[str, float | None] = {}
    num_den: dict[str, dict[str, float | None]] = {}
    headers = _header_index(payload)
    dx_i = headers.get("dx", 0)
    value_i = headers.get("value")
    num_i = headers.get("numerator")
    den_i = headers.get("denominator")

    for row in payload.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        dx = str(row[dx_i]) if dx_i < len(row) else None
        if not dx:
            continue
        if value_i is not None and value_i < len(row):
            values[dx] = _as_float(row[value_i])
        else:
            # Fallback: last cell is typically value when headers missing.
            values[dx] = _as_float(row[-1])
        if num_i is not None or den_i is not None:
            num_den[dx] = {
                "numerator": _as_float(row[num_i]) if num_i is not None and num_i < len(row) else None,
                "denominator": _as_float(row[den_i]) if den_i is not None and den_i < len(row) else None,
            }
    return {"values": values, "num_den": num_den}


def parse_analytics_rows(payload: dict[str, Any]) -> dict[str, float | None]:
    """Backward-compatible dx → value map."""
    return parse_analytics_payload(payload).get("values") or {}


def fetch_analytics_batch(
    client: Dhis2Client,
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str,
    include_num_den: bool = True,
) -> dict[str, Any]:
    """One GET /api/analytics.json for all UIDs."""
    if not dx_uids:
        return {
            "ok": True,
            "values": {},
            "num_den": {},
            "latency_ms": 0,
            "request": analytics_request_description(
                dx_uids=[], period=period, org_unit=org_unit, include_num_den=include_num_den
            ),
        }
    started = time.perf_counter()
    params = [
        ("dimension", f"dx:{';'.join(dx_uids)}"),
        ("dimension", f"pe:{period}"),
        ("dimension", f"ou:{org_unit}"),
        ("displayProperty", "NAME"),
        ("skipMeta", "false"),
        ("includeNumDen", "true" if include_num_den else "false"),
    ]
    payload = client.get_analytics(params)
    latency_ms = int((time.perf_counter() - started) * 1000)
    parsed = parse_analytics_payload(payload if isinstance(payload, dict) else {})
    request_meta = analytics_request_description(
        dx_uids=dx_uids,
        period=period,
        org_unit=org_unit,
        include_num_den=include_num_den,
    )
    request_meta["base_url"] = redact_url(getattr(client.settings, "base_url", "") or "")
    return {
        "ok": True,
        "values": parsed.get("values") or {},
        "num_den": parsed.get("num_den") or {},
        "latency_ms": latency_ms,
        "raw_row_count": len(payload.get("rows") or []) if isinstance(payload, dict) else 0,
        "request": request_meta,
        "dhis2_writes": 0,
    }


def map_indicator_values(
    indicator: dict[str, Any],
    values: dict[str, float | None],
    *,
    num_den: dict[str, dict[str, float | None]] | None = None,
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
    nd = (num_den or {}).get(value_uid) or {}
    if result_type == "count":
        count = raw
    elif result_type in {
        "percentage",
        "numerator_denominator_percentage",
        "ratio",
    }:
        percentage = raw
        if num_uid:
            numerator = values.get(num_uid)
        if den_uid:
            denominator = values.get(den_uid)
        # Prefer companion UIDs; else use analytics includeNumDen for the indicator itself.
        if numerator is None:
            numerator = nd.get("numerator")
        if denominator is None:
            denominator = nd.get("denominator")
    else:
        count = raw

    return {
        "count": count,
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
        "source_uid": value_uid,
    }
