"""Declarative ToolSpec registry for AiriX Unified Tool Runtime (Phase 1).

Wraps existing Hub RO tools — does not reimplement handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INTERACTION_MODES = ("smart", "ask", "inspect", "plan", "agent")

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

ACCESS_READ = "read"
ACCESS_WRITE = "write"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    capability: str
    domain: str
    access: str = ACCESS_READ
    risk: str = RISK_LOW
    allowed_modes: tuple[str, ...] = INTERACTION_MODES
    requires_approval: bool = False
    argument_schema: dict[str, Any] = field(default_factory=dict)
    rbac_permission: str = ""
    openai_compatible: bool = True

    @property
    def is_read_only(self) -> bool:
        return self.access == ACCESS_READ

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "domain": self.domain,
            "access": self.access,
            "risk": self.risk,
            "allowed_modes": list(self.allowed_modes),
            "requires_approval": self.requires_approval,
            "argument_schema": dict(self.argument_schema),
            "rbac_permission": self.rbac_permission,
            "read_only": self.is_read_only,
        }

    def openai_tool_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.argument_schema)
            or {"type": "object", "properties": {}, "additionalProperties": False},
        }


def _obj(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


# Central registry — Phase 1 read-only tools only.
TOOL_SPECS: dict[str, ToolSpec] = {
    "repo_search": ToolSpec(
        name="repo_search",
        description="Search file paths under selected read-only repositories. Secrets and binaries are excluded.",
        capability="repository.search",
        domain="repository",
        risk=RISK_LOW,
        rbac_permission="tools.repository",
        argument_schema=_obj(
            {
                "query": {"type": "string", "description": "Substring or keyword to match in paths"},
                "repo_id": {"type": "string", "description": "Optional single repository id"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            required=["query"],
        ),
    ),
    "read_file": ToolSpec(
        name="read_file",
        description="Read an approved text file within a selected repository. Secrets/binaries/oversized files are rejected.",
        capability="repository.read",
        domain="repository",
        risk=RISK_LOW,
        rbac_permission="tools.repository",
        argument_schema=_obj(
            {
                "repo_id": {"type": "string"},
                "path": {"type": "string", "description": "Relative path within the repository"},
            },
            required=["repo_id", "path"],
        ),
    ),
    "repository_intelligence": ToolSpec(
        name="repository_intelligence",
        description=(
            "Retrieve bounded Repository Intelligence entries for selected repositories "
            "(compact profile + scored paths). Read-only; never scans or mutates the repo."
        ),
        capability="repository.intelligence",
        domain="repository",
        risk=RISK_LOW,
        rbac_permission="tools.repository",
        argument_schema=_obj(
            {
                "query": {"type": "string", "description": "Optional focus query; defaults to the user prompt"},
                "repo_id": {"type": "string", "description": "Optional single repository id"},
                "limit": {"type": "integer", "description": "Max entries (default 6)"},
            },
        ),
    ),
    "uid_lookup": ToolSpec(
        name="uid_lookup",
        description="Look up a UID in the local DHIS2 UID index (read-only). Does not call DHIS2 writes.",
        capability="dhis2.uid",
        domain="dhis2",
        risk=RISK_LOW,
        rbac_permission="tools.dhis2",
        argument_schema=_obj(
            {
                "resource": {"type": "string", "description": "Resource/type key used by the index"},
                "uid": {"type": "string"},
                "query": {"type": "string", "description": "Optional search string when uid omitted"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "org_unit_lookup": ToolSpec(
        name="org_unit_lookup",
        description=(
            "Search DHIS2 organisation units from Hub cache/SQLite (read-only). "
            "Use for regions, provinces, municipalities — project OU trees, not general geography."
        ),
        capability="dhis2.org_unit",
        domain="dhis2",
        risk=RISK_LOW,
        rbac_permission="tools.dhis2",
        argument_schema=_obj(
            {
                "query": {"type": "string", "description": "Name/code search"},
                "environment": {"type": "string", "description": "stage or live (default stage)"},
                "parent_id": {"type": "string", "description": "Optional parent OU UID"},
                "level": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "sql_lookup": ToolSpec(
        name="sql_lookup",
        description="Look up saved SQL Workspace queries (text/metadata only). Never executes SQL.",
        capability="sql.lookup",
        domain="sql",
        risk=RISK_LOW,
        rbac_permission="tools.sql",
        argument_schema=_obj(
            {
                "query_id": {"type": "string"},
                "search": {"type": "string"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "sql_query_execute": ToolSpec(
        name="sql_query_execute",
        description=(
            "Execute a saved SQL Workspace query on its configured read-only connection. "
            "Rejects writes and free-form SQL; saved query id required."
        ),
        capability="sql.execute_ro",
        domain="sql",
        risk=RISK_MEDIUM,
        rbac_permission="tools.sql",
        argument_schema=_obj(
            {
                "query_id": {"type": "string", "description": "Saved SQL Workspace query id"},
                "params": {
                    "type": "object",
                    "description": "Optional named parameter bindings",
                    "additionalProperties": True,
                },
                "page_size": {"type": "integer", "description": "Max rows (default 50, cap 100)"},
            },
            required=["query_id"],
        ),
    ),
    "dhis2_reports_lookup": ToolSpec(
        name="dhis2_reports_lookup",
        description="Look up DHIS2 report metadata / Standard Report library (read-only).",
        capability="dhis2.reports",
        domain="dhis2",
        risk=RISK_LOW,
        rbac_permission="tools.dhis2",
        argument_schema=_obj(
            {
                "query": {"type": "string"},
                "environment": {"type": "string"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "data_explorer_lookup": ToolSpec(
        name="data_explorer_lookup",
        description=(
            "Read-only Data Explorer metadata lookup (inventory/object detail). "
            "Does not export or mutate data."
        ),
        capability="data_explorer.lookup",
        domain="data_explorer",
        risk=RISK_LOW,
        rbac_permission="tools.sql",
        argument_schema=_obj(
            {
                "environment": {"type": "string", "description": "stage or live"},
                "schema": {"type": "string"},
                "name": {"type": "string", "description": "Object/table name"},
                "search": {"type": "string", "description": "Inventory search when object omitted"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "jobs_lookup": ToolSpec(
        name="jobs_lookup",
        description="List existing Work job records and statuses. Never starts or cancels jobs.",
        capability="jobs.lookup",
        domain="jobs",
        risk=RISK_LOW,
        rbac_permission="",
        argument_schema=_obj(
            {
                "status": {"type": "string"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "audit_lookup": ToolSpec(
        name="audit_lookup",
        description="Search Hub audit log entries (read-only).",
        capability="audit.lookup",
        domain="audit",
        risk=RISK_LOW,
        rbac_permission="",
        argument_schema=_obj(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        ),
    ),
    "notebook_lookup": ToolSpec(
        name="notebook_lookup",
        description="Search Repository Notebook notes (read-only). Does not create or edit notes.",
        capability="notebook.lookup",
        domain="notebook",
        risk=RISK_LOW,
        rbac_permission="",
        argument_schema=_obj(
            {
                "search": {"type": "string"},
                "limit": {"type": "integer"},
            },
            required=["search"],
        ),
    ),
    "notepad_lookup": ToolSpec(
        name="notepad_lookup",
        description="Read the Quick Notepad for the active assistant workspace.",
        capability="notepad.lookup",
        domain="notepad",
        risk=RISK_LOW,
        rbac_permission="",
        argument_schema=_obj({}),
    ),
    "email_search": ToolSpec(
        name="email_search",
        description="Search message metadata in connected Email accounts. Read-only.",
        capability="email.search",
        domain="email",
        risk=RISK_LOW,
        allowed_modes=("smart", "ask", "agent"),
        rbac_permission="",
        argument_schema=_obj(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            required=["query"],
        ),
    ),
    "calendar_lookup": ToolSpec(
        name="calendar_lookup",
        description="List upcoming Calendar events. Read-only.",
        capability="calendar.lookup",
        domain="calendar",
        risk=RISK_LOW,
        allowed_modes=("smart", "ask", "agent"),
        rbac_permission="",
        argument_schema=_obj(
            {
                "limit": {"type": "integer"},
            },
        ),
    ),
    "skill_recall": ToolSpec(
        name="skill_recall",
        description=(
            "On-demand recall of repository instruction / skill markdown "
            "(AGENTS.md, AI_REFERENCE.md, SKILLS.md, etc.). Read-only; "
            "use instead of packing all instruction files into the initial prompt."
        ),
        capability="repository.skill_recall",
        domain="repository",
        risk=RISK_LOW,
        rbac_permission="tools.repository",
        argument_schema=_obj(
            {
                "repo_id": {"type": "string", "description": "Optional single repository id"},
                "name": {
                    "type": "string",
                    "description": "Optional filename filter (e.g. AGENTS.md, SKILLS.md)",
                },
                "query": {"type": "string", "description": "Optional keyword filter within instruction text"},
                "limit": {"type": "integer", "description": "Max files (default 4)"},
            },
        ),
    ),
}


PHASE1_CORE_TOOLS = frozenset(
    {
        "repo_search",
        "read_file",
        "repository_intelligence",
        "skill_recall",
        "uid_lookup",
        "org_unit_lookup",
        "sql_lookup",
        "sql_query_execute",
        "dhis2_reports_lookup",
        "data_explorer_lookup",
        "jobs_lookup",
        "audit_lookup",
    }
)


def get_tool_spec(name: str) -> ToolSpec | None:
    return TOOL_SPECS.get(str(name or "").strip())


def list_tool_specs(*, read_only_only: bool = True) -> list[ToolSpec]:
    specs = list(TOOL_SPECS.values())
    if read_only_only:
        specs = [s for s in specs if s.is_read_only]
    return specs


def openai_tool_definitions(names: set[str] | list[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI/xAI Responses function schemas for the active tool subset."""
    if names is None:
        selected = list(TOOL_SPECS.keys())
    else:
        selected = [str(n).strip() for n in names if str(n).strip()]
    out: list[dict[str, Any]] = []
    for name in selected:
        spec = TOOL_SPECS.get(name)
        if spec is None or not spec.openai_compatible:
            continue
        out.append(spec.openai_tool_definition())
    return out
