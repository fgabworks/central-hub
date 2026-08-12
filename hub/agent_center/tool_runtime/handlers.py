"""Extra Phase-1 tool handlers wrapping existing Hub services (not openai_tools)."""

from __future__ import annotations

import json
from typing import Any

from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.redact import redact_text


def handle_repository_intelligence(
    args: dict[str, Any], ctx: AgentToolsContext
) -> dict[str, Any]:
    svc = getattr(ctx, "repository_intelligence", None)
    if svc is None:
        return {"error": "Repository Intelligence is not available"}
    query = str(args.get("query") or getattr(ctx, "prompt_hint", "") or "").strip()
    repo_filter = str(args.get("repo_id") or "").strip()
    limit = max(1, min(int(args.get("limit") or 6), 12))
    repos = [repo_filter] if repo_filter else list(ctx.repository_ids or [])
    if not repos:
        return {"error": "No repository selected for Repository Intelligence"}
    try:
        payload = svc.retrieve(repos, query or "repository", limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Repository Intelligence failed: {exc}"}
    items = list(payload.get("items") or [])[:limit]
    profiles = list(payload.get("profiles") or [])
    return {
        "summary": f"{len(items)} knowledge entries from {len(profiles)} repositories",
        "profiles": [
            {
                "repository_id": p.get("repository_id"),
                "status": p.get("status"),
                "freshness": p.get("freshness"),
                "indexed_commit": p.get("indexed_commit"),
                "compact_summary": str(p.get("compact_summary") or "")[:400],
            }
            for p in profiles[:3]
        ],
        "items": [
            {
                "repository_id": it.get("repository_id"),
                "path": it.get("path"),
                "category": it.get("category"),
                "title": it.get("title"),
                "summary": str(it.get("summary") or "")[:500],
                "score": it.get("score"),
            }
            for it in items
        ],
        "diagnostics": payload.get("diagnostics") or {},
        "source": "repository_intelligence",
    }


def handle_sql_query_execute(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    """Execute a saved SQL Workspace query via the existing RO executor."""
    if ctx.sql_store is None:
        return {"error": "SQL Workspace store is not available"}
    if ctx.sql_executor is None or ctx.sql_connections is None:
        return {"error": "No configured read-only SQL executor is available"}
    query_id = str(args.get("query_id") or "").strip()
    if not query_id:
        return {"error": "query_id is required"}
    row = ctx.sql_store.get_query(query_id)
    if not row:
        return {"error": "Saved query not found"}
    sql_text = str(row.get("sql_text") or "").strip()
    connection_id = str(row.get("connection_id") or "").strip()
    if not sql_text:
        return {"error": "Saved query has no SQL text"}
    if not connection_id:
        return {"error": "Saved query has no connection_id"}

    # Hard RO gate — parse-based validator before any execute.
    try:
        from hub.sql_workspace.safety import validate_readonly_sql

        validated = validate_readonly_sql(sql_text)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"SQL rejected by read-only policy: {exc}", "ok": False}

    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    page_size = max(1, min(int(args.get("page_size") or 50), 100))
    try:
        profile = ctx.sql_connections.get_configured(connection_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Connection {connection_id} unavailable: {exc}"}

    try:
        result = ctx.sql_executor.execute(
            profile,
            validated.sql if hasattr(validated, "sql") else sql_text,
            params=params,
            query_id=query_id,
            page=1,
            page_size=page_size,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"SQL execute failed: {exc}", "ok": False}

    ok = bool(getattr(result, "ok", False))
    columns = list(getattr(result, "columns", None) or [])
    data_rows = list(getattr(result, "rows", None) or [])
    # Bound observation size.
    preview_rows = data_rows[: min(len(data_rows), page_size, 25)]
    return {
        "ok": ok,
        "summary": (
            f"Executed saved query {row.get('title') or query_id} on {connection_id} "
            f"({getattr(result, 'row_count', len(data_rows))} rows)"
            if ok
            else f"SQL execute error: {getattr(result, 'error', 'unknown')}"
        ),
        "query_id": query_id,
        "connection_id": connection_id,
        "title": row.get("title"),
        "columns": columns[:40],
        "rows": preview_rows,
        "row_count": getattr(result, "row_count", len(data_rows)),
        "error": getattr(result, "error", None),
        "source": "sql_query_execute",
        "note": "Read-only saved-query execution only; free-form SQL is not accepted",
    }


def handle_data_explorer_lookup(
    args: dict[str, Any], ctx: AgentToolsContext
) -> dict[str, Any]:
    svc = getattr(ctx, "data_explorer", None)
    if svc is None:
        return {"error": "Data Explorer is not available"}
    environment = (
        str(args.get("environment") or ctx.dhis2_environment or "stage").strip().lower()
        or "stage"
    )
    if environment not in {"stage", "live"}:
        return {"error": "environment must be stage or live"}
    schema = str(args.get("schema") or "").strip()
    name = str(args.get("name") or "").strip()
    search = str(args.get("search") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    try:
        if schema and name:
            detail = svc.object_detail(
                environment=environment, schema=schema, name=name, actor="airix"
            )
            # Strip potentially large browse samples.
            compact = {
                "environment": environment,
                "schema": schema,
                "name": name,
                "object": {
                    k: detail.get("object", {}).get(k)
                    if isinstance(detail.get("object"), dict)
                    else None
                    for k in ("schema", "name", "kind", "row_estimate")
                }
                if isinstance(detail.get("object"), dict)
                else detail.get("object"),
                "classification": detail.get("classification"),
                "lineage": detail.get("lineage"),
                "columns": (
                    [
                        {"name": c.get("name"), "type": c.get("type")}
                        for c in (detail.get("object") or {}).get("columns") or []
                        if isinstance(c, dict)
                    ][:40]
                    if isinstance(detail.get("object"), dict)
                    else []
                ),
            }
            return {
                "summary": f"Data Explorer object {schema}.{name} ({environment})",
                "detail": compact,
                "source": "data_explorer_lookup",
            }
        inv = svc.inventory(environment=environment, actor="airix")
        objects = list(inv.get("objects") or inv.get("items") or [])
        if search:
            needle = search.lower()
            objects = [
                o
                for o in objects
                if needle
                in " ".join(
                    str(o.get(k) or "") for k in ("schema", "name", "kind", "title")
                ).lower()
            ]
        objects = objects[:limit]
        return {
            "summary": f"{len(objects)} Data Explorer objects ({environment})",
            "environment": environment,
            "connection_id": inv.get("connection_id"),
            "objects": [
                {
                    "schema": o.get("schema"),
                    "name": o.get("name"),
                    "kind": o.get("kind") or o.get("type"),
                }
                for o in objects
                if isinstance(o, dict)
            ],
            "source": "data_explorer_lookup",
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": redact_text(str(exc), limit=400)}


def handle_skill_recall(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    """Load instruction/skill markdown on demand (Phase 2 — avoid overpacking)."""
    from hub.agent_center.models import INSTRUCTION_FILENAMES, MAX_INSTRUCTION_CHARS
    from hub.agent_center.openai_tools import load_instructions_for_scope

    repo_filter = str(args.get("repo_id") or "").strip()
    name_filter = str(args.get("name") or "").strip().lower()
    query = str(args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 4), 8))

    # Prefer scoped loader (path-jailed via existing helpers).
    items = load_instructions_for_scope(ctx)
    if repo_filter:
        items = [it for it in items if str(it.get("repo_id") or "") == repo_filter]
    if name_filter:
        items = [
            it
            for it in items
            if name_filter in str(it.get("path") or "").lower()
            or str(it.get("path") or "").lower() == name_filter
        ]
    # Also try SKILLS.md even if not in default instruction list.
    extra: list[dict[str, Any]] = []
    for repo, root in ctx.scoped_repos():
        if repo_filter and repo.id != repo_filter:
            continue
        for fname in ("SKILLS.md", "docs/SKILLS.md"):
            path = root / fname
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text:
                continue
            extra.append(
                {
                    "repo_id": repo.id,
                    "path": fname,
                    "chars": min(len(text), MAX_INSTRUCTION_CHARS),
                    "truncated": len(text) > MAX_INSTRUCTION_CHARS,
                    "content": text[:MAX_INSTRUCTION_CHARS],
                }
            )
    # Merge extras that aren't already present.
    seen = {(str(it.get("repo_id")), str(it.get("path"))) for it in items}
    for it in extra:
        key = (str(it.get("repo_id")), str(it.get("path")))
        if key not in seen:
            items.append(it)
            seen.add(key)

    if query:
        items = [
            it
            for it in items
            if query in str(it.get("content") or "").lower()
            or query in str(it.get("path") or "").lower()
        ]
    # Prefer known instruction filenames first.
    preferred = {n.lower(): i for i, n in enumerate(INSTRUCTION_FILENAMES)}
    items.sort(
        key=lambda it: (
            preferred.get(str(it.get("path") or "").lower().split("/")[-1], 99),
            str(it.get("path") or ""),
        )
    )
    items = items[:limit]
    for it in items:
        ctx.referenced_files.append(
            {
                "repo_id": str(it.get("repo_id") or ""),
                "path": str(it.get("path") or ""),
                "kind": "skill_recall",
            }
        )
    return {
        "summary": f"{len(items)} instruction/skill files recalled",
        "files": [
            {
                "repo_id": it.get("repo_id"),
                "path": it.get("path"),
                "chars": it.get("chars"),
                "truncated": it.get("truncated"),
                "content": str(it.get("content") or "")[:4000],
            }
            for it in items
        ],
        "source": "skill_recall",
        "note": "On-demand skill recall — not packed into the initial prompt",
    }


def handle_extra_tool(
    name: str, args: dict[str, Any], ctx: AgentToolsContext
) -> dict[str, Any] | None:
    handlers = {
        "repository_intelligence": handle_repository_intelligence,
        "sql_query_execute": handle_sql_query_execute,
        "data_explorer_lookup": handle_data_explorer_lookup,
        "skill_recall": handle_skill_recall,
    }
    fn = handlers.get(name)
    if fn is None:
        return None
    return fn(args, ctx)


def observation_from_payload(payload: dict[str, Any] | str) -> tuple[bool, str, str]:
    """Return (ok, summary, observation_text)."""
    if isinstance(payload, str):
        try:
            data = json.loads(payload) if payload.strip() else {}
        except json.JSONDecodeError:
            return False, "invalid_json", payload[:2000]
    else:
        data = payload if isinstance(payload, dict) else {"error": "invalid payload"}
    if not isinstance(data, dict):
        text = str(data)
        return False, "invalid", text[:2000]
    err = data.get("error")
    ok = bool(data.get("ok", err is None))
    if err and data.get("ok") is not True:
        ok = False
    summary = str(data.get("summary") or err or ("ok" if ok else "error"))[:240]
    observation = redact_text(json.dumps(data, ensure_ascii=False, default=str), limit=12_000)
    return ok, summary, observation
