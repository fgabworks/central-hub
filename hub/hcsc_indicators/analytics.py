"""Batched DHIS2 analytics retrieval (GET-only)."""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlencode

from hub.dhis2.client import Dhis2Client
from hub.dhis2.redact import redact_url


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1.0, float(raw))
    except ValueError:
        return default


def resolve_analytics_timeout(
    *,
    org_unit: str | list[str] | None = None,
    ou_level: int | None = None,
) -> float:
    """HCSC report analytics timeouts (separate from the short OU-picker default).

    National-scope batched dx queries routinely exceed DHIS2_TIMEOUT_SECONDS (10s).

    Returns:
      - national default: 600s (10 min)
      - national with env 0/none/unlimited: 0 (no DHIS2 requests timeout)
      - other scopes: HCSC_ANALYTICS_TIMEOUT_SECONDS (default 60)
    """
    base = _env_float("HCSC_ANALYTICS_TIMEOUT_SECONDS", 60.0)
    national_raw = (os.environ.get("HCSC_NATIONAL_ANALYTICS_TIMEOUT_SECONDS") or "").strip().lower()
    if national_raw in {"0", "none", "unlimited", "inf"}:
        national = 0.0
    elif national_raw:
        national = _env_float("HCSC_NATIONAL_ANALYTICS_TIMEOUT_SECONDS", 600.0)
    else:
        national = 600.0
    ou_list = (
        [u for u in org_unit if u]
        if isinstance(org_unit, list)
        else ([org_unit] if org_unit else [])
    )
    if ou_level == 1:
        return national
    # Multi-OU geographic breakdowns also need headroom beyond the 10s default.
    if len(ou_list) > 1:
        national_floor = 600.0 if national <= 0 else national
        return max(base, min(national_floor, base + min(len(ou_list), 40) * 0.5))
    return base


def analytics_request_description(
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str | list[str],
    include_num_den: bool = True,
) -> dict[str, Any]:
    """Registry/adapter-owned request description (not model free-text)."""
    ou_dim = ";".join(org_unit) if isinstance(org_unit, list) else org_unit
    ou_list = org_unit if isinstance(org_unit, list) else [org_unit]
    params_list = [
        ("dimension", f"dx:{';'.join(dx_uids)}"),
        ("dimension", f"pe:{period}"),
        ("dimension", f"ou:{ou_dim}"),
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
            "ou": ou_list if len(ou_list) > 1 else (ou_list[0] if ou_list else ""),
            "ou_count": len(ou_list),
            "displayProperty": "NAME",
            "skipMeta": False,
            "includeNumDen": bool(include_num_den),
        },
        "query_string": query,
        "readable": (
            f"GET /api/analytics.json with dx=[{len(dx_uids)} UIDs], "
            f"pe={period}, ou=[{len(ou_list)} unit(s)], includeNumDen={bool(include_num_den)} "
            f"(batched request)."
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
    """Parse analytics.json into values + optional numerator/denominator by dx.

    Single-OU responses (no ou column / one ou) keep the legacy dx→value map.
    Multi-OU responses also populate ``by_ou``: {ou_uid: {values, num_den}}.
    """
    values: dict[str, float | None] = {}
    num_den: dict[str, dict[str, float | None]] = {}
    by_ou: dict[str, dict[str, Any]] = {}
    headers = _header_index(payload)
    dx_i = headers.get("dx", 0)
    ou_i = headers.get("ou")
    value_i = headers.get("value")
    num_i = headers.get("numerator")
    den_i = headers.get("denominator")

    for row in payload.get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        dx = str(row[dx_i]) if dx_i < len(row) else None
        if not dx:
            continue
        ou = ""
        if ou_i is not None and ou_i < len(row):
            ou = str(row[ou_i] or "").strip()
        if value_i is not None and value_i < len(row):
            val = _as_float(row[value_i])
        else:
            val = _as_float(row[-1])
        nd = None
        if num_i is not None or den_i is not None:
            nd = {
                "numerator": _as_float(row[num_i]) if num_i is not None and num_i < len(row) else None,
                "denominator": _as_float(row[den_i]) if den_i is not None and den_i < len(row) else None,
            }
        # Legacy flat map (last write wins for multi-OU — prefer by_ou for breakdown).
        values[dx] = val
        if nd is not None:
            num_den[dx] = nd
        if ou:
            bucket = by_ou.setdefault(ou, {"values": {}, "num_den": {}})
            bucket["values"][dx] = val
            if nd is not None:
                bucket["num_den"][dx] = nd
    return {"values": values, "num_den": num_den, "by_ou": by_ou}


def parse_analytics_rows(payload: dict[str, Any]) -> dict[str, float | None]:
    """Backward-compatible dx → value map."""
    return parse_analytics_payload(payload).get("values") or {}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def analytics_dx_chunk_size(*, ou_level: int | None = None) -> int:
    """How many dx UIDs per /api/analytics.json call.

    National Live often hits nginx 504 when all HCSC UIDs go in one request.
    """
    if ou_level == 1:
        return _env_int("HCSC_ANALYTICS_DX_CHUNK_NATIONAL", 6)
    return _env_int("HCSC_ANALYTICS_DX_CHUNK", 24)


def analytics_chunk_timeout_seconds(total_timeout: float) -> float:
    """Per-chunk timeout so one nginx 504 fails fast and retries/next chunk can proceed."""
    per_chunk = _env_float("HCSC_ANALYTICS_CHUNK_TIMEOUT_SECONDS", 90.0)
    if total_timeout <= 0:
        return per_chunk
    return max(15.0, min(float(total_timeout), per_chunk))


def fetch_analytics_batch(
    client: Dhis2Client,
    *,
    dx_uids: list[str],
    period: str,
    org_unit: str | list[str],
    include_num_den: bool = True,
    timeout: float | None = None,
    ou_level: int | None = None,
) -> dict[str, Any]:
    """One or more GET /api/analytics.json calls for all UIDs (optionally multi-OU)."""
    ou_list = (
        [u for u in org_unit if u]
        if isinstance(org_unit, list)
        else ([org_unit] if org_unit else [])
    )
    dx_list = [u for u in (dx_uids or []) if u]
    if not dx_list or not ou_list:
        return {
            "ok": True,
            "values": {},
            "num_den": {},
            "by_ou": {},
            "latency_ms": 0,
            "http_requests": 0,
            "request": analytics_request_description(
                dx_uids=dx_list or [],
                period=period,
                org_unit=ou_list or "",
                include_num_den=include_num_den,
            ),
        }

    # Chunk OUs to keep analytics URLs within practical limits.
    ou_chunk_size = 40
    try:
        from hub.hcsc_indicators.geographic_breakdown import breakdown_thresholds

        ou_chunk_size = int(breakdown_thresholds().get("analytics_ou_chunk") or 40)
    except Exception:  # noqa: BLE001
        ou_chunk_size = 40
    ou_chunk_size = max(1, min(ou_chunk_size, 80))
    dx_chunk_size = max(1, analytics_dx_chunk_size(ou_level=ou_level))

    timeout_s = (
        float(timeout)
        if timeout is not None
        else resolve_analytics_timeout(org_unit=ou_list, ou_level=ou_level)
    )
    chunk_timeout = analytics_chunk_timeout_seconds(timeout_s)

    started = time.perf_counter()
    merged_values: dict[str, float | None] = {}
    merged_num_den: dict[str, dict[str, float | None]] = {}
    merged_by_ou: dict[str, dict[str, Any]] = {}
    raw_rows = 0
    http_requests = 0
    last_request_meta: dict[str, Any] | None = None
    dx_chunks = 0

    for i in range(0, len(ou_list), ou_chunk_size):
        ou_chunk = ou_list[i : i + ou_chunk_size]
        ou_dim = ";".join(ou_chunk)
        for j in range(0, len(dx_list), dx_chunk_size):
            dx_chunk = dx_list[j : j + dx_chunk_size]
            dx_chunks += 1
            params = [
                ("dimension", f"dx:{';'.join(dx_chunk)}"),
                ("dimension", f"pe:{period}"),
                ("dimension", f"ou:{ou_dim}"),
                ("displayProperty", "NAME"),
                ("skipMeta", "true" if ou_level == 1 else "false"),
                ("includeNumDen", "true" if include_num_den else "false"),
            ]
            payload = client.get_analytics(params, timeout=chunk_timeout)
            http_requests += 1
            parsed = parse_analytics_payload(payload if isinstance(payload, dict) else {})
            raw_rows += len(payload.get("rows") or []) if isinstance(payload, dict) else 0
            if len(ou_chunk) == 1 and not (parsed.get("by_ou") or {}):
                ou = ou_chunk[0]
                dest = merged_by_ou.setdefault(ou, {"values": {}, "num_den": {}})
                dest["values"].update(parsed.get("values") or {})
                dest["num_den"].update(parsed.get("num_den") or {})
            else:
                for ou, bucket in (parsed.get("by_ou") or {}).items():
                    dest = merged_by_ou.setdefault(ou, {"values": {}, "num_den": {}})
                    dest["values"].update(bucket.get("values") or {})
                    dest["num_den"].update(bucket.get("num_den") or {})
            merged_values.update(parsed.get("values") or {})
            merged_num_den.update(parsed.get("num_den") or {})
            last_request_meta = analytics_request_description(
                dx_uids=dx_chunk,
                period=period,
                org_unit=ou_chunk if len(ou_chunk) > 1 else ou_chunk[0],
                include_num_den=include_num_den,
            )

    latency_ms = int((time.perf_counter() - started) * 1000)
    request_meta = last_request_meta or analytics_request_description(
        dx_uids=dx_list,
        period=period,
        org_unit=ou_list if len(ou_list) > 1 else ou_list[0],
        include_num_den=include_num_den,
    )
    request_meta["base_url"] = redact_url(getattr(client.settings, "base_url", "") or "")
    request_meta["parameters"]["ou_count"] = len(ou_list)
    request_meta["parameters"]["dx_count"] = len(dx_list)
    request_meta["parameters"]["dx_chunk_size"] = dx_chunk_size
    request_meta["parameters"]["dx_chunks"] = dx_chunks
    request_meta["http_requests"] = http_requests
    request_meta["timeout_seconds"] = None if timeout_s <= 0 else timeout_s
    request_meta["chunk_timeout_seconds"] = chunk_timeout
    request_meta["readable"] = (
        f"GET /api/analytics.json batched: dx={len(dx_list)} UIDs in {dx_chunks} chunk(s) "
        f"(size {dx_chunk_size}), ou={len(ou_list)} unit(s), includeNumDen={bool(include_num_den)}."
    )
    return {
        "ok": True,
        "values": merged_values,
        "num_den": merged_num_den,
        "by_ou": merged_by_ou,
        "latency_ms": latency_ms,
        "raw_row_count": raw_rows,
        "http_requests": http_requests,
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
