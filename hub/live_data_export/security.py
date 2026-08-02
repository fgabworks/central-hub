"""Security helpers for Live Data Export (identifiers, filters, filenames)."""

from __future__ import annotations

import re
from typing import Any

from hub.live_data_export.registry import ExportSource, RegistryError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_BLOCKED_SCHEMAS = frozenset(
    {
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "mysql",
        "sys",
        "performance_schema",
    }
)
_CREDENTIAL_LIKE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)",
    re.IGNORECASE,
)


class ExportSafetyError(ValueError):
    """Rejected unsafe export request."""


def assert_safe_identifier(name: str, *, kind: str = "identifier") -> str:
    value = str(name or "").strip()
    if not value or not _IDENT.match(value):
        raise ExportSafetyError(f"Invalid {kind}: {name!r}")
    if value.lower() in _BLOCKED_SCHEMAS:
        raise ExportSafetyError(f"Blocked schema/object: {value}")
    if _CREDENTIAL_LIKE.search(value):
        raise ExportSafetyError(f"Blocked credential-like {kind}: {value}")
    return value


def quote_ident(name: str, *, dialect: str = "postgres") -> str:
    ident = assert_safe_identifier(name)
    if dialect == "sqlite":
        return f'"{ident}"'
    return f'"{ident}"'


def resolve_columns(source: ExportSource, selected: list[str] | None) -> list[str]:
    allowed = list(source.allowed_columns)
    excluded = set(source.excluded_columns)
    sensitive = set(source.sensitive_columns)
    allowed_set = set(allowed)

    if selected:
        cols = [str(c).strip() for c in selected if str(c).strip()]
    else:
        cols = list(source.default_columns) or list(allowed)

    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        if c in seen:
            continue
        seen.add(c)
        if c not in allowed_set:
            raise ExportSafetyError(f"Column not allowlisted: {c}")
        if c in excluded:
            raise ExportSafetyError(f"Column is excluded by policy: {c}")
        if c in sensitive:
            raise ExportSafetyError(f"Sensitive column blocked: {c}")
        if _CREDENTIAL_LIKE.search(c):
            raise ExportSafetyError(f"Credential-like column blocked: {c}")
        assert_safe_identifier(c, kind="column")
        out.append(c)
    if not out:
        raise ExportSafetyError("At least one column is required")
    return out


def sanitize_filename(name: str, *, max_len: int = 120) -> str:
    base = _SAFE_FILENAME.sub("_", str(name or "export").strip()) or "export"
    base = base.strip("._-") or "export"
    return base[:max_len]


def normalize_environment(raw: str) -> str:
    env = str(raw or "live").strip().lower()
    if env not in ("live", "stage", "dev"):
        raise ExportSafetyError(f"Invalid environment: {raw}")
    return env


def validate_filters(source: ExportSource, filters: dict[str, Any]) -> dict[str, Any]:
    """Return normalized filters; raise if required missing or unsupported values."""
    supported = set(source.filters_supported)
    required = set(source.required_filters)
    out: dict[str, Any] = {}

    env = normalize_environment(str(filters.get("environment") or "live"))
    out["environment"] = env

    if "quarter" in supported or "quarter" in required:
        quarter = str(filters.get("quarter") or "").strip()
        if "quarter" in required and not quarter:
            raise ExportSafetyError("quarter filter is required")
        if quarter:
            # Accept YYYYQn or free quarter codes already used in hub (alphanumeric + Q)
            if not re.match(r"^[A-Za-z0-9._-]{1,32}$", quarter):
                raise ExportSafetyError("Invalid quarter value")
            out["quarter"] = quarter

    if "organisation_unit" in supported:
        ou = filters.get("organisation_unit") or {}
        if isinstance(ou, str):
            ou = {"uid": ou}
        uid = str((ou or {}).get("uid") or filters.get("org_unit_uid") or "").strip()
        if uid:
            if not re.match(r"^[A-Za-z0-9]{6,15}$", uid):
                raise ExportSafetyError("Invalid organisation unit UID")
            out["org_unit_uid"] = uid
            out["org_unit_name"] = str((ou or {}).get("name") or "")[:200]

    if "date_range" in supported or source.date_column:
        date_from = str(filters.get("date_from") or "").strip()
        date_to = str(filters.get("date_to") or "").strip()
        if date_from:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_from):
                raise ExportSafetyError("Invalid date_from")
            out["date_from"] = date_from
        if date_to:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_to):
                raise ExportSafetyError("Invalid date_to")
            out["date_to"] = date_to

    if "status" in supported and source.status_column:
        status = str(filters.get("status") or "").strip()
        if status:
            if not re.match(r"^[A-Za-z0-9 _.-]{1,64}$", status):
                raise ExportSafetyError("Invalid status")
            out["status"] = status

    if "ip_flag" in supported and source.ip_column:
        ip_raw = filters.get("ip_flag")
        if ip_raw is not None and str(ip_raw).strip() != "":
            ip = str(ip_raw).strip().lower()
            if ip in ("1", "true", "yes", "ip"):
                out["ip_flag"] = "IP"
            elif ip in ("0", "false", "no", "non-ip", "non_ip"):
                out["ip_flag"] = "Non-IP"
            else:
                raise ExportSafetyError("Invalid ip_flag")

    row_limit = filters.get("row_limit")
    if row_limit is None or str(row_limit).strip() == "":
        row_limit = source.maximum_rows
    try:
        limit = int(row_limit)
    except (TypeError, ValueError) as exc:
        raise ExportSafetyError("Invalid row_limit") from exc
    if limit < 1:
        raise ExportSafetyError("row_limit must be >= 1")
    limit = min(limit, int(source.maximum_rows))
    out["row_limit"] = limit

    # Reject unknown filter keys that look like SQL injection attempts
    banned_keys = {
        "sql",
        "query",
        "table",
        "schema",
        "join",
        "expression",
        "where",
        "order_by_raw",
    }
    for k in filters.keys():
        if str(k).lower() in banned_keys:
            raise ExportSafetyError(f"Filter key not allowed: {k}")

    missing = [r for r in required if r == "quarter" and "quarter" not in out]
    if missing:
        raise ExportSafetyError(f"Missing required filters: {', '.join(missing)}")

    return out


def assert_source_object_safe(source: ExportSource) -> None:
    if source.source_type == "saved_query":
        if not source.saved_query_id or not re.match(r"^[A-Za-z0-9_-]{8,64}$", source.saved_query_id):
            raise RegistryError("Invalid saved_query_id")
        return
    if source.schema:
        assert_safe_identifier(source.schema, kind="schema")
        if source.schema.lower() in _BLOCKED_SCHEMAS:
            raise ExportSafetyError(f"Blocked schema: {source.schema}")
    assert_safe_identifier(source.object_name, kind="object")
