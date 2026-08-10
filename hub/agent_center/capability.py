"""Capability-aware escalation after T0 — classify failure, try DB before AI.

Evidence discovery is not completion. When T0 is unsolved, resolve the cheapest
capable next step (connected read-only SQL / OU tools) before Cannot verify or AI.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from hub.agent_center.completion import (
    INTENT_COUNT,
    INTENT_LIST,
    INTENT_LOOKUP,
    INTENT_STATUS,
    CompletionContract,
)

# Why T0 remained unsolved
FAILURE_MISSING_SOURCE = "missing_authoritative_source"
FAILURE_QUERY_NOT_EXECUTED = "source_available_query_not_executed"
FAILURE_FILTERS_INCOMPLETE = "filters_or_entity_resolution_incomplete"
FAILURE_NEEDS_REASONING = "source_available_needs_query_construction"
FAILURE_PROVIDER_UNAVAILABLE = "provider_or_tool_unavailable"
FAILURE_UNVERIFIABLE = "genuinely_unverifiable"

# Next capability
NEXT_SQL_EXECUTE = "deterministic_sql_execute"
NEXT_OU_RESOLVE = "org_unit_resolution"
NEXT_AI = "ai_escalate"
NEXT_CANNOT_VERIFY = "cannot_verify"

_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "location": ("location", "ou_name", "org_unit", "barangay", "municipality", "name"),
    "period": ("period", "date", "quarter", "year"),
    "population_group": ("population", "population_group", "group", "cohort"),
    "indicator_ref": ("indicator", "indicator_ref", "data_element", "de", "pi"),
    "status": ("status",),
    "environment": ("environment", "env"),
    "uid": ("uid", "ou_uid", "org_unit_uid", "id"),
}


@dataclass(frozen=True)
class CapabilitySnapshot:
    sql_store_available: bool = False
    sql_executor_available: bool = False
    sql_connections_configured: tuple[str, ...] = ()
    dhis2_available: bool = False
    saved_query_match_count: int = 0
    executable_query_ids: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureClassification:
    reason: str
    detail: str
    next_capability: str
    can_ai_help: bool
    db_available: bool = False
    db_query_attempted: bool = False
    filters: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeterministicSqlAttempt:
    attempted: bool = False
    ok: bool = False
    answer: str | None = None
    reason: str = ""
    query_id: str = ""
    connection_id: str = ""
    tool_result: dict[str, Any] | None = None
    hits: list[dict[str, Any]] = field(default_factory=list)
    needs_ai: bool = False
    unavailable: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "ok": self.ok,
            "answer": self.answer,
            "reason": self.reason,
            "query_id": self.query_id,
            "connection_id": self.connection_id,
            "needs_ai": self.needs_ai,
            "unavailable": self.unavailable,
            "hit_count": len(self.hits),
        }


def _callable_attr(obj: Any, name: str) -> bool:
    return obj is not None and callable(getattr(obj, name, None))


def snapshot_capabilities(
    *,
    sql_store: Any | None,
    sql_executor: Any | None,
    sql_connections: Any | None,
    dhis2_reports: Any | None,
    saved_matches: list[dict[str, Any]] | None = None,
) -> CapabilitySnapshot:
    configured: list[str] = []
    if sql_connections is not None and callable(getattr(sql_connections, "list_public", None)):
        try:
            raw = sql_connections.list_public()
            if isinstance(raw, (list, tuple)):
                for row in raw:
                    if isinstance(row, dict) and row.get("configured") and row.get("enabled", True):
                        cid = str(row.get("id") or "").strip()
                        if cid:
                            configured.append(cid)
        except Exception:  # noqa: BLE001 — capability probe must not crash T0
            configured = []
    matches = [m for m in (saved_matches or []) if isinstance(m, dict)]
    executable = [
        str(m.get("id") or "")
        for m in matches
        if m.get("id") and m.get("connection_id") and str(m.get("connection_id")) in configured
    ]
    return CapabilitySnapshot(
        sql_store_available=_callable_attr(sql_store, "list_queries"),
        sql_executor_available=_callable_attr(sql_executor, "execute"),
        sql_connections_configured=tuple(c for c in configured if c),
        dhis2_available=dhis2_reports is not None
        and callable(getattr(dhis2_reports, "search_org_units", None)),
        saved_query_match_count=len(matches),
        executable_query_ids=tuple(q for q in executable if q),
    )


def _search_terms_from_filters(filters: dict[str, Any], prompt: str) -> list[str]:
    terms: list[str] = []
    for key in ("location", "indicator_ref", "population_group", "status", "environment"):
        val = filters.get(key)
        if isinstance(val, str) and val.strip():
            terms.append(val.strip())
        elif isinstance(val, list):
            for item in val[:3]:
                if str(item).strip():
                    terms.append(str(item).strip())
    for uid in filters.get("uids") or []:
        if str(uid).strip():
            terms.append(str(uid).strip())
    for term in filters.get("search_terms") or []:
        if str(term).strip():
            terms.append(str(term).strip())
    # Soft prompt tokens (generic — not a place catalog).
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9_-]{2,40})\b", prompt or ""):
        token = m.group(1)
        if token.lower() in {
            "count",
            "total",
            "how",
            "many",
            "what",
            "show",
            "list",
            "the",
            "for",
            "and",
            "with",
            "from",
            "number",
            "eligible",
            "status",
            "approved",
        }:
            continue
        terms.append(token)
    return list(dict.fromkeys(t for t in terms if t))[:8]


def find_saved_sql_matches(
    sql_store: Any,
    *,
    filters: dict[str, Any],
    prompt: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not _callable_attr(sql_store, "list_queries"):
        return []
    terms = _search_terms_from_filters(filters, prompt)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    searches = terms[:5] or [(prompt or "").strip()[:80]]
    for term in searches:
        try:
            rows = sql_store.list_queries(q=term, limit=limit) or []
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            qid = str(row.get("id") or "").strip()
            if not qid or qid in seen:
                continue
            seen.add(qid)
            # Prefer full row when get_query is available (includes sql_text).
            full = row
            if _callable_attr(sql_store, "get_query"):
                try:
                    loaded = sql_store.get_query(qid)
                    if isinstance(loaded, dict) and loaded.get("sql_text"):
                        full = loaded
                except Exception:  # noqa: BLE001
                    pass
            found.append(full)
            if len(found) >= limit:
                return found
    return found


def _period_value(filters: dict[str, Any]) -> str | None:
    period = filters.get("period")
    if isinstance(period, list) and period:
        return str(period[0])
    if isinstance(period, str) and period.strip():
        return period.strip()
    return None


def map_filters_to_sql_params(
    needed: list[str],
    filters: dict[str, Any],
) -> dict[str, Any] | None:
    """Map completion filters onto named SQL params. None = cannot bind fully."""
    if not needed:
        return {}
    flat: dict[str, Any] = {}
    for key, val in (filters or {}).items():
        if key == "period":
            pv = _period_value(filters)
            if pv:
                flat["period"] = pv
                flat["date"] = pv
                flat["quarter"] = pv
            continue
        if key == "uids" and isinstance(val, list) and val:
            flat["uid"] = str(val[0])
            flat["ou_uid"] = str(val[0])
            flat["id"] = str(val[0])
            continue
        if key == "status" and isinstance(val, list) and val:
            flat["status"] = str(val[0])
            continue
        if isinstance(val, (str, int, float)) and str(val).strip():
            flat[key] = val
    bound: dict[str, Any] = {}
    for name in needed:
        key = name.lower()
        if key in flat:
            bound[name] = flat[key]
            continue
        matched = False
        for filter_key, aliases in _PARAM_ALIASES.items():
            if key in aliases or key == filter_key:
                if filter_key in flat:
                    bound[name] = flat[filter_key]
                    matched = True
                    break
                # Try alias keys directly on flat
                for alias in aliases:
                    if alias in flat:
                        bound[name] = flat[alias]
                        matched = True
                        break
            if matched:
                break
        if not matched:
            return None
    return bound


def _first_numeric(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    text = str(value or "").strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    return None


def extract_result_value(
    columns: list[str],
    rows: list[list[Any]],
    *,
    intent: str,
) -> str | None:
    if not rows:
        return None
    cols = [str(c or "").strip().lower() for c in columns]
    preferred = ("count", "total", "n", "value", "result", "numerator", "denominator", "status", "name")
    row0 = rows[0]
    if intent == INTENT_COUNT:
        for name in preferred:
            if name in cols:
                idx = cols.index(name)
                if idx < len(row0):
                    num = _first_numeric(row0[idx])
                    if num is not None:
                        return num
        for cell in row0:
            num = _first_numeric(cell)
            if num is not None:
                return num
        return None
    if intent == INTENT_STATUS:
        for name in ("status", "state", "result"):
            if name in cols:
                idx = cols.index(name)
                if idx < len(row0) and str(row0[idx] or "").strip():
                    return str(row0[idx]).strip()
        if row0 and str(row0[0] or "").strip():
            return str(row0[0]).strip()
        return None
    if intent in {INTENT_LIST, INTENT_LOOKUP}:
        lines: list[str] = []
        for row in rows[:30]:
            parts = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if parts:
                lines.append("- " + " | ".join(parts[:4]))
        return "\n".join(lines) if lines else None
    # General numeric-or-first-cell fallback
    for cell in row0:
        num = _first_numeric(cell)
        if num is not None:
            return num
    if row0 and str(row0[0] or "").strip():
        return str(row0[0]).strip()
    return None


def format_sql_answer(
    *,
    intent: str,
    value: str,
    query_title: str = "",
    connection_id: str = "",
) -> str:
    src = connection_id or "connected database"
    title = f" ({query_title})" if query_title else ""
    if intent == INTENT_COUNT:
        return f"Count: {value}\nSource: read-only SQL{title} via {src}"
    if intent == INTENT_STATUS:
        return f"Status: {value}\nSource: read-only SQL{title} via {src}"
    if intent in {INTENT_LIST, INTENT_LOOKUP}:
        return (
            f"Results from read-only SQL{title} via {src}:\n{value}"
        )
    return f"Value: {value}\nSource: read-only SQL{title} via {src}"


def _sql_looks_complex(sql: str) -> bool:
    text = sql or ""
    # Heuristic: dynamic construction needed when SQL is empty or obviously incomplete.
    if len(text.strip()) < 12:
        return True
    if re.search(r"(?i)\bTODO\b|\bFIXME\b|\?\s*$", text):
        return True
    return False


def attempt_deterministic_sql(
    *,
    prompt: str,
    contract: CompletionContract,
    sql_store: Any | None,
    sql_executor: Any | None,
    sql_connections: Any | None,
    matches: list[dict[str, Any]] | None = None,
) -> DeterministicSqlAttempt:
    """
    Try cheapest deterministic DB path: match saved RO queries → bind filters → execute.

    Never invents SQL from scratch. If a saved query needs construction/unbound params,
    signals needs_ai instead of fabricating values.
    """
    caps = snapshot_capabilities(
        sql_store=sql_store,
        sql_executor=sql_executor,
        sql_connections=sql_connections,
        dhis2_reports=None,
        saved_matches=matches,
    )
    if not caps.sql_store_available:
        return DeterministicSqlAttempt(
            attempted=False,
            unavailable=True,
            reason="SQL Workspace store is not available",
        )
    if not caps.sql_executor_available or not caps.sql_connections_configured:
        return DeterministicSqlAttempt(
            attempted=False,
            unavailable=True,
            reason="No configured read-only database connection is available",
        )

    rows = list(matches or [])
    if not rows:
        rows = find_saved_sql_matches(
            sql_store, filters=dict(contract.filters or {}), prompt=prompt
        )
    if not rows:
        return DeterministicSqlAttempt(
            attempted=True,
            ok=False,
            needs_ai=True,
            reason="No matching saved SQL query for the detected filters; query construction needed",
        )

    from hub.sql_workspace.safety import extract_named_params

    last_reason = "No executable saved query matched the filters"
    for query in rows:
        qid = str(query.get("id") or "").strip()
        sql_text = str(query.get("sql_text") or "").strip()
        connection_id = str(query.get("connection_id") or "").strip()
        title = str(query.get("title") or "").strip()
        if not qid or not sql_text:
            last_reason = "Saved query missing SQL text"
            continue
        if not connection_id or connection_id not in caps.sql_connections_configured:
            last_reason = f"Saved query {qid} has no configured connection"
            continue
        if _sql_looks_complex(sql_text):
            return DeterministicSqlAttempt(
                attempted=True,
                ok=False,
                needs_ai=True,
                query_id=qid,
                connection_id=connection_id,
                reason="Saved SQL needs query construction/reasoning before execution",
            )
        needed = extract_named_params(sql_text)
        params = map_filters_to_sql_params(needed, dict(contract.filters or {}))
        if params is None:
            return DeterministicSqlAttempt(
                attempted=True,
                ok=False,
                needs_ai=True,
                query_id=qid,
                connection_id=connection_id,
                reason=(
                    "Saved SQL has unbound parameters that filters could not resolve; "
                    "AI may complete entity/filter binding"
                ),
            )
        try:
            profile = sql_connections.get_configured(connection_id)
        except Exception as exc:  # noqa: BLE001
            last_reason = f"Connection {connection_id} unavailable: {exc}"
            continue
        try:
            result = sql_executor.execute(
                profile,
                sql_text,
                params=params,
                query_id=qid,
                page=1,
                page_size=50,
            )
        except Exception as exc:  # noqa: BLE001
            last_reason = f"SQL execute failed: {exc}"
            continue
        ok = bool(getattr(result, "ok", False) is True)
        columns = list(getattr(result, "columns", None) or [])
        data_rows = list(getattr(result, "rows", None) or [])
        if not isinstance(columns, list) or not isinstance(data_rows, list):
            last_reason = "SQL execute returned an unexpected result shape"
            continue
        # Reject MagicMock-ish non-results.
        if columns and not all(isinstance(c, (str, type(None))) or c is not None for c in columns[:1]):
            pass
        tool_result = {
            "tool": "sql_query_execute",
            "ok": ok,
            "query_id": qid,
            "connection_id": connection_id,
            "title": title,
            "row_count": getattr(result, "row_count", len(data_rows)),
            "error": getattr(result, "error", None),
            "summary": (
                f"Executed saved query {title or qid} on {connection_id}"
                if ok
                else f"SQL execute error: {getattr(result, 'error', 'unknown')}"
            ),
        }
        if not ok:
            last_reason = str(getattr(result, "error", None) or "SQL execute returned error")
            continue
        value = extract_result_value(columns, data_rows, intent=contract.intent)
        if value is None:
            return DeterministicSqlAttempt(
                attempted=True,
                ok=False,
                needs_ai=True,
                query_id=qid,
                connection_id=connection_id,
                tool_result=tool_result,
                reason="SQL ran but result shape does not satisfy the completion contract",
            )
        answer = format_sql_answer(
            intent=contract.intent,
            value=value,
            query_title=title,
            connection_id=connection_id,
        )
        hits = [
            {
                "source": "sql:query",
                "query_id": qid,
                "connection_id": connection_id,
                "title": title,
                "value": value,
                "columns": columns[:12],
            }
        ]
        return DeterministicSqlAttempt(
            attempted=True,
            ok=True,
            answer=answer,
            reason="Deterministic read-only SQL satisfied the completion contract",
            query_id=qid,
            connection_id=connection_id,
            tool_result=tool_result,
            hits=hits,
        )

    return DeterministicSqlAttempt(
        attempted=True,
        ok=False,
        unavailable=not caps.sql_connections_configured,
        needs_ai=bool(rows),
        reason=last_reason,
    )


def classify_t0_failure(
    *,
    contract: CompletionContract,
    packet: dict[str, Any] | None,
    caps: CapabilitySnapshot,
    sql_attempt: DeterministicSqlAttempt | None = None,
    authoritative_data: bool = False,
) -> FailureClassification:
    """Classify why T0 is still unsolved and choose the next capability."""
    filters = dict(contract.filters or {})
    attempt = sql_attempt or DeterministicSqlAttempt()
    usable = bool((packet or {}).get("usable")) or bool((packet or {}).get("hits"))
    sources = list((packet or {}).get("sources") or [])

    if attempt.ok:
        return FailureClassification(
            reason="",
            detail="Resolved by deterministic SQL",
            next_capability="",
            can_ai_help=False,
            db_available=caps.sql_executor_available and bool(caps.sql_connections_configured),
            db_query_attempted=attempt.attempted,
            filters=filters,
        )

    db_available = caps.sql_executor_available and bool(caps.sql_connections_configured)

    if attempt.unavailable or (not db_available and authoritative_data):
        # Connected DB required for structured counts but missing.
        if authoritative_data and contract.intent in {
            INTENT_COUNT,
            INTENT_STATUS,
            INTENT_LIST,
            INTENT_LOOKUP,
        }:
            return FailureClassification(
                reason=FAILURE_PROVIDER_UNAVAILABLE
                if attempt.unavailable or not db_available
                else FAILURE_MISSING_SOURCE,
                detail=attempt.reason
                or "Authoritative database/tool is unavailable for this request",
                next_capability=NEXT_CANNOT_VERIFY,
                can_ai_help=False,
                db_available=db_available,
                db_query_attempted=attempt.attempted,
                filters=filters,
            )

    if attempt.needs_ai:
        return FailureClassification(
            reason=FAILURE_NEEDS_REASONING
            if "construction" in (attempt.reason or "").lower()
            or "parameters" in (attempt.reason or "").lower()
            else FAILURE_FILTERS_INCOMPLETE,
            detail=attempt.reason or "Deterministic tools need AI-assisted query construction",
            next_capability=NEXT_AI,
            can_ai_help=True,
            db_available=db_available,
            db_query_attempted=attempt.attempted,
            filters=filters,
        )

    if db_available and not attempt.attempted and authoritative_data:
        return FailureClassification(
            reason=FAILURE_QUERY_NOT_EXECUTED,
            detail="Database is connected but the required query has not been executed yet",
            next_capability=NEXT_SQL_EXECUTE,
            can_ai_help=False,
            db_available=True,
            db_query_attempted=False,
            filters=filters,
        )

    if usable and not attempt.ok:
        # Repo/SQL metadata discovery without a verified value.
        if any("sql" in str(s).lower() or "repository" in str(s).lower() for s in sources) or any(
            str(h.get("source") or "").startswith("repository")
            or str(h.get("path") or "").endswith(".sql")
            for h in (packet or {}).get("hits") or []
            if isinstance(h, dict)
        ):
            if db_available:
                return FailureClassification(
                    reason=FAILURE_QUERY_NOT_EXECUTED,
                    detail="Supporting evidence found but authoritative query was not executed",
                    next_capability=NEXT_SQL_EXECUTE if not attempt.attempted else NEXT_AI,
                    can_ai_help=attempt.attempted,
                    db_available=True,
                    db_query_attempted=attempt.attempted,
                    filters=filters,
                )
        if caps.dhis2_available and contract.intent in {INTENT_LIST, INTENT_LOOKUP}:
            return FailureClassification(
                reason=FAILURE_FILTERS_INCOMPLETE,
                detail="Entity/OU resolution incomplete for the requested filters",
                next_capability=NEXT_OU_RESOLVE,
                can_ai_help=True,
                db_available=db_available,
                db_query_attempted=attempt.attempted,
                filters=filters,
            )
        return FailureClassification(
            reason=FAILURE_NEEDS_REASONING if db_available else FAILURE_UNVERIFIABLE,
            detail="Evidence found but completion contract still unsatisfied",
            next_capability=NEXT_AI if db_available else NEXT_CANNOT_VERIFY,
            can_ai_help=bool(db_available or caps.dhis2_available),
            db_available=db_available,
            db_query_attempted=attempt.attempted,
            filters=filters,
        )

    if not usable and authoritative_data:
        return FailureClassification(
            reason=FAILURE_MISSING_SOURCE,
            detail="No usable authoritative evidence and no deterministic query result",
            next_capability=NEXT_CANNOT_VERIFY,
            can_ai_help=False,
            db_available=db_available,
            db_query_attempted=attempt.attempted,
            filters=filters,
        )

    return FailureClassification(
        reason=FAILURE_UNVERIFIABLE,
        detail="No capable tool or provider can materially advance this task",
        next_capability=NEXT_CANNOT_VERIFY,
        can_ai_help=False,
        db_available=db_available,
        db_query_attempted=attempt.attempted,
        filters=filters,
    )


def should_escalate_to_ai(classification: FailureClassification) -> bool:
    return bool(
        classification.can_ai_help and classification.next_capability == NEXT_AI
    )


def merge_sql_attempt_into_packet(
    packet: dict[str, Any],
    attempt: DeterministicSqlAttempt,
) -> dict[str, Any]:
    out = dict(packet or {})
    hits = list(out.get("hits") or [])
    hits.extend(attempt.hits)
    out["hits"] = hits
    results = list(out.get("tool_results") or [])
    if attempt.tool_result:
        results.append(attempt.tool_result)
    out["tool_results"] = results
    sources = list(out.get("sources") or [])
    if attempt.ok:
        sources.append("tool:sql_query_execute")
        if attempt.connection_id:
            sources.append(f"sql:{attempt.connection_id}")
        out["usable"] = True
        out["summary"] = attempt.reason or out.get("summary") or "SQL result"
    out["sources"] = list(dict.fromkeys(sources))
    return out
