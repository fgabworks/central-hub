"""Profile id helpers for AiriX Smart Routing APIs."""

from __future__ import annotations

# Public URL/profile slug for Work Smart Routing.
CANONICAL_ROUTING_PROFILE = "airix"
# Internal AssistantProfile / DB profile id (unchanged for history compatibility).
INTERNAL_WORK_PROFILE = "okarun"

_WORK_ALIASES = frozenset({"airix", "okarun"})


def is_work_routing_profile(profile_id: str | None) -> bool:
    return (profile_id or "").strip().lower() in _WORK_ALIASES


def canonical_routing_profile(profile_id: str | None) -> str:
    key = (profile_id or "").strip().lower()
    if key in _WORK_ALIASES:
        return CANONICAL_ROUTING_PROFILE
    if key == "aira":
        return "aira"
    raise ValueError(f"Unknown assistant profile: {profile_id}")


def internal_assistant_profile(profile_id: str | None) -> str:
    """Map public routing profile ids onto AssistantProfile ids."""
    key = (profile_id or "").strip().lower()
    if key in _WORK_ALIASES:
        return INTERNAL_WORK_PROFILE
    if key == "aira":
        return "aira"
    raise ValueError(f"Unknown assistant profile: {profile_id}")
