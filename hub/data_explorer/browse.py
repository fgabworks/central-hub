"""Build parameterized browse SELECT from discovered metadata only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hub.data_access import compose_select
from hub.data_explorer.discovery import ObjectMeta
from hub.data_explorer.security import (
    ExplorerSafetyError,
    apply_column_policies,
    assert_safe_identifier,
    column_action,
    quote_ident,
    validate_filter_ops,
    validate_sort_column,
    validate_sort_direction,
)


@dataclass(frozen=True)
class BrowseQuery:
    sql: str
    count_sql: str
    params: dict[str, Any]
    columns: list[str]
    warnings: list[str]
    dialect: str


def build_browse_query(
    obj: ObjectMeta,
    *,
    columns: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    sort_column: str | None = None,
    sort_dir: str = "asc",
    limit: int = 100,
    offset: int = 0,
    dialect: str = "postgres",
    for_export: bool = False,
) -> BrowseQuery:
    meta_cols = [c.name for c in obj.columns]
    allowed = set(meta_cols)
    browsable_types = {
        c.name: c.data_type for c in obj.columns if column_action(c.name) != "hide"
    }
    if not meta_cols:
        raise ExplorerSafetyError("Object has no discoverable columns")

    requested = columns or meta_cols
    requested = [assert_safe_identifier(c, kind="column") for c in requested]
    for c in requested:
        if c not in allowed:
            raise ExplorerSafetyError(f"Column not in object metadata: {c}")

    visible, warnings, _actions = apply_column_policies(requested, for_export=for_export)
    if not visible:
        raise ExplorerSafetyError("No browsable columns after sensitivity policy")

    select_list = ", ".join(quote_ident(c) for c in visible)
    from_sql = _from_clause(obj, dialect)

    where_parts: list[str] = []
    params: dict[str, Any] = {}
    norm_filters = validate_filter_ops(filters or [], browsable_types)
    for f in norm_filters:
        col = quote_ident(f["column"])
        op = f["op"]
        key = f"f{f['_i']}"
        if op == "is_null":
            where_parts.append(f"{col} IS NULL")
        elif op == "not_null":
            where_parts.append(f"{col} IS NOT NULL")
        elif op == "contains":
            where_parts.append(f"CAST({col} AS TEXT) LIKE :{key}")
            params[key] = f"%{f['value']}%"
        else:
            sql_op = {
                "eq": "=",
                "neq": "<>",
                "gt": ">",
                "gte": ">=",
                "lt": "<",
                "lte": "<=",
            }[op]
            where_parts.append(f"{col} {sql_op} :{key}")
            params[key] = f["value"]

    sort_col = validate_sort_column(sort_column, set(browsable_types))
    order_parts: list[str] = []
    if sort_col:
        direction = validate_sort_direction(sort_dir).upper()
        order_parts.append(f"{quote_ident(sort_col)} {direction}")

    limit = max(1, int(limit))
    offset = max(0, int(offset))
    params["row_limit"] = limit
    params["row_offset"] = offset

    data_sql, count_sql = compose_select(
        select_list=select_list,
        from_sql=from_sql,
        where_parts=where_parts,
        order_parts=order_parts,
        limit_placeholder=":row_limit",
        offset_placeholder=":row_offset",
    )
    return BrowseQuery(
        sql=data_sql,
        count_sql=count_sql,
        params=params,
        columns=visible,
        warnings=warnings,
        dialect=dialect,
    )


def generate_safe_query_text(obj: ObjectMeta, browse: BrowseQuery) -> str:
    return browse.sql


def _from_clause(obj: ObjectMeta, dialect: str) -> str:
    assert_safe_identifier(obj.name, kind="object")
    if obj.schema and not (dialect == "sqlite" and obj.schema == "main"):
        assert_safe_identifier(obj.schema, kind="schema")
        return f"{quote_ident(obj.schema)}.{quote_ident(obj.name)}"
    return quote_ident(obj.name)
