"""Resolve adapters for registered repositories."""

from __future__ import annotations

from typing import Any

from hub.adapters.api_adapter import ApiAdapter
from hub.adapters.command_adapter import CommandAdapter
from hub.registry.models import Registry, Repository


class AdapterManager:
    def __init__(self, registry: Registry, default_timeout: float = 5.0) -> None:
        self.registry = registry
        self.default_timeout = default_timeout

    def get_adapter(self, repository: Repository) -> ApiAdapter | CommandAdapter:
        if repository.type == "api":
            return ApiAdapter(repository, default_timeout=self.default_timeout)
        if repository.type == "command":
            return CommandAdapter(repository, default_timeout=self.default_timeout)
        raise ValueError(f"Unsupported repository type: {repository.type}")

    def check_repository(self, repository: Repository) -> dict[str, Any]:
        if not repository.enabled:
            return {
                "repository_id": repository.id,
                "name": repository.name,
                "type": repository.type,
                "enabled": False,
                "ok": False,
                "status": "skipped",
                "detail": "Repository is disabled in the registry",
                "latency_ms": 0,
                "checked_at": None,
            }

        adapter = self.get_adapter(repository)
        result = adapter.health_check()
        return {
            "repository_id": repository.id,
            "name": repository.name,
            "type": repository.type,
            "enabled": repository.enabled,
            **result,
        }

    def check_all(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        repos = (
            self.registry.enabled_repositories()
            if enabled_only
            else self.registry.repositories
        )
        return [self.check_repository(repo) for repo in repos]
