"""Compact prior findings for AiriX Smart Routing Phase 3."""

from __future__ import annotations

import re
from typing import Any

from hub.agent_center.redact import redact_text
from hub.agent_center.routing.models import PromptClassification

_WORD = re.compile(r"[a-z0-9_]{3,}", re.I)
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "look",
        "show",
        "find",
        "please",
        "what",
        "when",
        "where",
        "which",
        "have",
        "been",
        "will",
        "your",
        "about",
        "just",
        "like",
        "need",
        "make",
        "using",
        "used",
        "also",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "some",
        "only",
        "into",
        "over",
        "under",
        "after",
        "before",
        "because",
        "should",
        "could",
        "would",
        "does",
        "did",
        "are",
        "was",
        "were",
        "has",
        "had",
        "not",
        "but",
        "you",
        "our",
        "can",
        "may",
        "all",
        "any",
        "how",
        "why",
        "out",
        "get",
        "got",
        "run",
        "via",
    }
)


def extract_keywords(text: str, *, limit: int = 12) -> list[str]:
    words: list[str] = []
    for m in _WORD.finditer((text or "").lower()):
        w = m.group(0)
        if w in _STOP or w.isdigit():
            continue
        if w not in words:
            words.append(w)
        if len(words) >= limit:
            break
    return words


def compact_finding_summary(answer: str, *, limit: int = 200) -> str:
    """One short sanitized finding — never a full conversation dump."""
    cleaned = redact_text((answer or "").strip(), limit=800)
    if not cleaned:
        return ""
    # Prefer first non-empty line / sentence.
    line = cleaned.splitlines()[0].strip()
    for sep in (". ", "! ", "? "):
        if sep in line:
            line = line.split(sep, 1)[0].strip() + "."
            break
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def extract_findings_from_answer(
    *,
    answer: str,
    task_type: str,
    prompt: str = "",
    provider_id: str = "",
    source_event_id: str = "",
    max_findings: int = 2,
) -> list[dict[str, Any]]:
    summary = compact_finding_summary(answer)
    if not summary or len(summary) < 12:
        return []
    keywords = extract_keywords(f"{prompt} {summary} {task_type}")
    return [
        {
            "task_type": task_type or "general",
            "keywords": keywords[:10],
            "summary": summary,
            "provider_id": provider_id or "",
            "source_event_id": source_event_id or "",
        }
    ][:max_findings]


def select_relevant_findings(
    findings: list[dict[str, Any]],
    *,
    prompt: str,
    classification: PromptClassification,
    max_items: int = 3,
) -> list[dict[str, Any]]:
    """Keep only findings that share task type and keyword overlap."""
    prompt_kw = set(extract_keywords(prompt, limit=20))
    prompt_kw.update(extract_keywords(" ".join(classification.signals), limit=10))
    prompt_kw.update(extract_keywords(classification.task_type, limit=4))
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in findings:
        if str(row.get("task_type") or "") != classification.task_type:
            # Allow general findings only when keyword overlap is strong.
            if str(row.get("task_type") or "") not in {"general", classification.task_type}:
                continue
        keys = {str(k).lower() for k in (row.get("keywords") or []) if str(k).strip()}
        overlap = len(prompt_kw & keys)
        if classification.task_type == str(row.get("task_type") or ""):
            overlap += 1
        if overlap <= 0:
            continue
        scored.append((overlap, row))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("created_at") or "")), reverse=False)
    scored.sort(key=lambda x: -x[0])
    out: list[dict[str, Any]] = []
    for _score, row in scored[:max_items]:
        out.append(
            {
                "id": row.get("id"),
                "task_type": row.get("task_type"),
                "summary": redact_text(str(row.get("summary") or ""), limit=200),
                "provider_id": row.get("provider_id") or "",
                "keywords": list(row.get("keywords") or [])[:8],
            }
        )
    return out
