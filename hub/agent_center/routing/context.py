"""Minimal context selection for AiriX Smart Routing Phase 3."""

from __future__ import annotations

from typing import Any

from hub.agent_center.routing.findings import select_relevant_findings
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation


# Keep AI context lean: task-scoped tools only, no whole-repo dumps.
_TASK_TOOLS: dict[str, tuple[str, ...]] = {
    "lookup": ("notebook_lookup", "uid_lookup", "org_unit_lookup", "jobs_lookup", "audit_lookup"),
    "css_ui": ("repo_search", "read_file"),
    "sql_investigation": ("sql_lookup", "notebook_lookup", "repo_search", "read_file"),
    "dhis2_investigation": (
        "uid_lookup",
        "org_unit_lookup",
        "dhis2_reports_lookup",
        "sql_lookup",
        "repo_search",
        "read_file",
    ),
    "coding": ("repo_search", "read_file", "notebook_lookup"),
    "testing": ("repo_search", "read_file", "notebook_lookup"),
    "architecture": ("repo_search", "read_file", "notebook_lookup"),
    "refactor": ("repo_search", "read_file", "notebook_lookup"),
    "general": ("notebook_lookup", "repo_search", "read_file"),
}


def select_minimal_tools(classification: PromptClassification) -> list[str]:
    tools = list(_TASK_TOOLS.get(classification.task_type, _TASK_TOOLS["general"]))
    signals = set(classification.signals or [])
    if (
        "project_lookup" in signals
        or "project_grounding_required" in signals
        or "authoritative_data_query" in signals
        or "structured_data_lookup" in signals
        or "data_query" in signals
    ):
        tools = [
            "org_unit_lookup",
            "uid_lookup",
            "dhis2_reports_lookup",
            "sql_lookup",
            "repo_search",
            "notebook_lookup",
        ] + tools
    if classification.deterministic_capable:
        tools = [
            t
            for t in tools
            if t.endswith("_lookup")
            or t in {"notebook_lookup", "uid_lookup", "org_unit_lookup", "sql_lookup", "repo_search"}
        ]
        if not tools:
            tools = ["notebook_lookup", "uid_lookup", "org_unit_lookup", "jobs_lookup"]
    return list(dict.fromkeys(tools))[:6]


def select_repository_ids(
    classification: PromptClassification,
    requested: list[str] | None,
    *,
    max_repos: int = 2,
) -> list[str]:
    """Attach repositories when coding/architecture needs them, or when selected for project grounding."""
    ids = [str(x).strip() for x in (requested or []) if str(x).strip()]
    if not ids:
        return []
    signals = set(classification.signals or [])
    # Explicit broader/national/GK scope drops selected repo from packing.
    if any(
        s.startswith("scope:national_general")
        or s.startswith("scope:general_knowledge")
        or s.startswith("scope:current_web")
        or s == "allow_general_knowledge"
        for s in signals
    ) and "project_grounding_required" not in signals and "project_lookup" not in signals:
        # Keep repos only for coding tasks that still need workspace files.
        if classification.needs_coding or classification.task_type in {
            "architecture",
            "refactor",
            "coding",
            "testing",
            "css_ui",
            "sql_investigation",
        }:
            return ids[: max(1, min(int(max_repos), 3))]
        return []
    if classification.needs_architecture or classification.task_type in {
        "architecture",
        "refactor",
        "coding",
        "testing",
        "css_ui",
        "sql_investigation",
        "dhis2_investigation",
    }:
        return ids[: max(1, min(int(max_repos), 3))]
    # Preserve explicit selection for project-grounded lookups (OU/DHIS2/config).
    if (
        "project_grounding_required" in signals
        or "project_lookup" in signals
        or "authoritative_data_query" in signals
        or "structured_data_lookup" in signals
    ):
        return ids[: max(1, min(int(max_repos), 3))]
    if "dhis2_or_ou_topic" in signals and "national_or_general_lookup" not in signals:
        return ids[:1]
    return []


def provider_to_adapter_id(provider_id: str) -> str | None:
    """Map routing provider id → existing agents.yaml adapter id (None = T0 tools)."""
    key = (provider_id or "").strip().lower()
    mapping = {
        "deterministic": None,
        "low-cost": "hub-simulator",
        "grok": "grok",
        "codex": "codex",
        "openai-api": "openai-api",
        "claude-code": "claude-code",
        "cursor-agent": "cursor-agent",
        "hub-simulator": "hub-simulator",
    }
    if key in mapping:
        return mapping[key]
    return key or None


def build_minimal_context_preview(
    *,
    prompt: str,
    classification: PromptClassification,
    recommendation: RouteRecommendation,
    repository_ids: list[str] | None = None,
    agent_override: str | None = None,
    candidate_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the minimal context packed for execution (no whole-repo send)."""
    provider = (agent_override or recommendation.recommended_agent or "").strip()
    adapter_id = provider_to_adapter_id(provider)
    tools = select_minimal_tools(classification)
    repos = select_repository_ids(classification, repository_ids)
    max_files = 6 if classification.context_size == "large" else (4 if classification.context_size == "medium" else 2)
    if classification.needs_architecture:
        max_files = min(12, max(6, classification.estimated_scope_files))
    prior = select_relevant_findings(
        list(candidate_findings or []),
        prompt=prompt,
        classification=classification,
        max_items=3,
    )
    return {
        "strategy": "minimal",
        "include_whole_repo": False,
        "adapter_id": adapter_id,
        "provider_id": provider,
        "tool_ids": tools,
        "repository_ids": repos,
        "max_context_files": max_files,
        "hints": list(classification.signals)[:8],
        "include_instruction_files": bool(repos) and classification.needs_architecture,
        "prior_findings": prior,
        "prompt_chars": len((prompt or "").strip()),
        "note": "Only task-relevant tools/files/rules/findings are packed; whole-repo context is never default.",
    }
