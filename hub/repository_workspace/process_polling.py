"""Polling policy for Repository Run tab (Active Application + Processes).

Shared constants/decision helpers keep browser timers consistent and testable.
Process scans are intentionally slower than active-status polls and pause when
the app is idle/stopped, the processes panel is off-screen, or the tab is hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Active Application status poll while Starting / Running / Stopping.
ACTIVE_STATUS_INTERVAL_MS = 4000  # within the 3–5s requirement

# Full Repository Processes scan cadence (manual or automatic).
PROCESS_SCAN_INTERVAL_MS = 15000

# Abort an in-flight process scan after this many milliseconds.
PROCESS_SCAN_TIMEOUT_MS = 10000

ACTIVE_DISPLAY_STATUSES = frozenset(
    {
        "Starting",
        "Running",
        "Running + Healthy",
        "Running + Unhealthy",
        "Stopping",
    }
)

IDLE_DISPLAY_STATUSES = frozenset({"Stopped", "Failed"})


@dataclass(frozen=True)
class PollDecision:
    poll_active_status: bool
    poll_process_scan: bool
    reason: str


def is_active_lifecycle(display_status: str | None = None, *, auto_refresh: bool = False) -> bool:
    """True when the managed app is Starting / Running / Stopping."""
    if auto_refresh:
        return True
    status = (display_status or "").strip()
    return status in ACTIVE_DISPLAY_STATUSES


def should_poll_active_status(
    *,
    auto_refresh: bool = False,
    display_status: str | None = None,
    tab_visible: bool = True,
) -> bool:
    if not tab_visible:
        return False
    return is_active_lifecycle(display_status, auto_refresh=auto_refresh)


def should_poll_process_scan(
    *,
    auto_refresh: bool = False,
    display_status: str | None = None,
    panel_visible: bool = True,
    tab_visible: bool = True,
) -> bool:
    """Automatic full scans only while active + panel visible + tab visible."""
    if not tab_visible or not panel_visible:
        return False
    if not is_active_lifecycle(display_status, auto_refresh=auto_refresh):
        return False
    return True


def decide_polls(
    *,
    auto_refresh: bool = False,
    display_status: str | None = None,
    panel_visible: bool = True,
    tab_visible: bool = True,
) -> PollDecision:
    active = should_poll_active_status(
        auto_refresh=auto_refresh,
        display_status=display_status,
        tab_visible=tab_visible,
    )
    processes = should_poll_process_scan(
        auto_refresh=auto_refresh,
        display_status=display_status,
        panel_visible=panel_visible,
        tab_visible=tab_visible,
    )
    if not tab_visible:
        reason = "tab_hidden"
    elif not is_active_lifecycle(display_status, auto_refresh=auto_refresh):
        reason = "idle_or_stopped"
    elif not panel_visible and active:
        reason = "panel_hidden_status_only"
    elif active and processes:
        reason = "active_visible"
    else:
        reason = "status_only"
    return PollDecision(
        poll_active_status=active,
        poll_process_scan=processes,
        reason=reason,
    )


class ScanRequestGate:
    """Prevent overlapping process scans; track in-flight + timeout bookkeeping."""

    def __init__(self, timeout_ms: int = PROCESS_SCAN_TIMEOUT_MS) -> None:
        self.timeout_ms = int(timeout_ms)
        self.in_flight = False
        self.last_started_at_ms: int | None = None
        self.last_finished_at_ms: int | None = None
        self.last_error: str | None = None

    def begin(self, now_ms: int) -> bool:
        """Return True if a new scan may start."""
        if self.in_flight:
            return False
        self.in_flight = True
        self.last_started_at_ms = int(now_ms)
        self.last_error = None
        return True

    def finish(self, now_ms: int, *, error: str | None = None) -> None:
        self.in_flight = False
        self.last_finished_at_ms = int(now_ms)
        self.last_error = error

    def timed_out(self, now_ms: int) -> bool:
        if not self.in_flight or self.last_started_at_ms is None:
            return False
        return (int(now_ms) - self.last_started_at_ms) >= self.timeout_ms

    def to_public(self) -> dict[str, Any]:
        return {
            "in_flight": self.in_flight,
            "timeout_ms": self.timeout_ms,
            "last_started_at_ms": self.last_started_at_ms,
            "last_finished_at_ms": self.last_finished_at_ms,
            "last_error": self.last_error,
            "scan_button_disabled": self.in_flight,
        }


def polling_config_for_ui() -> dict[str, int]:
    return {
        "active_status_interval_ms": ACTIVE_STATUS_INTERVAL_MS,
        "process_scan_interval_ms": PROCESS_SCAN_INTERVAL_MS,
        "process_scan_timeout_ms": PROCESS_SCAN_TIMEOUT_MS,
    }
