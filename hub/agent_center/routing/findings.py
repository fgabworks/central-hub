"""Compact prior findings for AiriX Smart Routing Phase 5.

Light semantic/relevance matching without embedding models or heavy deps.
Never injects full chats or raw provider dumps.
"""

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

# Domain aliases expand query tokens for light semantic overlap (no embeddings).
_ALIASES: dict[str, frozenset[str]] = {
    "dhis2": frozenset({"dhis", "orgunit", "organisation", "analytics", "indicator", "uid"}),
    "uid": frozenset({"identifier", "orgunit", "dhis2"}),
    "sql": frozenset({"query", "join", "select", "table", "database", "postgres"}),
    "query": frozenset({"sql", "select", "join"}),
    "indicator": frozenset({"dhis2", "numerator", "denominator", "coverage"}),
    "css": frozenset({"style", "stylesheet", "padding", "layout", "ui"}),
    "ui": frozenset({"css", "playwright", "button", "layout", "frontend"}),
    "playwright": frozenset({"browser", "ui", "e2e", "selenium"}),
    "repo": frozenset({"repository", "codebase", "git", "module"}),
    "repository": frozenset({"repo", "codebase", "git"}),
    "refactor": frozenset({"architecture", "rewrite", "migrate"}),
    "architecture": frozenset({"design", "refactor", "module", "boundary"}),
}


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


def expand_keywords(keywords: set[str] | list[str], *, limit: int = 40) -> set[str]:
    out: set[str] = set()
    for kw in keywords:
        k = str(kw).lower().strip()
        if not k:
            continue
        out.add(k)
        for alias in _ALIASES.get(k, frozenset()):
            out.add(alias)
        # Light stem: share 4+ char prefixes with domain terms.
        if len(k) >= 4:
            out.add(k[:4])
        if len(out) >= limit:
            break
    return out


def compact_finding_summary(answer: str, *, limit: int = 200) -> str:
    """One short sanitized finding — never a full conversation dump."""
    cleaned = redact_text((answer or "").strip(), limit=800)
    if not cleaned:
        return ""
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
    grounding_scope: str = "",
) -> list[dict[str, Any]]:
    summary = compact_finding_summary(answer)
    if not summary or len(summary) < 12:
        return []
    keywords = extract_keywords(f"{prompt} {summary} {task_type}")
    scope = grounding_scope
    if not scope and prompt:
        from hub.agent_center.scope import detect_prompt_scope

        scope = detect_prompt_scope(prompt).kind
    return [
        {
            "task_type": task_type or "general",
            "keywords": keywords[:10],
            "summary": summary,
            "provider_id": provider_id or "",
            "source_event_id": source_event_id or "",
            "grounding_scope": scope or "",
        }
    ][:max_findings]


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    s = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter <= 0:
        return 0.0
    return inter / float(len(a | b))


def score_finding_relevance(
    finding: dict[str, Any],
    *,
    prompt: str,
    classification: PromptClassification,
) -> float:
    """
    Lightweight relevance score (higher is better).

    Combines keyword/alias overlap, task-type match, token Jaccard, and
    character trigram similarity on the compact summary — no embeddings.
    """
    prompt_kw = set(extract_keywords(prompt, limit=24))
    prompt_kw.update(extract_keywords(" ".join(classification.signals), limit=12))
    prompt_kw.update(extract_keywords(classification.task_type, limit=4))
    query = expand_keywords(prompt_kw)

    keys = {str(k).lower() for k in (finding.get("keywords") or []) if str(k).strip()}
    keys = expand_keywords(keys)
    summary = str(finding.get("summary") or "")
    summary_kw = expand_keywords(extract_keywords(summary, limit=20))

    overlap = len(query & keys) + 0.5 * len(query & summary_kw)
    score = float(overlap)

    ft = str(finding.get("task_type") or "")
    if ft == classification.task_type:
        score += 1.5
    elif ft == "general":
        score += 0.25
    else:
        # Different specialized task — require stronger textual overlap.
        score -= 0.75

    token_j = _jaccard(query, keys | summary_kw)
    score += 2.0 * token_j

    gram_j = _jaccard(_char_ngrams(prompt), _char_ngrams(summary))
    score += 1.5 * gram_j

    # Soft boost for hit_count (previously useful) without dominating.
    try:
        hits = int(finding.get("hit_count") or 0)
    except (TypeError, ValueError):
        hits = 0
    score += min(0.5, 0.05 * max(0, hits))

    return round(score, 4)


def select_relevant_findings(
    findings: list[dict[str, Any]],
    *,
    prompt: str,
    classification: PromptClassification,
    max_items: int = 3,
    min_score: float = 1.25,
) -> list[dict[str, Any]]:
    """Keep a small set of compact, relevant findings (never full chats)."""
    from hub.agent_center.scope import detect_prompt_scope, scopes_compatible

    current_scope = ""
    for sig in classification.signals or []:
        if str(sig).startswith("scope:") and not str(sig).startswith("scope_signal:"):
            current_scope = str(sig).split(":", 1)[1]
            break
    if not current_scope:
        current_scope = detect_prompt_scope(prompt).kind

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in findings:
        prior_scope = str(row.get("grounding_scope") or row.get("scope") or "").strip()
        if prior_scope and not scopes_compatible(current_scope, prior_scope):
            continue
        ft = str(row.get("task_type") or "")
        if ft not in {classification.task_type, "general", ""}:
            # Still allow cross-type if semantic score clears a higher bar later.
            pass
        score = score_finding_relevance(row, prompt=prompt, classification=classification)
        threshold = min_score
        if ft and ft not in {classification.task_type, "general"}:
            threshold = min_score + 0.75
        if score < threshold:
            continue
        scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("created_at") or "")))
    out: list[dict[str, Any]] = []
    for score, row in scored[:max_items]:
        out.append(
            {
                "id": row.get("id"),
                "task_type": row.get("task_type"),
                "summary": redact_text(str(row.get("summary") or ""), limit=200),
                "provider_id": row.get("provider_id") or "",
                "keywords": list(row.get("keywords") or [])[:8],
                "relevance_score": score,
                "reused": True,
                "grounding_scope": row.get("grounding_scope") or row.get("scope") or "",
            }
        )
    return out
