"""Phase 2 dynamic tool selection — intent/context/RI/mode scored subsets."""

from __future__ import annotations

import re
from typing import Any

from hub.agent_center.tool_runtime.policy import _ASK_TOOLS, _CONTEXT_SOURCE_TOOLS, _INSPECT_TOOLS, _normalize_mode
from hub.agent_center.tool_runtime.specs import PHASE1_CORE_TOOLS, ToolSpec, get_tool_spec

# Always keep recall tools available so the loop can fetch skills/RI on demand.
_RECALL_TOOLS = ("repository_intelligence", "skill_recall")

_INTENT_TOOLS: dict[str, tuple[str, ...]] = {
    "count": ("sql_lookup", "sql_query_execute", "org_unit_lookup", "uid_lookup"),
    "list": ("sql_lookup", "sql_query_execute", "org_unit_lookup", "repo_search"),
    "lookup": ("uid_lookup", "org_unit_lookup", "sql_lookup", "notebook_lookup"),
    "status": ("jobs_lookup", "sql_lookup", "audit_lookup"),
    "metadata": ("dhis2_reports_lookup", "uid_lookup", "data_explorer_lookup", "sql_lookup"),
    "comparison": ("sql_lookup", "sql_query_execute", "dhis2_reports_lookup"),
    "trace": ("repo_search", "read_file", "repository_intelligence", "skill_recall"),
    "explanation": ("repository_intelligence", "skill_recall", "repo_search", "read_file"),
    "file_search": ("repo_search", "read_file", "repository_intelligence"),
    "general": ("notebook_lookup", "repository_intelligence", "skill_recall"),
}

_SIGNAL_TOOLS: dict[str, tuple[str, ...]] = {
    "authoritative_data_query": ("sql_lookup", "sql_query_execute", "org_unit_lookup"),
    "structured_data_lookup": ("sql_lookup", "data_explorer_lookup"),
    "data_query": ("sql_lookup", "sql_query_execute"),
    "project_lookup": ("repository_intelligence", "repo_search", "read_file", "skill_recall"),
    "project_grounding_required": ("repository_intelligence", "skill_recall", "repo_search"),
}

_RI_CATEGORY_TOOLS: dict[str, tuple[str, ...]] = {
    "data_sources": ("sql_lookup", "sql_query_execute", "dhis2_reports_lookup", "uid_lookup"),
    "architecture": ("repo_search", "read_file", "skill_recall"),
    "business_logic": ("repo_search", "read_file", "repository_intelligence"),
    "configuration": ("repo_search", "read_file", "uid_lookup"),
    "integrations": ("dhis2_reports_lookup", "sql_lookup", "repo_search"),
    "guidance": ("skill_recall", "repository_intelligence", "notebook_lookup"),
}

_SQL_HINT = re.compile(r"\b(sql|query|count|how\s+many|eligible|coverage|numerator)\b", re.I)
_DHIS2_HINT = re.compile(r"\b(dhis2|org\s*unit|ou\b|uid|indicator|data\s*element|report)\b", re.I)
_FILE_HINT = re.compile(r"\b(file|path|code|repo|readme|module|function|class)\b", re.I)
_JOB_HINT = re.compile(r"\b(job|run|status|log|audit|failed)\b", re.I)


def _ri_categories(repository_intelligence: dict[str, Any] | None) -> set[str]:
    cats: set[str] = set()
    knowledge = repository_intelligence if isinstance(repository_intelligence, dict) else {}
    for profile in knowledge.get("profiles") or []:
        for c in profile.get("categories") or []:
            if str(c).strip():
                cats.add(str(c).strip().lower())
    for item in knowledge.get("items") or []:
        c = str(item.get("category") or "").strip().lower()
        if c:
            cats.add(c)
    return cats


def score_tools(
    *,
    prompt: str,
    interaction_mode: str | None,
    classification: Any | None = None,
    context_sources: list[str] | None = None,
    completion_intent: str | None = None,
    repository_intelligence: dict[str, Any] | None = None,
    prior_tool_names: list[str] | None = None,
) -> dict[str, int]:
    """Return tool_name → relevance score (higher = more relevant)."""
    mode = _normalize_mode(interaction_mode)
    scores: dict[str, int] = {}

    def bump(name: str, points: int = 1) -> None:
        if not name:
            return
        scores[name] = int(scores.get(name) or 0) + int(points)

    # Mode baseline (lean for Ask; broader for Inspect/Plan/Agent).
    baseline = _ASK_TOOLS if mode == "ask" else _INSPECT_TOOLS
    if mode == "agent":
        baseline = tuple(list(_INSPECT_TOOLS) + ["email_search", "calendar_lookup"])
    for name in baseline:
        bump(name, 1 if mode == "ask" else 2)

    # Always allow on-demand recall (Phase 2: do not overpack; recall mid-run).
    for name in _RECALL_TOOLS:
        bump(name, 3)

    intent = str(completion_intent or "").strip().lower()
    if not intent and classification is not None:
        intent = str(getattr(classification, "task_type", "") or "").strip().lower()
    for name in _INTENT_TOOLS.get(intent, ()):
        bump(name, 4)

    signals = set(getattr(classification, "signals", None) or []) if classification else set()
    for sig in signals:
        for name in _SIGNAL_TOOLS.get(str(sig), ()):
            bump(name, 5)

    for src in context_sources or []:
        for name in _CONTEXT_SOURCE_TOOLS.get(str(src).strip().lower(), ()):
            bump(name, 4)

    for cat in _ri_categories(repository_intelligence):
        for name in _RI_CATEGORY_TOOLS.get(cat, ()):
            bump(name, 3)

    text = prompt or ""
    if _SQL_HINT.search(text):
        bump("sql_lookup", 4)
        bump("sql_query_execute", 4)
    if _DHIS2_HINT.search(text):
        bump("uid_lookup", 4)
        bump("org_unit_lookup", 4)
        bump("dhis2_reports_lookup", 3)
    if _FILE_HINT.search(text):
        bump("repo_search", 4)
        bump("read_file", 3)
        bump("repository_intelligence", 3)
    if _JOB_HINT.search(text):
        bump("jobs_lookup", 4)
        bump("audit_lookup", 2)

    # Mid-run adaptation: if prior tools failed or were narrow, boost complements.
    prior = {str(n) for n in (prior_tool_names or []) if str(n).strip()}
    if "sql_lookup" in prior and "sql_query_execute" not in prior:
        bump("sql_query_execute", 5)
    if "repo_search" in prior and "read_file" not in prior:
        bump("read_file", 4)
    if "repository_intelligence" in prior and "skill_recall" not in prior:
        bump("skill_recall", 3)
    if prior & {"uid_lookup", "org_unit_lookup"}:
        bump("dhis2_reports_lookup", 2)

    return scores


def select_dynamic_tools(
    *,
    prompt: str = "",
    interaction_mode: str | None = None,
    classification: Any | None = None,
    context_sources: list[str] | None = None,
    completion_intent: str | None = None,
    repository_intelligence: dict[str, Any] | None = None,
    prior_tool_names: list[str] | None = None,
    profile_allowed: set[str] | list[str] | None = None,
    permissions: set[str] | frozenset[str] | None = None,
    max_tools: int = 8,
) -> list[ToolSpec]:
    """Choose only task-relevant RO tools dynamically."""
    mode = _normalize_mode(interaction_mode)
    scores = score_tools(
        prompt=prompt,
        interaction_mode=mode,
        classification=classification,
        context_sources=context_sources,
        completion_intent=completion_intent,
        repository_intelligence=repository_intelligence,
        prior_tool_names=prior_tool_names,
    )
    profile_set = (
        set(profile_allowed) | set(PHASE1_CORE_TOOLS) | set(_RECALL_TOOLS)
        if profile_allowed is not None
        else None
    )
    perm_set = set(permissions) if permissions is not None else None

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[ToolSpec] = []
    seen: set[str] = set()
    for name, _score in ranked:
        if name in seen:
            continue
        spec = get_tool_spec(name)
        if spec is None or not spec.is_read_only:
            continue
        if mode != "smart" and mode not in spec.allowed_modes:
            continue
        if profile_set is not None and name not in profile_set:
            continue
        if perm_set is not None and spec.rbac_permission and spec.rbac_permission not in perm_set:
            continue
        out.append(spec)
        seen.add(name)
        if len(out) >= max(1, int(max_tools)):
            break

    # Guarantee recall tools when under cap and allowed.
    for name in _RECALL_TOOLS:
        if len(out) >= max(1, int(max_tools)):
            break
        if name in seen:
            continue
        spec = get_tool_spec(name)
        if spec is None:
            continue
        if profile_set is not None and name not in profile_set:
            continue
        if perm_set is not None and spec.rbac_permission and spec.rbac_permission not in perm_set:
            continue
        out.append(spec)
        seen.add(name)
    return out
