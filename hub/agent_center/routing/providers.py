"""Provider Registry — routing metadata over existing agent adapters + free tools."""

from __future__ import annotations

from typing import Any, Iterable

from hub.agent_center.routing.models import ProviderSpec


# Stable registry used by the router. adapter_id links to config/agents.yaml ids
# where an execution adapter already exists. Deterministic has no paid adapter.
_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="deterministic",
        label="Deterministic / Free tools",
        tier="T0",
        cost_tier="free",
        speed="fast",
        context_capacity="medium",
        capabilities=(
            "lookup",
            "notebook",
            "sql_read",
            "dhis2_read",
            "uid_lookup",
            "jobs_lookup",
            "audit_lookup",
        ),
        tools=(
            "notebook_lookup",
            "sql_lookup",
            "uid_lookup",
            "dhis2_reports_lookup",
            "jobs_lookup",
            "audit_lookup",
            "repo_search",
            "read_file",
        ),
        requires_approval=False,
        adapter_id=None,
        notes="Hub read-only tools; no LLM spend.",
    ),
    ProviderSpec(
        id="low-cost",
        label="Free / low-cost cloud agent",
        tier="T1",
        cost_tier="low",
        speed="fast",
        context_capacity="medium",
        capabilities=("coding_simple", "css_ui", "qa", "summarize"),
        tools=("repo_search", "read_file", "notebook_lookup"),
        requires_approval=False,
        adapter_id="hub-simulator",
        notes="Maps to Hub Simulator / future low-cost cloud (T1).",
    ),
    ProviderSpec(
        id="grok",
        label="Grok",
        tier="T2",
        cost_tier="standard",
        speed="medium",
        context_capacity="large",
        capabilities=(
            "coding",
            "investigation",
            "sql",
            "dhis2",
            "testing",
            "explain",
        ),
        tools=("repo_search", "read_file", "sql_lookup", "uid_lookup", "dhis2_reports_lookup"),
        requires_approval=False,
        adapter_id="grok",
        notes="xAI Grok via existing adapter; default for routine coding/investigation.",
    ),
    ProviderSpec(
        id="codex",
        label="Codex",
        tier="T3",
        cost_tier="premium",
        speed="slow",
        context_capacity="large",
        capabilities=(
            "architecture",
            "refactor",
            "cross_module",
            "complex_coding",
            "high_risk",
            "testing",
        ),
        tools=("repo_search", "read_file", "notebook_lookup", "sql_lookup"),
        requires_approval=False,
        adapter_id="codex",
        notes="Advanced / high-risk work; Send authorizes provider; action policy gates writes.",
    ),
    ProviderSpec(
        id="openai-api",
        label="OpenAI API",
        tier="T1",
        cost_tier="low",
        speed="fast",
        context_capacity="large",
        capabilities=("coding_simple", "summarize", "qa"),
        tools=("repo_search", "read_file"),
        requires_approval=False,
        adapter_id="openai-api",
        notes="Low-cost cloud path via existing OpenAI adapter.",
    ),
    ProviderSpec(
        id="claude-code",
        label="Claude Code",
        tier="T3",
        cost_tier="premium",
        speed="medium",
        context_capacity="large",
        capabilities=("architecture", "complex_coding", "refactor"),
        tools=("repo_search", "read_file"),
        requires_approval=False,
        adapter_id="claude-code",
        notes="Future advanced provider.",
    ),
    ProviderSpec(
        id="cursor-agent",
        label="Cursor Agent",
        tier="T3",
        cost_tier="premium",
        speed="medium",
        context_capacity="large",
        capabilities=("architecture", "complex_coding", "refactor"),
        tools=("repo_search", "read_file"),
        requires_approval=False,
        adapter_id="cursor-agent",
        notes="Future advanced provider.",
    ),
)


class ProviderRegistry:
    """Provider-agnostic registry for Smart Routing."""

    def __init__(self, specs: Iterable[ProviderSpec] | None = None) -> None:
        self._specs = {p.id: p for p in (specs or _PROVIDER_SPECS)}

    def get(self, provider_id: str) -> ProviderSpec | None:
        return self._specs.get((provider_id or "").strip())

    def by_adapter_id(self, adapter_id: str) -> ProviderSpec | None:
        key = (adapter_id or "").strip()
        for spec in self._specs.values():
            if (spec.adapter_id or spec.id) == key:
                return spec
        return None

    def all(self) -> list[ProviderSpec]:
        return list(self._specs.values())

    def by_tier(self, tier: str) -> list[ProviderSpec]:
        return [p for p in self._specs.values() if p.tier == tier]

    def list_public(
        self,
        *,
        availability: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Merge static specs with live adapter availability when provided."""
        avail = availability or {}
        rows: list[dict[str, Any]] = []
        for spec in self._specs.values():
            if spec.id == "deterministic":
                rows.append(spec.public(available=True, availability_detail="always_on"))
                continue
            adapter_key = spec.adapter_id or spec.id
            info = avail.get(adapter_key) or avail.get(spec.id) or {}
            status = str(info.get("status") or "")
            runnable = bool(info.get("runnable")) or status in {"available", "degraded"}
            # Phase 1: still list providers even if offline so routing can explain fallbacks.
            detail = str(info.get("detail") or status or "not_probed")
            rows.append(spec.public(available=runnable or status == "", availability_detail=detail))
        # Prefer canonical tier order for UI.
        order = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
        rows.sort(key=lambda r: (order.get(str(r.get("tier")), 9), str(r.get("label") or "")))
        return rows
