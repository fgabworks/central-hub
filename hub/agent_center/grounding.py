"""AiriX selected-context grounding — scope-aware evidence, no silent GK fallback.

Project / ambiguous prompts with a selected repository require Hub tool evidence.
Explicit national/general/web scope overrides the selected repo and may use model
knowledge. Scope detection lives in ``hub.agent_center.scope``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from hub.agent_center.openai_tools import AgentToolsContext, execute_tool
from hub.agent_center.scope import (
    SCOPE_GK,
    SCOPE_NATIONAL,
    SCOPE_WEB,
    PromptScope,
    detect_prompt_scope,
)

_LOOKUP_UNAVAILABLE = re.compile(
    r"("
    r"could\s+not\s+(access|reach|query|look\s*up)|"
    r"(repository|dhis2|uid\s*index|tool|lookup)\s+(is\s+)?(not\s+)?(available|unavailable|failed)|"
    r"without\s+access\s+to\s+(the\s+)?(repo|repository|dhis2|database)|"
    r"unable\s+to\s+(verify|look\s*up|query|access)|"
    r"no\s+(access|evidence|results)\s+(from|in)\s+(the\s+)?(selected\s+)?(repo|repository|dhis2|context)|"
    r"based\s+on\s+(general|my)\s+knowledge|"
    r"standard\s+philippine|"
    r"typically\s+include(?:s)?"
    r")",
    re.I,
)


def resolve_prompt_scope(
    prompt: str, *, repository_ids: list[str] | None = None
) -> PromptScope:
    """Public wrapper used by routing / execution."""
    return detect_prompt_scope(prompt, repository_ids=repository_ids)


def requires_project_grounding(prompt: str, *, repository_ids: list[str] | None = None) -> bool:
    """True when the prompt needs selected-context evidence (no silent GK)."""
    return resolve_prompt_scope(prompt, repository_ids=repository_ids).requires_project_evidence


def allows_general_knowledge(prompt: str, *, repository_ids: list[str] | None = None) -> bool:
    return resolve_prompt_scope(prompt, repository_ids=repository_ids).allow_general_knowledge


def is_project_lookup_prompt(prompt: str) -> bool:
    """OU / region / province style questions that T0 tools should try first."""
    scope = detect_prompt_scope(prompt, repository_ids=None)
    if not scope.try_deterministic_tools:
        return False
    text = (prompt or "").strip()
    if not text:
        return False
    return bool(
        re.search(
            r"\b("
            r"provinces?\s+(for|in|under)|"
            r"region\s+(i{1,3}|iv|v|vi{0,3}|\d+|iii)|"
            r"central\s+luzon|"
            r"org(?:anisation|anization)?\s*units?|"
            r"what\s+(are|is)\s+the\s+(provinces?|org)"
            r")\b",
            text,
            re.I,
        )
    )


def extract_search_terms(prompt: str) -> list[str]:
    """Compact search needles for tools (Region III, Central Luzon, quoted terms…)."""
    from hub.agent_center.data_intent import detect_data_query_intent

    text = (prompt or "").strip()
    terms: list[str] = []
    # Prefer dynamically extracted data-query filters (location, period, UIDs…).
    data_intent = detect_data_query_intent(text)
    terms.extend(data_intent.search_terms)
    for m in re.finditer(r"[\"']([^\"']{2,80})[\"']", text):
        terms.append(m.group(1).strip())
    for m in re.finditer(
        r"\b(Region\s+(?:III|II|I|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|\d+)(?:\s*[-–]\s*[A-Za-z ]+)?|"
        r"Central\s+Luzon|"
        r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b",
        text,
    ):
        candidate = m.group(1).strip()
        if len(candidate) >= 3 and candidate.lower() not in {"what", "the", "for", "are"}:
            terms.append(candidate)
    # Always include a cleaned full-ish query fallback.
    cleaned = re.sub(
        r"^(what\s+are|what\s+is|list|show|find|look\s*up|count|how\s+many|total)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    if cleaned:
        terms.append(cleaned[:120])
    # Dedupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:8]


def grounding_rules_text(
    *,
    repository_ids: list[str],
    requires: bool,
    scope: PromptScope | None = None,
) -> str:
    kind = scope.kind if scope is not None else ("project" if requires else SCOPE_GK)
    if not requires:
        return (
            "# Grounding policy\n"
            f"Detected scope: {kind}.\n"
            "General knowledge / national domain answers are allowed for this prompt. "
            "Do not restrict the answer to selected-repository evidence. "
            "If Hub tools returned useful evidence, you may cite it, but it is not required."
        )
    repos = ", ".join(repository_ids) if repository_ids else "(none selected)"
    return (
        "# Grounding policy (authoritative)\n"
        f"Detected scope: {kind}.\n"
        f"Selected repository/workspace context is authoritative: {repos}.\n"
        "For organisational units, UIDs, reports, indicators, mappings, DHIS2, "
        "database/data coverage, or project configuration:\n"
        "1. Answer ONLY from the Selected evidence packet and repository/DHIS2 tool results below.\n"
        "2. Do NOT substitute general geographic or DHIS2 knowledge when project evidence is missing.\n"
        "3. If evidence is missing or tools failed, reply exactly that you cannot verify from "
        "the selected context and briefly say why. Do not invent provinces, UIDs, or coverage.\n"
        "4. Broader/national/general answers are allowed only when the user explicitly "
        "requests that scope."
    )


def dedupe_evidence_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate identical evidence rows by stable UID/ID (then name+source)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        uid = str(hit.get("uid") or hit.get("id") or "").strip()
        if uid:
            key = f"uid:{uid.lower()}"
        else:
            name = str(hit.get("name") or hit.get("path") or "").strip().lower()
            source = str(hit.get("source") or "").strip().lower()
            key = f"name:{source}:{name}"
        if not key or key in {"uid:", "name::"}:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def empty_evidence_packet(*, repository_ids: list[str] | None = None) -> dict[str, Any]:
    return {
        "repository_ids": list(repository_ids or []),
        "tool_results": [],
        "hits": [],
        "sources": [],
        "usable": False,
        "errors": [],
        "summary": "No evidence collected",
    }


def evidence_has_usable_hits(packet: dict[str, Any] | None) -> bool:
    if not isinstance(packet, dict):
        return False
    if packet.get("usable"):
        return True
    hits = packet.get("hits") or []
    return bool(hits)


def collect_evidence_packet(
    prompt: str,
    ctx: AgentToolsContext,
    *,
    repository_ids: list[str] | None = None,
    max_tools: int = 6,
) -> dict[str, Any]:
    """Run allowlisted Hub tools and build a compact evidence packet."""
    repos = list(repository_ids or ctx.repository_ids or [])
    packet = empty_evidence_packet(repository_ids=repos)
    terms = extract_search_terms(prompt) or [(prompt or "").strip()[:120]]
    primary = terms[0]
    tool_plan: list[tuple[str, dict[str, Any]]] = []

    # Org units / regions / provinces → DHIS2 OU cache/search.
    if re.search(r"\b(province|region|org|ou|luzon|municipality|barangay)\b", prompt, re.I):
        tool_plan.append(
            (
                "org_unit_lookup",
                {"query": primary, "limit": 25, "environment": "stage"},
            )
        )
        if "region" in primary.lower() or re.search(r"\bregion\b", prompt, re.I):
            # Also try children cascade when a region name is known.
            tool_plan.append(
                (
                    "org_unit_lookup",
                    {"query": primary, "limit": 25, "environment": "live"},
                )
            )
        tool_plan.append(
            (
                "uid_lookup",
                {
                    "query": primary,
                    "resource": "organisationUnits",
                    "limit": 20,
                },
            )
        )

    if re.search(r"\b(uid|data\s*element|indicator|program)\b", prompt, re.I):
        tool_plan.append(("uid_lookup", {"query": primary, "limit": 20}))

    if repos:
        for term in terms[:3]:
            tool_plan.append(("repo_search", {"query": term, "limit": 15}))

    if re.search(r"\b(report|dhis2)\b", prompt, re.I):
        tool_plan.append(("dhis2_reports_lookup", {"query": primary, "limit": 15}))

    # Structured value intents → also search saved SQL library (metadata; execute later).
    if re.search(
        r"\b(count|how\s+many|total|numerator|denominator|coverage|eligible|"
        r"status|approved|households?|beneficiar)\b",
        prompt,
        re.I,
    ):
        tool_plan.append(("sql_lookup", {"search": primary, "limit": 15}))
        for term in terms[1:3]:
            tool_plan.append(("sql_lookup", {"search": term, "limit": 10}))

    # Deduplicate tool+query pairs
    seen_keys: set[str] = set()
    unique_plan: list[tuple[str, dict[str, Any]]] = []
    for name, args in tool_plan:
        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_plan.append((name, args))
    unique_plan = unique_plan[: max(1, max_tools)]

    hits: list[dict[str, Any]] = []
    sources: list[str] = []
    errors: list[str] = []
    tool_results: list[dict[str, Any]] = []

    allowed = set(ctx.allowed_tools) | set(unique_plan and [p[0] for p in unique_plan])
    # Temporarily widen allowed for evidence collection within AiriX work profile.
    original_allowed = set(ctx.allowed_tools)
    ctx.allowed_tools = original_allowed | {name for name, _ in unique_plan}

    try:
        for name, args in unique_plan:
            if name not in ALLOWED_EVIDENCE_TOOLS:
                continue
            raw = execute_tool(name, args, ctx)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            ok = "error" not in parsed
            tool_results.append({"tool": name, "ok": ok, "args": args, "result": parsed})
            if not ok:
                errors.append(f"{name}: {parsed.get('error')}")
                continue
            sources.append(f"tool:{name}")
            extracted = _extract_hits(name, parsed)
            hits.extend(extracted)
    finally:
        ctx.allowed_tools = original_allowed

    if repos:
        sources.extend(f"repository:{rid}" for rid in repos)

    hits = dedupe_evidence_hits(hits)
    usable = bool(hits)
    packet.update(
        {
            "tool_results": tool_results,
            "hits": hits[:40],
            "sources": list(dict.fromkeys(sources)),
            "usable": usable,
            "errors": errors,
            "summary": (
                f"{len(hits)} evidence hit(s) from {len(tool_results)} tool call(s)"
                if usable
                else (
                    "No project evidence found"
                    + (f" ({'; '.join(errors[:2])})" if errors else "")
                )
            ),
            "search_terms": terms,
            "scope": detect_prompt_scope(prompt, repository_ids=repos).public(),
        }
    )
    return packet


ALLOWED_EVIDENCE_TOOLS = frozenset(
    {
        "repo_search",
        "read_file",
        "uid_lookup",
        "org_unit_lookup",
        "dhis2_reports_lookup",
        "sql_lookup",
        "notebook_lookup",
    }
)


def _extract_hits(tool: str, parsed: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if tool == "org_unit_lookup":
        for row in parsed.get("org_units") or parsed.get("results") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("displayName") or row.get("name") or "").strip()
            uid = str(row.get("id") or row.get("uid") or "").strip()
            if name or uid:
                hits.append(
                    {
                        "source": "dhis2:org_unit",
                        "name": name,
                        "uid": uid,
                        "level": row.get("level"),
                        "path": row.get("path"),
                    }
                )
        # Children when cascading from a region.
        for row in parsed.get("children") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("displayName") or row.get("name") or "").strip()
            uid = str(row.get("id") or "").strip()
            if name or uid:
                hits.append(
                    {
                        "source": "dhis2:org_unit_child",
                        "name": name,
                        "uid": uid,
                        "level": row.get("level"),
                    }
                )
    elif tool == "uid_lookup":
        rows = parsed.get("results") or []
        if parsed.get("result"):
            rows = [parsed["result"]] + list(rows)
        for row in rows:
            if not isinstance(row, dict):
                continue
            hits.append(
                {
                    "source": "uid_index",
                    "resource": parsed.get("resource"),
                    "name": row.get("name") or row.get("displayName"),
                    "uid": row.get("id") or row.get("uid"),
                }
            )
    elif tool == "repo_search":
        for row in parsed.get("matches") or parsed.get("results") or []:
            if isinstance(row, dict):
                hits.append(
                    {
                        "source": "repository",
                        "repo_id": row.get("repo_id"),
                        "path": row.get("path") or row.get("rel"),
                    }
                )
            elif isinstance(row, str):
                hits.append({"source": "repository", "path": row})
    elif tool == "dhis2_reports_lookup":
        for row in (parsed.get("standard_reports") or [])[:10]:
            hits.append(
                {
                    "source": "dhis2:report",
                    "name": row.get("name"),
                    "uid": row.get("id"),
                    "environment": row.get("environment"),
                }
            )
    elif tool == "sql_lookup":
        for row in (parsed.get("queries") or [])[:15]:
            if not isinstance(row, dict):
                continue
            hits.append(
                {
                    "source": "sql:saved_query",
                    "query_id": row.get("id"),
                    "name": row.get("title") or row.get("name"),
                    "path": row.get("id"),
                }
            )
        q = parsed.get("query")
        if isinstance(q, dict) and q.get("id"):
            hits.append(
                {
                    "source": "sql:saved_query",
                    "query_id": q.get("id"),
                    "name": q.get("title"),
                    "connection_id": q.get("connection_id"),
                    "path": q.get("id"),
                }
            )
    elif tool == "sql_query_execute":
        if parsed.get("value") is not None or parsed.get("ok"):
            hits.append(
                {
                    "source": "sql:query",
                    "query_id": parsed.get("query_id"),
                    "connection_id": parsed.get("connection_id"),
                    "title": parsed.get("title"),
                    "value": parsed.get("value"),
                }
            )
    return hits


def format_evidence_for_prompt(packet: dict[str, Any] | None) -> str:
    if not isinstance(packet, dict):
        return "# Selected evidence packet\n(none)"
    lines = [
        "# Selected evidence packet",
        f"Repositories: {', '.join(packet.get('repository_ids') or []) or '(none)'}",
        f"Usable: {'yes' if evidence_has_usable_hits(packet) else 'no'}",
        f"Summary: {packet.get('summary') or ''}",
    ]
    hits = packet.get("hits") or []
    if hits:
        lines.append("Evidence hits:")
        for hit in hits[:25]:
            lines.append(f"- {json.dumps(hit, ensure_ascii=False)[:300]}")
    errors = packet.get("errors") or []
    if errors:
        lines.append("Tool errors:")
        for err in errors[:5]:
            lines.append(f"- {err}")
    if not hits:
        lines.append(
            "No usable project evidence. Do not invent OU/province/UID/report facts."
        )
    return "\n".join(lines)


def format_cannot_verify(
    *,
    repository_ids: list[str] | None = None,
    reason: str = "",
    errors: list[str] | None = None,
) -> str:
    repos = ", ".join(repository_ids or []) or "(no repository selected)"
    parts = [
        "Cannot verify from selected context.",
        f"Selected repository/workspace: {repos}.",
    ]
    if reason:
        parts.append(f"Reason: {reason}")
    if errors:
        parts.append("Tool issues: " + "; ".join(errors[:3]))
    parts.append(
        "I will not substitute general geographic or DHIS2 knowledge for missing "
        "project evidence. Ask again with general knowledge explicitly requested "
        "if you want an unlabeled-outside-project answer."
    )
    return "\n".join(parts)


def evaluate_answer_grounding(
    prompt: str,
    answer: str,
    *,
    repository_ids: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    allow_general: bool = False,
) -> dict[str, Any]:
    """
    Return grounding status for UI/API.

    Keys: grounded, grounded_label, source, reason, policy_violation, cannot_verify,
    evidence_found, task_solved, answer_grounded (+ labels).
    """
    from hub.agent_center.completion import (
        derive_completion_contract,
        merge_completion_into_grounding,
        validate_completion,
    )

    repos = list(repository_ids or [])
    scope = resolve_prompt_scope(prompt, repository_ids=repos)
    requires = scope.requires_project_evidence
    text = (answer or "").strip()
    sources = list((evidence or {}).get("sources") or [])
    if repos and scope.use_selected_repo:
        sources.extend(f"repository:{r}" for r in repos)
    sources = list(dict.fromkeys(sources))

    contract = derive_completion_contract(prompt)
    completion = validate_completion(
        contract,
        prompt=prompt,
        answer=text,
        evidence=evidence,
        require_authoritative_evidence=bool(requires),
    )

    if not requires:
        usable = evidence_has_usable_hits(evidence)
        if completion.task_solved and completion.answer_grounded:
            status = {
                "grounded": True,
                "grounded_label": "Yes",
                "source": ", ".join(sources) or "hub tools",
                "reason": f"Scope {scope.kind}: answered with Hub evidence.",
                "policy_violation": False,
                "cannot_verify": False,
                "required": False,
                "scope": scope.kind,
            }
            return merge_completion_into_grounding(status, completion)
        if completion.evidence_found and not completion.task_solved:
            status = {
                "grounded": False,
                "grounded_label": "No",
                "source": ", ".join(sources) or "hub tools",
                "reason": completion.reason,
                "policy_violation": False,
                "cannot_verify": True,
                "required": False,
                "scope": scope.kind,
            }
            return merge_completion_into_grounding(status, completion)
        src = "general knowledge"
        if scope.kind in {SCOPE_NATIONAL, SCOPE_WEB}:
            src = f"{scope.kind.replace('_', ' ')} / model knowledge"
        status = {
            # Spec: Grounded=Yes only with authoritative evidence.
            "grounded": bool(completion.answer_grounded),
            "grounded_label": "Yes" if completion.answer_grounded else "No",
            "source": src if not usable else (", ".join(sources) or "hub tools"),
            "reason": (
                scope.reason
                if completion.task_solved
                else completion.reason
            )
            or "Prompt does not require project-data grounding.",
            "policy_violation": False,
            "cannot_verify": bool(not completion.task_solved and completion.evidence_found),
            "required": False,
            "scope": scope.kind,
        }
        merged = merge_completion_into_grounding(status, completion)
        if completion.task_solved and not completion.answer_grounded:
            merged["source"] = src if not usable else merged.get("source") or src
            merged["cannot_verify"] = False
            merged["reason"] = (
                scope.reason or "Answer allowed from model knowledge for this scope."
            )
        return merged

    if allow_general:
        status = {
            "grounded": False,
            "grounded_label": "No",
            "source": "general knowledge (explicitly requested)",
            "reason": "Caller allowed a general-knowledge answer for this prompt.",
            "policy_violation": False,
            "cannot_verify": False,
            "required": True,
            "scope": scope.kind,
        }
        return merge_completion_into_grounding(status, completion)

    usable = evidence_has_usable_hits(evidence)
    admits_unavailable = bool(_LOOKUP_UNAVAILABLE.search(text))
    cannot_verify = bool(
        re.search(r"cannot\s+verify\s+from\s+selected\s+context", text, re.I)
    ) or (completion.evidence_found and not completion.task_solved)

    if cannot_verify and not admits_unavailable:
        status = {
            "grounded": False,
            "grounded_label": "No",
            "source": ", ".join(sources) or "selected context (no evidence)",
            "reason": completion.reason
            if not completion.task_solved
            else "No usable evidence in selected context; answered cannot-verify.",
            "policy_violation": False,
            "cannot_verify": True,
            "required": True,
            "scope": scope.kind,
        }
        return merge_completion_into_grounding(status, completion)

    if admits_unavailable and text and not cannot_verify:
        status = {
            "grounded": False,
            "grounded_label": "No",
            "source": ", ".join(sources) or "none",
            "reason": (
                "Answer admitted required project lookup was unavailable and then "
                "substituted general knowledge."
            ),
            "policy_violation": True,
            "cannot_verify": False,
            "required": True,
            "scope": scope.kind,
        }
        return merge_completion_into_grounding(status, completion)

    if not usable:
        if cannot_verify:
            status = {
                "grounded": False,
                "grounded_label": "No",
                "source": ", ".join(sources) or "selected context (no evidence)",
                "reason": (evidence or {}).get("summary")
                or "Required project tools returned no evidence.",
                "policy_violation": False,
                "cannot_verify": True,
                "required": True,
                "scope": scope.kind,
            }
            return merge_completion_into_grounding(status, completion)
        status = {
            "grounded": False,
            "grounded_label": "No",
            "source": ", ".join(sources) or "none",
            "reason": "No usable selected-context evidence; answer is not grounded.",
            "policy_violation": True,
            "cannot_verify": False,
            "required": True,
            "scope": scope.kind,
        }
        return merge_completion_into_grounding(status, completion)

    # Usable evidence exists — only grounded if completion contract is satisfied.
    if completion.task_solved and completion.answer_grounded:
        status = {
            "grounded": True,
            "grounded_label": "Yes",
            "source": ", ".join(sources) or "selected context",
            "reason": (evidence or {}).get("summary")
            or "Answer grounded in tool/repo evidence.",
            "policy_violation": False,
            "cannot_verify": False,
            "required": True,
            "scope": scope.kind,
        }
        return merge_completion_into_grounding(status, completion)

    status = {
        "grounded": False,
        "grounded_label": "No",
        "source": ", ".join(sources) or "selected context",
        "reason": completion.reason,
        "policy_violation": False,
        "cannot_verify": True,
        "required": True,
        "scope": scope.kind,
    }
    return merge_completion_into_grounding(status, completion)


def format_grounding_status_block(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return ""
    evidence = status.get("evidence_found_label")
    if evidence is None and "evidence_found" in status:
        evidence = "Yes" if status.get("evidence_found") else "No"
    solved = status.get("task_solved_label")
    if solved is None and "task_solved" in status:
        solved = "Yes" if status.get("task_solved") else "No"
    sources = status.get("sources_used") or []
    if not sources and status.get("source"):
        sources = [str(status.get("source"))]
    source_line = ", ".join(str(s) for s in sources if str(s).strip()) or (
        status.get("source") or "unknown"
    )
    lines = [
        "",
        "—",
        f"Evidence Found: {evidence or 'No'}",
        f"Task Solved: {solved or ('Yes' if status.get('task_solved') else 'No')}",
        f"Grounded: {status.get('grounded_label') or ('Yes' if status.get('grounded') else 'No')}",
        f"Sources used: {source_line}",
    ]
    if not status.get("grounded") and status.get("reason"):
        lines.append(f"Why not grounded: {status.get('reason')}")
    if not status.get("task_solved") and status.get("completion_reason"):
        lines.append(f"Completion: {status.get('completion_reason')}")
    return "\n".join(lines) + "\n"


def apply_grounding_to_answer(answer: str, status: dict[str, Any] | None) -> str:
    text = (answer or "").rstrip()
    footer = format_grounding_status_block(status)
    if not footer:
        return text
    if "Grounded:" in text and ("Evidence Found:" in text or "Source:" in text):
        return text
    return text + footer


def answer_from_evidence(
    prompt: str,
    packet: dict[str, Any],
) -> str | None:
    """
    Build a deterministic answer only when evidence satisfies the completion contract.

    Finding related files/UIDs alone is not enough for count/status/etc.
    """
    from hub.agent_center.completion import (
        INTENT_COUNT,
        INTENT_FILE_SEARCH,
        INTENT_GENERAL,
        INTENT_LIST,
        INTENT_LOOKUP,
        INTENT_STATUS,
        INTENT_TRACE,
        derive_completion_contract,
        validate_completion,
    )

    if not evidence_has_usable_hits(packet):
        return None
    contract = derive_completion_contract(prompt)
    hits = dedupe_evidence_hits(
        [h for h in (packet.get("hits") or []) if isinstance(h, dict)]
    )

    # Prefer verified SQL numeric/status/list hits when present.
    sql_hits = [h for h in hits if str(h.get("source") or "") == "sql:query" and h.get("value") is not None]
    candidate: str | None = None
    if sql_hits:
        hit = sql_hits[0]
        value = str(hit.get("value"))
        conn = str(hit.get("connection_id") or "connected database")
        title = str(hit.get("title") or "")
        title_s = f" ({title})" if title else ""
        if contract.intent == INTENT_COUNT:
            candidate = f"Count: {value}\nSource: read-only SQL{title_s} via {conn}"
        elif contract.intent == INTENT_STATUS:
            candidate = f"Status: {value}\nSource: read-only SQL{title_s} via {conn}"
        elif contract.intent in {INTENT_LIST, INTENT_LOOKUP}:
            candidate = f"Results from read-only SQL{title_s} via {conn}:\n{value}"
        else:
            candidate = f"Value: {value}\nSource: read-only SQL{title_s} via {conn}"

    ou_hits = [
        h
        for h in hits
        if str(h.get("source") or "").startswith("dhis2:org_unit")
        or h.get("source") == "uid_index"
    ]
    # Prefer child provinces when present.
    children = [h for h in ou_hits if h.get("source") == "dhis2:org_unit_child"]
    rows = children or ou_hits
    names: list[str] = []
    seen_uids: set[str] = set()
    for h in rows:
        name = str(h.get("name") or "").strip()
        uid = str(h.get("uid") or "").strip()
        if uid and uid.lower() in seen_uids:
            continue
        if uid:
            seen_uids.add(uid.lower())
        label = name if not uid else f"{name} ({uid})" if name else uid
        if label and label not in names:
            names.append(label)

    if candidate is None and names and re.search(
        r"\b(province|region|org|ou|list|what\s+are)\b", prompt, re.I
    ):
        if contract.intent in {INTENT_LIST, INTENT_LOOKUP, INTENT_GENERAL}:
            label = "Organisation units from selected DHIS2/project context"
            body = "\n".join(f"- {n}" for n in names[:30])
            candidate = f"{label}:\n{body}"

    repo_hits = [
        h
        for h in hits
        if h.get("source") == "repository"
        or str(h.get("source") or "").startswith("repository_intelligence")
    ]
    if candidate is None and repo_hits:
        paths = []
        for h in repo_hits[:15]:
            path = str(h.get("path") or "").strip()
            rid = str(h.get("repo_id") or h.get("repository_id") or "").strip()
            if path:
                paths.append(f"{rid}:{path}" if rid else path)
        if paths and contract.intent in {INTENT_FILE_SEARCH, INTENT_TRACE}:
            candidate = (
                "Selected-repository matches (read-only):\n"
                + "\n".join(f"- {p}" for p in paths)
            )
        # For other intents, path discovery is evidence only — not a final answer.

    if not candidate:
        return None

    completion = validate_completion(
        contract, prompt=prompt, answer=candidate, evidence=packet
    )
    if completion.task_solved and completion.answer_grounded:
        return candidate
    return None
