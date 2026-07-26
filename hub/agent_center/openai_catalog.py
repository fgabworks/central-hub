"""Centralized OpenAI model catalog for Prompting & Agent Center.

All display metadata and Hub-supported IDs live here — not in routes/templates.
Accessibility is determined by intersecting with GET /v1/models for the API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Selector groups (Recommended is computed per mode from accessible models).
GROUP_ADVANCED = "Advanced"
GROUP_BALANCED = "Balanced"
GROUP_FAST = "Fast"
GROUP_PRO = "Pro"
GROUP_RECOMMENDED = "Recommended"

GROUP_ORDER = (
    GROUP_RECOMMENDED,
    GROUP_ADVANCED,
    GROUP_BALANCED,
    GROUP_FAST,
    GROUP_PRO,
)

REASONING_EFFORTS = ("low", "medium", "high")

# Per-mode preferred IDs (first accessible wins; then continue down the chain).
MODE_RECOMMENDATIONS: dict[str, tuple[str, ...]] = {
    "find": (
        "gpt-5.6-luna",
        "gpt-5.4-nano",
        "gpt-5.4-mini",
        "gpt-5.6-terra",
        "gpt-5.4",
        "gpt-5.6-sol",
        "gpt-5.5",
    ),
    "ask": (
        "gpt-5.6-terra",
        "gpt-5.4",
        "gpt-5.6-sol",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.4-mini",
    ),
    "plan": (
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.4",
        "gpt-5.5",
        "gpt-5.4-pro",
        "gpt-5.5-pro",
    ),
    "review": (
        "gpt-5.6-sol",
        "gpt-5.5-pro",
        "gpt-5.4-pro",
        "gpt-5.5",
        "gpt-5.6-terra",
        "gpt-5.4",
    ),
}


@dataclass(frozen=True)
class OpenAIModelSpec:
    id: str
    display_name: str
    description: str
    tier: str  # estimated tier label only — never pricing
    group: str
    recommended_uses: tuple[str, ...]
    supports_reasoning_effort: bool
    is_pro: bool
    sort_order: int = 0

    def public_dict(self, *, availability: str = "available") -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "tier": self.tier,
            "group": self.group,
            "recommended_uses": list(self.recommended_uses),
            "supports_reasoning_effort": self.supports_reasoning_effort,
            "is_pro": self.is_pro,
            "availability": availability,
        }


# Single source of truth for Hub-supported OpenAI models.
OPENAI_MODEL_CATALOG: tuple[OpenAIModelSpec, ...] = (
    OpenAIModelSpec(
        id="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        description="Advanced reasoning for careful review and complex analysis.",
        tier="Advanced",
        group=GROUP_ADVANCED,
        recommended_uses=("Review", "Deep analysis", "Architecture critique"),
        supports_reasoning_effort=True,
        is_pro=False,
        sort_order=10,
    ),
    OpenAIModelSpec(
        id="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        description="Balanced quality and speed for everyday Ask and Plan work.",
        tier="Balanced",
        group=GROUP_BALANCED,
        recommended_uses=("Ask", "Plan", "General professional work"),
        supports_reasoning_effort=True,
        is_pro=False,
        sort_order=20,
    ),
    OpenAIModelSpec(
        id="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        description="Fast and economical responses for Find and lightweight tasks.",
        tier="Fast",
        group=GROUP_FAST,
        recommended_uses=("Find", "Quick lookups", "High-volume scans"),
        supports_reasoning_effort=False,
        is_pro=False,
        sort_order=30,
    ),
    OpenAIModelSpec(
        id="gpt-5.5",
        display_name="GPT-5.5",
        description="Advanced model for demanding professional tasks.",
        tier="Advanced",
        group=GROUP_ADVANCED,
        recommended_uses=("Complex Ask", "Design", "Multi-file reasoning"),
        supports_reasoning_effort=True,
        is_pro=False,
        sort_order=40,
    ),
    OpenAIModelSpec(
        id="gpt-5.5-pro",
        display_name="GPT-5.5 Pro",
        description="Highest-quality model for complex work (longer runs).",
        tier="Pro",
        group=GROUP_PRO,
        recommended_uses=("Highest-quality complex work", "Critical reviews"),
        supports_reasoning_effort=True,
        is_pro=True,
        sort_order=50,
    ),
    OpenAIModelSpec(
        id="gpt-5.4",
        display_name="GPT-5.4",
        description="General professional workhorse with solid balance.",
        tier="Balanced",
        group=GROUP_BALANCED,
        recommended_uses=("General professional work", "Ask", "Plan"),
        supports_reasoning_effort=True,
        is_pro=False,
        sort_order=60,
    ),
    OpenAIModelSpec(
        id="gpt-5.4-pro",
        display_name="GPT-5.4 Pro",
        description="Deep analysis for harder problems (longer runs).",
        tier="Pro",
        group=GROUP_PRO,
        recommended_uses=("Deep analysis", "Hard reviews", "Long plans"),
        supports_reasoning_effort=True,
        is_pro=True,
        sort_order=70,
    ),
    OpenAIModelSpec(
        id="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        description="Fast everyday work with lower latency.",
        tier="Fast",
        group=GROUP_FAST,
        recommended_uses=("Fast everyday work", "Find", "Short asks"),
        supports_reasoning_effort=False,
        is_pro=False,
        sort_order=80,
    ),
    OpenAIModelSpec(
        id="gpt-5.4-nano",
        display_name="GPT-5.4 Nano",
        description="Lightweight model for high-volume, low-cost tasks.",
        tier="Fast",
        group=GROUP_FAST,
        recommended_uses=("Lightweight high-volume work", "Bulk Find"),
        supports_reasoning_effort=False,
        is_pro=False,
        sort_order=90,
    ),
)

_CATALOG_BY_ID: dict[str, OpenAIModelSpec] = {m.id: m for m in OPENAI_MODEL_CATALOG}


def catalog_ids() -> tuple[str, ...]:
    return tuple(m.id for m in OPENAI_MODEL_CATALOG)


def get_spec(model_id: str) -> OpenAIModelSpec | None:
    return _CATALOG_BY_ID.get((model_id or "").strip())


def parse_allowed_models(raw: str | None) -> frozenset[str] | None:
    """Optional OPENAI_ALLOWED_MODELS restriction. None means no extra restriction."""
    text = (raw or "").strip()
    if not text:
        return None
    items = {p.strip() for p in text.split(",") if p.strip()}
    return frozenset(items) if items else None


def intersect_accessible(
    api_model_ids: Iterable[str],
    *,
    allowed: frozenset[str] | None = None,
) -> list[OpenAIModelSpec]:
    """Return catalog specs the API key can access (and optional allowlist).

    Models in the catalog but missing from the API are omitted — never an error.
    """
    accessible = {str(x).strip() for x in api_model_ids if str(x).strip()}
    out: list[OpenAIModelSpec] = []
    for spec in OPENAI_MODEL_CATALOG:
        if spec.id not in accessible:
            continue
        if allowed is not None and spec.id not in allowed:
            continue
        out.append(spec)
    return out


def recommend_model_id(
    mode: str,
    accessible_ids: Iterable[str],
    *,
    user_override: str | None = None,
    default_model: str = "",
) -> tuple[str | None, str]:
    """Pick a model ID.

    Returns (model_id, reason) where reason is user_override | recommended |
    fallback | default | none.
    """
    available = {str(x).strip() for x in accessible_ids if str(x).strip()}
    override = (user_override or "").strip()
    if override:
        if override in available:
            return override, "user_override"
        return None, "override_unavailable"

    mode_key = (mode or "ask").strip().lower()
    chain = list(MODE_RECOMMENDATIONS.get(mode_key) or MODE_RECOMMENDATIONS["ask"])
    default = (default_model or "").strip()

    for idx, mid in enumerate(chain):
        if mid in available:
            return mid, "recommended" if idx == 0 else "fallback"

    if default and default in available:
        return default, "default"

    # Last resort: first catalog-ordered accessible model
    for spec in OPENAI_MODEL_CATALOG:
        if spec.id in available:
            return spec.id, "fallback"
    return None, "none"


def build_grouped_models(
    accessible: list[OpenAIModelSpec],
    *,
    mode: str,
    recommended_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Group accessible models for the selector UI."""
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUP_ORDER}
    by_id = {m.id: m for m in accessible}

    if recommended_id and recommended_id in by_id:
        groups[GROUP_RECOMMENDED].append(
            by_id[recommended_id].public_dict(availability="available")
        )

    for spec in accessible:
        row = spec.public_dict(availability="available")
        if spec.group in groups:
            # Avoid duplicating the recommended entry inside its tier group? Spec says
            # group into Recommended / Advanced / Balanced / Fast / Pro — include in both
            # Recommended and its tier so users still see it under Advanced etc.
            groups[spec.group].append(row)
        else:
            groups[GROUP_BALANCED].append(row)

    # Drop empty groups from output order helpers; keep keys for stable UI.
    return groups


def catalog_public_snapshot() -> list[dict[str, Any]]:
    """Full catalog metadata (availability unknown until API intersection)."""
    return [m.public_dict(availability="catalog") for m in OPENAI_MODEL_CATALOG]


def normalize_reasoning_effort(value: str | None, *, supported: bool) -> str | None:
    if not supported:
        return None
    effort = (value or "").strip().lower()
    if not effort:
        return "medium"
    if effort not in REASONING_EFFORTS:
        return None
    return effort
