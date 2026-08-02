"""Security helpers for Data Explorer."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from hub.data_access import BLOCKED_SCHEMAS, normalize_environment as normalize_data_environment, validate_identifier
from hub.data_explorer.config import ExplorerConfig, get_explorer_config

_BLOCKED_SCHEMAS = BLOCKED_SCHEMAS


class ExplorerSafetyError(ValueError):
    """Rejected unsafe explorer request."""


class ExplorerFilterError(ExplorerSafetyError):
    """Rejected filter that does not match discovered column metadata."""

    code = "invalid_filter"


class ExplorerSortError(ExplorerSafetyError):
    """Rejected sort that does not match discovered column metadata."""

    code = "invalid_sort"


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
    try:
        col = assert_safe_identifier(column, kind="column")
    except ExplorerSafetyError as exc:
        raise ExplorerSortError(str(exc)) from exc
    if col not in allowed:
        raise ExplorerSortError(f"Sort column not in object metadata: {col}")
    return col


def column_type_family(data_type: str) -> str:
    """Map SQLite/PostgreSQL type names to a small operator-safe family."""
    value = str(data_type or "").strip().lower()
    if any(token in value for token in ("char", "text", "clob", "varchar")):
        return "text"
    if any(
        token in value
        for token in (
            "int",
            "numeric",
            "decimal",
            "real",
            "double",
            "float",
            "serial",
            "money",
        )
    ):
        return "number"
    if any(token in value for token in ("date", "time", "timestamp", "interval")):
        return "temporal"
    if "bool" in value:
        return "boolean"
    return "other"


def filter_operators_for_type(data_type: str) -> list[str]:
    family = column_type_family(data_type)
    common = ["eq", "neq"]
    if family == "text":
        common.append("contains")
    elif family in ("number", "temporal"):
        common.extend(["gt", "gte", "lt", "lte"])
    return [*common, "is_null", "not_null"]


def validate_sort_direction(direction: str | None) -> str:
    value = str(direction or "asc").strip().lower()
    if value not in {"asc", "desc"}:
        raise ExplorerSortError(f"Unsupported sort direction: {value}")
    return value


def validate_filter_ops(
    filters: list[dict[str, Any]],
    columns: dict[str, str],
) -> list[dict[str, Any]]:
    """Normalize AND filters against discovered names and column types."""
    out: list[dict[str, Any]] = []
    for i, f in enumerate(filters or []):
        if not isinstance(f, dict):
            raise ExplorerFilterError("Invalid filter")
        try:
            col = assert_safe_identifier(str(f.get("column") or ""), kind="column")
        except ExplorerSafetyError as exc:
            raise ExplorerFilterError(str(exc)) from exc
        if col not in columns:
            raise ExplorerFilterError(f"Filter column not in browsable object metadata: {col}")
        op = str(f.get("op") or "eq").lower()
        allowed_ops = filter_operators_for_type(columns[col])
        if op not in allowed_ops:
            raise ExplorerFilterError(
                f"Operator {op} is not valid for {col} ({columns[col] or 'unknown type'})"
            )
        value = f.get("value")
        if op in ("is_null", "not_null"):
            value = None
        elif value is None:
            raise ExplorerFilterError(f"Filter value required for {op}")
        elif op == "contains":
            value = str(value)[:200]
        elif column_type_family(columns[col]) == "number":
            try:
                number = Decimal(str(value).strip())
            except (InvalidOperation, ValueError) as exc:
                raise ExplorerFilterError(f"Numeric filter value required for {col}") from exc
            if not number.is_finite():
                raise ExplorerFilterError(f"Numeric filter value required for {col}")
            value = str(value).strip()
        elif column_type_family(columns[col]) == "temporal":
            value = str(value).strip()
            if not value:
                raise ExplorerFilterError(f"Date/time filter value required for {col}")
        elif column_type_family(columns[col]) == "boolean":
            normalized = str(value).strip().lower()
            if normalized not in {"true", "false", "1", "0"}:
                raise ExplorerFilterError(f"Boolean filter value must be true or false for {col}")
            value = normalized in {"true", "1"}
        out.append({"column": col, "op": op, "value": value, "_i": i})
    if len(out) > 20:
        raise ExplorerFilterError("Too many filters")
    return out
