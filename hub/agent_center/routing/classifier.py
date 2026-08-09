"""Heuristic prompt classifier for AiriX Smart Routing (Phase 1 — no LLM calls)."""

from __future__ import annotations

import re
from typing import Iterable

from hub.agent_center.data_intent import detect_data_query_intent
from hub.agent_center.routing.models import PromptClassification
from hub.agent_center.scope import (
    SCOPE_DHIS2,
    SCOPE_GK,
    SCOPE_PROJECT,
    SCOPE_WEB,
    detect_prompt_scope,
)


_LOOKUP = re.compile(
    r"\b("
    r"look\s*up|lookup|find\s+uid|what\s+is|what\s+are|"
    r"show\s+(me\s+)?(recent\s+)?|"
    r"list\s+(open|recent|the)?|"
    r"search\s+(notes?|email|audit|jobs?)|"
    r"status\s+of|"
    r"recent\s+(dhis2\s+)?(jobs?|logs?|statuses?)|"
    r"(jobs?|logs?)\s+and\s+statuses?"
    r")\b",
    re.I,
)
_PROJECT_LOOKUP = re.compile(
    r"\b("
    r"provinces?\s+(for|in|under)|"
    r"region\s+(i{1,3}|iv|v|vi{0,3}|\d+)|"
    r"central\s+luzon|"
    r"org(?:anisation|anization)?\s*units?|"
    r"what\s+(are|is)\s+the\s+(provinces?|org)"
    r")\b",
    re.I,
)
_CSS = re.compile(
    r"\b(css|stylesheet|padding|margin|color|font|button\s+style|layout|sidebar|"
    r"responsive|hover|border-radius|flexbox|grid)\b",
    re.I,
)
_SQL = re.compile(
    r"\b(sql|query|select\s+|join\s+|postgres|database|table\s+|schema)\b",
    re.I,
)
_DHIS2 = re.compile(
    r"\b(dhis2|dhis|org\s*unit|program\s+indicator|analytics|uid|data\s*element|"
    r"hcsc|indicator\s+mapping)\b",
    re.I,
)
_CODING = re.compile(
    r"\b(fix|bug|implement|code|function|class|python|javascript|typescript|"
    r"endpoint|api|refactor\s+this\s+file)\b",
    re.I,
)
_TEST = re.compile(r"\b(test|unittest|pytest|coverage|assert|regression)\b", re.I)
_ARCH = re.compile(
    r"\b(architect|architecture|cross[- ]module|system\s+design|migrate\s+the\s+whole|"
    r"redesign|multi[- ]repo|large\s+refactor|entire\s+(codebase|module|system))\b",
    re.I,
)
_REFACTOR = re.compile(r"\b(refactor|restructure|rewrite|overhaul)\b", re.I)
_RISK = re.compile(
    r"\b(production|live\s+write|destructive|delete\s+all|drop\s+table|credential|"
    r"secret|migration|breaking\s+change)\b",
    re.I,
)


def _clamp(n: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(n)))


def _file_estimate(prompt: str) -> int:
    paths = re.findall(r"\b[\w./\\-]+\.(?:py|js|ts|tsx|css|html|yaml|yml|sql|md)\b", prompt, re.I)
    if paths:
        return max(1, len(set(p.lower() for p in paths)))
    m = re.search(r"\b(\d+)\s+files?\b", prompt, re.I)
    if m:
        return max(1, int(m.group(1)))
    m2 = re.search(r"\bacross\s+(\d+)\s+modules?\b", prompt, re.I)
    if m2:
        return max(3, int(m2.group(1)) * 3)
    if _ARCH.search(prompt) or (_REFACTOR.search(prompt) and len(prompt) > 180):
        return 12
    if _DHIS2.search(prompt) or _SQL.search(prompt):
        return 4
    if _CSS.search(prompt):
        return 2
    if _LOOKUP.search(prompt):
        return 1
    return 2 if len(prompt) > 240 else 1


def classify_prompt(
    prompt: str,
    *,
    hints: Iterable[str] | None = None,
    repository_ids: list[str] | None = None,
) -> PromptClassification:
    text = (prompt or "").strip()
    signals: list[str] = []
    if hints:
        signals.extend(str(h) for h in hints if str(h).strip())

    scope = detect_prompt_scope(text, repository_ids=repository_ids)
    data_intent = detect_data_query_intent(text)
    signals.append(f"scope:{scope.kind}")
    signals.extend(f"scope_signal:{s}" for s in scope.signals[:6])
    if data_intent.is_data_query:
        signals.append("data_query")
        signals.extend(s for s in data_intent.signals if s not in signals)

    needs_testing = bool(_TEST.search(text))
    needs_architecture = bool(_ARCH.search(text))
    needs_coding = bool(_CODING.search(text) or _CSS.search(text) or _REFACTOR.search(text))
    # Language-name mentions inside explain/what-is GK prompts are not coding tasks.
    if scope.kind in {SCOPE_GK, SCOPE_WEB} and not data_intent.is_data_query and not re.search(
        r"\b(fix|bug|implement|refactor|endpoint|pull\s+request|codebase)\b",
        text,
        re.I,
    ):
        needs_coding = bool(_CSS.search(text) or _REFACTOR.search(text))

    is_data_lookup = bool(data_intent.is_data_query and scope.try_deterministic_tools)
    is_project_lookup = (
        bool(_PROJECT_LOOKUP.search(text)) and scope.try_deterministic_tools
    ) or is_data_lookup
    is_lookup = (
        bool(_LOOKUP.search(text)) or is_project_lookup or is_data_lookup
    ) and not needs_architecture
    is_css = bool(_CSS.search(text))
    is_sql = bool(_SQL.search(text))
    is_dhis2 = (
        bool(_DHIS2.search(text))
        or is_data_lookup
        or (
            is_project_lookup
            and scope.kind in {SCOPE_PROJECT, SCOPE_DHIS2, "ambiguous"}
        )
    )
    is_refactor = bool(_REFACTOR.search(text))
    # Never treat authoritative data questions as simple GK.
    is_simple_gk = (
        scope.kind in {SCOPE_GK, SCOPE_WEB}
        and not needs_coding
        and not needs_architecture
        and not data_intent.is_data_query
    )

    if needs_architecture or (is_refactor and _file_estimate(text) >= 8):
        task_type = "architecture" if needs_architecture else "refactor"
        signals.append("architecture_or_large_refactor")
    elif is_dhis2 and (is_sql or "investigat" in text.lower() or "debug" in text.lower()):
        task_type = "dhis2_investigation"
        signals.append("dhis2_investigation")
    elif is_sql and not is_lookup:
        task_type = "sql_investigation"
        signals.append("sql_investigation")
    elif is_css and not needs_architecture:
        task_type = "css_ui"
        signals.append("css_ui")
    elif is_lookup and not needs_coding and not is_simple_gk:
        task_type = "lookup"
        signals.append("simple_lookup")
        if is_data_lookup:
            signals.append("structured_data_lookup")
        if (is_project_lookup or is_data_lookup) and scope.requires_project_evidence:
            signals.append("project_lookup")
            signals.append("project_grounding_required")
        elif (is_project_lookup or is_data_lookup) and scope.allow_general_knowledge:
            signals.append("national_or_general_lookup")
    elif needs_testing and not needs_architecture:
        task_type = "testing"
        signals.append("testing")
    elif needs_coding:
        task_type = "coding"
        signals.append("coding")
    else:
        task_type = "general"
        signals.append("general")
        if is_simple_gk:
            signals.append("simple_general_knowledge")

    if scope.requires_project_evidence and "project_grounding_required" not in signals:
        if task_type in {"lookup", "general", "dhis2_investigation"} and not needs_coding:
            signals.append("project_grounding_required")

    if is_dhis2 or (is_project_lookup and scope.kind != SCOPE_GK) or is_data_lookup:
        signals.append("dhis2_or_ou_topic")

    # Attach compact filter hints for downstream tools/context (not answers).
    for key in ("location", "period", "population_group", "status", "environment"):
        val = data_intent.filters.get(key)
        if val:
            signals.append(f"filter:{key}")

    scope_est = _file_estimate(text)
    complexity = 15
    if "simple_general_knowledge" in signals or (
        task_type == "general" and scope.kind in {SCOPE_GK, SCOPE_WEB} and len(text) < 280
    ):
        complexity = 12
        needs_coding = False
    elif task_type == "lookup":
        complexity = 8 + min(12, len(text) // 80)
    elif task_type == "css_ui":
        complexity = 22 + min(20, scope_est * 5)
    elif task_type in {"sql_investigation", "dhis2_investigation"}:
        complexity = 45 + min(25, scope_est * 4)
        needs_coding = True
    elif task_type == "testing":
        complexity = 40 + min(20, scope_est * 3)
    elif task_type == "coding":
        complexity = 35 + min(30, scope_est * 4)
    elif task_type == "refactor":
        complexity = 65 + min(25, scope_est * 2)
        needs_architecture = needs_architecture or scope_est >= 8
    elif task_type == "architecture":
        complexity = 80 + min(15, scope_est)
    else:
        complexity = 25 + min(20, len(text) // 100)

    if _RISK.search(text):
        signals.append("risk_keyword")
        complexity = _clamp(complexity + 10)

    complexity = _clamp(complexity)

    if complexity >= 70 or needs_architecture or _RISK.search(text):
        risk = "high"
    elif task_type == "lookup" or "simple_general_knowledge" in signals:
        risk = "low"
    elif complexity >= 40 or is_sql or (is_dhis2 and scope.requires_project_evidence):
        risk = "medium"
    else:
        risk = "low"

    if scope_est <= 2 and len(text) < 400:
        context_size = "small"
    elif scope_est <= 8 and len(text) < 2000:
        context_size = "medium"
    else:
        context_size = "large"

    # Data / OU / region lookups are T0-first.
    if (is_project_lookup or is_data_lookup) and task_type == "lookup":
        needs_coding = False
        complexity = min(complexity, 20)
        risk = "low"

    deterministic_capable = (
        task_type == "lookup"
        and risk in {"low", "medium"}
        and not needs_architecture
        and not needs_coding
        and complexity < 35
        and scope.try_deterministic_tools
    )
    # Pure general knowledge never claims T0 capability.
    if "simple_general_knowledge" in signals or (
        scope.kind in {SCOPE_GK, SCOPE_WEB}
        and not scope.try_deterministic_tools
        and not data_intent.is_data_query
    ):
        deterministic_capable = False
    if data_intent.is_data_query and task_type == "lookup":
        deterministic_capable = True
        signals.append("authoritative_data_query")
    if deterministic_capable:
        signals.append("deterministic_capable")
    if scope.allow_general_knowledge and not data_intent.is_data_query:
        signals.append("allow_general_knowledge")
    elif scope.allow_general_knowledge and data_intent.is_data_query and not scope.requires_project_evidence:
        signals.append("allow_general_knowledge")
    return PromptClassification(
        task_type=task_type,
        complexity=complexity,
        risk=risk,
        estimated_scope_files=scope_est,
        context_size=context_size,
        needs_coding=needs_coding,
        needs_testing=needs_testing,
        needs_architecture=needs_architecture or task_type == "architecture",
        deterministic_capable=deterministic_capable,
        signals=list(dict.fromkeys(signals)),
    )
