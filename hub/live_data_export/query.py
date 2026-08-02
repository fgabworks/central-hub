"""Build parameterized SELECT statements from allowlisted registry metadata only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hub.live_data_export.registry import ExportSource
from hub.live_data_export.security import (
    ExportSafetyError,
    assert_source_object_safe,
    quote_ident,
    resolve_columns,
    validate_filters,
)


@dataclass(frozen=True)
class BuiltQuery:
    sql: str
    count_sql: str
    params: dict[str, Any]
    columns: list[str]
    filters: dict[str, Any]
    dialect: str


def _from_clause(source: ExportSource, dialect: str) -> str:
    assert_source_object_safe(source)
    obj = quote_ident(source.object_name, dialect=dialect)
    if source.schema and dialect != "sqlite":
        return f"{quote_ident(source.schema, dialect=dialect)}.{obj}"
    if source.schema and dialect == "sqlite" and source.schema not in ("main", ""):
        return f"{quote_ident(source.schema, dialect=dialect)}.{obj}"
    return obj


def _param_placeholder(name: str, dialect: str) -> str:
    # Named params as :name — converted per driver in the runner.
    _ = dialect
    return f":{name}"


def build_select(
    source: ExportSource,
    *,
    filters: dict[str, Any],
    columns: list[str] | None = None,
    dialect: str = "postgres",
    for_preview: bool = False,
    preview_limit: int = 25,
) -> BuiltQuery:
    if source.source_type == "saved_query":
        raise ExportSafetyError(
            "Saved-query export sources are not enabled in Phase 1 until verified"
        )

    norm_filters = validate_filters(source, filters)
    cols = resolve_columns(source, columns)
    select_list = ", ".join(quote_ident(c, dialect=dialect) for c in cols)
    from_sql = _from_clause(source, dialect)
    where_parts: list[str] = []
    params: dict[str, Any] = {}

    if "quarter" in norm_filters and source.quarter_column:
        qc = quote_ident(source.quarter_column, dialect=dialect)
        where_parts.append(f"{qc} = {_param_placeholder('quarter', dialect)}")
        params["quarter"] = norm_filters["quarter"]

    if "org_unit_uid" in norm_filters and source.organisation_unit_column:
        oc = quote_ident(source.organisation_unit_column, dialect=dialect)
        where_parts.append(f"{oc} = {_param_placeholder('org_unit_uid', dialect)}")
        params["org_unit_uid"] = norm_filters["org_unit_uid"]

    if "date_from" in norm_filters and source.date_column:
        dc = quote_ident(source.date_column, dialect=dialect)
        where_parts.append(f"{dc} >= {_param_placeholder('date_from', dialect)}")
        params["date_from"] = norm_filters["date_from"]

    if "date_to" in norm_filters and source.date_column:
        dc = quote_ident(source.date_column, dialect=dialect)
        where_parts.append(f"{dc} <= {_param_placeholder('date_to', dialect)}")
        params["date_to"] = norm_filters["date_to"]

    if "status" in norm_filters and source.status_column:
        sc = quote_ident(source.status_column, dialect=dialect)
        where_parts.append(f"{sc} = {_param_placeholder('status', dialect)}")
        params["status"] = norm_filters["status"]

    if "ip_flag" in norm_filters and source.ip_column:
        ic = quote_ident(source.ip_column, dialect=dialect)
        where_parts.append(f"{ic} = {_param_placeholder('ip_flag', dialect)}")
        params["ip_flag"] = norm_filters["ip_flag"]

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    order_parts: list[str] = []
    allowed = set(source.allowed_columns)
    for spec in source.default_sort:
        if spec.column not in allowed:
            continue
        direction = "DESC" if spec.direction == "desc" else "ASC"
        order_parts.append(f"{quote_ident(spec.column, dialect=dialect)} {direction}")
    order_sql = (" ORDER BY " + ", ".join(order_parts)) if order_parts else ""

    limit = int(norm_filters["row_limit"])
    if for_preview:
        limit = min(limit, int(preview_limit))
    params["row_limit"] = limit

    data_sql = (
        f"SELECT {select_list} FROM {from_sql}{where_sql}{order_sql} "
        f"LIMIT {_param_placeholder('row_limit', dialect)}"
    )
    count_sql = f"SELECT COUNT(*) AS row_count FROM {from_sql}{where_sql}"
    # count does not need row_limit
    count_params = {k: v for k, v in params.items() if k != "row_limit"}

    return BuiltQuery(
        sql=data_sql,
        count_sql=count_sql,
        params=params,
        columns=cols,
        filters={**norm_filters, "_count_params": count_params},
        dialect=dialect,
    )


def estimate_export_bytes(row_count: int, column_count: int, *, format: str = "csv") -> int:
    """Rough size estimate for UI warnings (not exact)."""
    # ~24 bytes per cell average + overhead
    per_row = max(32, column_count * 24)
    raw = row_count * per_row + 1024
    if format == "xlsx":
        return int(raw * 1.3)
    if format == "csv_gz":
        return max(256, int(raw * 0.25))
    return raw
