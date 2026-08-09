"""Dynamic prompt scope detection for AiriX grounding + routing.

Classifies whether a prompt is project/repo-bound, DHIS2/data-specific,
national/general domain, general knowledge, web-needed, or ambiguous.
Explicit user scope always overrides the selected repository.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from hub.agent_center.data_intent import detect_data_query_intent

SCOPE_PROJECT = "project"
SCOPE_DHIS2 = "dhis2_data"
SCOPE_NATIONAL = "national_general"
SCOPE_GK = "general_knowledge"
SCOPE_WEB = "current_web"
SCOPE_AMBIGUOUS = "ambiguous"

SCOPE_KINDS = (
    SCOPE_PROJECT,
    SCOPE_DHIS2,
    SCOPE_NATIONAL,
    SCOPE_GK,
    SCOPE_WEB,
    SCOPE_AMBIGUOUS,
)

# Strong explicit broader / non-repo overrides (ignore selected context).
_STRONG_BROADER = re.compile(
    r"\b("
    r"general\s+knowledge|"
    r"from\s+general\s+knowledge|"
    r"in\s+general|"
    r"generally|"
    r"from\s+general|"
    r"across\s+the\s+(country|philippines|nation)|"
    r"philippine\s+(provinces?|regions?|geography)|"
    r"standard\s+(philippine|geographic)|"
    r"not\s+(from\s+)?(the\s+)?(project|repo|repository|selected\s+context)|"
    r"ignore\s+(the\s+)?(repo|repository|project|context|selected)|"
    r"outside\s+(the\s+)?(project|repo|repository)|"
    r"without\s+(using\s+)?(the\s+)?(repo|repository|project)|"
    r"broader\s+(than\s+)?(the\s+)?(project|repo)"
    r")\b",
    re.I,
)

# National wording alone — may be admin scope on a data query, or a GK override.
_NATIONAL_BROADER = re.compile(
    r"\b(national(?:ly)?|nationwide|country[- ]?wide)\b",
    re.I,
)

# Explicit broader / non-repo scope from the user (union; used by callers/tests).
_EXPLICIT_BROADER = re.compile(
    r"\b("
    r"general\s+knowledge|"
    r"from\s+general\s+knowledge|"
    r"in\s+general|"
    r"generally|"
    r"from\s+general|"
    r"national(?:ly)?|"
    r"nationwide|"
    r"country[- ]?wide|"
    r"across\s+the\s+(country|philippines|nation)|"
    r"philippine\s+(provinces?|regions?|geography)|"
    r"standard\s+(philippine|geographic)|"
    r"not\s+(from\s+)?(the\s+)?(project|repo|repository|selected\s+context)|"
    r"ignore\s+(the\s+)?(repo|repository|project|context|selected)|"
    r"outside\s+(the\s+)?(project|repo|repository)|"
    r"without\s+(using\s+)?(the\s+)?(repo|repository|project)|"
    r"broader\s+(than\s+)?(the\s+)?(project|repo)"
    r")\b",
    re.I,
)

# Explicit project / selected-context scope.
_EXPLICIT_PROJECT = re.compile(
    r"\b("
    r"in\s+this\s+project|"
    r"from\s+(the\s+)?(selected\s+)?(repo|repository|context)|"
    r"selected\s+(repo|repository|context)|"
    r"our\s+(mapping|config|configuration|uid\s*index|org\s*units?)|"
    r"project\s+(config|configuration|data|mapping|uid)|"
    r"in\s+the\s+(codebase|repo|repository)|"
    r"pmnp|"
    r"live[- ]processing|"
    r"hcsc\s+(report|config)"
    r")\b",
    re.I,
)

_DHIS2_DATA = re.compile(
    r"\b("
    r"dhis2?|"
    r"data\s*element(?:s)?|"
    r"program\s+indicator|"
    r"indicator\s+mapping|"
    r"uid\s*(lookup|index|for)?|"
    r"analytics\s+(query|sql)|"
    r"org(?:anisation|anization)?\s*unit\s*(uid|id|tree)?"
    r")\b",
    re.I,
)

_GEO_OR_OU = re.compile(
    r"\b("
    r"org(?:anisation|anization)?\s*units?|"
    r"\bou\b|"
    r"provinces?|"
    r"regions?|"
    r"municipalit(?:y|ies)|"
    r"barangay(?:s)?|"
    r"brgy\.?|"
    r"bgy\.?|"
    r"mun(?:icipality|\.)?|"
    r"city|cities|"
    r"prov(?:ince|\.)?|"
    r"reg(?:ion|\.)?|"
    r"central\s+luzon|"
    r"region\s+(i{1,3}|iv|v|vi{0,3}|\d+)"
    r")\b",
    re.I,
)

_WEB_CURRENT = re.compile(
    r"\b("
    r"today|"
    r"current(?:ly)?|"
    r"latest\s+(news|price|version|release)|"
    r"as\s+of\s+20\d{2}|"
    r"search\s+the\s+web|"
    r"look\s+up\s+online|"
    r"breaking\s+news|"
    r"live\s+score"
    r")\b",
    re.I,
)

_SIMPLE_GK = re.compile(
    r"\b("
    r"hello|hi\b|thanks|thank\s+you|"
    r"what\s+can\s+you\s+do|"
    r"explain\s+(what\s+)?(a\s+|an\s+|the\s+)?|"
    r"what\s+is\s+(a\s+|an\s+|the\s+)?|"
    r"how\s+does\s+.+\s+work|"
    r"write\s+a\s+(poem|story|joke)|"
    r"python\s+(list|dict|comprehension|decorator)|"
    r"javascript\s+(promise|closure)|"
    r"git\s+(rebase|merge|commit)"
    r")\b",
    re.I,
)

_CODING = re.compile(
    r"\b(fix|bug|implement|refactor|codebase|pull\s+request|endpoint|function|class)\b",
    re.I,
)


@dataclass(frozen=True)
class PromptScope:
    """Resolved grounding / routing scope for one prompt."""

    kind: str
    requires_project_evidence: bool
    allow_general_knowledge: bool
    use_selected_repo: bool
    try_deterministic_tools: bool
    reason: str
    signals: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return asdict(self)


def detect_prompt_scope(
    prompt: str,
    *,
    repository_ids: list[str] | None = None,
) -> PromptScope:
    """
    Classify prompt grounding scope from the text itself.

    Priority:
    1. Explicit user broader/national/GK override (ignores selected repo)
    2. Explicit project/selected-context request
    3. Current/web-needed
    4. DHIS2/data-specific
    5. Geo/OU without explicit scope → ambiguous (repo makes it project)
    6. Simple general knowledge
    7. Default ambiguous / GK
    """
    text = (prompt or "").strip()
    repos = [str(r).strip() for r in (repository_ids or []) if str(r).strip()]
    has_repo = bool(repos)
    signals: list[str] = []

    if not text:
        return PromptScope(
            kind=SCOPE_GK,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=False,
            try_deterministic_tools=False,
            reason="Empty prompt.",
            signals=("empty",),
        )

    # Detect structured data intent early so "national"/admin filters are not
    # mistaken for a general-knowledge override.
    data_intent = detect_data_query_intent(text)

    strong_broader = bool(_STRONG_BROADER.search(text))
    national_word = bool(_NATIONAL_BROADER.search(text))
    # Bare "national" inside a count/indicator question is admin scope, not GK override.
    if strong_broader or (national_word and not data_intent.is_data_query):
        signals.append("explicit_broader_scope")
        kind = SCOPE_NATIONAL if (_GEO_OR_OU.search(text) or national_word) else SCOPE_GK
        if _WEB_CURRENT.search(text):
            kind = SCOPE_WEB
            signals.append("web_needed")
        return PromptScope(
            kind=kind,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=False,
            try_deterministic_tools=bool(
                _GEO_OR_OU.search(text) or _DHIS2_DATA.search(text) or data_intent.is_data_query
            ),
            reason="Explicit general/national/broader scope overrides selected repository.",
            signals=tuple(signals),
        )

    if _EXPLICIT_PROJECT.search(text):
        signals.append("explicit_project_scope")
        return PromptScope(
            kind=SCOPE_PROJECT,
            requires_project_evidence=True,
            allow_general_knowledge=False,
            use_selected_repo=True,
            try_deterministic_tools=True,
            reason="Explicit project/selected-context request.",
            signals=tuple(signals),
        )

    # Structured data/DHIS2 lookup intent (counts, indicators, OU filters, periods…).
    # Must run before simple GK so "Brgy." / locality abbreviations are not dropped.
    if data_intent.is_data_query:
        signals.append("data_query")
        signals.extend(data_intent.signals[:8])
        requires = has_repo or bool(data_intent.entity_types) or bool(
            data_intent.filters.get("location") or data_intent.filters.get("uids")
        )
        # Explicit national/nationwide entity without repo → national domain, still T0-first.
        nationalish = "national" in data_intent.entity_types and not has_repo
        if nationalish and not has_repo:
            kind = SCOPE_NATIONAL
            requires = False
            allow_gk = True
            reason = "National-scope structured data question — tools first, then model knowledge if needed."
        else:
            kind = SCOPE_DHIS2
            allow_gk = not requires
            reason = (
                "Structured data/DHIS2 lookup; selected context is authoritative when present."
                if has_repo
                else "Structured data/DHIS2 lookup without selected repo — try Hub tools first."
            )
        return PromptScope(
            kind=kind,
            requires_project_evidence=requires and not nationalish,
            allow_general_knowledge=allow_gk,
            use_selected_repo=has_repo and not nationalish,
            try_deterministic_tools=True,
            reason=reason,
            signals=tuple(
                dict.fromkeys(
                    signals
                    + (["selected_repo"] if has_repo else ["no_selected_repo"])
                    + [f"entity:{e}" for e in data_intent.entity_types[:4]]
                )
            ),
        )

    if _WEB_CURRENT.search(text) and not _DHIS2_DATA.search(text) and not _CODING.search(text):
        signals.append("web_needed")
        return PromptScope(
            kind=SCOPE_WEB,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=False,
            try_deterministic_tools=False,
            reason="Prompt asks for current/web-timed information.",
            signals=tuple(signals),
        )

    if _DHIS2_DATA.search(text) and not _SIMPLE_GK.search(text):
        signals.append("dhis2_data_topic")
        # DHIS2/data questions use Hub tools; selected repo is useful but
        # open investigations may fall through when no repo is selected.
        requires = has_repo or bool(_GEO_OR_OU.search(text))
        return PromptScope(
            kind=SCOPE_DHIS2,
            requires_project_evidence=requires,
            allow_general_knowledge=not requires,
            use_selected_repo=has_repo,
            try_deterministic_tools=True,
            reason=(
                "DHIS2/data-specific prompt; selected repo is authoritative when present."
                if has_repo
                else "DHIS2/data-specific prompt without selected repo."
            ),
            signals=tuple(signals + (["selected_repo"] if has_repo else [])),
        )

    if _GEO_OR_OU.search(text):
        signals.append("geo_or_ou_topic")
        if has_repo:
            # Ambiguous geo/OU + selected repo → treat as project-authoritative.
            return PromptScope(
                kind=SCOPE_AMBIGUOUS,
                requires_project_evidence=True,
                allow_general_knowledge=False,
                use_selected_repo=True,
                try_deterministic_tools=True,
                reason="Ambiguous geo/OU prompt with selected repository — project evidence required.",
                signals=tuple(signals + ["selected_repo", "ambiguous_with_repo"]),
            )
        # No repo: national/general domain geography, tools optional then GK.
        return PromptScope(
            kind=SCOPE_NATIONAL,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=False,
            try_deterministic_tools=True,
            reason="Geo/OU prompt without selected repo — national/general domain allowed.",
            signals=tuple(signals + ["no_selected_repo"]),
        )

    if _SIMPLE_GK.search(text) or not _CODING.search(text):
        signals.append("general_knowledge")
        return PromptScope(
            kind=SCOPE_GK,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=False,
            try_deterministic_tools=False,
            reason="General-knowledge / non-project prompt.",
            signals=tuple(signals),
        )

    # Coding / other with selected repo remains project-adjacent but does not
    # hard-require OU evidence grounding.
    if has_repo and _CODING.search(text):
        signals.append("coding_with_repo")
        return PromptScope(
            kind=SCOPE_PROJECT,
            requires_project_evidence=False,
            allow_general_knowledge=True,
            use_selected_repo=True,
            try_deterministic_tools=False,
            reason="Coding prompt with selected repository — repo context without hard evidence gate.",
            signals=tuple(signals),
        )

    return PromptScope(
        kind=SCOPE_AMBIGUOUS,
        requires_project_evidence=has_repo,
        allow_general_knowledge=not has_repo,
        use_selected_repo=has_repo,
        try_deterministic_tools=False,
        reason="Ambiguous prompt; selected repo authoritative when present.",
        signals=tuple(signals + (["selected_repo"] if has_repo else ["no_selected_repo"])),
    )


def scopes_compatible(current: str, prior: str) -> bool:
    """False when prior findings belong to an incompatible grounding scope."""
    cur = (current or "").strip().lower()
    old = (prior or "").strip().lower()
    if not old or not cur:
        return True
    if cur == old:
        return True
    # Broader/GK/web prompts drop project/dhis2 findings.
    if cur in {SCOPE_NATIONAL, SCOPE_GK, SCOPE_WEB} and old in {
        SCOPE_PROJECT,
        SCOPE_DHIS2,
        SCOPE_AMBIGUOUS,
    }:
        return False
    # Project prompts drop national/GK/web findings.
    if cur in {SCOPE_PROJECT, SCOPE_DHIS2, SCOPE_AMBIGUOUS} and old in {
        SCOPE_NATIONAL,
        SCOPE_GK,
        SCOPE_WEB,
    }:
        return False
    return True
