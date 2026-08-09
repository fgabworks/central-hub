"""Heuristic prompt classifier for AiriX Smart Routing (Phase 1 — no LLM calls)."""

from __future__ import annotations

import re
from typing import Iterable

from hub.agent_center.routing.models import PromptClassification


_LOOKUP = re.compile(
    r"\b("
    r"look\s*up|lookup|find\s+uid|what\s+is|"
    r"show\s+(me\s+)?(recent\s+)?|"
    r"list\s+(open|recent)|"
    r"search\s+(notes?|email|audit|jobs?)|"
    r"status\s+of|"
    r"recent\s+(dhis2\s+)?(jobs?|logs?|statuses?)|"
    r"(jobs?|logs?)\s+and\s+statuses?"
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
_FILE_HINT = re.compile(
    r"(\b[\w./\\-]+\.(py|js|ts|tsx|css|html|yaml|yml|sql|md)\b)|"
    r"\b(\d+)\s+files?\b|"
    r"\bacross\s+(\d+)\s+modules?\b",
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


def classify_prompt(prompt: str, *, hints: Iterable[str] | None = None) -> PromptClassification:
    text = (prompt or "").strip()
    signals: list[str] = []
    if hints:
        signals.extend(str(h) for h in hints if str(h).strip())

    needs_testing = bool(_TEST.search(text))
    needs_architecture = bool(_ARCH.search(text))
    needs_coding = bool(_CODING.search(text) or _CSS.search(text) or _REFACTOR.search(text))
    is_lookup = bool(_LOOKUP.search(text)) and not needs_architecture
    is_css = bool(_CSS.search(text))
    is_sql = bool(_SQL.search(text))
    is_dhis2 = bool(_DHIS2.search(text))
    is_refactor = bool(_REFACTOR.search(text))

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
    elif is_lookup and not needs_coding:
        task_type = "lookup"
        signals.append("simple_lookup")
    elif needs_testing and not needs_architecture:
        task_type = "testing"
        signals.append("testing")
    elif needs_coding:
        task_type = "coding"
        signals.append("coding")
    else:
        task_type = "general"
        signals.append("general")

    scope = _file_estimate(text)
    complexity = 15
    if task_type == "lookup":
        complexity = 8 + min(12, len(text) // 80)
    elif task_type == "css_ui":
        complexity = 22 + min(20, scope * 5)
    elif task_type in {"sql_investigation", "dhis2_investigation"}:
        complexity = 45 + min(25, scope * 4)
        needs_coding = True
    elif task_type == "testing":
        complexity = 40 + min(20, scope * 3)
    elif task_type == "coding":
        complexity = 35 + min(30, scope * 4)
    elif task_type == "refactor":
        complexity = 65 + min(25, scope * 2)
        needs_architecture = needs_architecture or scope >= 8
    elif task_type == "architecture":
        complexity = 80 + min(15, scope)
    else:
        complexity = 25 + min(20, len(text) // 100)

    if _RISK.search(text):
        signals.append("risk_keyword")
        complexity = _clamp(complexity + 10)

    complexity = _clamp(complexity)

    if complexity >= 70 or needs_architecture or _RISK.search(text):
        risk = "high"
    elif task_type == "lookup":
        risk = "low"
    elif complexity >= 40 or is_sql or is_dhis2:
        risk = "medium"
    else:
        risk = "low"

    if scope <= 2 and len(text) < 400:
        context_size = "small"
    elif scope <= 8 and len(text) < 2000:
        context_size = "medium"
    else:
        context_size = "large"

    deterministic_capable = (
        task_type == "lookup"
        and risk in {"low", "medium"}
        and not needs_architecture
        and not needs_coding
        and complexity < 35
    )
    if deterministic_capable:
        signals.append("deterministic_capable")
    return PromptClassification(
        task_type=task_type,
        complexity=complexity,
        risk=risk,
        estimated_scope_files=scope,
        context_size=context_size,
        needs_coding=needs_coding,
        needs_testing=needs_testing,
        needs_architecture=needs_architecture or task_type == "architecture",
        deterministic_capable=deterministic_capable,
        signals=signals,
    )
