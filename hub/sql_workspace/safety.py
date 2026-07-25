"""Parse-based read-only SQL validation (not regex-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SqlSafetyError(ValueError):
    """Raised when SQL is rejected by the read-only policy."""


_DML_DDL_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Copy,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

_BLOCKED_COMMAND_PREFIXES = (
    "COPY",
    "CALL",
    "DO",
    "EXECUTE",
    "GRANT",
    "REVOKE",
    "ALTER",
    "CREATE",
    "DROP",
    "TRUNCATE",
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "REPLACE",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
    "CLUSTER",
    "LOCK",
    "NOTIFY",
    "LISTEN",
    "LOAD",
    "RESET",
    "DISCARD",
    "SECURITY",
    "OWNER",
    "ANALYZE",  # standalone ANALYZE (stats), not EXPLAIN ANALYZE
)


@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    kind: str  # select | with | explain
    dialect: str


def _normalize_dialect(dialect: str | None) -> str:
    raw = (dialect or "postgres").strip().lower()
    if raw in {"postgresql", "postgres", "pg"}:
        return "postgres"
    if raw in {"sqlite", "sqlite3"}:
        return "sqlite"
    return raw


def _command_text(node: exp.Command) -> str:
    parts = []
    for attr in ("this", "expression"):
        val = getattr(node, attr, None)
        if val is not None:
            parts.append(str(val))
    if not parts:
        parts.append(node.sql())
    return " ".join(parts).strip()


def _is_explain_command(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Command):
        return False
    text = _command_text(node).lstrip().upper()
    return text.startswith("EXPLAIN")


def _strip_explain_prefix(body: str) -> str:
    rest = (body or "").strip()
    if rest.upper().startswith("ANALYZE"):
        rest = rest[7:].lstrip()
    if rest.startswith("("):
        depth = 0
        for i, ch in enumerate(rest):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    rest = rest[i + 1 :].lstrip()
                    break
    return rest.strip()


def _explain_body_sql(node: exp.Command) -> str:
    """Extract the statement after EXPLAIN [ANALYZE] [options]."""
    # sqlglot often stores the body as a string literal expression.
    expr = getattr(node, "expression", None)
    if isinstance(expr, exp.Literal) and expr.is_string:
        body = _strip_explain_prefix(str(expr.this or ""))
        if body:
            return body
    if isinstance(expr, exp.Expression) and not isinstance(expr, exp.Literal):
        return expr.sql()

    raw = _command_text(node).strip()
    if raw.upper().startswith("EXPLAIN"):
        rest = raw[7:].lstrip()
    else:
        rest = raw
    if len(rest) >= 2 and rest[0] == "'" and rest[-1] == "'":
        rest = rest[1:-1].replace("''", "'")
    rest = _strip_explain_prefix(rest)
    if not rest:
        raise SqlSafetyError("EXPLAIN requires a SELECT/WITH body.")
    return rest


def _is_modifying_cte(node: exp.Expression) -> bool:
    if not isinstance(node, exp.With):
        return False
    for cte in node.expressions or []:
        body = cte.this if isinstance(cte, exp.CTE) else cte
        if body is None:
            continue
        if isinstance(body, _DML_DDL_TYPES) or isinstance(body, exp.Command):
            return True
        for child in body.walk():
            if isinstance(child, _DML_DDL_TYPES):
                return True
            if isinstance(child, exp.Command) and not _is_explain_command(child):
                return True
    return False


def _assert_readonly_tree(root: exp.Expression, *, allow_explain_command: bool = False) -> None:
    for node in root.walk():
        if isinstance(node, _DML_DDL_TYPES):
            raise SqlSafetyError(f"Blocked statement type: {type(node).__name__.upper()}.")
        if isinstance(node, exp.With) and _is_modifying_cte(node):
            raise SqlSafetyError("Blocked modifying CTE (WITH … AS INSERT/UPDATE/DELETE).")
        if isinstance(node, exp.Command):
            if allow_explain_command and node is root and _is_explain_command(node):
                continue
            text = _command_text(node).lstrip().upper()
            token = text.split(None, 1)[0] if text else "COMMAND"
            if token in _BLOCKED_COMMAND_PREFIXES or token not in {"EXPLAIN"}:
                raise SqlSafetyError(f"Blocked statement type: {token}.")
            raise SqlSafetyError(f"Blocked statement type: COMMAND ({token}).")


def _classify_select_like(root: exp.Expression) -> str:
    if isinstance(root, exp.With) or root.find(exp.With):
        return "with"
    if isinstance(root, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return "select"
    if root.find(exp.Select):
        return "select"
    raise SqlSafetyError(
        f"Only SELECT, read-only WITH, and EXPLAIN are allowed (got {type(root).__name__})."
    )


def validate_readonly_sql(sql: str, *, dialect: str | None = "postgres") -> ValidatedSql:
    """
    Validate a single read-only statement using sqlglot AST parsing.

    Rejects multi-statement batches, DML/DDL, COPY/CALL, modifying CTEs,
    and comment/case obfuscation that still parses to blocked forms.
    """
    text = (sql or "").strip()
    if not text:
        raise SqlSafetyError("SQL is empty.")

    dial = _normalize_dialect(dialect)

    try:
        statements = sqlglot.parse(text, read=dial)
    except ParseError as exc:
        raise SqlSafetyError(f"SQL could not be parsed: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if not statements:
        raise SqlSafetyError("SQL could not be parsed into a statement.")
    if len(statements) > 1:
        raise SqlSafetyError("Only one SQL statement is allowed per run.")

    root = statements[0]

    # EXPLAIN surfaces as Command in current sqlglot — validate body separately.
    if isinstance(root, exp.Command) and _is_explain_command(root):
        body_sql = _explain_body_sql(root)
        body = validate_readonly_sql(body_sql, dialect=dial)
        if body.kind not in {"select", "with"}:
            raise SqlSafetyError("EXPLAIN body must be read-only SELECT/WITH.")
        try:
            canonical = root.sql(dialect=dial)
        except Exception:  # noqa: BLE001
            canonical = text
        return ValidatedSql(sql=canonical, kind="explain", dialect=dial)

    _assert_readonly_tree(root, allow_explain_command=False)
    kind = _classify_select_like(root)

    try:
        canonical = root.sql(dialect=dial)
    except Exception:  # noqa: BLE001
        canonical = text

    return ValidatedSql(sql=canonical, kind=kind, dialect=dial)


def format_sql(sql: str, *, dialect: str | None = "postgres") -> str:
    dial = _normalize_dialect(dialect)
    text = (sql or "").strip()
    if not text:
        raise SqlSafetyError("SQL is empty.")
    try:
        expressions = sqlglot.parse(text, read=dial)
    except ParseError as exc:
        raise SqlSafetyError(f"SQL could not be parsed: {exc}") from exc
    parts = []
    for e in expressions:
        if e is None:
            continue
        if isinstance(e, exp.Command) and _is_explain_command(e):
            body = _explain_body_sql(e)
            pretty_body = format_sql(body, dialect=dial)
            parts.append(f"EXPLAIN\n{pretty_body}")
        else:
            parts.append(e.sql(dialect=dial, pretty=True))
    if not parts:
        raise SqlSafetyError("SQL could not be parsed.")
    if len(parts) > 1:
        # Formatter may be used before safety check; keep multi as joined for edit aid
        return ";\n".join(parts)
    return parts[0]


def extract_named_params(sql: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    i = 0
    text = sql or ""
    n = len(text)
    in_single = False
    while i < n:
        ch = text[i]
        if ch == "'" and not in_single:
            in_single = True
            i += 1
            continue
        if in_single:
            if ch == "'" and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == ":" and i + 1 < n and (text[i + 1].isalpha() or text[i + 1] == "_"):
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            name = text[i + 1 : j]
            if name and name not in seen:
                seen.add(name)
                names.append(name)
            i = j
            continue
        i += 1
    return names


def bind_named_params(sql: str, params: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    needed = extract_named_params(sql)
    raw = params or {}
    bound: dict[str, Any] = {}
    missing: list[str] = []
    for name in needed:
        if name not in raw:
            missing.append(name)
        else:
            bound[name] = raw[name]
    if missing:
        raise SqlSafetyError(
            "Missing parameter value(s): " + ", ".join(f":{m}" for m in missing)
        )
    return sql, bound
