"""ASK retrieval policy: noisy artifacts, large dumps, simple-query routing.

Hints only — does not encode connected-repository business rules.
Does not hide paths from Explorer; CLIMATE ASK ranking/search uses these gates.
"""

from __future__ import annotations

import re
from pathlib import Path

NOISY_PATH_MARKERS = (
    "/lookup/logs/",
    "lookup/logs/",
    "/bulk_apply_jobs/",
    "bulk_apply_jobs/",
    "/dry-run/",
    "/dry_run/",
    "/generated/",
    "/artifacts/",
)

NOISY_DIR_PARTS = frozenset(
    {
        "tmp",
        "temp",
        "tmp_output",
        "artifacts",
        "generated",
        "dry-run",
        "dry_run",
        "bulk_apply_jobs",
        "bulk_apply",
    }
)

LARGE_DUMP_SUFFIXES = {".json", ".csv", ".tsv", ".ndjson"}
LARGE_DUMP_PATH_MARKERS = (
    "/reference-json/",
    "reference-json/",
    "/ai_reference/",
    "ai_reference/",
)
LARGE_DUMP_BASENAMES = frozenset({"metadata.json"})
LARGE_FILE_BYTES = 262_144
HUGE_FILE_BYTES = 1_000_000

SEARCH_MAX_LINES = 80
SEARCH_MAX_CHARS = 8_000
SEARCH_MAX_UNIQUE_FILES = 24
SEARCH_MAX_HITS_PER_FILE = 6
SEARCH_TIMEOUT_SECONDS = 8.0
BOUNDED_EXCERPT_LINES = 12
BOUNDED_EXCERPT_CHARS = 1_200
LOW_CONFIDENCE_MAX_HINTS = 3
SIMPLE_QUERY_MAX_SOURCES = 3

_IMPLEMENTATION_RE = re.compile(
    r"\b(implementation|implement|logic|function|symbol|code|derive[ds]?|scor(?:e|ing))\b",
    re.I,
)
_SIMPLE_REFERENCE_RE = re.compile(
    r"\b("
    r"provinces?|municipalit(?:y|ies)|barangays?|"
    r"org(?:anisation|anization)?\s+units?|"
    r"name of|what(?:'s| is) the name|"
    r"which file defines|what file defines|where is .+ defined|"
    r"program stage containing|"
    r"list the|give me the (?!logic|implementation|code\b)"
    r")\b",
    re.I,
)
_LOGS_HISTORY_RE = re.compile(
    r"\b(logs?|history|bulk[_\s-]?apply|dry[_\s-]?run|job(?:s| id)?|audit trail)\b",
    re.I,
)
_REGION_PHRASE_RE = re.compile(r"\bRegion\s+(?:[IVX]+|\d+)\b", re.I)
_PROPER_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_UID_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{10})\b")
_QUOTED_RE = re.compile(r'"([^"]{3,80})"|\'([^\']{3,80})\'')
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PII_KEY_RE = re.compile(
    r'(?i)("(?:email|e-mail|phone|mobile|username|user_name|userName|'
    r'password|secret)"\s*:\s*")([^"]+)(")'
)
_RECORD_NAME_RE = re.compile(
    r'(?i)("(?:(?:full_)?name|displayName|firstName|lastName|phone|mobile|'
    r'username|userName|email)"\s*:\s*")([^"]{2,80})(")'
)

ASK_INVESTIGATION_CONSTRAINTS = (
    "Search progressively: filename/path/index and exact symbol/UID/phrase first, "
    "then likely modules, then definitions/references, then open authoritative files. "
    "Do not use lookup/logs/**, bulk_apply_jobs, dry-run exports, generated artifacts, "
    "or huge reference-json/metadata dumps as normal evidence unless the question is "
    "about logs, history, or those files. Bound rg (`-m 40 --max-count 40 --max-columns 240`, "
    "`--glob '*.py'` or an explicit path; `--glob '!lookup/logs/**'`). If output exceeds "
    "~80 lines or ~8KB, stop and refine; never paste entire huge JSON/CSV records. "
    "On Windows/PowerShell do not pass shell globs such as `tests *.py` or `lookup/test_*`; "
    "use rg `--glob` or explicit paths. A failed command is not a successful inspection. "
    "A timed-out search is also a failure — refine rather than broaden into a full-repo dump. "
    "Prefer paths, counts, matched symbols, and bounded excerpts in working notes."
)


def normalize_rel(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("/")


def looks_like_uid(value: str) -> bool:
    token = str(value or "")
    return (
        len(token) == 11
        and token[0].isalpha()
        and token.isalnum()
        and any(ch.isdigit() for ch in token)
    )


def is_implementation_question(prompt: str) -> bool:
    return bool(_IMPLEMENTATION_RE.search(str(prompt or "")))


def is_logs_history_query(prompt: str) -> bool:
    return bool(_LOGS_HISTORY_RE.search(str(prompt or "")))


def is_simple_reference_query(prompt: str) -> bool:
    text = str(prompt or "")
    if not _SIMPLE_REFERENCE_RE.search(text):
        return False
    if is_implementation_question(text) and not re.search(
        r"\b(what file defines|which file defines|name of|provinces?|program stage containing)\b",
        text,
        re.I,
    ):
        return False
    return True


def is_noisy_artifact(path: str) -> bool:
    rel = f"/{normalize_rel(path).lower()}"
    if any(marker in rel for marker in NOISY_PATH_MARKERS):
        return True
    parts = [part.lower() for part in normalize_rel(path).split("/") if part]
    return any(part in NOISY_DIR_PARTS for part in parts[:-1])


def is_large_reference_dump(path: str, size: int | None = None) -> bool:
    rel = normalize_rel(path).lower()
    name = rel.rsplit("/", 1)[-1]
    suffix = Path(rel).suffix.lower()
    if any(marker in f"/{rel}" for marker in LARGE_DUMP_PATH_MARKERS) and suffix in LARGE_DUMP_SUFFIXES:
        return True
    if name in LARGE_DUMP_BASENAMES:
        return True
    if size is not None and suffix in LARGE_DUMP_SUFFIXES:
        if size >= HUGE_FILE_BYTES:
            return True
        if size >= LARGE_FILE_BYTES and any(
            marker in f"/{rel}" for marker in ("/logs/", "bulk_apply", "dry-run", "dry_run", "export")
        ):
            return True
    return False


def should_skip_as_evidence(path: str, prompt: str, *, explicit: bool = False) -> bool:
    if explicit:
        return False
    if is_noisy_artifact(path) and not is_logs_history_query(prompt):
        return True
    return False


def path_matches_query_markers(path: str, needles: list[str] | tuple[str, ...] | set[str]) -> bool:
    rel = normalize_rel(path).lower()
    return any(str(needle).lower() in rel for needle in needles if needle)


def ranking_adjustment(path: str, *, prompt: str = "") -> int:
    rel = normalize_rel(path).lower()
    adj = 0
    if is_noisy_artifact(path) and not is_logs_history_query(prompt):
        adj -= 80
    if is_large_reference_dump(path):
        adj -= 35
    if any(
        marker in rel
        for marker in ("/hierarchy", "org_unit", "orgunit", "/reference/", "index.")
    ):
        adj += 10
    return adj


def extract_reference_phrases(text: str) -> list[str]:
    out: list[str] = []
    raw = str(text or "")
    for match in _QUOTED_RE.finditer(raw):
        out.append(match.group(1) or match.group(2))
    for match in _REGION_PHRASE_RE.finditer(raw):
        out.append(match.group(0))
    for match in _PROPER_NAME_RE.finditer(raw):
        out.append(match.group(1))
    for match in _UID_RE.finditer(raw):
        token = match.group(1)
        if looks_like_uid(token):
            out.append(token)
    return list(dict.fromkeys(item.strip() for item in out if item and item.strip()))


def bounded_matching_excerpt(
    text: str,
    needles: list[str] | tuple[str, ...],
    *,
    max_lines: int = BOUNDED_EXCERPT_LINES,
    max_chars: int = BOUNDED_EXCERPT_CHARS,
) -> str:
    lines: list[str] = []
    lowered = [str(needle).lower() for needle in needles if needle]
    if not lowered:
        clipped = str(text or "")[:max_chars]
        return clipped
    for line in str(text or "").splitlines():
        low = line.lower()
        if any(needle in low for needle in lowered):
            clipped = line.strip()
            if len(clipped) > 240:
                clipped = clipped[:239] + "…"
            lines.append(clipped)
            if len(lines) >= max_lines:
                break
    return "\n".join(lines)[:max_chars]


def redact_search_snippet(text: str, *, path: str = "") -> str:
    """Hide record-level PII in search diagnostics; keep geographic names elsewhere."""
    out = _EMAIL_RE.sub("[redacted-email]", str(text or ""))
    out = _PII_KEY_RE.sub(r"\1[redacted]\3", out)
    if is_noisy_artifact(path):
        out = _RECORD_NAME_RE.sub(r"\1[redacted]\3", out)
        out = _EMAIL_RE.sub("[redacted-email]", out)
    return out


def skip_path_substrings_for_prompt(prompt: str) -> tuple[str, ...]:
    lower = str(prompt or "").lower()
    skip: list[str] = []
    if not is_logs_history_query(prompt):
        skip.extend(NOISY_PATH_MARKERS)
    if "metadata.json" not in lower and "reference-json" not in lower:
        skip.extend(LARGE_DUMP_PATH_MARKERS)
        skip.append("metadata.json")
    return tuple(skip)
