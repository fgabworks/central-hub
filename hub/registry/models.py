"""Typed models for the repository registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RepoType = Literal["api", "command"]
HealthCheckType = Literal["http", "path", "command"]
AdapterType = Literal["api", "command"]


@dataclass(frozen=True)
class HealthCheckConfig:
    type: HealthCheckType
    method: str = "GET"
    path: str = "/health"
    timeout_seconds: float = 5.0
    # Harmless path/executable checks only in Phase 1.
    local_path: str | None = None
    executable: str | None = None
    # Optional allowlisted argv for a harmless probe (e.g. python -c "print('ok')").
    command: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    adapter_type: AdapterType
    input_types: list[str] = field(default_factory=list)
    dry_run_default: bool = True
    # Stored for later phases; unused in Phase 1.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Repository:
    id: str
    name: str
    type: RepoType
    enabled: bool
    description: str = ""
    local_path: str | None = None
    working_directory: str | None = None
    base_url: str | None = None
    git_url: str | None = None
    health_check: HealthCheckConfig | None = None
    capabilities: list[Capability] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RegistryDefaults:
    job_timeout_seconds: int = 3600
    max_concurrent_jobs: int = 2
    require_explicit_apply: bool = True


@dataclass(frozen=True)
class Registry:
    repositories: list[Repository]
    defaults: RegistryDefaults = field(default_factory=RegistryDefaults)

    def get(self, repo_id: str) -> Repository | None:
        for repo in self.repositories:
            if repo.id == repo_id:
                return repo
        return None

    def enabled_repositories(self) -> list[Repository]:
        return [repo for repo in self.repositories if repo.enabled]
