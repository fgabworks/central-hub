"""CLIMATE execution mode — orchestration, not provider identity.

AiriX uses the CLIMATE orchestration/context layer, then the selected provider.
Direct sends the prompt to that same provider with minimal CLIMATE wrapping.
Repository/file context is never implied; it is used only when explicitly selected.
"""

from __future__ import annotations

CLIMATE_ASSISTED = "climate_assisted"
DIRECT = "direct"
EXECUTION_MODES = (CLIMATE_ASSISTED, DIRECT)

MODE_LABELS = {
    CLIMATE_ASSISTED: "AiriX",
    DIRECT: "Direct",
}

MODE_TOOLTIPS = {
    CLIMATE_ASSISTED: "AiriX — CLIMATE orchestration, then the selected provider/model.",
    DIRECT: "Direct — send the prompt to the selected provider/model with minimal CLIMATE orchestration.",
}

_ALIASES = {
    "climate_assisted": CLIMATE_ASSISTED,
    "climate-assisted": CLIMATE_ASSISTED,
    "assisted": CLIMATE_ASSISTED,
    "climate": CLIMATE_ASSISTED,
    "airix": CLIMATE_ASSISTED,
    "direct": DIRECT,
    "direct_provider": DIRECT,
    "direct-provider": DIRECT,
}


def normalize_execution_mode(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(raw, CLIMATE_ASSISTED)


def coerce_execution_mode(value: str | None) -> str:
    """Strict parser for saved defaults. Blank becomes AiriX; unknown raises."""
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not raw:
        return CLIMATE_ASSISTED
    if raw not in _ALIASES:
        raise ValueError("Unknown execution mode")
    return _ALIASES[raw]


def is_direct_mode(value: str | None) -> bool:
    return normalize_execution_mode(value) == DIRECT


def mode_label(value: str | None) -> str:
    return MODE_LABELS[normalize_execution_mode(value)]


def mode_tooltip(value: str | None) -> str:
    return MODE_TOOLTIPS[normalize_execution_mode(value)]


def execution_mode_public(value: str | None) -> dict[str, str]:
    mode = normalize_execution_mode(value)
    return {
        "id": mode,
        "label": MODE_LABELS[mode],
        "tooltip": MODE_TOOLTIPS[mode],
        "compare_label": "Compare with CLIMATE" if mode == DIRECT else "Compare with Direct",
    }
