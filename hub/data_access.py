"""Shared SELECT-only primitives for Data Explorer browse and export paths."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BLOCKED_SCHEMAS = frozenset(
    {"pg_catalog", "information_schema", "pg_toast", "mysql", "sys", "performance_schema"}
)
CREDENTIAL_LIKE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)",
    re.IGNORECASE,
)


def validate_identifier(
    name: str,
    *,
    kind: str,
    error: Callable[[str], Exception],
    block_credentials: bool = False,
) -> str:
    value = str(name or "").strip()
    if not value or not IDENTIFIER.match(value):
        raise error(f"Invalid {kind}: {name!r}")
    if value.lower() in BLOCKED_SCHEMAS:
        raise error(f"Blocked {kind}: {value}")
    if block_credentials and CREDENTIAL_LIKE.search(value):
        raise error(f"Blocked credential-like {kind}: {value}")
    return value


def normalize_environment(
    raw: str,
    *,
    default: str,
    error: Callable[[str], Exception],
) -> str:
    env = str(raw or default).strip().lower()
    if env not in ("live", "stage", "dev"):
        raise error(f"Invalid environment: {raw}")
    return env


def compose_select(
    *,
    select_list: str,
    from_sql: str,
    where_parts: Sequence[str],
    order_parts: Sequence[str] = (),
    limit_placeholder: str,
    offset_placeholder: str | None = None,
) -> tuple[str, str]:
    """Render the only executable statement shape used by data browsing/export."""
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    order_sql = (" ORDER BY " + ", ".join(order_parts)) if order_parts else ""
    paging = f" LIMIT {limit_placeholder}"
    if offset_placeholder:
        paging += f" OFFSET {offset_placeholder}"
    data_sql = f"SELECT {select_list} FROM {from_sql}{where_sql}{order_sql}{paging}"
    count_sql = f"SELECT COUNT(*) AS row_count FROM {from_sql}{where_sql}"
    return data_sql, count_sql
