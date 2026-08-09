"""Shared models for AiriX Smart Routing (Phase 5)."""

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
    tier: str
    cost_tier: str
    speed: str
    context_capacity: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    requires_approval: bool = False
    adapter_id: str | None = None
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
    # Phase 4 budgets / orchestration
    enable_orchestration: bool = True
    max_orchestration_steps: int = 4
    daily_token_budget: int = 50000
    monthly_token_budget: int = 500000
    per_task_max_tokens: int = 20000
    warn_before_expensive_escalation: bool = True
    # Phase 5 cost intelligence (public USD / 1M tokens rates — never secrets)
    enable_cost_estimates: bool = True
    price_per_mtok: dict[str, float] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        # Normalize rates to plain floats for JSON prefs.
        rates: dict[str, float] = {}
        for key, val in (self.price_per_mtok or {}).items():
            try:
                rates[str(key)] = max(0.0, float(val))
            except (TypeError, ValueError):
                continue
        data["price_per_mtok"] = rates
        return data


@dataclass
class PromptClassification:
    task_type: str
    complexity: int
    risk: str
    estimated_scope_files: int
    context_size: str
    needs_coding: bool
    needs_testing: bool
    needs_architecture: bool
    deterministic_capable: bool
    signals: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteExplanation:
    recommended_provider: str
    historical_success_rate: float | None = None
    sample_size: int = 0
    expected_retries: int = 0
    estimated_usage: str = ""
    escalation_reason: str | None = None
    history_influenced: bool = False
    reason: str = ""
    role_id: str | None = None
    budget_warning: str | None = None
    expensive_warning: str | None = None
    permission_warning: str | None = None
    rbac_role: str | None = None

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
            "role_id": self.role_id,
            "budget_warning": self.budget_warning,
            "expensive_warning": self.expensive_warning,
            "permission_warning": self.permission_warning,
            "rbac_role": self.rbac_role,
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
    role_id: str | None = None
    orchestration: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, Any] = field(default_factory=dict)
    prior_findings: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float | None = None
    recommended_model: str | None = None
    recommended_model_reason: str = ""

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
            "role_id": self.role_id,
            "budget_warning": None,
            "expensive_warning": None,
            "permission_warning": None,
            "rbac_role": None,
        }
        return {
            "task_type": self.task_type,
            "complexity": self.complexity,
            "risk": self.risk,
            "recommended_agent": self.recommended_agent,
            "recommended_label": self.recommended_label,
            "recommended_tier": self.recommended_tier,
            "recommended_model": self.recommended_model,
            "recommended_model_reason": self.recommended_model_reason,
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
            "role_id": self.role_id,
            "orchestration": list(self.orchestration),
            "budget": dict(self.budget),
            "permissions": dict(self.permissions),
            "prior_findings": list(self.prior_findings),
            "estimated_cost_usd": self.estimated_cost_usd,
            "phase": 5,
            "execution": "ready",
        }


@dataclass
class ExecutionPlan:
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
    orchestration: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    role_id: str | None = None
    session_id: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)
    prior_findings: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float | None = None

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
            "orchestration": list(self.orchestration),
            "budget": dict(self.budget),
            "role_id": self.role_id,
            "session_id": self.session_id,
            "permissions": dict(self.permissions),
            "prior_findings": list(self.prior_findings),
            "estimated_cost_usd": self.estimated_cost_usd,
            "phase": 5,
            "execute": True,
            "note": (
                "Phase 5: cost intelligence + RBAC + relevance findings; "
                "capability → permissions → budget → history; Codex still requires approval."
            ),
        }
