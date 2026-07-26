"""Relevant-file selection with secret exclusion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hub.agent_center.models import MAX_CONTEXT_FILE_CHARS, MAX_CONTEXT_FILES
from hub.agent_center.secrets import is_secret_path

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{2,}")
_CODE_GLOBS = ("*.py", "*.md", "*.yaml", "*.yml", "*.json", "*.toml", "*.txt", "*.js", "*.ts")


def select_relevant_files(
    repo_root: Path,
    *,
    repo_id: str,
    prompt: str,
    hints: list[str] | None = None,
    explicit_rel_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    root = Path(repo_root)
    chosen: list[tuple[str, str]] = []  # (rel, reason)
    seen: set[str] = set()

    for rel in explicit_rel_paths or []:
        rel_n = rel.replace("\\", "/").lstrip("./")
        if rel_n in seen:
            continue
        if _safe_rel(root, rel_n) is None:
            continue
        seen.add(rel_n)
        chosen.append((rel_n, "explicit"))

    tokens = {t.lower() for t in _TOKEN.findall(prompt or "")}
    for hint in hints or []:
        tokens.update(t.lower() for t in _TOKEN.findall(hint))

    candidates: list[tuple[int, str]] = []
    for pattern in _CODE_GLOBS:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            if is_secret_path(path, repo_root=root):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel in seen:
                continue
            score = _score(rel, tokens)
            if score > 0:
                candidates.append((score, rel))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    for score, rel in candidates:
        if len(chosen) >= MAX_CONTEXT_FILES:
            break
        seen.add(rel)
        chosen.append((rel, f"keyword:{score}"))

    # Always try a few top-level docs if still empty.
    if not chosen:
        for name in ("README.md", "AGENTS.md", "AI_REFERENCE.md"):
            if (root / name).is_file() and not is_secret_path(root / name, repo_root=root):
                chosen.append((name, "fallback-doc"))
                if len(chosen) >= 3:
                    break

    out: list[dict[str, Any]] = []
    for rel, reason in chosen[:MAX_CONTEXT_FILES]:
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        clipped = text[:MAX_CONTEXT_FILE_CHARS]
        out.append(
            {
                "repo_id": repo_id,
                "path": rel,
                "chars": len(clipped),
                "truncated": len(text) > len(clipped),
                "reason": reason,
                "content": clipped,
            }
        )
    return out


def _safe_rel(root: Path, rel: str) -> Path | None:
    try:
        path = (root / rel).resolve()
        path.relative_to(root.resolve())
    except Exception:  # noqa: BLE001
        return None
    if not path.is_file() or is_secret_path(path, repo_root=root):
        return None
    return path


def _score(rel: str, tokens: set[str]) -> int:
    if not tokens:
        return 0
    hay = rel.lower().replace("\\", "/")
    score = 0
    for tok in tokens:
        if tok in hay:
            score += 3
        base = Path(hay).stem
        if tok == base or tok in base:
            score += 2
    return score
