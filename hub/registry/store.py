"""Read/write the repository registry YAML (raw, preserves placeholders)."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from hub.registry.git_util import git_urls_match, normalize_git_url, slugify_repo_id
from hub.registry.loader import RegistryError, expand_env

_HEADER = """# Central Hub repository registry
# Connected repositories only. Secrets stay in .env — use ${VAR:-default} when useful.
# Demo/sample repos live in tests/fixtures/ — not in the active registry.

"""


class RegistryStore:
    """Mutate config/repositories.yaml without expanding env placeholders."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read_raw(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise RegistryError(f"Repository registry not found: {self.path}")
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise RegistryError(f"Invalid YAML in {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RegistryError("Registry root must be a mapping")
        repos = raw.get("repositories")
        if repos is None:
            raw["repositories"] = []
        elif not isinstance(repos, list):
            raise RegistryError("'repositories' must be a list")
        if "defaults" not in raw or not isinstance(raw.get("defaults"), dict):
            raw["defaults"] = {
                "job_timeout_seconds": 3600,
                "max_concurrent_jobs": 2,
                "require_explicit_apply": True,
            }
        return raw

    def write_raw(self, data: dict[str, Any]) -> None:
        payload = deepcopy(data)
        if not isinstance(payload.get("repositories"), list):
            raise RegistryError("'repositories' must be a list")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(
            payload,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=100,
        )
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(_HEADER + body, encoding="utf-8")
        tmp.replace(self.path)

    def list_raw(self) -> list[dict[str, Any]]:
        return list(self.read_raw().get("repositories") or [])

    def get_raw(self, repo_id: str) -> dict[str, Any] | None:
        for item in self.list_raw():
            if isinstance(item, dict) and str(item.get("id")) == repo_id:
                return deepcopy(item)
        return None

    def find_duplicates(
        self,
        *,
        repo_id: str | None = None,
        repo_type: str | None = None,
        git_url: str | None = None,
        local_path: str | None = None,
        base_url: str | None = None,
        exclude_id: str | None = None,
    ) -> list[str]:
        """Return human-readable duplicate reasons (empty if none)."""
        reasons: list[str] = []
        want_git = normalize_git_url(git_url)
        want_path = (expand_env(local_path) if local_path else "").strip().lower()
        want_base = (expand_env(base_url) if base_url else "").rstrip("/").lower()
        want_type = (repo_type or "").strip().lower()
        for item in self.list_raw():
            if not isinstance(item, dict):
                continue
            existing_id = str(item.get("id") or "")
            if exclude_id and existing_id == exclude_id:
                continue
            if repo_id and existing_id == repo_id:
                reasons.append(f"id already registered: {repo_id}")
            existing_type = str(item.get("type") or "").strip().lower()
            existing_git = str(item.get("git_url") or "")
            # Same Git remote may back an API entry and a local checkout; only
            # collide when the adapter type matches.
            if (
                want_git
                and git_urls_match(want_git, existing_git)
                and want_type
                and existing_type == want_type
            ):
                reasons.append(f"git_url already used by {existing_id} ({existing_type})")
            existing_path = str(expand_env(str(item.get("local_path") or ""))).strip().lower()
            if want_path and existing_path and want_path == existing_path:
                reasons.append(f"local_path already used by {existing_id}")
            existing_base = str(expand_env(str(item.get("base_url") or ""))).rstrip("/").lower()
            if want_base and existing_base and want_base == existing_base:
                reasons.append(f"base_url already used by {existing_id}")
        return reasons

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        data = self.read_raw()
        repos: list[Any] = list(data.get("repositories") or [])
        repo_id = str(entry.get("id") or "").strip()
        if not repo_id:
            raise RegistryError("id is required")
        if any(isinstance(r, dict) and str(r.get("id")) == repo_id for r in repos):
            raise RegistryError(f"Duplicate repository id: {repo_id}")
        dupes = self.find_duplicates(
            repo_id=repo_id,
            repo_type=entry.get("type"),
            git_url=entry.get("git_url"),
            local_path=entry.get("local_path"),
            base_url=entry.get("base_url"),
        )
        if dupes:
            raise RegistryError("; ".join(dupes))
        cleaned = _clean_entry(entry)
        repos.append(cleaned)
        data["repositories"] = repos
        self.write_raw(data)
        return cleaned

    def update(self, repo_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        data = self.read_raw()
        repos: list[Any] = list(data.get("repositories") or [])
        idx = next(
            (i for i, r in enumerate(repos) if isinstance(r, dict) and str(r.get("id")) == repo_id),
            None,
        )
        if idx is None:
            raise RegistryError(f"Repository not found: {repo_id}")
        current = dict(repos[idx])
        merged = {**current, **{k: v for k, v in updates.items() if k != "id"}}
        merged["id"] = repo_id
        dupes = self.find_duplicates(
            repo_type=merged.get("type"),
            git_url=merged.get("git_url"),
            local_path=merged.get("local_path"),
            base_url=merged.get("base_url"),
            exclude_id=repo_id,
        )
        if dupes:
            raise RegistryError("; ".join(dupes))
        cleaned = _clean_entry(merged)
        # Preserve capabilities / health_check when not supplied.
        if "capabilities" not in updates and "capabilities" in current:
            cleaned["capabilities"] = current.get("capabilities") or []
        if "health_check" not in updates and "health_check" in current:
            cleaned["health_check"] = current.get("health_check")
        if "tags" not in updates and "tags" in current:
            cleaned["tags"] = current.get("tags") or []
        if "description" not in updates and "description" in current:
            cleaned["description"] = current.get("description") or ""
        if "repository_group_id" not in updates and "repository_group_id" in current:
            cleaned["repository_group_id"] = current.get("repository_group_id")
        repos[idx] = cleaned
        data["repositories"] = repos
        self.write_raw(data)
        return cleaned

    def set_enabled(self, repo_id: str, enabled: bool) -> dict[str, Any]:
        return self.update(repo_id, {"enabled": bool(enabled)})


def build_entry_from_form(
    *,
    name: str,
    repo_type: str,
    enabled: bool,
    git_url: str | None = None,
    local_path: str | None = None,
    base_url: str | None = None,
    description: str = "",
    repo_id: str | None = None,
    tags: list[str] | None = None,
    repository_group_id: str | None = None,
) -> dict[str, Any]:
    rid = (repo_id or slugify_repo_id(name)).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", rid):
        raise RegistryError(
            "id must be lowercase alphanumeric with hyphens/underscores (max 64)"
        )
    if repo_type not in {"api", "command"}:
        raise RegistryError("type must be 'api' or 'command'")

    group_id = (repository_group_id or "").strip() or None
    if group_id and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", group_id):
        raise RegistryError(
            "repository_group_id must be lowercase alphanumeric with hyphens/underscores (max 64)"
        )

    entry: dict[str, Any] = {
        "id": rid,
        "name": name.strip(),
        "type": repo_type,
        "enabled": bool(enabled),
        "description": (description or "").strip(),
        "tags": tags or (["connected", repo_type]),
        "capabilities": [],
    }
    if group_id:
        entry["repository_group_id"] = group_id
    if git_url:
        entry["git_url"] = git_url.strip()
    if local_path:
        entry["local_path"] = local_path.strip()
        entry["working_directory"] = local_path.strip()
    if base_url:
        entry["base_url"] = base_url.strip()

    if repo_type == "api":
        if not entry.get("base_url"):
            raise RegistryError("API repositories require a base_url")
        entry["health_check"] = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "timeout_seconds": 2,
        }
    else:
        if not entry.get("git_url") and not entry.get("local_path"):
            raise RegistryError("Command repositories require a git_url and/or local_path")
        entry["health_check"] = {
            "type": "path",
            "local_path": entry.get("local_path") or "",
            "executable": "python",
            "timeout_seconds": 3,
        }
    return entry


def _clean_entry(entry: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {
        "id": str(entry["id"]).strip(),
        "name": str(entry.get("name") or "").strip(),
        "type": str(entry.get("type") or "").strip(),
        "enabled": bool(entry.get("enabled", True)),
    }
    for key in (
        "description",
        "git_url",
        "local_path",
        "working_directory",
        "base_url",
    ):
        value = entry.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    if entry.get("tags") is not None:
        cleaned["tags"] = [str(t) for t in (entry.get("tags") or [])]
    if entry.get("health_check") is not None:
        cleaned["health_check"] = entry.get("health_check")
    if entry.get("capabilities") is not None:
        cleaned["capabilities"] = entry.get("capabilities") or []
    group_id = entry.get("repository_group_id")
    if group_id is not None and str(group_id).strip():
        cleaned["repository_group_id"] = str(group_id).strip()
    return cleaned
