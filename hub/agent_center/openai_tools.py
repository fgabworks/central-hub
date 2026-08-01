"""Read-only allowlisted function tools for the OpenAI adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hub.agent_center.instructions import load_repo_instructions
from hub.agent_center.models import MAX_CONTEXT_FILE_CHARS
from hub.agent_center.redact import redact_text
from hub.registry.models import Registry, Repository

# Repository Workspace imports are deferred inside tool helpers to avoid
# circular import: agent_center → openai_tools → repository_workspace → agent_center.

ALLOWED_TOOLS = frozenset(
    {
        "repo_search",
        "read_file",
        "uid_lookup",
        "sql_lookup",
        "notebook_lookup",
        "notepad_lookup",
        "email_search",
        "calendar_lookup",
        "jobs_lookup",
        "audit_lookup",
        "dhis2_reports_lookup",
    }
)


@dataclass
class ToolActivity:
    name: str
    arguments: dict[str, Any]
    ok: bool
    detail: str
    chars: int = 0


@dataclass
class AgentToolsContext:
    registry: Registry
    repository_ids: list[str]
    notebook: Any | None = None
    sql_store: Any | None = None
    uid_index: Any | None = None
    profile_id: str = "okarun"
    workspace: str = "work"
    allowed_tools: set[str] = field(default_factory=set)
    email: Any | None = None
    calendar: Any | None = None
    job_store: Any | None = None
    audit_store: Any | None = None
    dhis2_reports: Any | None = None
    notepad_factory: Callable[[str], Any] | None = None
    max_result_chars: int = 12_000
    activity: list[ToolActivity] = field(default_factory=list)
    referenced_files: list[dict[str, str]] = field(default_factory=list)

    def scoped_repos(self) -> list[tuple[Repository, Path]]:
        out: list[tuple[Repository, Path]] = []
        for rid in self.repository_ids:
            repo = self.registry.get(rid)
            if repo is None or not repo.enabled or repo.type != "command":
                continue
            root = _resolve_repo_path(repo)
            if root is None:
                continue
            out.append((repo, root))
        return out


def tool_definitions(allowed_tools: set[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI Responses API function tool schemas (read-only)."""
    definitions = [
        {
            "type": "function",
            "name": "repo_search",
            "description": "Search file paths under selected read-only repositories. Secrets and binaries are excluded.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring or keyword to match in paths"},
                    "repo_id": {"type": "string", "description": "Optional single repository id"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_file",
            "description": "Read an approved text file within a selected repository. Secrets/binaries/oversized files are rejected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string"},
                    "path": {"type": "string", "description": "Relative path within the repository"},
                },
                "required": ["repo_id", "path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "uid_lookup",
            "description": "Look up a UID in the local DHIS2 UID index (read-only). Does not call DHIS2 writes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {"type": "string", "description": "Resource/type key used by the index"},
                    "uid": {"type": "string"},
                    "query": {"type": "string", "description": "Optional search string when uid omitted"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "sql_lookup",
            "description": "Look up saved SQL Workspace queries (text/metadata only). Never executes SQL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_id": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "notebook_lookup",
            "description": "Search Repository Notebook notes (read-only). Does not create or edit notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "notepad_lookup",
            "description": "Read the Quick Notepad for the active assistant workspace.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "email_search",
            "description": "Search message metadata in connected Email accounts for the active workspace. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "calendar_lookup",
            "description": "List upcoming Calendar events for the active workspace. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "jobs_lookup",
            "description": "List existing Work job records and statuses. Never starts or cancels jobs.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "audit_lookup",
            "description": "List recent redacted Audit records. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "dhis2_reports_lookup",
            "description": "Search cached DHIS2 standard reports and configured report shortcuts. Read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "environment": {"type": "string", "enum": ["", "stage", "live"]},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    ]
    allowed = set(allowed_tools or ALLOWED_TOOLS)
    return [item for item in definitions if item["name"] in allowed]


def execute_tool(name: str, arguments: dict[str, Any] | str, ctx: AgentToolsContext) -> str:
    effective_allowed = ctx.allowed_tools or set(ALLOWED_TOOLS)
    if name not in ALLOWED_TOOLS or name not in effective_allowed:
        act = ToolActivity(name=name, arguments={}, ok=False, detail="Tool not allowlisted")
        ctx.activity.append(act)
        return json.dumps({"error": "Tool not allowlisted for this assistant profile", "allowed": sorted(effective_allowed)})

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    handlers: dict[str, Callable[[dict[str, Any], AgentToolsContext], dict[str, Any]]] = {
        "repo_search": _tool_repo_search,
        "read_file": _tool_read_file,
        "uid_lookup": _tool_uid_lookup,
        "sql_lookup": _tool_sql_lookup,
        "notebook_lookup": _tool_notebook_lookup,
        "notepad_lookup": _tool_notepad_lookup,
        "email_search": _tool_email_search,
        "calendar_lookup": _tool_calendar_lookup,
        "jobs_lookup": _tool_jobs_lookup,
        "audit_lookup": _tool_audit_lookup,
        "dhis2_reports_lookup": _tool_dhis2_reports_lookup,
    }
    try:
        result = handlers[name](arguments, ctx)
        ok = "error" not in result
        payload = json.dumps(result, ensure_ascii=False)
        payload = redact_text(payload, limit=ctx.max_result_chars)
        ctx.activity.append(
            ToolActivity(
                name=name,
                arguments={k: v for k, v in arguments.items() if k != "content"},
                ok=ok,
                detail=str(result.get("error") or result.get("summary") or "ok")[:240],
                chars=len(payload),
            )
        )
        return payload
    except Exception as exc:  # noqa: BLE001
        msg = redact_text(str(exc), limit=500)
        ctx.activity.append(ToolActivity(name=name, arguments=arguments, ok=False, detail=msg))
        return json.dumps({"error": msg})


def load_instructions_for_scope(ctx: AgentToolsContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for repo, root in ctx.scoped_repos():
        out.extend(load_repo_instructions(root, repo_id=repo.id))
    return out


def _tool_repo_search(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    from hub.repository_workspace.files import RepositoryFiles
    from hub.repository_workspace.settings import load_workspace_settings

    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    repo_filter = str(args.get("repo_id") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    settings = load_workspace_settings()
    hits: list[dict[str, str]] = []
    for repo, root in ctx.scoped_repos():
        if repo_filter and repo.id != repo_filter:
            continue
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        files = RepositoryFiles(root, settings)
        for match in files.search_filenames(query, limit=remaining):
            hits.append({"repo_id": repo.id, "path": match["path"]})
            if len(hits) >= limit:
                return {"summary": f"{len(hits)} matches", "matches": hits}
    return {"summary": f"{len(hits)} matches", "matches": hits}


def _normalize_rel(path: str) -> str:
    rel = (path or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def _tool_read_file(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    from hub.repository_workspace.security import (
        WorkspaceSecurityError,
        is_blocked_secret,
        is_supported_text_path,
        looks_binary,
        safe_join,
    )

    repo_id = str(args.get("repo_id") or "").strip()
    rel = _normalize_rel(str(args.get("path") or ""))
    if not repo_id or not rel:
        return {"error": "repo_id and path are required"}
    scoped = {r.id: root for r, root in ctx.scoped_repos()}
    root = scoped.get(repo_id)
    if root is None:
        return {"error": f"Repository not in run scope: {repo_id}"}
    try:
        path = safe_join(root, rel)
    except WorkspaceSecurityError as exc:
        code = getattr(exc, "code", "")
        if code in {"secret_blocked", "path_escape", "path_traversal", "absolute_path"}:
            return {"error": "File excluded (secret, binary, or disallowed)"}
        return {"error": str(exc)}
    if is_blocked_secret(rel):
        return {"error": "File excluded (secret, binary, or disallowed)"}
    if not path.is_file():
        return {"error": "File not found"}
    if not is_supported_text_path(path):
        return {"error": "File excluded (secret, binary, or disallowed)"}
    size = path.stat().st_size
    if size > MAX_CONTEXT_FILE_CHARS * 4:
        return {"error": f"File too large ({size} bytes)"}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"error": str(exc)}
    if looks_binary(raw[:8192]):
        return {"error": "Binary file excluded"}
    text = raw.decode("utf-8", errors="replace")
    clipped = text[:MAX_CONTEXT_FILE_CHARS]
    ctx.referenced_files.append({"repo_id": repo_id, "path": rel, "kind": "tool-read"})
    return {
        "summary": f"read {repo_id}:{rel}",
        "repo_id": repo_id,
        "path": rel,
        "chars": len(clipped),
        "truncated": len(text) > len(clipped),
        "content": clipped,
    }


def _tool_uid_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.uid_index is None:
        return {"error": "UID index not available in this hub process"}
    resource = str(args.get("resource") or "").strip() or "dataElements"
    uid = str(args.get("uid") or "").strip()
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    if uid:
        row = ctx.uid_index.get(resource, uid)
        return {"summary": "uid get", "resource": resource, "result": row}
    if not query:
        return {"error": "uid or query is required"}
    rows = ctx.uid_index.search(resource, query, limit=limit)
    return {"summary": f"{len(rows)} hits", "resource": resource, "results": rows}


def _tool_sql_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.sql_store is None:
        return {"error": "SQL Workspace store not available"}
    query_id = str(args.get("query_id") or "").strip()
    search = str(args.get("search") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    if query_id:
        row = ctx.sql_store.get_query(query_id)
        if not row:
            return {"error": "Query not found"}
        return {
            "summary": "sql get",
            "query": {
                "id": row.get("id"),
                "title": row.get("title"),
                "description": row.get("description"),
                "sql_text": row.get("sql_text"),
                "connection_id": row.get("connection_id"),
                "tags": row.get("tags"),
            },
            "note": "Read-only lookup — SQL was not executed",
        }
    rows = ctx.sql_store.list_queries(q=search, limit=limit)
    return {
        "summary": f"{len(rows)} saved queries",
        "queries": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "description": (r.get("description") or "")[:200],
                "sql_preview": (r.get("sql_text") or "")[:400],
            }
            for r in rows[:limit]
        ],
        "note": "Read-only lookup — SQL was not executed",
    }


def _tool_notebook_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.notebook is None:
        return {"error": "Notebook store not available"}
    search = str(args.get("search") or "").strip()
    scope = ctx.workspace
    limit = max(1, min(int(args.get("limit") or 20), 50))
    if not search:
        return {"error": "search is required"}
    rows = ctx.notebook.search(q=search, scope=scope, limit=limit)
    return {
        "summary": f"{len(rows)} notes",
        "notes": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "type": r.get("note_type") or r.get("type"),
                "scope": r.get("scope"),
                "status": r.get("status"),
                "body_preview": (r.get("body_md") or r.get("body") or r.get("content") or "")[:500],
            }
            for r in rows[:limit]
        ],
    }


def _tool_notepad_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.notepad_factory is None:
        return {"error": "Quick Notepad is not available"}
    pad = ctx.notepad_factory(ctx.workspace).get(include_revisions=False)
    return {
        "summary": f"{ctx.workspace} Quick Notepad",
        "content": str(pad.get("content") or "")[:4000],
        "updated_at": pad.get("updated_at"),
    }


def _tool_email_search(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.email is None:
        return {"error": "Email service is not available"}
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    limit = max(1, min(int(args.get("limit") or 10), 20))
    messages: list[dict[str, Any]] = []
    for account in ctx.email.list_accounts(ctx.workspace):
        if account.get("status") != "connected" or not account.get("has_gmail", True):
            continue
        result = ctx.email.list_messages(account["id"], q=query, page_size=limit)
        for item in result.get("messages") or []:
            messages.append(
                {
                    "account": account.get("email"),
                    "id": item.get("id"),
                    "subject": item.get("subject"),
                    "from": item.get("from_addr"),
                    "date": item.get("date_header") or item.get("internal_date"),
                    "snippet": item.get("snippet"),
                }
            )
            if len(messages) >= limit:
                break
        if len(messages) >= limit:
            break
    return {"summary": f"{len(messages)} messages", "messages": messages}


def _tool_calendar_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.calendar is None:
        return {"error": "Calendar service is not available"}
    limit = max(1, min(int(args.get("limit") or 10), 25))
    rows = ctx.calendar.upcoming_for_workspace(ctx.workspace, limit=limit)
    return {
        "summary": f"{len(rows)} upcoming events",
        "events": [
            {
                "id": row.get("id"),
                "summary": row.get("summary"),
                "start": row.get("start"),
                "end": row.get("end"),
                "location": row.get("location"),
                "account": row.get("account_email"),
            }
            for row in rows
        ],
    }


def _tool_jobs_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.job_store is None:
        return {"error": "Job store is not available"}
    limit = max(1, min(int(args.get("limit") or 20), 50))
    rows = ctx.job_store.list_recent(limit=limit)
    return {
        "summary": f"{len(rows)} jobs",
        "jobs": [
            {
                "id": row.get("id"),
                "repository_id": row.get("repository_id"),
                "capability_id": row.get("capability_id"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
                "finished_at": row.get("finished_at"),
            }
            for row in rows
        ],
    }


def _tool_audit_lookup(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    if ctx.audit_store is None:
        return {"error": "Audit store is not available"}
    limit = max(1, min(int(args.get("limit") or 20), 50))
    rows = ctx.audit_store.list_recent(limit=limit)
    return {
        "summary": f"{len(rows)} audit records",
        "events": [
            {
                "timestamp": row.get("timestamp"),
                "action": row.get("action"),
                "target": redact_text(str(row.get("target") or ""), limit=300),
                "detail": redact_text(str(row.get("detail") or ""), limit=500),
                "ok": row.get("ok"),
            }
            for row in rows
        ],
    }


def _tool_dhis2_reports_lookup(
    args: dict[str, Any], ctx: AgentToolsContext
) -> dict[str, Any]:
    if ctx.dhis2_reports is None:
        return {"error": "DHIS2 Reports service is not available"}
    query = str(args.get("query") or "").strip()
    environment = str(args.get("environment") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    standard = ctx.dhis2_reports.list_standard_library(
        q=query, environment=environment
    )
    standard_rows: list[dict[str, Any]] = []
    for section in standard.get("sections") or []:
        for row in section.get("reports") or []:
            standard_rows.append(
                {
                    "environment": section.get("environment"),
                    "id": row.get("uid") or row.get("id"),
                    "name": row.get("name"),
                    "type": row.get("report_type"),
                    "html_available": row.get("html_available"),
                }
            )
            if len(standard_rows) >= limit:
                break
        if len(standard_rows) >= limit:
            break
    shortcuts = ctx.dhis2_reports.list_library(q=query)[:limit]
    return {
        "summary": f"{len(standard_rows)} standard reports; {len(shortcuts)} shortcuts",
        "standard_reports": standard_rows,
        "shortcuts": [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "type": row.get("report_type"),
                "repository_id": row.get("repository_id"),
            }
            for row in shortcuts
        ],
    }


def _reject_file(path: Path, root: Path) -> bool:
    """Compatibility helper — prefer workspace safe_join / secret checks."""
    from hub.repository_workspace.security import is_blocked_secret, is_supported_text_path

    try:
        rel = path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return True
    if is_blocked_secret(rel):
        return True
    if not is_supported_text_path(path):
        return True
    return False


def _resolve_repo_path(repo: Repository) -> Path | None:
    from hub.repository_workspace.security import resolve_repo_root

    return resolve_repo_root(repo.local_path or repo.working_directory)
