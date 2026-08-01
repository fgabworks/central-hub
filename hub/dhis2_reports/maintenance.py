"""DHIS2 Stage/Live availability helpers (maintenance-aware, no cross-env bleed)."""

from __future__ import annotations

import os
from typing import Any

STAGE_MAINTENANCE_MESSAGE = "Stage is temporarily unavailable due to maintenance."


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_stage_maintenance(*, getenv=os.getenv) -> bool:
    """True when Stage is flagged under scheduled maintenance (no auto Stage polling)."""
    return _as_bool(getenv("DHIS2_STAGE_MAINTENANCE"), default=False)


def environment_availability(environment: str, *, getenv=os.getenv) -> dict[str, Any]:
    """Per-environment availability status. Never maps Live status onto Stage."""
    env = (environment or "").strip().lower() or "stage"
    if env == "stage" and is_stage_maintenance(getenv=getenv):
        return {
            "environment": "stage",
            "status": "maintenance",
            "message": STAGE_MAINTENANCE_MESSAGE,
            "network_allowed": False,
            "maintenance": True,
        }
    return {
        "environment": env,
        "status": "ok",
        "message": None,
        "network_allowed": True,
        "maintenance": False,
    }
