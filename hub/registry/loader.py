"""Load and validate `config/repositories.yaml`."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from hub.registry.models import (
    Capability,
    HealthCheckConfig,
    Registry,
    RegistryDefaults,
    Repository,
)

# ${VAR} or ${VAR:-default} — keeps secrets/hostnames out of committed YAML when desired.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class RegistryError(ValueError):
    """Raised when the registry config is missing or invalid."""


def expand_env(value: str) -> str:
    """Expand ${VAR} / ${VAR:-default} placeholders from process environment."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        env_val = os.getenv(key)
        if env_val is not None and env_val != "":
            return env_val
        return default if default is not None else ""

    return _ENV_PATTERN.sub(_replace, value)


def load_registry(config_path: Path | str) -> Registry:
    path = Path(config_path)
    if not path.is_file():
        raise RegistryError(f"Repository registry not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError("Registry root must be a mapping")

    repos_raw = raw.get("repositories")
    if repos_raw is None:
        raise RegistryError("Registry must define a 'repositories' list")
    if not isinstance(repos_raw, list):
        raise RegistryError("'repositories' must be a list")

    repositories = [_parse_repository(item, index) for index, item in enumerate(repos_raw)]
    _ensure_unique_ids(repositories)
    defaults = _parse_defaults(raw.get("defaults") or {})
    return Registry(repositories=repositories, defaults=defaults)


def _parse_repository(item: Any, index: int) -> Repository:
    if not isinstance(item, dict):
        raise RegistryError(f"repositories[{index}] must be a mapping")

    repo_id = _require_str(item, "id", f"repositories[{index}]")
    name = _require_str(item, "name", f"repositories[{index}]")
    repo_type = _require_str(item, "type", f"repositories[{index}]")
    if repo_type not in {"api", "command"}:
        raise RegistryError(
            f"repositories[{index}].type must be 'api' or 'command', got {repo_type!r}"
        )

    capabilities = [
        _parse_capability(cap, f"repositories[{index}].capabilities[{cap_index}]")
        for cap_index, cap in enumerate(item.get("capabilities") or [])
    ]

    return Repository(
        id=repo_id,
        name=name,
        type=repo_type,  # type: ignore[arg-type]
        enabled=bool(item.get("enabled", True)),
        description=str(item.get("description") or ""),
        local_path=_optional_str(_expand_optional(item.get("local_path"))),
        working_directory=_optional_str(_expand_optional(item.get("working_directory"))),
        base_url=_optional_str(_expand_optional(item.get("base_url"))),
        git_url=_optional_str(_expand_optional(item.get("git_url"))),
        health_check=_parse_health_check(item.get("health_check"), f"repositories[{index}]"),
        capabilities=capabilities,
        tags=[str(tag) for tag in (item.get("tags") or [])],
        repository_group_id=_optional_str(item.get("repository_group_id")),
    )


def _parse_health_check(raw: Any, context: str) -> HealthCheckConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RegistryError(f"{context}.health_check must be a mapping")

    check_type = _require_str(raw, "type", f"{context}.health_check")
    if check_type not in {"http", "path", "command"}:
        raise RegistryError(
            f"{context}.health_check.type must be 'http', 'path', or 'command'"
        )

    command = raw.get("command") or []
    if command and not isinstance(command, list):
        raise RegistryError(f"{context}.health_check.command must be a list")

    return HealthCheckConfig(
        type=check_type,  # type: ignore[arg-type]
        method=str(raw.get("method") or "GET").upper(),
        path=expand_env(str(raw.get("path") or "/health")),
        timeout_seconds=float(raw.get("timeout_seconds") or 5),
        local_path=_optional_str(_expand_optional(raw.get("local_path"))),
        executable=_optional_str(_expand_optional(raw.get("executable"))),
        command=[expand_env(str(part)) for part in command],
    )


def _parse_capability(raw: Any, context: str) -> Capability:
    if not isinstance(raw, dict):
        raise RegistryError(f"{context} must be a mapping")

    adapter_type = _require_str(raw, "adapter_type", context)
    if adapter_type not in {"api", "command"}:
        raise RegistryError(f"{context}.adapter_type must be 'api' or 'command'")

    return Capability(
        id=_require_str(raw, "id", context),
        label=_require_str(raw, "label", context),
        adapter_type=adapter_type,  # type: ignore[arg-type]
        input_types=[str(item) for item in (raw.get("input_types") or [])],
        dry_run_default=bool(raw.get("dry_run_default", True)),
        raw=dict(raw),
    )


def _parse_defaults(raw: Any) -> RegistryDefaults:
    if not isinstance(raw, dict):
        raise RegistryError("'defaults' must be a mapping when present")
    return RegistryDefaults(
        job_timeout_seconds=int(raw.get("job_timeout_seconds") or 3600),
        max_concurrent_jobs=int(raw.get("max_concurrent_jobs") or 2),
        require_explicit_apply=bool(raw.get("require_explicit_apply", True)),
    )


def _ensure_unique_ids(repositories: list[Repository]) -> None:
    seen: set[str] = set()
    for repo in repositories:
        if repo.id in seen:
            raise RegistryError(f"Duplicate repository id: {repo.id}")
        seen.add(repo.id)


def _require_str(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if value is None or str(value).strip() == "":
        raise RegistryError(f"{context}.{key} is required")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _expand_optional(value: Any) -> str | None:
    if value is None:
        return None
    return expand_env(str(value))
