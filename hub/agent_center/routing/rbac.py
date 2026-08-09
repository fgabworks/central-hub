"""AiriX Smart Routing Phase 5 — explicit RBAC by actor/workspace.

Roles are permission scopes over the existing router/executor — not a parallel
execution system. Sensitive capabilities default-deny.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.service import AgentCenterError
from hub.notebook.models import normalize_workspace

PERMISSIONS = (
    "analytics.view",
    "ai.execute",
    "provider.grok",
    "provider.codex",
    "codex.approve",
    "live.access",
    "tools.sql",
    "tools.dhis2",
    "tools.repository",
    "tools.playwright",
    "settings.budget",
    "settings.rbac",
)

RBAC_ROLES = ("viewer", "analyst", "developer", "admin")

_ROLE_PERMS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"analytics.view"}),
    "analyst": frozenset(
        {
            "analytics.view",
            "ai.execute",
            "provider.grok",
            "tools.sql",
            "tools.dhis2",
        }
    ),
    "developer": frozenset(
        {
            "analytics.view",
            "ai.execute",
            "provider.grok",
            "provider.codex",
            "codex.approve",
            "tools.sql",
            "tools.dhis2",
            "tools.repository",
            "tools.playwright",
        }
    ),
    "admin": frozenset(PERMISSIONS),
}

_PROVIDER_PERM = {
    "grok": "provider.grok",
    "hub-simulator": "provider.grok",
    "low-cost": "provider.grok",
    "codex": "provider.codex",
    "claude-code": "provider.codex",
    "cursor-agent": "provider.codex",
    "openai-api": "provider.codex",
}

_TOOL_PERMS = {
    "sql_lookup": "tools.sql",
    "dhis2_reports_lookup": "tools.dhis2",
    "uid_lookup": "tools.dhis2",
    "repo_search": "tools.repository",
    "read_file": "tools.repository",
    "playwright_lookup": "tools.playwright",
    "ui_snapshot": "tools.playwright",
}


def list_rbac_roles() -> list[dict[str, Any]]:
    return [
        {
            "id": rid,
            "label": rid.replace("_", " ").title(),
            "permissions": sorted(_ROLE_PERMS.get(rid, frozenset())),
        }
        for rid in RBAC_ROLES
    ]


def permissions_for_role(role_id: str) -> frozenset[str]:
    rid = (role_id or "").strip().lower()
    return _ROLE_PERMS.get(rid, frozenset())


def default_role_for_actor(actor: str) -> str:
    a = (actor or "").strip().lower() or "anonymous"
    if a in {"owner", "admin"}:
        return "admin"
    if a == "anonymous":
        return "viewer"
    return "viewer"


def normalize_role_id(role_id: str | None) -> str:
    rid = (role_id or "").strip().lower()
    return rid if rid in RBAC_ROLES else "viewer"


class RoutingAclStore:
    """Persist actor→role assignments per workspace (agent_center.db)."""

    def __init__(self, db: AgentCenterDb | None = None) -> None:
        self.db = db or AgentCenterDb()

    def get_role(self, actor: str, *, workspace: str = "work") -> str:
        workspace = normalize_workspace(workspace)
        actor_n = (actor or "owner").strip() or "owner"
        with self.db.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "airix_routing_acl" not in tables:
                return default_role_for_actor(actor_n)
            row = conn.execute(
                "SELECT role_id FROM airix_routing_acl WHERE workspace=? AND actor=?",
                (workspace, actor_n),
            ).fetchone()
        if row is None:
            return default_role_for_actor(actor_n)
        return normalize_role_id(str(row["role_id"]))

    def set_role(
        self,
        actor: str,
        role_id: str,
        *,
        workspace: str = "work",
    ) -> dict[str, Any]:
        workspace = normalize_workspace(workspace)
        actor_n = (actor or "").strip() or "anonymous"
        rid = normalize_role_id(role_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO airix_routing_acl(workspace, actor, role_id, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(workspace, actor) DO UPDATE SET
                    role_id=excluded.role_id,
                    updated_at=excluded.updated_at
                """,
                (workspace, actor_n, rid, now),
            )
        return {
            "workspace": workspace,
            "actor": actor_n,
            "role_id": rid,
            "permissions": sorted(permissions_for_role(rid)),
            "updated_at": now,
        }

    def list_assignments(self, *, workspace: str = "work") -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        with self.db.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "airix_routing_acl" not in tables:
                return []
            rows = conn.execute(
                """
                SELECT workspace, actor, role_id, updated_at
                FROM airix_routing_acl
                WHERE workspace=?
                ORDER BY actor ASC
                """,
                (workspace,),
            ).fetchall()
        return [
            {
                "workspace": r["workspace"],
                "actor": r["actor"],
                "role_id": r["role_id"],
                "permissions": sorted(permissions_for_role(str(r["role_id"]))),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


def actor_permissions(
    actor: str,
    *,
    workspace: str = "work",
    acl: RoutingAclStore | None = None,
) -> frozenset[str]:
    store = acl or RoutingAclStore()
    role = store.get_role(actor, workspace=workspace)
    return permissions_for_role(role)


def has_permission(perms: frozenset[str] | set[str], permission: str) -> bool:
    return permission in perms


def assert_permission(perms: frozenset[str] | set[str], permission: str, *, detail: str = "") -> None:
    if permission not in perms:
        msg = detail or f"Permission denied: {permission}"
        raise AgentCenterError(msg, code="permission_denied")


def provider_permission(provider_id: str) -> str | None:
    pid = (provider_id or "").strip().lower()
    if not pid or pid == "deterministic":
        return None
    return _PROVIDER_PERM.get(pid, "ai.execute")


def tool_permission(tool_id: str) -> str | None:
    tid = (tool_id or "").strip().lower()
    if tid in _TOOL_PERMS:
        return _TOOL_PERMS[tid]
    if "sql" in tid:
        return "tools.sql"
    if "dhis2" in tid or tid.startswith("uid_"):
        return "tools.dhis2"
    if tid in {"repo_search", "read_file"} or "repo" in tid:
        return "tools.repository"
    if "playwright" in tid or tid.startswith("ui_"):
        return "tools.playwright"
    return None


def filter_tools_for_permissions(
    tool_ids: list[str],
    perms: frozenset[str] | set[str],
) -> list[str]:
    out: list[str] = []
    for tid in tool_ids:
        need = tool_permission(tid)
        if need is None or need in perms:
            out.append(tid)
    return out


def check_execution_allowed(
    *,
    perms: frozenset[str] | set[str],
    provider_id: str,
    tool_ids: list[str] | None = None,
    approve_codex: bool = False,
    live_requested: bool = False,
) -> tuple[bool, str]:
    """Return (ok, reason). Default-deny sensitive caps."""
    if "ai.execute" not in perms and provider_id != "deterministic":
        # Deterministic T0 still requires analytics/view path separately; execute needs ai.execute
        # except pure recommend. For execute of T0 tools, still require ai.execute.
        if "ai.execute" not in perms:
            return False, "AI execution requires permission ai.execute"
    if "ai.execute" not in perms:
        return False, "AI execution requires permission ai.execute"

    need = provider_permission(provider_id)
    if need and need not in perms:
        return False, f"Provider '{provider_id}' requires permission {need}"

    if approve_codex and "codex.approve" not in perms:
        return False, "Codex approval requires permission codex.approve"

    if live_requested and "live.access" not in perms:
        return False, "Live access requires permission live.access"

    for tid in tool_ids or []:
        tneed = tool_permission(tid)
        if tneed and tneed not in perms:
            return False, f"Tool '{tid}' requires permission {tneed}"

    return True, ""


def assert_execution_allowed(**kwargs: Any) -> None:
    ok, reason = check_execution_allowed(**kwargs)
    if not ok:
        raise AgentCenterError(reason, code="permission_denied")


def live_requested_from_prompt(prompt: str, hints: list[str] | None = None) -> bool:
    text = f"{prompt or ''} {' '.join(hints or [])}".lower()
    markers = (" live ", "live environment", "production dhis2", "prod dhis2", "live server")
    padded = f" {text} "
    return any(m in padded for m in markers)


def export_acl_public(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public ACL rows — no secrets."""
    return [
        {
            "workspace": a.get("workspace"),
            "actor": a.get("actor"),
            "role_id": a.get("role_id"),
            "permissions": list(a.get("permissions") or []),
            "updated_at": a.get("updated_at"),
        }
        for a in assignments
    ]


def dump_role_matrix() -> dict[str, list[str]]:
    return {rid: sorted(perms) for rid, perms in _ROLE_PERMS.items()}
