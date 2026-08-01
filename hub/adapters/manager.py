"""Resolve adapters for registered repositories."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from hub.adapters.api_adapter import ApiAdapter
from hub.adapters.command_adapter import CommandAdapter
from hub.perf import coalesce, record_external, timed
from hub.registry.models import Registry, Repository


class AdapterManager:
    def __init__(
        self,
        registry: Registry,
        default_timeout: float = 5.0,
        *,
        cache_ttl_seconds: float = 30.0,
        max_workers: int = 6,
    ) -> None:
        self.registry = registry
        self.default_timeout = default_timeout
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.max_workers = max(1, int(max_workers))
        # cache_key -> (expires_at, results) — results kept after expiry for stale reads
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

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

    def check_all(
        self,
        *,
        enabled_only: bool = False,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Probe all repositories (parallel). Cached briefly unless force=True."""
        cache_key = "enabled" if enabled_only else "all"
        now = time.monotonic()
        if not force and self.cache_ttl_seconds > 0:
            cached = self._cache.get(cache_key)
            if cached is not None:
                expires_at, results = cached
                if now < expires_at:
                    return [dict(item) for item in results]

        def _probe() -> list[dict[str, Any]]:
            with timed("health_check_all", enabled_only=enabled_only, force=force):
                start = time.perf_counter()
                repos = (
                    self.registry.enabled_repositories()
                    if enabled_only
                    else self.registry.repositories
                )
                if not repos:
                    results: list[dict[str, Any]] = []
                elif len(repos) == 1:
                    results = [self.check_repository(repos[0])]
                else:
                    workers = min(self.max_workers, len(repos))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        results = list(pool.map(self.check_repository, repos))
                record_external(
                    (time.perf_counter() - start) * 1000.0,
                    name="health_check_all",
                )
                return results

        results = coalesce(f"health:{cache_key}:{int(force)}", _probe)
        if self.cache_ttl_seconds > 0:
            self._cache[cache_key] = (time.monotonic() + self.cache_ttl_seconds, results)
        return [dict(item) for item in results]

    def cached_results(
        self,
        *,
        enabled_only: bool = False,
        allow_stale: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return cached health without probing. Never blocks on network/path checks."""
        cache_key = "enabled" if enabled_only else "all"
        cached = self._cache.get(cache_key)
        if cached is None:
            return [], {"fresh": False, "stale": False, "cached": False}
        expires_at, results = cached
        fresh = self.cache_ttl_seconds <= 0 or time.monotonic() < expires_at
        if fresh or allow_stale:
            return [dict(item) for item in results], {
                "fresh": fresh,
                "stale": not fresh,
                "cached": True,
            }
        return [], {"fresh": False, "stale": False, "cached": False}

    def invalidate_health_cache(self) -> None:
        self._cache.clear()
