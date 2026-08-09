"""Repository context resolution for AiriX coding agents.

Priority (never blind first-of-many):
  1. Explicitly selected repository IDs
  2. Current active workspace repository
  3. Sole connected selectable repository (exactly one)
  4. Otherwise require user selection when the agent needs a repo
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Coding CLIs that execute against a local connected repository path.
REPO_REQUIRED_AGENTS = frozenset({"codex", "claude-code", "cursor-agent"})


def agent_requires_repository(agent_id: str) -> bool:
    """True for coding CLIs that need a connected local repository cwd."""
    return str(agent_id or "").strip().lower() in REPO_REQUIRED_AGENTS


def normalize_repository_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        rid = str(item or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def selectable_repository_map(repositories: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in repositories or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid or not bool(row.get("selectable")):
            continue
        out[rid] = row
    return out


def list_selectable_ids(repositories: list[dict[str, Any]] | None) -> list[str]:
    return list(selectable_repository_map(repositories).keys())


def validate_repository_access(
    repository_ids: list[str],
    *,
    repositories: list[dict[str, Any]] | None,
) -> tuple[bool, str | None, str | None]:
    """Ensure every id is selectable and has an existing local path when declared."""
    ids = normalize_repository_ids(repository_ids)
    if not ids:
        return True, None, None
    by_id = selectable_repository_map(repositories)
    for rid in ids:
        row = by_id.get(rid)
        if row is None:
            return False, f"Repository '{rid}' is not an accessible connected repository.", "repository_inaccessible"
        path = str(row.get("path") or row.get("local_path") or "").strip()
        if path:
            try:
                if not Path(path).exists():
                    return (
                        False,
                        f"Repository '{rid}' path does not exist: {path}",
                        "repository_path_missing",
                    )
            except OSError as exc:
                return False, f"Repository '{rid}' path is not accessible: {exc}", "repository_path_error"
    return True, None, None


def resolve_repository_context(
    *,
    agent_id: str,
    repository_ids: Any = None,
    active_repository_id: str | None = None,
    selected_repository_id: str | None = None,
    repositories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Resolve repository IDs for a run.

    Returns keys: required, ok, repository_ids, source, error, code, needs_selection,
    selectable_count, selectable_ids.
    """
    required = agent_requires_repository(agent_id)
    selectable = list_selectable_ids(repositories)
    selectable_set = set(selectable)
    raw_explicit = normalize_repository_ids(repository_ids)
    explicit = [rid for rid in raw_explicit if rid in selectable_set]
    persisted = str(selected_repository_id or "").strip()
    active = str(active_repository_id or "").strip()

    result: dict[str, Any] = {
        "required": required,
        "ok": True,
        "repository_ids": [],
        "source": "none",
        "error": None,
        "code": None,
        "needs_selection": False,
        "selectable_count": len(selectable),
        "selectable_ids": selectable,
    }

    if raw_explicit and not explicit:
        result["ok"] = False
        result["error"] = (
            "Selected repository is not an accessible connected repository."
        )
        result["code"] = "repository_inaccessible"
        result["needs_selection"] = True
        result["source"] = "explicit"
        return result

    if not required:
        # Non-repo agents may still accept optional repos, but never require them.
        if explicit:
            ok, err, code = validate_repository_access(explicit, repositories=repositories)
            result["repository_ids"] = explicit if ok else []
            result["source"] = "explicit" if ok else "none"
            if not ok:
                result["ok"] = False
                result["error"] = err
                result["code"] = code
        return result

    chosen: list[str] = []
    source = "none"

    if explicit:
        chosen = explicit
        source = "explicit"
    elif persisted and persisted in selectable_set:
        chosen = [persisted]
        source = "persisted_selection"
    elif active and active in selectable_set:
        chosen = [active]
        source = "active_workspace"
    elif len(selectable) == 1:
        chosen = [selectable[0]]
        source = "sole_connected"
    elif len(selectable) == 0:
        result["ok"] = False
        result["error"] = (
            f"Agent '{agent_id}' requires a connected repository, but none are selectable."
        )
        result["code"] = "repository_unavailable"
        result["needs_selection"] = True
        result["source"] = "missing"
        return result
    else:
        result["ok"] = False
        result["error"] = (
            f"Agent '{agent_id}' requires a selected connected repository. "
            "Choose a repository in AiriX (multiple connected repos are available)."
        )
        result["code"] = "repository_required"
        result["needs_selection"] = True
        result["source"] = "missing"
        return result

    ok, err, code = validate_repository_access(chosen, repositories=repositories)
    if not ok:
        result["ok"] = False
        result["error"] = err
        result["code"] = code
        result["needs_selection"] = True
        result["source"] = source
        return result

    result["repository_ids"] = chosen
    result["source"] = source
    return result
