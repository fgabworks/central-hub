"""Load repository AI instruction files for agent context (read-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub.agent_center.models import INSTRUCTION_FILENAMES, MAX_INSTRUCTION_CHARS
from hub.agent_center.secrets import is_secret_path


def load_repo_instructions(repo_root: Path, *, repo_id: str = "") -> list[dict[str, Any]]:
    """Return instruction file snippets found under a repository root."""
    root = Path(repo_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    remaining = MAX_INSTRUCTION_CHARS
    for name in INSTRUCTION_FILENAMES:
        if remaining <= 0:
            break
        path = root / name
        if not path.is_file() or is_secret_path(path, repo_root=root):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text.strip()
        if not text:
            continue
        clipped = text[:remaining]
        out.append(
            {
                "repo_id": repo_id,
                "path": name,
                "chars": len(clipped),
                "truncated": len(text) > len(clipped),
                "content": clipped,
            }
        )
        remaining -= len(clipped)
    return out
