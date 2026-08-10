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


def tools_for_repository_knowledge(
    tools: list[str], repository_knowledge: dict[str, Any] | None
) -> list[str]:
    """Add only category-relevant read-only tools from retrieved repo knowledge."""
    categories: set[str] = set()
    for profile in (repository_knowledge or {}).get("profiles") or []:
        categories.update(str(x) for x in (profile.get("categories") or []))
    for item in (repository_knowledge or {}).get("items") or []:
        categories.add(str(item.get("category") or ""))
    additions: list[str] = []
    if categories & {"data_sources"}:
        additions.extend(["sql_lookup", "dhis2_reports_lookup", "uid_lookup"])
    if categories & {"architecture", "business_logic", "configuration", "integrations", "guidance"}:
        additions.extend(["repo_search", "read_file"])
    return list(dict.fromkeys(list(tools) + additions))[:6]


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


ROUTING_MODE_SMART = "smart"
ROUTING_MODE_DIRECT = "direct"

INTERACTION_MODES = ("smart", "ask", "inspect", "plan", "agent")
INTERACTION_MODE_SMART = "smart"
INTERACTION_MODE_AGENT = "agent"
CONTEXT_SOURCES = (
    "dhis2_environment",
    "ro_database",
    "data_explorer",
    "files",
    "workspace",
    "prior_findings",
)


def normalize_interaction_mode(value: str | None) -> str:
    """Normalize the Cursor/VS Code-style AiriX interaction mode."""
    key = str(value or INTERACTION_MODE_SMART).strip().lower().replace("-", "_")
    aliases = {
        "direct": "agent",
        "direct_agent": "agent",
        "efficient": "agent",
        "find": "inspect",
    }
    key = aliases.get(key, key)
    return key if key in INTERACTION_MODES else INTERACTION_MODE_SMART


def normalize_context_sources(values: list[str] | tuple[str, ...] | None) -> list[str]:
    allowed = set(CONTEXT_SOURCES)
    return list(
        dict.fromkeys(
            str(value or "").strip().lower()
            for value in (values or [])
            if str(value or "").strip().lower() in allowed
        )
    )


def normalize_routing_mode(value: str | None) -> str:
    key = normalize_interaction_mode(value)
    if key == INTERACTION_MODE_AGENT:
        return ROUTING_MODE_DIRECT
    return ROUTING_MODE_SMART


def build_direct_agent_recommendation(
    prompt: str,
    *,
    provider_id: str,
    model: str | None = None,
    repository_ids: list[str] | None = None,
) -> RouteRecommendation:
    """
    Lightweight recommendation used only for Direct Agent context packing.

    Does not choose a provider — the caller already selected one. Classification
    still drives minimal tools / file hints / prior-finding relevance.
    """
    from hub.agent_center.routing.classifier import classify_prompt
    from hub.agent_center.routing.providers import ProviderRegistry

    provider = (provider_id or "").strip()
    if not provider or provider_to_adapter_id(provider) is None:
        raise ValueError("Direct Agent requires an explicit AI provider (not T0/deterministic)")
    c = classify_prompt(prompt, repository_ids=repository_ids)
    try:
        spec = ProviderRegistry().get(provider)
        label = spec.label if spec else provider
    except Exception:  # noqa: BLE001
        label = provider
    # Tier is informational only in Direct mode (routing bypassed).
    tier = "T3" if provider in {"codex", "claude-code", "cursor-agent"} else (
        "T1" if provider in {"low-cost", "hub-simulator"} else "T2"
    )
    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent=provider,
        recommended_label=str(label),
        recommended_tier=tier,
        recommended_model=(model or "").strip(),
        recommended_model_reason="Direct Agent — selected model",
        alternative_agent=None,
        alternative_label=None,
        confidence=1.0,
        reason="Direct Agent — Efficient (Smart Routing bypassed)",
        estimated_usage="Medium",
        approval_required=provider in {"codex", "claude-code", "cursor-agent"},
        classification=c,
        providers_considered=[provider],
        escalation_reason=None,
        history_influenced=False,
    )


def build_minimal_context_preview(
    *,
    prompt: str,
    classification: PromptClassification,
    recommendation: RouteRecommendation,
    repository_ids: list[str] | None = None,
    agent_override: str | None = None,
    candidate_findings: list[dict[str, Any]] | None = None,
    context_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Describe the minimal context packed for execution (no whole-repo send)."""
    provider = (agent_override or recommendation.recommended_agent or "").strip()
    adapter_id = provider_to_adapter_id(provider)
    sources = normalize_context_sources(context_sources)
    tools = select_minimal_tools(classification)
    source_tools = {
        "dhis2_environment": ["org_unit_lookup", "uid_lookup", "dhis2_reports_lookup"],
        "ro_database": ["sql_lookup"],
        "data_explorer": ["sql_lookup", "repo_search"],
        "files": ["repo_search", "read_file"],
        "workspace": ["notebook_lookup", "jobs_lookup", "audit_lookup"],
    }
    for source in sources:
        tools.extend(source_tools.get(source, []))
    tools = list(dict.fromkeys(tools))[:6]
    repos = select_repository_ids(classification, repository_ids)
    max_files = 6 if classification.context_size == "large" else (4 if classification.context_size == "medium" else 2)
    if classification.needs_architecture:
        max_files = min(12, max(6, classification.estimated_scope_files))
    prior = select_relevant_findings(
        list(candidate_findings or []) if not sources or "prior_findings" in sources else [],
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
        "context_sources": sources,
        "repository_ids": repos,
        "max_context_files": max_files,
        "hints": list(classification.signals)[:8],
        "include_instruction_files": bool(repos) and classification.needs_architecture,
        "prior_findings": prior,
        "prompt_chars": len((prompt or "").strip()),
        "note": "Only task-relevant tools/files/rules/findings are packed; whole-repo context is never default.",
    }
