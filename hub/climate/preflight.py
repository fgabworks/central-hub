"""Compatibility shim — CLIMATE Context Resolver lives in context_resolver.py."""

from __future__ import annotations

from hub.climate.context_resolver import (  # noqa: F401
    GATE_MESSAGE,
    ContextResolverResult,
    PreflightResult,
    make_blocked_run,
    resolve_climate_context,
    run_climate_preflight,
    select_applicable_instructions,
    select_relevant_skill_sections,
)
