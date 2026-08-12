"""Mode / intent / RBAC policy for active Tool Runtime tools."""

from __future__ import annotations

from typing import Any

from hub.agent_center.tool_runtime.specs import (
    ACCESS_READ,
    PHASE1_CORE_TOOLS,
    TOOL_SPECS,
    ToolSpec,
    get_tool_spec,
)

# Ask mode: smallest useful RO set.
_ASK_TOOLS = (
    "notebook_lookup",
    "uid_lookup",
    "org_unit_lookup",
    "sql_lookup",
    "repository_intelligence",
    "jobs_lookup",
)

# Inspect: broader RO discovery set.
_INSPECT_TOOLS = (
    "repo_search",
    "read_file",
    "repository_intelligence",
    "uid_lookup",
    "org_unit_lookup",
    "sql_lookup",
    "sql_query_execute",
    "dhis2_reports_lookup",
    "data_explorer_lookup",
    "jobs_lookup",
    "audit_lookup",
    "notebook_lookup",
)

_PLAN_TOOLS = _INSPECT_TOOLS  # RO only; writes already blocked by access=read

_CONTEXT_SOURCE_TOOLS: dict[str, tuple[str, ...]] = {
    "dhis2_environment": ("uid_lookup", "org_unit_lookup", "dhis2_reports_lookup"),
    "ro_database": ("sql_lookup", "sql_query_execute"),
    "data_explorer": ("data_explorer_lookup", "sql_lookup", "sql_query_execute"),
    "files": ("repo_search", "read_file", "repository_intelligence"),
    "workspace": ("notebook_lookup", "jobs_lookup", "audit_lookup"),
    "prior_findings": ("notebook_lookup", "jobs_lookup"),
}


def _normalize_mode(value: str | None) -> str:
    # Local copy to avoid circular import with routing.service at package import time.
    key = str(value or "smart").strip().lower().replace("-", "_")
    aliases = {
        "direct": "agent",
        "direct_agent": "agent",
        "efficient": "agent",
        "find": "inspect",
    }
    key = aliases.get(key, key)
    return key if key in {"smart", "ask", "inspect", "plan", "agent"} else "smart"


def tool_runtime_needed(
    *,
    interaction_mode: str | None,
    classification: Any | None = None,
    t0_solved: bool = False,
    authoritative_data: bool = False,
    adapter_is_api: bool = False,
    force: bool = False,
) -> bool:
    """Decide whether unresolved work should enter the iterative Tool Runtime."""
    if force:
        return True
    if t0_solved:
        return False
    if not adapter_is_api:
        # Phase 1: Hub-driven iterative tools for API adapters only.
        return False
    mode = _normalize_mode(interaction_mode)
    if mode in {"ask", "inspect", "plan", "agent"}:
        return True
    # Smart: use when routing signals tool work / structured data / project lookup.
    if mode == "smart":
        if authoritative_data:
            return True
        signals = set(getattr(classification, "signals", None) or [])
        if signals & {
            "project_lookup",
            "project_grounding_required",
            "authoritative_data_query",
            "structured_data_lookup",
            "data_query",
        }:
            return True
        task = str(getattr(classification, "task_type", "") or "")
        if task in {"sql_investigation", "dhis2_investigation", "lookup", "coding"}:
            return True
    return False


def select_active_tools(
    *,
    interaction_mode: str | None,
    classification: Any | None = None,
    context_sources: list[str] | None = None,
    profile_allowed: set[str] | list[str] | None = None,
    requested: set[str] | list[str] | None = None,
    permissions: set[str] | frozenset[str] | None = None,
    max_tools: int = 10,
    prompt: str = "",
    completion_intent: str | None = None,
    repository_intelligence: dict[str, Any] | None = None,
    prior_tool_names: list[str] | None = None,
) -> list[ToolSpec]:
    """Expose only intent/context-relevant read-only tools (Phase 2 dynamic)."""
    from hub.agent_center.tool_runtime.intelligence import select_dynamic_tools

    specs = select_dynamic_tools(
        prompt=prompt,
        interaction_mode=interaction_mode,
        classification=classification,
        context_sources=context_sources,
        completion_intent=completion_intent,
        repository_intelligence=repository_intelligence,
        prior_tool_names=prior_tool_names,
        profile_allowed=profile_allowed,
        permissions=permissions,
        max_tools=max_tools,
    )
    if requested:
        # Soft-merge explicitly requested tools that pass gates.
        have = {s.name for s in specs}
        for name in requested:
            n = str(name).strip()
            if not n or n in have:
                continue
            spec = get_tool_spec(n)
            if spec is None or not spec.is_read_only:
                continue
            mode = _normalize_mode(interaction_mode)
            if mode != "smart" and mode not in spec.allowed_modes:
                continue
            if permissions is not None and spec.rbac_permission and spec.rbac_permission not in set(permissions):
                continue
            specs.append(spec)
            have.add(n)
            if len(specs) >= max(1, int(max_tools)):
                break
    return specs[: max(1, int(max_tools))]


def policy_gate(
    tool_name: str,
    *,
    interaction_mode: str | None,
    active_names: set[str] | None = None,
    permissions: set[str] | frozenset[str] | None = None,
    allow_writes: bool = False,
) -> dict[str, Any]:
    """Enforce mode + RO + RBAC before execution."""
    name = str(tool_name or "").strip()
    spec = get_tool_spec(name)
    mode = _normalize_mode(interaction_mode)
    if spec is None:
        return {"allowed": False, "reason": "unknown_tool", "requires_approval": False}
    if not allow_writes and spec.access != ACCESS_READ:
        return {"allowed": False, "reason": "write_tools_blocked_phase1", "requires_approval": True}
    if mode not in spec.allowed_modes and mode != "smart":
        return {"allowed": False, "reason": f"tool_not_allowed_in_{mode}", "requires_approval": False}
    if active_names is not None and name not in active_names:
        return {"allowed": False, "reason": "tool_not_in_active_subset", "requires_approval": False}
    if permissions is not None and spec.rbac_permission and spec.rbac_permission not in permissions:
        return {"allowed": False, "reason": "rbac_denied", "requires_approval": True}
    if spec.requires_approval:
        return {"allowed": False, "reason": "approval_required", "requires_approval": True}
    # Inspect/Ask/Plan/Agent: RO tools auto-allowed when in active set.
    return {"allowed": True, "reason": "", "requires_approval": False, "spec": spec}


def active_tool_names(specs: list[ToolSpec]) -> set[str]:
    return {s.name for s in specs}


def registry_snapshot() -> list[dict[str, Any]]:
    return [s.public() for s in TOOL_SPECS.values() if s.is_read_only]
