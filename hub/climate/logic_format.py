"""Presentation-only formatting for CLIMATE implementation-logic answers.

Does not change repository investigation, scoring, citations, or provider
execution. Never invents thresholds, DEs, functions, or behavior.
"""

from __future__ import annotations

import re

LOGIC_SECTION_ORDER = (
    "core_rule",
    "decision_table",
    "example",
    "edge_cases",
    "rollup",
    "implementation",
    "one_line",
)

CANONICAL_HEADINGS = {
    "core_rule": "Core rule",
    "decision_table": "Decision table",
    "example": "Example",
    "edge_cases": "Eligibility + edge cases",
    "rollup": "Household/member roll-up",
    "implementation": "Exact implementation files/functions",
    "one_line": "In one line",
}

_SECTION_ALIASES = {
    "core rule": "core_rule",
    "period policy": "core_rule",
    "decision table": "decision_table",
    "decision table / thresholds": "decision_table",
    "thresholds": "decision_table",
    "required checks": "decision_table",
    "due dates": "decision_table",
    "four pnc checks and due dates": "decision_table",
    "example": "example",
    "examples": "example",
    "eligibility + edge cases": "edge_cases",
    "edge cases": "edge_cases",
    "eligibility": "edge_cases",
    "missing dates": "edge_cases",
    "missing dates fallback": "edge_cases",
    "missing required dates": "edge_cases",
    "household/member roll-up": "rollup",
    "household roll-up": "rollup",
    "member roll-up": "rollup",
    "household": "rollup",
    "eligibility and roll-up": "rollup",
    "exact implementation files/functions": "implementation",
    "exact implementation": "implementation",
    "source trace": "implementation",
    "implementation files/functions": "implementation",
    "implementation": "implementation",
    "in one line": "one_line",
    "one-line summary": "one_line",
    "one line": "one_line",
}

_LOGIC_RE = re.compile(
    r"(?:"
    r"\blogic of\b|"
    r"give me the logic|"
    r"\bscoring logic\b|"
    r"\bindicator logic\b|"
    r"\beligibility rules?\b|"
    r"\bdecision table\b|"
    r"\bpass\s*/\s*fail\b|"
    r"\bthresholds?\b|"
    r"\bhow (?:is|are)\b.{0,80}\bderiv|"
    r"\bexplain how\b.{0,80}\bderiv|"
    r"\bderivation of\b|"
    r"\bcore rule\b|"
    r"\bimplementation logic\b|"
    r"explain (?:this |the )?(?:implementation|indicator|scoring|eligibility|threshold)"
    r")",
    re.I | re.S,
)

_INSUFFICIENT_RE = re.compile(
    r"not enough repository evidence|cannot verify|insufficient (?:repo(?:sitory)? )?evidence",
    re.I,
)

_PACKET_TASK_RE = re.compile(
    r"Task:\n(.*?)(?:\n(?:Confidence:|Repository access:))",
    re.S,
)
_MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_LABEL_RE = re.compile(r"^(.+?):\s*$")
_SOURCE_TRACE_RE = re.compile(
    r"`([^`]+)`\s+[—–-]\s+`([^`]+)`",
)

LOGIC_EXPLANATION_INSTRUCTIONS = """
When this question asks for implementation, indicator, scoring, eligibility, or threshold logic, write the final answer in this order and do not add extra narrative sections:

## <Topic> Logic

Core rule:
- the pass/fail policy only (period-specific rules first)

Decision table / thresholds:
- one compact markdown table when windows or cutoffs exist

Example:
- one concrete case, not a restatement of the core rule

Eligibility + edge cases:
- no eligible member, nothing due yet, missing dates, member Pass/Fail values
- label surprising code fallbacks as edge cases

Household/member roll-up:
- any-member-pass vs all-members-pass
- cite the actual implementation condition/function

Exact implementation files/functions:
- `path/file.py` — `function_name`
- production scoring functions first; helper or recommended-timing functions after, labeled as helpers

### In one line
One plain-language sentence for the whole rule.

Formatting:
- result first, source trace second
- do not repeat the same rule in multiple sections
- bold only important outcomes
- keep exact DE UIDs when the repository has them
- never invent thresholds, functions, DEs, or behavior
- if repository evidence is insufficient, say so instead of filling gaps
""".strip()


def is_logic_explanation_prompt(text: str) -> bool:
    blob = str(text or "").strip()
    if not blob:
        return False
    match = _PACKET_TASK_RE.search(blob)
    task = match.group(1).strip() if match else blob
    return bool(_LOGIC_RE.search(task))


def logic_explanation_instructions() -> str:
    return LOGIC_EXPLANATION_INSTRUCTIONS


def format_logic_explanation(answer: str) -> str:
    """Reorder recognizable logic sections. Leave unknown answers unchanged."""
    text = str(answer or "").strip()
    if not text or _INSUFFICIENT_RE.search(text):
        return text
    title, mapped, leftover = _split_sections(text)
    if len(mapped) < 2:
        return text
    chunks: list[str] = []
    if title:
        chunks.append(f"## {title}")
    for key in LOGIC_SECTION_ORDER:
        body = mapped.get(key)
        if not body:
            continue
        heading = CANONICAL_HEADINGS[key]
        if key == "one_line":
            chunks.append(f"### {heading}\n{_dedupe_lines(body)}")
        else:
            chunks.append(f"{heading}:\n{_dedupe_lines(body)}")
    for heading, body in leftover:
        chunks.append(f"{heading}:\n{_dedupe_lines(body)}" if heading else _dedupe_lines(body))
    formatted = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    return formatted or text


def _split_sections(text: str) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    marks = _section_marks(text)
    if not marks:
        return "", {}, []
    title = ""
    mapped: dict[str, str] = {}
    leftover: list[tuple[str, str]] = []
    preamble = text[: marks[0][0]].strip()
    if preamble:
        leftover.append(("", preamble))
    start_index = 0
    first_heading = marks[0][2]
    first_is_title = marks[0][4]
    if first_is_title:
        title = first_heading
        start_index = 1
        title_end = marks[1][0] if len(marks) > 1 else len(text)
        title_body = text[marks[0][1]:title_end].strip()
        if title_body:
            leftover.append(("", title_body))
    for i, mark in enumerate(marks[start_index:], start=start_index):
        _start, line_end, heading, key, _is_title = mark
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[line_end:end].strip()
        if key:
            mapped[key] = _merge_bodies(mapped.get(key), body)
        else:
            leftover.append((heading, body))
    return title, mapped, leftover


def _section_marks(text: str) -> list[tuple[int, int, str, str, bool]]:
    marks: list[tuple[int, int, str, str, bool]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        heading, key, is_title, is_mark = _classify_line(line.strip())
        if is_mark:
            marks.append((pos, pos + len(line), heading, key, is_title))
        pos += len(line)
    return marks


def _classify_line(stripped: str) -> tuple[str, str, bool, bool]:
    if not stripped or stripped.startswith("|"):
        return "", "", False, False
    md = _MD_HEADING_RE.match(stripped)
    if md:
        heading = md.group(2).strip().rstrip(":")
        key = _alias(heading)
        is_title = not key and bool(re.search(r"\blogic\b", heading, re.I))
        return heading, key, is_title, True
    label = _LABEL_RE.match(stripped)
    if label:
        heading = _strip_markup(label.group(1))
        key = _alias(heading)
        if key:
            return heading, key, False, True
    return "", "", False, False


def _strip_markup(value: str) -> str:
    return re.sub(r"^[*_`]+|[*_`]+$", "", str(value or "").strip())


def _alias(heading: str) -> str:
    cleaned = re.sub(r"[^a-z0-9+/ -]+", "", heading.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :")
    return _SECTION_ALIASES.get(cleaned, "")


def _merge_bodies(existing: str | None, incoming: str) -> str:
    incoming = incoming.strip()
    if not existing:
        return incoming
    if incoming and incoming not in existing:
        return existing.rstrip() + "\n\n" + incoming
    return existing


def _dedupe_lines(body: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for line in str(body or "").splitlines():
        key = re.sub(r"\s+", " ", line.strip())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line.rstrip())
    return "\n".join(out).strip()


def source_traces(text: str) -> list[tuple[str, str]]:
    return [(path, name) for path, name in _SOURCE_TRACE_RE.findall(text or "")]
