"""Runtime caps and timeouts for AiriX Tool Runtime Phase 2."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        n = int(str(value or "").strip() or default)
    except ValueError:
        n = default
    return max(minimum, min(maximum, n))


def _as_float(value: str | None, default: float, *, minimum: float, maximum: float) -> float:
    try:
        n = float(str(value or "").strip() or default)
    except ValueError:
        n = default
    return max(minimum, min(maximum, n))


@dataclass(frozen=True)
class ToolRuntimeSettings:
    max_steps: int = 8
    hard_runaway_cap: int = 16
    max_observation_chars: int = 6_000
    max_kept_observations: int = 4
    stuck_duplicate_limit: int = 2
    stuck_max_recoveries: int = 2
    timeout_seconds: float = 120.0
    tool_timeout_seconds: float = 30.0
    max_active_tools: int = 8
    lean_initial_context: bool = True


def load_tool_runtime_settings() -> ToolRuntimeSettings:
    return ToolRuntimeSettings(
        max_steps=_as_int(os.getenv("AIRIX_TOOL_RUNTIME_MAX_STEPS"), 8, minimum=1, maximum=24),
        hard_runaway_cap=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_HARD_CAP"), 16, minimum=2, maximum=40
        ),
        max_observation_chars=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_MAX_OBS_CHARS"), 6_000, minimum=500, maximum=50_000
        ),
        max_kept_observations=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_KEEP_OBS"), 4, minimum=1, maximum=12
        ),
        stuck_duplicate_limit=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_STUCK_DUP"), 2, minimum=1, maximum=6
        ),
        stuck_max_recoveries=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_STUCK_RECOVER"), 2, minimum=0, maximum=6
        ),
        timeout_seconds=_as_float(
            os.getenv("AIRIX_TOOL_RUNTIME_TIMEOUT"), 120.0, minimum=10.0, maximum=600.0
        ),
        tool_timeout_seconds=_as_float(
            os.getenv("AIRIX_TOOL_RUNTIME_TOOL_TIMEOUT"), 30.0, minimum=2.0, maximum=120.0
        ),
        max_active_tools=_as_int(
            os.getenv("AIRIX_TOOL_RUNTIME_MAX_TOOLS"), 8, minimum=3, maximum=16
        ),
        lean_initial_context=str(os.getenv("AIRIX_TOOL_RUNTIME_LEAN_CONTEXT") or "1").strip().lower()
        not in {"0", "false", "no"},
    )
