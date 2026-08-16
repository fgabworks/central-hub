"""CLIMATE execution mode — orchestration, not provider identity.

Assisted uses the Context Resolver as hints. Direct sends the raw prompt to the
provider inside the same approved-repo / ASK-read-only / EDIT-proposal safety.
"""

from __future__ import annotations

CLIMATE_ASSISTED = "climate_assisted"
DIRECT = "direct"
EXECUTION_MODES = (CLIMATE_ASSISTED, DIRECT)

MODE_LABELS = {
    CLIMATE_ASSISTED: "CLIMATE Assisted",
    DIRECT: "Direct Provider",
}

MODE_TOOLTIPS = {
    CLIMATE_ASSISTED: "CLIMATE Assisted — CLIMATE finds useful repo context first.",
    DIRECT: "Direct Provider — provider investigates the repo directly.",
}

_ALIASES = {
    "climate_assisted": CLIMATE_ASSISTED,
    "climate-assisted": CLIMATE_ASSISTED,
    "assisted": CLIMATE_ASSISTED,
    "climate": CLIMATE_ASSISTED,
    "direct": DIRECT,
    "direct_provider": DIRECT,
    "direct-provider": DIRECT,
}


def normalize_execution_mode(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return _ALIASES.get(raw, CLIMATE_ASSISTED)


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
