"""Security helpers for Data Explorer."""

from __future__ import annotations

from typing import Any

from hub.data_access import BLOCKED_SCHEMAS, normalize_environment as normalize_data_environment, validate_identifier
from hub.data_explorer.config import ExplorerConfig, get_explorer_config

_BLOCKED_SCHEMAS = BLOCKED_SCHEMAS


class ExplorerSafetyError(ValueError):
    """Rejected unsafe explorer request."""


def normalize_environment(raw: str) -> str:
    return normalize_data_environment(raw, default="dev", error=ExplorerSafetyError)


def assert_safe_identifier(name: str, *, kind: str = "identifier") -> str:
    return validate_identifier(name, kind=kind, error=ExplorerSafetyError)


def quote_ident(name: str) -> str:
    return f'"{assert_safe_identifier(name)}"'


def is_excluded_schema(schema: str, cfg: ExplorerConfig | None = None) -> bool:
    cfg = cfg or get_explorer_config()
    s = (schema or "").lower()
    if s in _BLOCKED_SCHEMAS:
        return True
    return s in {x.lower() for x in cfg.defaults.excluded_schemas}


def column_action(column: str, cfg: ExplorerConfig | None = None) -> str | None:
    cfg = cfg or get_explorer_config()
    for pol in cfg.column_policies:
        if pol.pattern.search(column or ""):
            return pol.action
    return None


def apply_column_policies(
    columns: list[str],
    *,
    cfg: ExplorerConfig | None = None,
    for_export: bool = False,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Return (visible_columns, warnings, actions_by_column)."""
    cfg = cfg or get_explorer_config()
    visible: list[str] = []
    warnings: list[str] = []
    actions: dict[str, str] = {}
    for col in columns:
        assert_safe_identifier(col, kind="column")
        action = column_action(col, cfg)
        if action:
            actions[col] = action
        if action == "hide":
            warnings.append(f"Hidden sensitive column: {col}")
            continue
        if for_export and action in ("export_prohibit", "preview_only"):
            warnings.append(f"Export blocked for column: {col}")
            continue
        visible.append(col)
    return visible, warnings, actions


def mask_row_values(
    columns: list[str],
    rows: list[list[Any]],
    actions: dict[str, str],
) -> list[list[Any]]:
    if not actions:
        return rows
    idxs = [i for i, c in enumerate(columns) if actions.get(c) == "mask"]
    if not idxs:
        return rows
    out: list[list[Any]] = []
    for row in rows:
        copy = list(row)
        for i in idxs:
            if i < len(copy) and copy[i] not in (None, ""):
                copy[i] = "***"
        out.append(copy)
    return out


def validate_sort_column(column: str | None, allowed: set[str]) -> str | None:
    if not column:
        return None
    col = assert_safe_identifier(column, kind="column")
    if col not in allowed:
        raise ExplorerSafetyError(f"Sort column not in object metadata: {col}")
    return col


def validate_filter_ops(filters: list[dict[str, Any]], allowed: set[str]) -> list[dict[str, Any]]:
    """Normalize grid filters: eq, neq, gt, gte, lt, lte, contains, is_null, not_null."""
    allowed_ops = {"eq", "neq", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"}
    out: list[dict[str, Any]] = []
    for i, f in enumerate(filters or []):
        if not isinstance(f, dict):
            raise ExplorerSafetyError("Invalid filter")
        col = assert_safe_identifier(str(f.get("column") or ""), kind="column")
        if col not in allowed:
            raise ExplorerSafetyError(f"Filter column not in object metadata: {col}")
        op = str(f.get("op") or "eq").lower()
        if op not in allowed_ops:
            raise ExplorerSafetyError(f"Unsupported filter op: {op}")
        value = f.get("value")
        if op in ("is_null", "not_null"):
            value = None
        elif value is None:
            raise ExplorerSafetyError(f"Filter value required for {op}")
        elif op == "contains":
            value = str(value)[:200]
        out.append({"column": col, "op": op, "value": value, "_i": i})
    if len(out) > 20:
        raise ExplorerSafetyError("Too many filters")
    return out
