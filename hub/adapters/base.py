"""Adapter protocol for connected repositories."""

from __future__ import annotations

from typing import Any, Protocol

from hub.registry.models import Repository


class RepositoryAdapter(Protocol):
    """Phase 1 adapters only implement health checks. Job methods arrive later."""

    repository: Repository

    def health_check(self) -> dict[str, Any]:
        """Return {ok, status, detail, latency_ms, checked_at}."""
