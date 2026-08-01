"""Sanitized Query Used to Generate This payloads."""

from __future__ import annotations

from typing import Any

from hub.dhis2.redact import redact_text
from hub.dhis2_reports.security import redact_report_detail

_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "authorization",
        "cookie",
        "cookies",
        "auth",
        "username",
        "user",
        "credential",
    }
)


def _scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if str(key).lower() in _SECRET_KEYS:
                out[key] = "[REDACTED]"
            else:
                out[key] = _scrub(value)
        return out
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, secrets=[])
    return obj


def build_query_panel(
    *,
    retrieval_method: str,
    request_meta: dict[str, Any] | None = None,
    sql_text: str | None = None,
    capability_ref: str | None = None,
) -> dict[str, Any]:
    """Build collapsible Query Used panel from registry/adapter metadata only."""
    method = (retrieval_method or "").strip()
    if method == "DHIS2 Analytics":
        meta = _scrub(request_meta or {})
        copy_text = meta.get("query_string") or meta.get("readable") or ""
        return {
            "retrieval_method": "DHIS2 Analytics",
            "endpoint": meta.get("endpoint") or "/api/analytics.json",
            "parameters": meta.get("parameters") or {},
            "indicator_uids": (meta.get("parameters") or {}).get("dx") or [],
            "period": (meta.get("parameters") or {}).get("pe"),
            "organisation_unit": (meta.get("parameters") or {}).get("ou"),
            "aggregation_request": meta.get("aggregation_request"),
            "readable": meta.get("readable"),
            "query_string": meta.get("query_string"),
            "base_url": meta.get("base_url"),
            "copy_text": redact_report_detail(str(copy_text)),
            "sql": None,
            "note": "Request generated from the indicator registry + analytics adapter. Credentials are never included.",
        }
    if method == "Approved SQL":
        sql = redact_report_detail(sql_text or "")
        return {
            "retrieval_method": "Approved SQL",
            "endpoint": None,
            "parameters": {},
            "sql": sql,
            "copy_text": sql,
            "note": "Exact approved/saved SQL from the connected source adapter.",
        }
    return {
        "retrieval_method": "Connected Repository Capability",
        "endpoint": None,
        "parameters": {},
        "capability_ref": capability_ref,
        "copy_text": capability_ref or "",
        "sql": None,
        "note": "Values come from a connected-repository capability — hub does not reimplement formulas.",
    }
