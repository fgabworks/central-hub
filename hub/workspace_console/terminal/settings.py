"""Environment knobs for interactive Workspace Console terminals."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        raw = int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        raw = default
    raw = max(minimum, raw)
    if maximum is not None:
        raw = min(maximum, raw)
    return raw


@dataclass(frozen=True)
class TerminalSettings:
    enabled: bool
    allow_cmd: bool
    max_sessions: int
    max_output_buffer_bytes: int
    read_chunk_bytes: int
    ws_ticket_ttl_seconds: int
    idle_ws_grace_seconds: int
    default_cols: int
    default_rows: int


def load_terminal_settings() -> TerminalSettings:
    return TerminalSettings(
        enabled=_as_bool(os.getenv("WC_TERMINAL_ENABLED"), True),
        allow_cmd=_as_bool(os.getenv("WC_TERMINAL_ALLOW_CMD"), False),
        max_sessions=_as_int(os.getenv("WC_TERMINAL_MAX_SESSIONS"), 8, minimum=1, maximum=24),
        max_output_buffer_bytes=_as_int(
            os.getenv("WC_TERMINAL_OUTPUT_BUFFER_BYTES"), 512_000, minimum=32_000, maximum=4_000_000
        ),
        read_chunk_bytes=_as_int(os.getenv("WC_TERMINAL_READ_CHUNK_BYTES"), 16_384, minimum=1024, maximum=65_536),
        ws_ticket_ttl_seconds=_as_int(os.getenv("WC_TERMINAL_WS_TICKET_TTL"), 60, minimum=15, maximum=300),
        idle_ws_grace_seconds=_as_int(os.getenv("WC_TERMINAL_IDLE_WS_GRACE"), 3600, minimum=60, maximum=86_400),
        default_cols=_as_int(os.getenv("WC_TERMINAL_COLS"), 120, minimum=20, maximum=400),
        default_rows=_as_int(os.getenv("WC_TERMINAL_ROWS"), 32, minimum=8, maximum=120),
    )
