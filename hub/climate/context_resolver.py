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
from hub.registry.models import Repository

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{2,}")
_HEADING = re.compile(r"(?m)^(#{1,3})\s+(.+)$")
_META_LINE = re.compile(
    r"(?im)^(description|triggers|paths|modes|capabilities|tags)\s*:\s*(.+)$"
)
_SYMBOL = re.compile(r"\b(?:def|class|function|const|let|var|interface|type)\s+([A-Za-z_][\w]*)")

PROVIDER_INSTRUCTION_FILES = {
    "codex": ("CODEX.md", ".codex.md"),
    "claude-code": ("CLAUDE.md",),
    "cursor-agent": (".cursorrules", ".cursor/rules"),
}

GATE_MESSAGE = "Not enough repository evidence. Model not invoked · 0 tokens."
MAX_PACKET_CHARS = 24_000
MAX_SKILL_CHARS = 4_000
MAX_SOURCE_CHARS = 8_000
MAX_SOURCES = 8
MAX_SEARCH_HITS = 12

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
            f"context_chars={self.context_chars}",
            f"context_tokens_est={self.context_tokens_est}",
            f"confidence={self.confidence}",
            f"provider_invoked={'Yes' if self.provider_invoked else 'No'}",
            "current_run_tokens=0",
        ]
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


def _authoritative_sources(source_files: list[str], instruction_files: list[str]) -> list[str]:
    instruction_set = {p.replace("\\", "/").lstrip("/") for p in instruction_files}
    banned = {n.lower() for n in INSTRUCTION_FILENAMES} | {"skills.md"}
    out: list[str] = []
    for path in source_files:
        rel = path.replace("\\", "/").lstrip("/")
        name = rel.split("/")[-1].lower()
        if rel in instruction_set or name in banned or rel.lower() in {"skills.md", "docs/skills.md"}:
            continue
        out.append(rel)
    return out


def _confidence(
    *,
    has_instructions: bool,
    authoritative: list[str],
    source_rows: list[dict[str, Any]],
    skills_used: list[dict[str, Any]],
) -> str:
    if not has_instructions:
        return "low"
    auth = set(authoritative)
    strong = [
        row for row in source_rows
        if row.get("path") in auth and (
            int(row.get("score") or 0) >= 10
            or str(row.get("reason") or "") in {"selected", "expand:symbol", "expand:content"}
            or str(row.get("reason") or "").startswith("ri:")
        )
    ]
    if len(strong) >= 2 or (len(strong) >= 1 and skills_used):
        return "high"
    if len(authoritative) >= 1:
        return "medium" if len(strong) == 0 or not skills_used else "high"
    return "low"


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
) -> ContextResolverResult:
    """Local zero-token context resolution + confidence gate."""
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

    activity.append(f"Resolved repo {repo.id}")
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

    tokens = _tokens(prompt)
    source_rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def _add_source(path: str, content: str, *, reason: str, score: int = 1) -> None:
        rel = path.replace("\\", "/").lstrip("/")
        if not rel or rel in seen_paths:
            return
        clipped = _clip(content, MAX_SOURCE_CHARS)
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
                score = 40 + _score_text(path + "\n" + content[:1500], tokens)
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

        query_terms = sorted(tokens, key=len, reverse=True)[:8 if expand else 6]
        queries = []
        if query_terms:
            queries.append(" ".join(query_terms[:3]))
        if expand:
            queries.extend(query_terms[:4])
            for sym in _SYMBOL.findall(prompt or ""):
                queries.append(sym)
            if current_file:
                queries.append(Path(current_file).stem)

        for search_q in queries:
            if not str(search_q).strip():
                continue
            for mode_name in ("filename", "content"):
                try:
                    result = repository_workspace.search(repo, q=str(search_q), mode=mode_name)
                except Exception:  # noqa: BLE001
                    continue
                for match in list(result.get("matches") or [])[:MAX_SEARCH_HITS]:
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
                    score = _score_text(path + "\n" + content[:2000], tokens)
                    if score <= 0 and mode_name == "content" and not expand:
                        continue
                    reason = f"{'expand' if expand else 'search'}:{mode_name}"
                    _add_source(path, content, reason=reason, score=max(1, score + (3 if expand else 0)))
                    if len(source_rows) >= MAX_SOURCES:
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
                if len(source_rows) >= MAX_SOURCES:
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

    source_rows.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("path") or "")))
    source_rows[:] = source_rows[:MAX_SOURCES]
    source_files = [str(row.get("path") or "") for row in source_rows]
    authoritative = _authoritative_sources(source_files, instruction_files)
    has_instructions = bool(instruction_files)
    confidence = _confidence(
        has_instructions=has_instructions,
        authoritative=authoritative,
        source_rows=source_rows,
        skills_used=skills_used,
    )

    expanded = False
    if confidence == "medium":
        activity.append("Expanding local search")
        _gather(expand=True)
        expanded = True
        source_rows.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("path") or "")))
        source_rows[:] = source_rows[:MAX_SOURCES]
        source_files = [str(row.get("path") or "") for row in source_rows]
        authoritative = _authoritative_sources(source_files, instruction_files)
        confidence = _confidence(
            has_instructions=has_instructions,
            authoritative=authoritative,
            source_rows=source_rows,
            skills_used=skills_used,
        )
        # After one expansion, medium with authoritative source may promote to high.
        if confidence == "medium" and authoritative and has_instructions:
            confidence = "high"

    activity.append(f"Found {len(source_files)} sources")

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

    gate_ok = confidence == "high" and has_instructions and bool(authoritative)
    if confidence == "low" or not gate_ok:
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
            "has_sources": bool(authoritative),
            "confidence": result.confidence,
            "expanded_search": expanded,
            "provider": provider,
            "model": model,
            "handoff": bool(handoff),
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
    if source_rows:
        parts.append("Relevant code/docs:")
        for row in source_rows:
            parts.append(
                f"### {row.get('path')} ({row.get('reason')})\n"
                f"{_clip(str(row.get('content') or ''), MAX_CONTEXT_FILE_CHARS)}"
            )
    if selection and selection.strip():
        parts.append("Selected code:\n" + _clip(selection.strip(), 8_000))
    if diff_note:
        parts.append(diff_note)
    if mode == "ask":
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
            "include_repo_context": bool(include_repo_context),
            "confidence": confidence,
            "expanded_search": expanded,
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
