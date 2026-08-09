"""Shared models for AiriX Smart Routing (Phase 3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ROUTING_TIERS = ("T0", "T1", "T2", "T3")
COST_TIERS = ("free", "low", "standard", "premium")
USAGE_BANDS = ("Very Low", "Low", "Moderate", "High")
RISK_LEVELS = ("low", "medium", "high")
TASK_TYPES = (
    "lookup",
    "css_ui",
    "sql_investigation",
    "dhis2_investigation",
    "coding",
    "testing",
    "architecture",
    "refactor",
    "general",
)
ROUTING_MODES = ("balanced", "cheapest", "best_quality", "max_speed")


@dataclass(frozen=True)
class ProviderSpec:
    """Provider-agnostic routing metadata (not an execution adapter)."""

    id: str
    label: str
    tier: str  # T0–T3
    cost_tier: str
    speed: str  # fast | medium | slow
    context_capacity: str  # small | medium | large
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    requires_approval: bool = False
    adapter_id: str | None = None  # maps to existing agents.yaml / adapter id
    notes: str = ""

    def public(self, *, available: bool = True, availability_detail: str = "") -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "cost_tier": self.cost_tier,
            "speed": self.speed,
            "context_capacity": self.context_capacity,
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "requires_approval": self.requires_approval,
            "adapter_id": self.adapter_id or self.id,
            "notes": self.notes,
            "available": bool(available),
            "availability_detail": availability_detail or ("available" if available else "unavailable"),
        }


@dataclass
class RoutingSettings:
    mode: str = "balanced"
    prefer_deterministic: bool = True
    prefer_grok_for_routine: bool = True
    require_approval_before_codex: bool = True
    allow_escalation: bool = True
    max_retries: int = 2
    use_history: bool = True

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PromptClassification:
    task_type: str
    complexity: int  # 0–100
    risk: str
    estimated_scope_files: int
    context_size: str  # small | medium | large
    needs_coding: bool
    needs_testing: bool
    needs_architecture: bool
    deterministic_capable: bool
    signals: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteExplanation:
    """Human-readable routing explanation (Phase 3)."""

    recommended_provider: str
    historical_success_rate: float | None = None
    sample_size: int = 0
    expected_retries: int = 0
    estimated_usage: str = ""
    escalation_reason: str | None = None
    history_influenced: bool = False
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "recommended_provider": self.recommended_provider,
            "historical_success_rate": self.historical_success_rate,
            "sample_size": self.sample_size,
            "expected_retries": self.expected_retries,
            "estimated_usage": self.estimated_usage,
            "escalation_reason": self.escalation_reason,
            "history_influenced": self.history_influenced,
            "reason": self.reason,
        }


@dataclass
class RouteRecommendation:
    task_type: str
    complexity: int
    risk: str
    recommended_agent: str
    recommended_label: str
    recommended_tier: str
    alternative_agent: str | None
    alternative_label: str | None
    confidence: float
    reason: str
    estimated_usage: str
    approval_required: bool
    classification: PromptClassification
    providers_considered: list[str] = field(default_factory=list)
    explanation: RouteExplanation | None = None
    expected_retries: int = 0
    history_influenced: bool = False
    escalation_reason: str | None = None

    def public(self) -> dict[str, Any]:
        expl = self.explanation.public() if self.explanation else {
            "recommended_provider": self.recommended_agent,
            "historical_success_rate": None,
            "sample_size": 0,
            "expected_retries": self.expected_retries,
            "estimated_usage": self.estimated_usage,
            "escalation_reason": self.escalation_reason,
            "history_influenced": self.history_influenced,
            "reason": self.reason,
        }
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "risk": self.risk,
            "recommended_agent": self.recommended_agent,
            "recommended_label": self.recommended_label,
            "recommended_tier": self.recommended_tier,
            "alternative_agent": self.alternative_agent,
            "alternative_label": self.alternative_label,
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "estimated_usage": self.estimated_usage,
            "approval_required": self.approval_required,
            "classification": self.classification.public(),
            "providers_considered": list(self.providers_considered),
            "explanation": expl,
            "expected_retries": self.expected_retries,
            "history_influenced": self.history_influenced,
            "escalation_reason": self.escalation_reason,
            "phase": 3,
            "execution": "ready",
        }


@dataclass
class ExecutionPlan:
    """Execution plan preview (Phase 3 includes history-aware explanation)."""

    prompt: str
    recommended_agent: str
    alternative_agent: str | None
    tier: str
    approval_required: bool
    estimated_usage: str
    max_retries: int
    steps: list[str]
    status: str = "planned"
    context: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "recommended_agent": self.recommended_agent,
            "alternative_agent": self.alternative_agent,
            "tier": self.tier,
            "approval_required": self.approval_required,
            "estimated_usage": self.estimated_usage,
            "max_retries": self.max_retries,
            "steps": list(self.steps),
            "status": self.status,
            "context": dict(self.context),
            "explanation": dict(self.explanation),
            "phase": 3,
            "execute": True,
            "note": "Phase 3 executes via adapters; history influences routing; Codex still requires approval.",
        }
