"""Dynamic completion contracts for AiriX deterministic (T0) answers.

Evidence discovery is not completion. Each prompt yields an intent-specific
contract; T0 may finish only when the required output type is satisfied.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from hub.agent_center.data_intent import detect_data_query_intent, extract_data_filters

INTENT_COUNT = "count"
INTENT_LIST = "list"
INTENT_LOOKUP = "lookup"
INTENT_STATUS = "status"
INTENT_METADATA = "metadata"
INTENT_COMPARISON = "comparison"
INTENT_TRACE = "trace"
INTENT_EXPLANATION = "explanation"
INTENT_FILE_SEARCH = "file_search"
INTENT_GENERAL = "general"

_COUNT = re.compile(
    r"\b(count(?:s|ing)?|how\s+many|total(?:s)?|number\s+of|sum|numerator|denominator|"
    r"percent(?:age)?s?|coverage|rate(?:s)?)\b",
    re.I,
)
_LIST = re.compile(r"\b(list|show\s+(me\s+)?(all|the)?|enumerate|what\s+are)\b", re.I)
_LOOKUP = re.compile(
    r"\b(look\s*up|lookup|find\s+(the\s+)?(uid|id|value|name)|what\s+is\s+the|"
    r"uid\s+for|resolve)\b",
    re.I,
)
_STATUS = re.compile(
    r"\b(status(?:es)?|approved|rejected|pending|state\s+of|is\s+it\s+(active|approved))\b",
    re.I,
)
_METADATA = re.compile(
    r"\b(metadata|schema|fields?|columns?|data\s*element|program\s+indicator|"
    r"attribute(?:s)?|properties)\b",
    re.I,
)
_COMPARISON = re.compile(
    r"\b(compar(?:e|ison)|versus|vs\.?|difference\s+between|against)\b",
    re.I,
)
_TRACE = re.compile(
    r"\b(trace|lineage|where\s+(does|is)|comes?\s+from|source\s+of|derived\s+from|"
    r"relationship|path\s+to)\b",
    re.I,
)
_EXPLAIN = re.compile(
    r"\b(explain|why|how\s+does|walk\s+me\s+through|describe\s+how)\b",
    re.I,
)
_FILE_SEARCH = re.compile(
    r"\b(find\s+(the\s+)?file|search\s+(the\s+)?(repo|code|codebase)|where\s+is\s+the\s+file|"
    r"which\s+file|repo\s+search|path\s+to\s+the)\b",
    re.I,
)

_NUMERIC_RESULT = re.compile(
    r"(?i)(?:^|\n)\s*(?:[-*]\s*)?(?:count|total|result|value|n)\s*[:=]\s*(\d+(?:\.\d+)?)"
    r"|(?:^|\n)\s*(?:[-*]\s*)?(\d+(?:\.\d+)?)\s*(?:%|percent|people|women|children|"
    r"households?|beneficiar(?:y|ies)|records?|rows?)?\s*$"
    r"|\b(?:is|are|=|:)\s*(\d+(?:\.\d+)?)\b",
)
_YEAR_ONLY = re.compile(r"^(19|20)\d{2}$")
_STATUS_VALUE = re.compile(
    r"\b(approved|rejected|pending|draft|active|inactive|eligible|final(?:ized)?|"
    r"available|unavailable|succeeded|failed|completed)\b",
    re.I,
)
_PATH_LIKE = re.compile(
    r"\b[\w./\\-]+\.(?:py|js|ts|tsx|css|html|yaml|yml|sql|md|json)\b"
    r"|[/\\][\w./\\-]{3,}",
    re.I,
)
_ITEM_LINE = re.compile(r"(?m)^\s*[-*]\s+\S+")
_DISCOVERY_DUMP = re.compile(
    r"(?i)(selected-repository matches|deterministic lookup for|"
    r"open these paths for project facts|evidence packet|"
    r"tool issues:|no usable project evidence)",
)
_UID_IN_ANSWER = re.compile(r"\b([A-Za-z](?=[A-Za-z0-9]*\d)[A-Za-z0-9]{10})\b")


@dataclass(frozen=True)
class CompletionContract:
    intent: str
    required_output: str
    filters: dict[str, Any] = field(default_factory=dict)
    authoritative_sources: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompletionResult:
    evidence_found: bool
    task_solved: bool
    answer_grounded: bool
    intent: str
    required_output: str
    reason: str
    missing: tuple[str, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _filters_for_prompt(prompt: str) -> dict[str, Any]:
    data = detect_data_query_intent(prompt)
    filters = dict(data.filters or {})
    # Preserve search terms as soft constraints (not answers).
    if data.search_terms:
        filters["search_terms"] = list(data.search_terms)
    if data.entity_types:
        filters["entity_types"] = list(data.entity_types)
    return filters


def derive_completion_contract(
    prompt: str,
    *,
    classification_signals: list[str] | None = None,
) -> CompletionContract:
    """Derive intent + required output + criteria from prompt structure/meaning."""
    text = (prompt or "").strip()
    filters = _filters_for_prompt(text) if text else {}
    signals = {str(s) for s in (classification_signals or [])}

    if _COMPARISON.search(text):
        intent = INTENT_COMPARISON
        required = "evidence_or_results_for_all_compared_sides"
        sources = ("repo_search", "sql_lookup", "uid_lookup", "dhis2_reports_lookup")
        criteria = ("mentions_multiple_sides", "evidence_backed")
        reason = "Comparison requires results/evidence for each compared side."
    elif _COUNT.search(text) or "authoritative_data_query" in signals or "data_query" in signals:
        # Counts/totals/rates need a verified numeric (or n/d) result — not file hits.
        if _COUNT.search(text) or filters.get("period") or filters.get("population_group"):
            intent = INTENT_COUNT
            required = "verified_numeric_value"
            sources = ("dhis2_reports_lookup", "sql_lookup", "org_unit_lookup", "uid_lookup")
            criteria = ("numeric_result", "filters_respected", "authoritative_source")
            reason = "Count/total/rate questions require a verified numeric result."
        elif _LIST.search(text):
            intent = INTENT_LIST
            required = "matching_item_list"
            sources = ("org_unit_lookup", "uid_lookup", "repo_search", "sql_lookup")
            criteria = ("item_list", "filters_respected")
            reason = "List questions require actual matching items."
        else:
            intent = INTENT_COUNT
            required = "verified_numeric_value"
            sources = ("dhis2_reports_lookup", "sql_lookup", "org_unit_lookup")
            criteria = ("numeric_result", "authoritative_source")
            reason = "Structured data intent requires a verified value, not discovery alone."
    elif _FILE_SEARCH.search(text):
        intent = INTENT_FILE_SEARCH
        required = "relevant_file_or_path_matches"
        sources = ("repo_search", "read_file")
        criteria = ("path_matches",)
        reason = "File search requires relevant path matches."
    elif _TRACE.search(text):
        intent = INTENT_TRACE
        required = "resolved_source_path_or_relationship"
        sources = ("repo_search", "sql_lookup", "uid_lookup", "dhis2_reports_lookup")
        criteria = ("path_or_relationship", "evidence_backed")
        reason = "Trace questions require a resolved source/path/relationship."
    elif _STATUS.search(text) and not _EXPLAIN.search(text):
        intent = INTENT_STATUS
        required = "actual_status_value"
        sources = ("jobs_lookup", "audit_lookup", "sql_lookup", "dhis2_reports_lookup")
        criteria = ("status_value",)
        reason = "Status questions require an actual status value."
    elif _METADATA.search(text):
        intent = INTENT_METADATA
        required = "requested_metadata_fields"
        sources = ("uid_lookup", "sql_lookup", "repo_search", "dhis2_reports_lookup")
        criteria = ("metadata_fields",)
        reason = "Metadata questions require the requested fields/values."
    elif _EXPLAIN.search(text):
        # Prefer explanation over list/lookup so "explain … list comprehension" is not a list.
        intent = INTENT_EXPLANATION
        required = "evidence_backed_explanation"
        sources = ("repo_search", "read_file", "sql_lookup", "notebook_lookup")
        criteria = ("explanatory_answer", "evidence_backed")
        reason = "Explanations require evidence-backed reasoning (T0 rarely completes alone)."
    elif _LOOKUP.search(text) or "simple_lookup" in signals:
        intent = INTENT_LOOKUP
        required = "requested_value_or_entity"
        sources = ("uid_lookup", "org_unit_lookup", "notebook_lookup", "repo_search")
        criteria = ("entity_or_value",)
        reason = "Lookup requires the requested value/entity."
    elif _LIST.search(text):
        intent = INTENT_LIST
        required = "matching_item_list"
        sources = ("org_unit_lookup", "uid_lookup", "repo_search", "jobs_lookup")
        criteria = ("item_list",)
        reason = "List questions require actual matching items."
    else:
        intent = INTENT_GENERAL
        required = "direct_answer_or_cannot_verify"
        sources = ("notebook_lookup", "repo_search", "uid_lookup")
        criteria = ("non_discovery_answer",)
        reason = "General prompt — discovery dumps are not a final answer."

    return CompletionContract(
        intent=intent,
        required_output=required,
        filters=filters,
        authoritative_sources=tuple(sources),
        completion_criteria=tuple(criteria),
        reason=reason,
    )


def _evidence_found(packet: dict[str, Any] | None) -> bool:
    if not isinstance(packet, dict):
        return False
    if packet.get("usable"):
        return True
    hits = packet.get("hits") or []
    tools = packet.get("tool_results") or []
    return bool(hits) or any(isinstance(t, dict) and t.get("ok") for t in tools)


def _sources(packet: dict[str, Any] | None) -> list[str]:
    if not isinstance(packet, dict):
        return []
    out: list[str] = []
    for src in packet.get("sources") or []:
        text = str(src or "").strip()
        if text:
            out.append(text)
    for hit in packet.get("hits") or []:
        if isinstance(hit, dict) and hit.get("source"):
            out.append(str(hit.get("source")))
    return list(dict.fromkeys(out))


def _extract_numeric_candidates(answer: str) -> list[str]:
    found: list[str] = []
    for m in _NUMERIC_RESULT.finditer(answer or ""):
        for g in m.groups():
            if g and not _YEAR_ONLY.match(g):
                found.append(g)
    # Also accept plain integers that are not years when explicitly labeled nearby.
    for m in re.finditer(
        r"(?i)\b(?:count|total|n|numerator|denominator|result)\b[^0-9]{0,12}(\d+(?:\.\d+)?)",
        answer or "",
    ):
        val = m.group(1)
        if not _YEAR_ONLY.match(val):
            found.append(val)
    return list(dict.fromkeys(found))


def _has_item_list(answer: str) -> bool:
    text = answer or ""
    if _ITEM_LINE.findall(text):
        return True
    # Prose enumerations ("includes A, B, and C") count for list shape checks.
    if re.search(
        r"(?i)\b(?:include|includes|including|are|comprising|consist(?:s|ing)?\s+of)\b[^.]{8,}",
        text,
    ) and text.count(",") >= 1:
        return True
    return False


def _is_discovery_only(answer: str, *, intent: str = "") -> bool:
    text = (answer or "").strip()
    if not text:
        return True
    # File/trace intents may legitimately return path match lists.
    if intent in {INTENT_FILE_SEARCH, INTENT_TRACE} and _PATH_LIKE.search(text):
        return False
    if _DISCOVERY_DUMP.search(text):
        return True
    if text.lower().startswith("deterministic lookup for:"):
        return True
    return False


def _cannot_verify(answer: str) -> bool:
    return bool(re.search(r"cannot\s+verify\s+from\s+selected\s+context", answer or "", re.I))


def validate_completion(
    contract: CompletionContract,
    *,
    prompt: str,
    answer: str,
    evidence: dict[str, Any] | None,
    require_authoritative_evidence: bool = True,
) -> CompletionResult:
    """
    Validate whether the answer satisfies the completion contract.

    evidence_found  — Hub tools returned usable related hits
    task_solved     — required output type is present
    answer_grounded — final answer is supported by authoritative evidence

    When require_authoritative_evidence is False (non-project scopes), an answer
    with the right output shape may be task_solved without being grounded.
    """
    text = (answer or "").strip()
    evidence_found = _evidence_found(evidence)
    sources = tuple(_sources(evidence))
    missing: list[str] = []
    intent = contract.intent
    solved = False
    grounded = False
    need_auth = bool(require_authoritative_evidence)

    if _cannot_verify(text):
        # Honest incomplete answer — not solved, not grounded.
        return CompletionResult(
            evidence_found=evidence_found,
            task_solved=False,
            answer_grounded=False,
            intent=intent,
            required_output=contract.required_output,
            reason="Cannot verify — completion contract not satisfied.",
            missing=tuple(contract.completion_criteria),
            filters=dict(contract.filters),
            sources=sources,
        )

    discovery_only = _is_discovery_only(text, intent=intent)

    def _accept_shape(*, shape_ok: bool, missing_key: str) -> None:
        nonlocal solved, grounded
        if not shape_ok or discovery_only:
            missing.append(missing_key)
            return
        if evidence_found:
            solved = True
            grounded = True
            return
        if not need_auth:
            # Model-knowledge / national-general answers may solve without Hub evidence.
            solved = True
            grounded = False
            return
        missing.append(missing_key)
        missing.append("authoritative_source")

    if intent == INTENT_COUNT:
        nums = _extract_numeric_candidates(text)
        _accept_shape(shape_ok=bool(nums), missing_key="verified_numeric_value")
    elif intent == INTENT_LIST:
        repo_dump = bool(re.search(r"(?i)selected-repository matches", text))
        ou_list = bool(
            re.search(r"(?i)organisation units from selected|org(?:anisation)? units?", text)
        )
        codeish = bool(re.search(r"(?i)\.(py|js|sql|md|yaml)\b", text))
        shape_ok = _has_item_list(text) and not repo_dump and (ou_list or not codeish or not discovery_only)
        if discovery_only and repo_dump:
            shape_ok = False
        _accept_shape(shape_ok=shape_ok, missing_key="matching_item_list")
    elif intent == INTENT_FILE_SEARCH:
        paths = _PATH_LIKE.findall(text) or [
            str(h.get("path") or "")
            for h in (evidence or {}).get("hits") or []
            if isinstance(h, dict) and h.get("path")
        ]
        paths = [p for p in paths if str(p).strip()]
        _accept_shape(shape_ok=bool(paths), missing_key="relevant_file_or_path_matches")
    elif intent == INTENT_LOOKUP:
        has_entity = bool(_UID_IN_ANSWER.search(text) or _has_item_list(text))
        if not has_entity and re.search(r"(?i)\b(uid|id|name|value)\b\s*[:=]\s*\S+", text):
            has_entity = True
        if discovery_only and re.search(r"(?i)selected-repository matches", text):
            has_entity = False
        _accept_shape(shape_ok=has_entity, missing_key="requested_value_or_entity")
    elif intent == INTENT_STATUS:
        _accept_shape(
            shape_ok=bool(_STATUS_VALUE.search(text)),
            missing_key="actual_status_value",
        )
    elif intent == INTENT_METADATA:
        _accept_shape(
            shape_ok=bool(re.search(r"(?i)\b\w+\s*[:=]\s*\S+", text)),
            missing_key="requested_metadata_fields",
        )
    elif intent == INTENT_COMPARISON:
        sides = re.findall(
            r"\b(vs\.?|versus|compared\s+to|against|difference)\b",
            text,
            re.I,
        )
        _accept_shape(
            shape_ok=bool(text and (sides or len(text) > 80)),
            missing_key="evidence_or_results_for_all_compared_sides",
        )
    elif intent == INTENT_TRACE:
        has_path = bool(_PATH_LIKE.search(text)) or any(
            str(h.get("path") or "").strip()
            for h in (evidence or {}).get("hits") or []
            if isinstance(h, dict)
        )
        has_rel = bool(
            re.search(r"(?i)\b(source|lineage|derived|from|path|relationship)\b", text)
        )
        _accept_shape(
            shape_ok=bool(has_path or has_rel),
            missing_key="resolved_source_path_or_relationship",
        )
    elif intent == INTENT_EXPLANATION:
        _accept_shape(
            shape_ok=bool(
                text
                and len(text) >= 40
                and not text.lower().startswith("deterministic lookup")
            ),
            missing_key="evidence_backed_explanation",
        )
    else:  # general
        if text and not discovery_only and evidence_found:
            solved = True
            grounded = True
        elif text and not discovery_only and not need_auth:
            # GK-style answer — not grounded in Hub evidence.
            solved = True
            grounded = False
        else:
            missing.append("direct_answer")

    if solved and grounded:
        reason = "Completion contract satisfied by authoritative evidence."
    elif solved and not grounded:
        reason = "Answer present but not grounded in authoritative Hub evidence."
    elif evidence_found:
        reason = (
            f"Evidence found but task unsolved for intent={intent} "
            f"(need {contract.required_output})."
        )
    else:
        reason = f"No usable evidence for intent={intent}; need {contract.required_output}."

    return CompletionResult(
        evidence_found=evidence_found,
        task_solved=solved,
        answer_grounded=bool(solved and grounded),
        intent=intent,
        required_output=contract.required_output,
        reason=reason,
        missing=tuple(dict.fromkeys(missing)),
        filters=dict(contract.filters),
        sources=sources,
    )


def merge_completion_into_grounding(
    grounding: dict[str, Any] | None,
    completion: CompletionResult,
) -> dict[str, Any]:
    """Attach evidence_found / task_solved / answer_grounded to a grounding status dict."""
    out = dict(grounding or {})
    out["evidence_found"] = bool(completion.evidence_found)
    out["evidence_found_label"] = "Yes" if completion.evidence_found else "No"
    out["task_solved"] = bool(completion.task_solved)
    out["task_solved_label"] = "Yes" if completion.task_solved else "No"
    out["answer_grounded"] = bool(completion.answer_grounded)
    # Authoritative grounded flag follows completion contract.
    out["grounded"] = bool(completion.answer_grounded)
    out["grounded_label"] = "Yes" if completion.answer_grounded else "No"
    out["completion_intent"] = completion.intent
    out["required_output"] = completion.required_output
    out["completion_reason"] = completion.reason
    out["completion_missing"] = list(completion.missing)
    out["completion_filters"] = dict(completion.filters)
    if completion.sources and not out.get("source"):
        out["source"] = ", ".join(completion.sources)
    elif completion.sources:
        # Prefer explicit tool sources list for UI.
        out["sources_used"] = list(completion.sources)
    else:
        out["sources_used"] = [
            s.strip()
            for s in str(out.get("source") or "").split(",")
            if s.strip()
        ]
    if not completion.task_solved and completion.evidence_found:
        out["cannot_verify"] = True
        out["reason"] = completion.reason
        out["policy_violation"] = False
    return out
