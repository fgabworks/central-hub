"""Read-only allowlisted function tools for the OpenAI adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hub.agent_center.instructions import load_repo_instructions
from hub.agent_center.models import MAX_CONTEXT_FILE_CHARS
from hub.agent_center.redact import redact_text
from hub.agent_center.secrets import is_secret_path
from hub.registry.models import Registry, Repository
from hub.settings import ROOT_DIR

ALLOWED_TOOLS = frozenset(
    {
        "repo_search",
        "read_file",
        "uid_lookup",
        "sql_lookup",
        "notebook_lookup",
    }
)

_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".a",
    ".wasm",
    ".mp3",
    ".mp4",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".db",
    ".sqlite",
}


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


def tool_definitions() -> list[dict[str, Any]]:
    """OpenAI Responses API function tool schemas (read-only)."""
    return [
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
                    "scope": {"type": "string", "description": "personal|work|omit for both"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    ]


def execute_tool(name: str, arguments: dict[str, Any] | str, ctx: AgentToolsContext) -> str:
    if name not in ALLOWED_TOOLS:
        act = ToolActivity(name=name, arguments={}, ok=False, detail="Tool not allowlisted")
        ctx.activity.append(act)
        return json.dumps({"error": "Tool not allowlisted", "allowed": sorted(ALLOWED_TOOLS)})

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
    query = str(args.get("query") or "").strip().lower()
    if not query:
        return {"error": "query is required"}
    repo_filter = str(args.get("repo_id") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 50))
    hits: list[dict[str, str]] = []
    for repo, root in ctx.scoped_repos():
        if repo_filter and repo.id != repo_filter:
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if _reject_file(path, root):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if query in rel.lower():
                hits.append({"repo_id": repo.id, "path": rel})
                if len(hits) >= limit:
                    return {"summary": f"{len(hits)} matches", "matches": hits}
    return {"summary": f"{len(hits)} matches", "matches": hits}


def _normalize_rel(path: str) -> str:
    rel = (path or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def _tool_read_file(args: dict[str, Any], ctx: AgentToolsContext) -> dict[str, Any]:
    repo_id = str(args.get("repo_id") or "").strip()
    rel = _normalize_rel(str(args.get("path") or ""))
    if not repo_id or not rel:
        return {"error": "repo_id and path are required"}
    scoped = {r.id: root for r, root in ctx.scoped_repos()}
    root = scoped.get(repo_id)
    if root is None:
        return {"error": f"Repository not in run scope: {repo_id}"}
    try:
        path = (root / rel).resolve()
        path.relative_to(root.resolve())
    except Exception:  # noqa: BLE001
        return {"error": "Path escapes repository root"}
    if _reject_file(path, root):
        return {"error": "File excluded (secret, binary, or disallowed)"}
    if not path.is_file():
        return {"error": "File not found"}
    size = path.stat().st_size
    if size > MAX_CONTEXT_FILE_CHARS * 4:
        return {"error": f"File too large ({size} bytes)"}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"error": str(exc)}
    if b"\x00" in raw[:8192]:
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
    scope = str(args.get("scope") or "").strip() or None
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


def _reject_file(path: Path, root: Path) -> bool:
    if is_secret_path(path, repo_root=root):
        return True
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return True
    name = path.name.lower()
    if name.startswith(".env"):
        return True
    return False


def _resolve_repo_path(repo: Repository) -> Path | None:
    raw = (repo.working_directory or repo.local_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    else:
        path = path.resolve()
    return path if path.is_dir() else None
