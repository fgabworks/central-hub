"""Map adapter health results to dashboard/registry UI status labels."""

from __future__ import annotations

from typing import Any

from hub.registry.models import Repository

# User-facing states for repository cards/tables.
UI_STATUSES = ("healthy", "unreachable", "not_cloned", "disabled")


def ui_repo_status(repo: Repository | None, health: dict[str, Any] | None) -> str:
    """
    Normalize probe results into: healthy | unreachable | not_cloned | disabled.
    """
    health = health or {}
    if repo is not None and not repo.enabled:
        return "disabled"
    if not health.get("enabled", True) or health.get("status") == "skipped":
        return "disabled"
    if health.get("ok"):
        return "healthy"
    status = str(health.get("status") or "").lower()
    if status == "not_cloned":
        return "not_cloned"
    if status in {"unreachable", "unhealthy", "timeout", "blocked", "misconfigured", "offline"}:
        return "unreachable"
    return "unreachable"
