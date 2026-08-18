"""CLIMATE Chat context scope — what sources a chat run may use.

General is the default: no repository limitation and no implied VANTA inheritance.
All Repositories searches connected repos and keeps only relevant bounded hits.
A specific repository keeps strict validation and that repo's evidence only.
"""

from __future__ import annotations

from typing import Any

from hub.agent_center.repository_context import explicit_repository_id, is_placeholder_repository_id

GENERAL = "general"
ALL = "all"
REPOSITORY = "repository"
CONTEXT_SCOPES = (GENERAL, ALL, REPOSITORY)

SCOPE_LABELS = {
    GENERAL: "General",
    ALL: "All Repositories",
    REPOSITORY: "Repository",
}

_GENERAL_ALIASES = {
    "",
    "general",
    "none",
    "null",
    "no-repository",
    "no_repository",
    "norepository",
    "no repository",
}
_ALL_ALIASES = {
    "all",
    "all-repositories",
    "all_repositories",
    "all repositories",
    "repositories",
}


def normalize_context_scope(value: Any, repository_id: Any = "") -> str:
    raw = str(value or "").strip().lower()
    repo_id = explicit_repository_id(repository_id)
    if raw in _ALL_ALIASES:
        return ALL
    if raw in _GENERAL_ALIASES or is_placeholder_repository_id(raw):
        return REPOSITORY if repo_id else GENERAL
    if raw in {"repository", "repo", "specific"}:
        return REPOSITORY if repo_id else GENERAL
    if explicit_repository_id(raw):
        return REPOSITORY
    return REPOSITORY if repo_id else GENERAL


def resolve_chat_scope(payload: dict[str, Any] | None) -> tuple[str, str]:
    """Return (scope, repository_id) from a chat run payload."""
    data = payload or {}
    repo_id = explicit_repository_id(data.get("repository_id"))
    raw_scope = data.get("context_scope")
    if raw_scope is None or str(raw_scope).strip() == "":
        if repo_id:
            return REPOSITORY, repo_id
        return GENERAL, ""
    scope = normalize_context_scope(raw_scope, repo_id)
    if scope == REPOSITORY:
        rid = explicit_repository_id(raw_scope) or repo_id
        return (REPOSITORY, rid) if rid else (GENERAL, "")
    return scope, ""
