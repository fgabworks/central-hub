"""Configurable size / scan limits for Repository Workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_int(name: str, default: int, *, minimum: int = 1, maximum: int = 100_000_000) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class WorkspaceSettings:
    max_preview_bytes: int = 1_048_576
    max_edit_bytes: int = 524_288
    max_search_file_bytes: int = 262_144
    max_search_matches: int = 200
    max_search_files: int = 2_000
    max_tree_entries: int = 5_000
    max_tree_depth: int = 16
    git_timeout_seconds: float = 8.0
    open_timeout_seconds: float = 5.0


def load_workspace_settings() -> WorkspaceSettings:
    return WorkspaceSettings(
        max_preview_bytes=_as_int("REPO_WS_MAX_PREVIEW_BYTES", 1_048_576, maximum=20_000_000),
        max_edit_bytes=_as_int("REPO_WS_MAX_EDIT_BYTES", 524_288, maximum=10_000_000),
        max_search_file_bytes=_as_int(
            "REPO_WS_MAX_SEARCH_FILE_BYTES", 262_144, maximum=5_000_000
        ),
        max_search_matches=_as_int("REPO_WS_MAX_SEARCH_MATCHES", 200, maximum=5_000),
        max_search_files=_as_int("REPO_WS_MAX_SEARCH_FILES", 2_000, maximum=50_000),
        max_tree_entries=_as_int("REPO_WS_MAX_TREE_ENTRIES", 5_000, maximum=100_000),
        max_tree_depth=_as_int("REPO_WS_MAX_TREE_DEPTH", 16, maximum=64),
        git_timeout_seconds=float(
            _as_int("REPO_WS_GIT_TIMEOUT_SECONDS", 8, minimum=1, maximum=120)
        ),
        open_timeout_seconds=float(
            _as_int("REPO_WS_OPEN_TIMEOUT_SECONDS", 5, minimum=1, maximum=60)
        ),
    )
