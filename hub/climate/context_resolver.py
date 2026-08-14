"""Deterministic CLIMATE Context Resolver (0 AI tokens).

Resolves instructions/skills/evidence locally before any coding-provider call.
Reuses Repository Intelligence, repo search, and instruction loaders — does not
create a parallel AiriX/router/runtime.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hub.agent_center.context import load_repo_instructions
from hub.agent_center.context_files import select_relevant_files
from hub.agent_center.models import (
    INSTRUCTION_FILENAMES,
    MAX_CONTEXT_FILE_CHARS,
    MAX_INSTRUCTION_CHARS,
)
from hub.agent_center.secrets import is_secret_path
from hub.climate.coding import classify_task_mode
from hub.climate.domain_query import extract_domain_query, identifier_matches_query, score_source
from hub.climate.repo_graph import concept_file_hints
from hub.registry.models import Repository

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{2,}")
_HEADING = re.compile(r"(?m)^(#{1,3})\s+(.+)$")
_META_LINE = re.compile(
    r"(?im)^(description|triggers|paths|modes|capabilities|tags)\s*:\s*(.+)$"
)
_SYMBOL = re.compile(r"\b(?:def|class|function|const|let|var|interface|type)\s+([A-Za-z_][\w]*)")
_PATH_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|cs))"
)
_BACKTICK_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})`")

_CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".cs",
}
_PROMPT_NOISE = {
    "anything", "cite", "exact", "files", "functions", "give", "implementation",
    "explain", "logic", "edit", "nothing", "please", "source", "sources", "the", "this",
}

PROVIDER_INSTRUCTION_FILES = {
    "codex": ("CODEX.md", ".codex.md"),
    "claude-code": ("CLAUDE.md",),
    "cursor-agent": (".cursorrules", ".cursor/rules"),
}

GATE_MESSAGE = "Not enough repository evidence. Model not invoked · 0 tokens."
MAX_PACKET_CHARS = 18_000
MAX_SKILL_CHARS = 4_000
MAX_SOURCE_CHARS = 8_000
MAX_SOURCES = 8
MAX_SEARCH_HITS = 12
MAX_CANDIDATES = 64

# Compatibility aliases for older imports/tests.
CouldNotFindMessage = GATE_MESSAGE


@dataclass
class ContextResolverResult:
    ok: bool
    task_mode: str
    provider_invoked: bool
    message: str
    packet: str
    confidence: str = "low"
    activity: list[str] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    context_chars: int = 0
    context_tokens_est: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def activity_log(self) -> str:
        lines = ["[climate_context_resolver]"]
        for step in self.activity:
            lines.append(step)
        if not self.ok:
            lines.append("No model invoked · 0 tokens")
            lines.append(GATE_MESSAGE)
        diag = [
            f"instruction_files={','.join(self.instruction_files) or '(none)'}",
            f"skills_used={','.join(self.skills_used) or '(none)'}",
            f"source_files={','.join(self.source_files) or '(none)'}",
            f"candidates_found={self.diagnostics.get('candidates_found', 0)}",
            f"authoritative_sources={','.join(self.diagnostics.get('authoritative_sources') or []) or '(none)'}",
            f"context_chars={self.context_chars}",
            f"context_tokens_est={self.context_tokens_est}",
            f"confidence={self.confidence}",
            f"provider_invoked={'Yes' if self.provider_invoked else 'No'}",
            "current_run_tokens=0",
        ]
        for item in list(self.diagnostics.get("qualification") or []):
            functions = ",".join(item.get("functions") or item.get("symbols") or []) or "(none)"
            diag.append(
                "evidence "
                f"file={item.get('path') or item.get('file')} "
                f"function/symbol={functions} score={item.get('score', 0)} "
                f"accepted={'Yes' if item.get('accepted') else 'No'} "
                f"reason={item.get('reason') or 'n/a'}"
            )
        lines.append("[climate_context_resolver_diagnostics]")
        lines.extend(diag)
        return "\n".join(lines)


# Backward-compatible name used by service/tests.
PreflightResult = ContextResolverResult


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 2}


def _score_text(haystack: str, tokens: set[str]) -> int:
    lower = (haystack or "").lower()
    if not tokens or not lower:
        return 0
    score = 0
    for token in tokens:
        if token in lower:
            score += 3 if token in lower.split("/")[-1] else 1
    return score


def _estimate_tokens(chars: int) -> int:
    return max(0, (int(chars) + 3) // 4)


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _source_excerpt(text: str, *, path: str, prompt: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """Keep the implementation hit, not just the beginning of a large file."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    lower = value.lower()
    domain = sorted(_domain_tokens(prompt))
    needles: list[str] = []
    if _is_code_path(path):
        for token in domain:
            needles.extend((f"def derive_{token}", f"function derive_{token}", f"def {token}_"))
    needles.extend(domain)
    position = next((lower.find(needle) for needle in needles if needle and lower.find(needle) >= 0), -1)
    if position < 0:
        return _clip(value, limit)
    start = max(0, position - 900)
    return _clip(value[start:], limit)


def _repo_root(repo: Repository) -> Path | None:
    raw = (repo.working_directory or repo.local_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _path_depth(path: str) -> int:
    return str(path or "").replace("\\", "/").count("/")


def _parse_meta_fields(body: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for match in _META_LINE.finditer(body or ""):
        meta[match.group(1).lower()] = match.group(2).strip()
    return meta


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,|;]", value or "") if part.strip()]


def _load_skills_markdown(root: Path, repo_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fname in ("SKILLS.md", "docs/SKILLS.md", "SKILLS/README.md"):
        path = root / fname
        if not path.is_file() or is_secret_path(path, repo_root=root):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        clipped = text[:MAX_INSTRUCTION_CHARS]
        out.append({
            "repo_id": repo_id,
            "path": fname,
            "chars": len(clipped),
            "truncated": len(text) > len(clipped),
            "content": clipped,
            "kind": "skills",
        })
    return out


def _nested_instruction_paths(root: Path, current_file: str) -> list[Path]:
    rel = (current_file or "").replace("\\", "/").lstrip("/")
    if not rel:
        return []
    try:
        base = root.resolve()
        start = (base / rel).parent
        start.relative_to(base)
    except Exception:  # noqa: BLE001
        return []
    found: list[Path] = []
    names = set(INSTRUCTION_FILENAMES) | {"SKILLS.md"}
    cursor = start
    while True:
        for name in names:
            path = cursor / name
            if path.is_file() and not is_secret_path(path, repo_root=root):
                found.append(path)
        if cursor == base:
            break
        parent = cursor.parent
        if parent == cursor:
            break
        try:
            parent.relative_to(base)
        except ValueError:
            break
        cursor = parent
    return found


def _load_path_instruction(path: Path, root: Path, repo_id: str) -> dict[str, Any] | None:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    clipped = text[:MAX_INSTRUCTION_CHARS]
    return {
        "repo_id": repo_id,
        "path": rel,
        "chars": len(clipped),
        "truncated": len(text) > len(clipped),
        "content": clipped,
        "kind": "instruction",
    }


def select_relevant_skill_sections(
    skills_content: str,
    *,
    prompt: str,
    path: str = "SKILLS.md",
    current_file: str = "",
    search_paths: list[str] | None = None,
    task_mode: str = "ask",
    max_chars: int = MAX_SKILL_CHARS,
) -> list[dict[str, Any]]:
    """Deterministic skill match via name/description/triggers/paths/modes/capabilities."""
    tokens = _tokens(prompt)
    path_tokens = _tokens(current_file)
    search_blob = " ".join(search_paths or []).lower()
    text = (skills_content or "").strip()
    if not text:
        return []

    matches = list(_HEADING.finditer(text))
    sections: list[dict[str, Any]] = []
    if not matches:
        score = _score_skill_blob(
            name=path,
            body=text,
            tokens=tokens,
            path_tokens=path_tokens,
            search_blob=search_blob,
            task_mode=task_mode,
        )
        if score <= 0:
            return []
        clipped = _clip(text, max_chars)
        return [{
            "path": path,
            "name": path,
            "score": score,
            "content": clipped,
            "chars": len(clipped),
            "meta": _parse_meta_fields(text),
        }]

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        title = match.group(2).strip()
        # Skip top-level status headings that are catalogs, not skills.
        if title.lower() in {"available", "partial", "placeholder / planned", "skills", "capability status"}:
            # Table rows under Available can still be skills — parse pipe rows.
            for row in re.findall(r"(?m)^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", body):
                name = row[0].strip().strip("*")
                desc = row[1].strip()
                if name.lower() in {"capability", "where", "---", ""}:
                    continue
                score = _score_skill_blob(
                    name=name,
                    body=desc,
                    tokens=tokens,
                    path_tokens=path_tokens,
                    search_blob=search_blob,
                    task_mode=task_mode,
                    meta={"description": desc, "capabilities": name},
                )
                if score > 0:
                    sections.append({
                        "path": path,
                        "name": name,
                        "score": score,
                        "content": f"{name}: {desc}",
                        "chars": len(name) + len(desc) + 2,
                        "meta": {"description": desc, "capabilities": name},
                    })
            continue
        meta = _parse_meta_fields(body)
        score = _score_skill_blob(
            name=title,
            body=body,
            tokens=tokens,
            path_tokens=path_tokens,
            search_blob=search_blob,
            task_mode=task_mode,
            meta=meta,
        )
        if score <= 0:
            continue
        sections.append({
            "path": path,
            "name": title,
            "score": score,
            "content": body,
            "chars": len(body),
            "meta": meta,
        })

    sections.sort(key=lambda row: (-int(row["score"]), str(row["name"])))
    chosen: list[dict[str, Any]] = []
    remaining = max_chars
    seen: set[str] = set()
    for row in sections:
        key = str(row.get("name") or "").lower()
        if key in seen or remaining <= 0:
            continue
        seen.add(key)
        clipped = _clip(str(row["content"]), remaining)
        chosen.append({**row, "content": clipped, "chars": len(clipped)})
        remaining -= len(clipped)
    return chosen


def _score_skill_blob(
    *,
    name: str,
    body: str,
    tokens: set[str],
    path_tokens: set[str],
    search_blob: str,
    task_mode: str,
    meta: dict[str, str] | None = None,
) -> int:
    meta = meta or _parse_meta_fields(body)
    score = 0
    name_l = (name or "").lower()
    desc = str(meta.get("description") or "")
    triggers = [t.lower() for t in _split_csv(meta.get("triggers", ""))]
    paths = [p.lower() for p in _split_csv(meta.get("paths", ""))]
    modes = [m.lower() for m in _split_csv(meta.get("modes", ""))]
    caps = [c.lower() for c in _split_csv(meta.get("capabilities", ""))]

    score += _score_text(name_l, tokens) * 2
    score += _score_text(desc, tokens)
    score += _score_text(body[:1500], tokens)

    for trigger in triggers:
        if trigger and (trigger in " ".join(tokens) or any(trigger in t or t in trigger for t in tokens)):
            score += 8
    for p in paths:
        if not p:
            continue
        if p in (search_blob or "") or any(p in sp for sp in (search_blob or "").split()):
            score += 10
        if path_tokens and _score_text(p, path_tokens):
            score += 8
    if modes:
        if task_mode.lower() in modes:
            score += 6
        elif modes and task_mode.lower() not in modes:
            score -= 4
    score += _score_text(" ".join(caps), tokens)
    score += _score_text(name_l, path_tokens)
    return score


def select_applicable_instructions(
    items: list[dict[str, Any]],
    *,
    prompt: str,
    provider: str,
    current_file: str = "",
    max_chars: int = 8_000,
) -> list[dict[str, Any]]:
    """Prefer nearest/more-specific instructions; never inject the whole library."""
    tokens = _tokens(prompt) | _tokens(current_file)
    provider_names = {n.lower() for n in PROVIDER_INSTRUCTION_FILES.get(provider, ())}
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        path = str(item.get("path") or "").replace("\\", "/")
        name = path.split("/")[-1].lower()
        content = str(item.get("content") or "")
        if name == "skills.md" or path.lower().endswith("/skills.md"):
            continue
        score = 0
        depth = _path_depth(path)
        if name == "agents.md":
            # Nested/scoped AGENTS beat root AGENTS.
            score += 40 + depth * 20
        if name in provider_names or any(path.lower().endswith(p.lower()) for p in provider_names):
            score += 18 + depth * 5
        if name in {"ai_reference.md", "ai_start_here.md", "security.md", ".cursorrules"}:
            score += (10 if _score_text(content, tokens) else 0) + depth * 3
        score += _score_text(path + "\n" + content[:2000], tokens)
        if current_file and path.rsplit("/", 1)[0] and current_file.replace("\\", "/").startswith(path.rsplit("/", 1)[0] + "/"):
            score += 12
        if score <= 0 and name != "agents.md":
            continue
        scored.append((score, {**item, "path": path}))

    # Prefer higher score, then deeper path (more specific).
    scored.sort(key=lambda pair: (-pair[0], -_path_depth(str(pair[1].get("path") or "")), str(pair[1].get("path") or "")))

    # Deduplicate same basename: keep the most specific (deepest) first; keep a
    # short root AGENTS pointer only when nested exists.
    chosen: list[dict[str, Any]] = []
    remaining = max_chars
    seen_basenames: dict[str, str] = {}
    seen_paths: set[str] = set()
    for score, item in scored:
        path = str(item.get("path") or "")
        base = path.split("/")[-1].lower()
        if path in seen_paths or remaining <= 0:
            continue
        if base in seen_basenames:
            # Broader duplicate — keep only a compact pointer if budget remains.
            prior = seen_basenames[base]
            if _path_depth(prior) >= _path_depth(path):
                pointer = f"(See also broader {path}; nearer guidance already included.)"
                if remaining > 80:
                    chosen.append({
                        **item,
                        "content": pointer,
                        "chars": len(pointer),
                        "score": score,
                        "deduped": True,
                    })
                    remaining -= len(pointer)
                    seen_paths.add(path)
                continue
        limit = min(remaining, 3500 if base == "agents.md" and _path_depth(path) > 0 else 2500)
        content = _clip(str(item.get("content") or ""), limit)
        if not content:
            continue
        seen_paths.add(path)
        seen_basenames.setdefault(base, path)
        chosen.append({**item, "content": content, "chars": len(content), "score": score})
        remaining -= len(content)
    return chosen


def _implementation_question(prompt: str) -> bool:
    lower = str(prompt or "").lower()
    return bool(re.search(r"\b(implementation|implement|logic|function|symbol|code|derive[ds]?|scor(?:e|ing))\b", lower))


def _is_test_path(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    name = rel.rsplit("/", 1)[-1]
    return (
        "/test" in f"/{rel}"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_code_path(path: str) -> bool:
    return Path(str(path or "")).suffix.lower() in _CODE_SUFFIXES


def _domain_tokens(prompt: str) -> set[str]:
    query = extract_domain_query(prompt)
    needles = {t.lower() for t in (*query.acronyms, *query.aliases, *query.strong) if t}
    if needles:
        return needles
    return {token for token in _tokens(prompt) if token not in _PROMPT_NOISE and token not in query.weak}


def _identifier_matches_domain(value: str, domain: set[str] | None = None, *, prompt: str = "") -> bool:
    if prompt:
        return identifier_matches_query(value, extract_domain_query(prompt))
    parts = {part for part in re.split(r"[^a-z0-9]+", str(value or "").lower()) if part}
    return any(token in parts for token in (domain or set()))


def _authority_question(prompt: str) -> bool:
    if _implementation_question(prompt):
        return True
    query = extract_domain_query(prompt)
    return bool(query.phrases or query.acronyms)


def _qualify_source_rows(
    source_rows: list[dict[str, Any]], *, prompt: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rank candidates and identify executable implementation authority."""
    authority = _authority_question(prompt)
    query = extract_domain_query(prompt)
    qualified: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for row in source_rows:
        path = str(row.get("path") or "")
        content = str(row.get("content") or "")
        symbols = list(dict.fromkeys(_SYMBOL.findall(content)))[:24]
        relevant_symbols = [
            symbol for symbol in symbols
            if identifier_matches_query(symbol, query)
        ]
        code = _is_code_path(path)
        test = _is_test_path(path)
        path_domain_match = identifier_matches_query(path, query)
        score = int(row.get("score") or 0)
        rank_score = score
        accepted = False
        reason = "supporting_candidate"

        if authority:
            if test:
                rank_score -= 10
                reason = "test_support_only"
            elif not code:
                rank_score -= 6
                reason = "documentation_support_only"
            elif relevant_symbols:
                rank_score += 24 + min(12, len(relevant_symbols) * 3)
                accepted = True
                reason = "executable_relevant_symbol"
            elif path_domain_match and symbols:
                rank_score += 18
                accepted = True
                reason = "executable_path_and_symbols"
            elif str(row.get("reason") or "") == "selected":
                rank_score += 14
                accepted = True
                reason = "explicit_executable_selection"
            else:
                rank_score += 4 if code else 0
                reason = "executable_without_relevant_symbol"
        else:
            accepted = not test
            reason = "relevant_source" if accepted else "test_support_only"
            rank_score += 8 if code else 0

        enriched = {
            **row,
            "rank_score": rank_score,
            "symbols": symbols,
            "relevant_symbols": relevant_symbols,
            "authoritative": accepted,
            "qualification_reason": reason,
        }
        if accepted:
            qualified.append(enriched)
        diagnostics.append({
            "path": path,
            "file": path,
            "functions": relevant_symbols or symbols[:6],
            "symbols": relevant_symbols or symbols[:6],
            "score": rank_score,
            "accepted": accepted,
            "reason": reason,
            "retrieval_reason": str(row.get("reason") or ""),
        })

    qualified.sort(key=lambda row: (-int(row.get("rank_score") or 0), str(row.get("path") or "")))
    diagnostics.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("path") or "")))
    return qualified, diagnostics


def _confidence(
    *,
    authoritative_rows: list[dict[str, Any]],
) -> str:
    if len(authoritative_rows) >= 2:
        return "high"
    if len(authoritative_rows) == 1:
        row = authoritative_rows[0]
        if str(row.get("reason") or "") == "selected" or len(row.get("relevant_symbols") or []) >= 2:
            return "high"
        return "medium"
    return "low"


def _has_primary_implementation(rows: list[dict[str, Any]], *, prompt: str) -> bool:
    domain = _domain_tokens(prompt)
    for row in rows:
        for symbol in row.get("relevant_symbols") or []:
            lower = str(symbol).lower()
            if any(
                lower.startswith(f"derive_{token}")
                or lower.startswith(f"compute_{token}")
                or lower.startswith(f"calculate_{token}")
                for token in domain
            ):
                return True
    return False


def resolve_climate_context(
    *,
    workspace: str,
    repo: Repository,
    repository_workspace: Any,
    prompt: str,
    provider: str,
    model: str = "",
    task_mode: str | None = None,
    current_file: str = "",
    selected_files: list[str] | None = None,
    selection: str = "",
    include_repo_context: bool = False,
    repository_intelligence: Any | None = None,
    handoff: bool = False,
    repository_agent: bool = False,
) -> ContextResolverResult:
    """Local zero-token context resolution and repository-agent acceleration."""
    mode = classify_task_mode(prompt, task_mode)
    activity: list[str] = ["Resolving repo"]
    root = _repo_root(repo)
    if root is None:
        result = ContextResolverResult(
            ok=False,
            task_mode=mode,
            provider_invoked=False,
            message=GATE_MESSAGE,
            packet="",
            confidence="low",
            activity=activity + ["Loading instructions", "Matching skill", "Searching repo", "Found 0 sources"],
        )
        result.diagnostics = {"reason": "repository_path_missing", "repository_id": repo.id, "confidence": "low"}
        return result

    activity.append(f"Repo resolved: {repo.id}")
    activity.append("Loading instructions")

    instruction_items = load_repo_instructions(root, repo_id=repo.id)
    for nested in _nested_instruction_paths(root, current_file):
        loaded = _load_path_instruction(nested, root, repo.id)
        if not loaded:
            continue
        key = str(loaded.get("path") or "")
        if any(str(item.get("path") or "") == key for item in instruction_items):
            continue
        instruction_items.append(loaded)

    applicable = select_applicable_instructions(
        instruction_items,
        prompt=prompt,
        provider=provider,
        current_file=current_file,
        max_chars=8_000 if include_repo_context else 5_000,
    )
    instruction_files = [str(item.get("path") or "") for item in applicable]
    activity.append(f"Instructions loaded: {len(instruction_files)}")

    tokens = _tokens(prompt)
    domain_query = extract_domain_query(prompt)
    source_rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    expansion_terms: list[str] = []
    expansion_paths: list[str] = []
    resolver_queries: list[str] = []

    def _add_source(path: str, content: str, *, reason: str, score: int = 1) -> None:
        rel = path.replace("\\", "/").lstrip("/")
        if not rel or rel in seen_paths:
            return
        clipped = _source_excerpt(content, path=rel, prompt=prompt)
        if not clipped.strip():
            return
        seen_paths.add(rel)
        source_rows.append({
            "path": rel,
            "content": clipped,
            "chars": len(clipped),
            "reason": reason,
            "score": score,
        })

    def _gather(expand: bool = False) -> None:
        explicit = list(dict.fromkeys(
            [p for p in ([current_file] + list(selected_files or [])) if str(p).strip()]
        ))
        if not expand:
            for path in explicit[:12]:
                try:
                    data = repository_workspace.preview(repo, path)
                except Exception:  # noqa: BLE001
                    continue
                if data.get("binary") or data.get("error"):
                    continue
                content = str(data.get("content") or "")
                score = 40 + score_source(path, content, domain_query)
                _add_source(path, content, reason="selected", score=score)

            if repository_intelligence is not None and workspace == "work":
                try:
                    retrieved = repository_intelligence.retrieve([repo.id], prompt, limit=6)
                except Exception:  # noqa: BLE001
                    retrieved = {}
                for item in list((retrieved or {}).get("items") or [])[:6]:
                    path = str(item.get("path") or "")
                    summary = str(item.get("summary") or item.get("title") or "")
                    if not path:
                        continue
                    try:
                        data = repository_workspace.preview(repo, path)
                        if not data.get("binary") and not data.get("error") and data.get("content"):
                            _add_source(
                                path,
                                str(data.get("content") or ""),
                                reason=f"ri:{item.get('category') or 'knowledge'}",
                                score=int(item.get("score") or 5) + 10,
                            )
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                    if summary:
                        _add_source(path, summary, reason="ri-summary", score=int(item.get("score") or 3))

            if root is not None:
                try:
                    for hint in concept_file_hints(root, prompt, limit=8):
                        path = str(hint.get("path") or "")
                        if not path or path in seen_paths:
                            continue
                        try:
                            data = repository_workspace.preview(repo, path)
                        except Exception:  # noqa: BLE001
                            continue
                        if data.get("binary") or data.get("error"):
                            continue
                        content = str(data.get("content") or "")
                        _add_source(
                            path,
                            content,
                            reason=str(hint.get("reason") or "graph"),
                            score=int(hint.get("score") or 12) + score_source(path, content, domain_query),
                        )
                except Exception:  # noqa: BLE001
                    pass

        if expand:
            for path in expansion_paths[:24]:
                if path in seen_paths:
                    continue
                try:
                    data = repository_workspace.preview(repo, path)
                except Exception:  # noqa: BLE001
                    continue
                if data.get("binary") or data.get("error"):
                    continue
                content = str(data.get("content") or "")
                score = 16 + score_source(path, content, domain_query)
                _add_source(path, content, reason="expand:reference", score=score)

        if expand:
            query_terms = expansion_terms[:4] or domain_query.search_terms()[:6]
        else:
            query_terms = domain_query.search_terms()[:8] or sorted(tokens, key=len, reverse=True)[:4]
        queries: list[str] = []
        if not expand:
            queries.extend(query_terms[:5])
        else:
            queries.extend(query_terms[:4])
            for sym in _SYMBOL.findall(prompt or ""):
                queries.append(sym)
            if current_file:
                queries.append(Path(current_file).stem)
        queries = [q for q in dict.fromkeys(queries) if str(q).strip()]
        resolver_queries.extend(queries)

        for search_q in queries:
            for mode_name in ("filename", "content"):
                try:
                    result = repository_workspace.search(repo, q=str(search_q), mode=mode_name)
                except Exception:  # noqa: BLE001
                    continue
                matches = list(result.get("matches") or [])
                if _authority_question(prompt):
                    matches.sort(key=lambda match: (
                        0 if _is_code_path(str(match.get("path") or "")) and not _is_test_path(str(match.get("path") or "")) else 1,
                        0 if identifier_matches_query(str(match.get("path") or ""), domain_query) else 1,
                        -score_source(str(match.get("path") or ""), "", domain_query),
                        str(match.get("path") or ""),
                    ))
                unique_matches: list[dict[str, Any]] = []
                unique_match_paths: set[str] = set()
                for match in matches:
                    match_path = str(match.get("path") or "")
                    if not match_path or match_path in unique_match_paths:
                        continue
                    unique_match_paths.add(match_path)
                    unique_matches.append(match)
                hit_limit = 8 if not expand else MAX_SEARCH_HITS
                for match in unique_matches[:hit_limit]:
                    path = str(match.get("path") or "")
                    if not path or path in seen_paths:
                        continue
                    try:
                        data = repository_workspace.preview(repo, path)
                    except Exception:  # noqa: BLE001
                        continue
                    if data.get("binary") or data.get("error"):
                        continue
                    content = str(data.get("content") or "")
                    score = score_source(path, content, domain_query)
                    if score <= 0 and mode_name == "content" and not expand:
                        continue
                    reason = f"{'expand' if expand else 'search'}:{mode_name}"
                    _add_source(path, content, reason=reason, score=max(1, score + (3 if expand else 0)))
                    if len(source_rows) >= MAX_CANDIDATES:
                        return

        if len(source_rows) < (3 if expand else 2):
            try:
                selected = select_relevant_files(
                    root,
                    repo_id=repo.id,
                    prompt=prompt,
                    hints=query_terms,
                    explicit_rel_paths=list(selected_files or []) + ([current_file] if current_file else []),
                )
            except Exception:  # noqa: BLE001
                selected = []
            for item in selected:
                path = str(item.get("path") or "")
                reason = str(item.get("reason") or "relevant")
                if reason.startswith("fallback") or path in seen_paths:
                    continue
                _add_source(
                    path,
                    str(item.get("content") or ""),
                    reason=("expand:" + reason) if expand else reason,
                    score=5 + (2 if expand else 0),
                )
                if len(source_rows) >= MAX_CANDIDATES:
                    break

    activity.append("Matching skill")
    # First pass search feeds skill path matching.
    activity.append("Searching repo")
    _gather(expand=False)
    provisional_paths = [str(row.get("path") or "") for row in source_rows]

    skills_docs = _load_skills_markdown(root, repo.id)
    skills_used: list[dict[str, Any]] = []
    for doc in skills_docs:
        skills_used.extend(
            select_relevant_skill_sections(
                str(doc.get("content") or ""),
                prompt=prompt,
                path=str(doc.get("path") or "SKILLS.md"),
                current_file=current_file,
                search_paths=provisional_paths,
                task_mode=mode,
            )
        )
    skill_names = [str(row.get("name") or row.get("path") or "") for row in skills_used]

    authoritative_rows, qualification = _qualify_source_rows(source_rows, prompt=prompt)
    has_instructions = bool(instruction_files)
    confidence = _confidence(authoritative_rows=authoritative_rows)

    expanded = False
    initial_evidence_weak = confidence != "high" or (
        _authority_question(prompt)
        and not _has_primary_implementation(authoritative_rows, prompt=prompt)
    )
    if initial_evidence_weak:
        # Exactly one deterministic local expansion. Follow concrete code paths and
        # terminology exposed by matched instructions, skills, docs, and symbols.
        expansion_blobs = [str(item.get("content") or "") for item in applicable]
        expansion_blobs.extend(str(item.get("content") or "") for item in skills_used)
        expansion_blobs.extend(str(row.get("content") or "") for row in source_rows[:MAX_CANDIDATES])
        term_candidates: list[str] = []
        path_candidates: list[str] = []
        domain = _domain_tokens(prompt)
        for blob in expansion_blobs:
            path_candidates.extend(_PATH_REFERENCE.findall(blob))
            symbols = _SYMBOL.findall(blob) + _BACKTICK_SYMBOL.findall(blob)
            term_candidates.extend(
                symbol for symbol in symbols if identifier_matches_query(symbol, domain_query)
            )
        for token in sorted(domain):
            term_candidates.extend((f"derive_{token}", f"{token}_score", f"{token}_rule", token))
        unique_terms = list(dict.fromkeys(term_candidates))
        unique_terms.sort(key=lambda value: (0 if str(value).startswith("derive_") else 1, -len(str(value)), str(value)))
        expansion_terms[:] = unique_terms[:12]
        unique_paths = list(dict.fromkeys(path.replace("\\", "/").lstrip("/") for path in path_candidates))
        unique_paths.sort(key=lambda value: (
            0 if _identifier_matches_domain(value, domain) else (1 if "derive" in value.lower() else 2),
            _is_test_path(value),
            value,
        ))
        expansion_paths[:] = unique_paths[:24]
        activity.append("Expanding local search")
        _gather(expand=True)
        expanded = True
        authoritative_rows, qualification = _qualify_source_rows(source_rows, prompt=prompt)
        confidence = _confidence(authoritative_rows=authoritative_rows)

    authoritative_paths = [str(row.get("path") or "") for row in authoritative_rows]
    authoritative_set = set(authoritative_paths)
    ranked_rows = sorted(
        source_rows,
        key=lambda row: (
            0 if str(row.get("path") or "") in authoritative_set else 1,
            -int(next((q.get("score") or 0 for q in qualification if q.get("path") == row.get("path")), row.get("score") or 0)),
            str(row.get("path") or ""),
        ),
    )[:MAX_SOURCES]
    source_rows = ranked_rows
    source_files = [str(row.get("path") or "") for row in source_rows]

    activity.append(f"Found {len(qualification)} candidates")
    activity.append(f"Selected {len(authoritative_paths)} authoritative sources")
    # Compatibility marker for the existing preflight activity renderer.
    activity.append(f"Found {len(source_files)} sources")
    activity.append(f"{len(source_files)} likely sources")

    diff_note = ""
    if mode == "edit":
        try:
            changes = repository_workspace.changes(repo)
            files = list(changes.get("files") or [])[:12]
            if files:
                names = ", ".join(str(f.get("path") or f) for f in files if f)[:500]
                diff_note = f"Current working tree changes: {names}"
        except Exception:  # noqa: BLE001
            diff_note = ""

    gate_ok = bool(repository_agent) or (confidence == "high" and bool(authoritative_paths))
    if not gate_ok:
        activity.append("Building context")
        result = ContextResolverResult(
            ok=False,
            task_mode=mode,
            provider_invoked=False,
            message=GATE_MESSAGE,
            packet="",
            confidence=confidence if confidence == "low" else "low",
            activity=activity,
            instruction_files=instruction_files,
            skills_used=skill_names,
            source_files=source_files,
        )
        result.diagnostics = {
            "reason": "insufficient_evidence",
            "repository_id": repo.id,
            "workspace": workspace,
            "has_instructions": has_instructions,
            "has_sources": bool(authoritative_paths),
            "candidates_found": len(qualification),
            "authoritative_sources": authoritative_paths,
            "qualification": qualification,
            "domain_terms": {
                "phrases": list(domain_query.phrases),
                "acronyms": list(domain_query.acronyms),
                "aliases": list(domain_query.aliases)[:16],
                "strong": list(domain_query.strong),
            },
            "resolver_queries": list(dict.fromkeys(resolver_queries))[:16],
            "confidence": result.confidence,
            "expanded_search": expanded,
            "context_chars": 0,
            "context_tokens_est": 0,
            "provider_invoked": False,
            "current_run_tokens": 0,
            "provider": provider,
            "model": model,
            "handoff": bool(handoff),
            "repository_agent": bool(repository_agent),
        }
        return result

    activity.append("Building context")
    provider_label = {
        "codex": "Codex",
        "claude-code": "Claude",
        "cursor-agent": "Cursor",
        "gemini": "Gemini",
    }.get(provider, provider or "provider")

    parts: list[str] = [
        f"CLIMATE context packet ({mode.upper()}).",
        f"Task:\n{(prompt or '').strip()}",
        f"Confidence: {confidence}",
        (
            "Repository access: the provider starts at the approved repository root and may "
            "independently search, read, and trace it. The items below are starting hints, not "
            "a complete evidence boundary."
            if repository_agent
            else "Repository access: bounded packet only."
        ),
    ]
    if applicable:
        parts.append("Applicable repository instructions (nearest/specific first):")
        for item in applicable:
            if item.get("deduped"):
                parts.append(str(item.get("content") or ""))
                continue
            parts.append(
                f"### {item.get('path')}\n{_clip(str(item.get('content') or ''), 3500)}"
            )
    if skills_used:
        parts.append("Relevant skill guidance (matched only):")
        for skill in skills_used:
            parts.append(
                f"### {skill.get('name')}\n{_clip(str(skill.get('content') or ''), 2000)}"
            )
    if source_rows and repository_agent:
        parts.append("Likely source hints (verify in the repository before answering):")
        qualification_by_path = {
            str(item.get("path") or ""): item for item in qualification
        }
        for row in source_rows:
            path = str(row.get("path") or "")
            qualified = qualification_by_path.get(path) or {}
            symbols = list(qualified.get("functions") or qualified.get("symbols") or [])[:8]
            label = "authoritative candidate" if path in authoritative_set else "supporting candidate"
            parts.append(
                f"- {path} | {label} | score={qualified.get('score', row.get('score', 0))} | "
                f"symbols={','.join(symbols) or '(none)'} | reason={qualified.get('reason') or row.get('reason')}"
            )
    elif source_rows:
        parts.append("Ranked repository evidence (authoritative implementation first):")
        for row in source_rows:
            evidence_label = "authoritative" if str(row.get("path") or "") in authoritative_set else "supporting"
            parts.append(
                f"### {row.get('path')} ({evidence_label}; {row.get('reason')})\n"
                f"{_clip(str(row.get('content') or ''), MAX_CONTEXT_FILE_CHARS)}"
            )
    if selection and selection.strip():
        parts.append("Selected code:\n" + _clip(selection.strip(), 8_000))
    if diff_note:
        parts.append(diff_note)
    if mode == "ask":
        if repository_agent:
            parts.append(
                "ASK/EXPLAIN constraints: investigate the repository as needed with safe read-only "
                "search, file/symbol/reference/import/test/git inspection commands. Do not modify "
                "files or repository state. Cite the exact implementation paths/functions you verify. "
                "Investigate progressively: exact symbol/acronym/phrase, then likely modules, then "
                "definitions/references, then open authoritative files; broaden only if evidence is "
                "insufficient. Prefer bounded rg (`--max-count`, `--glob '*.py'`). On Windows/"
                "PowerShell do not pass shell globs such as `tests *.py` or `lookup/test_*`; use "
                "rg `--glob` or explicit paths. A failed command is not a successful inspection."
            )
        else:
            parts.append(
                "ASK/EXPLAIN constraints: read-only evidence only; no edits/diffs; "
                "cite concrete paths/functions from this packet."
            )
    else:
        parts.append(
            "EDIT constraints: propose replacements only after using this evidence; "
            "do not apply edits at runtime."
        )
    if handoff:
        parts.append(
            "Cross-provider handoff: compact prior summary only — do not expect full CLIMATE history."
        )

    packet = _clip("\n\n".join(parts), MAX_PACKET_CHARS)
    activity.append(f"Asking {provider_label}")

    return ContextResolverResult(
        ok=True,
        task_mode=mode,
        provider_invoked=True,
        message="",
        packet=packet,
        confidence=confidence,
        activity=activity,
        instruction_files=instruction_files,
        skills_used=skill_names,
        source_files=source_files,
        context_chars=len(packet),
        context_tokens_est=_estimate_tokens(len(packet)),
        diagnostics={
            "repository_id": repo.id,
            "workspace": workspace,
            "provider": provider,
            "model": model,
            "handoff": bool(handoff),
            "repository_agent": bool(repository_agent),
            "include_repo_context": bool(include_repo_context),
            "confidence": confidence,
            "expanded_search": expanded,
            "candidates_found": len(qualification),
            "authoritative_sources": authoritative_paths,
            "qualification": qualification,
            "domain_terms": {
                "phrases": list(domain_query.phrases),
                "acronyms": list(domain_query.acronyms),
                "aliases": list(domain_query.aliases)[:16],
                "strong": list(domain_query.strong),
            },
            "resolver_queries": list(dict.fromkeys(resolver_queries))[:16],
            "context_chars": len(packet),
            "context_tokens_est": _estimate_tokens(len(packet)),
            "provider_invoked": True,
            "current_run_tokens": 0,
            "reuse_hint": "same_provider_session" if not handoff else "compact_handoff",
        },
    )


# Compatibility wrapper name.
run_climate_preflight = resolve_climate_context


def make_blocked_run(
    *,
    workspace: str,
    repository_id: str,
    provider: str,
    model: str,
    preflight: ContextResolverResult,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Synthetic completed run when the confidence gate blocks provider invocation."""
    rid = run_id or f"context-{int(time.time() * 1000)}"
    log = preflight.activity_log()
    return {
        "id": rid,
        "workspace": workspace,
        "repository_id": repository_id,
        "status": "completed",
        "provider": provider,
        "model": model,
        "answer": GATE_MESSAGE,
        "error": "",
        "logs": log,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "usage_source": "exact",
        },
        "cancel_requested": False,
        "created_at": int(time.time()),
        "finished_at": int(time.time()),
        "task_mode": preflight.task_mode,
        "provider_invoked": False,
        "preflight": {
            "ok": False,
            "activity": list(preflight.activity),
            "instruction_files": list(preflight.instruction_files),
            "skills_used": list(preflight.skills_used),
            "source_files": list(preflight.source_files),
            "context_chars": 0,
            "context_tokens_est": 0,
            "confidence": preflight.confidence,
            "provider_invoked": False,
            "diagnostics": dict(preflight.diagnostics),
        },
        "sources": list(preflight.source_files),
    }
