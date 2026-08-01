"""Sanitized Data Retrieval & Calculation payloads (no invented SQL/formulas)."""

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


def build_retrieval_panel(
    *,
    retrieval_method: str,
    request_meta: dict[str, Any] | None = None,
    sql_text: str | None = None,
    capability_ref: str | None = None,
    calculation_refs: list[dict[str, Any]] | None = None,
    source_mapping_rows: list[dict[str, Any]] | None = None,
    pi_expressions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Data Retrieval & Calculation tabs from registry/adapter metadata only."""
    method = (retrieval_method or "").strip()
    calc_refs = calculation_refs or []
    mapping_rows = source_mapping_rows or []

    if method == "DHIS2 Analytics":
        meta = _scrub(request_meta or {})
        copy_text = meta.get("query_string") or meta.get("readable") or ""
        return {
            "title": "Data Retrieval & Calculation",
            "retrieval_method": "DHIS2 Analytics",
            "tabs": {
                "retrieval_request": {
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
                    "note": (
                        "Request generated from the indicator registry + analytics adapter. "
                        "Credentials are never included."
                    ),
                },
                "calculation": {
                    "note": (
                        "Formulas are owned by DHIS2 program indicators / indicators. "
                        "Hub displays registry references and metadata expressions when available — "
                        "it does not recompute HCSC logic."
                    ),
                    "pi_expressions": _scrub(pi_expressions or {}),
                    "references": calc_refs,
                    "invented": False,
                },
                "source_mapping": {
                    "rows": mapping_rows,
                    "note": "Source mapping from indicator registry and NPMO design decode.",
                },
            },
            "sql": None,
            "copy_text": redact_report_detail(str(copy_text)),
            "open_sql_workspace": False,
        }

    if method == "Approved SQL":
        sql = redact_report_detail(sql_text or "")
        return {
            "title": "Data Retrieval & Calculation",
            "retrieval_method": "Approved SQL",
            "tabs": {
                "retrieval_request": {
                    "endpoint": None,
                    "sql": sql,
                    "copy_text": sql,
                    "open_sql_workspace": True,
                    "note": "Exact approved/saved SQL from the connected source adapter.",
                },
                "calculation": {
                    "note": "Calculation stays in the approved SQL / source system — hub does not invent formulas.",
                    "references": calc_refs,
                    "invented": False,
                },
                "source_mapping": {"rows": mapping_rows},
            },
            "sql": sql,
            "copy_text": sql,
            "open_sql_workspace": True,
        }

    return {
        "title": "Data Retrieval & Calculation",
        "retrieval_method": "Connected Repository Capability",
        "tabs": {
            "retrieval_request": {
                "capability_ref": capability_ref,
                "copy_text": capability_ref or "",
                "note": "Values come from a connected-repository capability — hub does not reimplement formulas.",
            },
            "calculation": {
                "note": "See capability / repository references. Hub does not invent formulas.",
                "references": calc_refs,
                "invented": False,
            },
            "source_mapping": {"rows": mapping_rows},
        },
        "sql": None,
        "copy_text": capability_ref or "",
        "open_sql_workspace": False,
    }


# Backward-compatible alias used by earlier Phase 0–1 callers/tests.
def build_query_panel(
    *,
    retrieval_method: str,
    request_meta: dict[str, Any] | None = None,
    sql_text: str | None = None,
    capability_ref: str | None = None,
) -> dict[str, Any]:
    panel = build_retrieval_panel(
        retrieval_method=retrieval_method,
        request_meta=request_meta,
        sql_text=sql_text,
        capability_ref=capability_ref,
    )
    # Flatten key fields expected by older tests/UI.
    req = (panel.get("tabs") or {}).get("retrieval_request") or {}
    return {
        **panel,
        "endpoint": req.get("endpoint"),
        "parameters": req.get("parameters") or {},
        "indicator_uids": req.get("indicator_uids") or [],
        "period": req.get("period"),
        "organisation_unit": req.get("organisation_unit"),
        "aggregation_request": req.get("aggregation_request"),
        "readable": req.get("readable"),
        "query_string": req.get("query_string"),
        "base_url": req.get("base_url"),
        "note": req.get("note"),
    }
