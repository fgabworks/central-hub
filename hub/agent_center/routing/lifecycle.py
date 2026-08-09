"""Execution lifecycle helpers for AiriX Smart Routing.

Normalizes terminal states so parent orchestration and UI polling never leave
a run stuck as "running" / "active".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("hub.agent_center.routing.lifecycle")

# Public terminal statuses (UI + API contract).
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "paused_for_approval",
        "timed_out",
    }
)

# In-flight statuses that keep the spinner/poll alive.
ACTIVE_STATUSES = frozenset({"queued", "running"})

# How long a session may remain non-terminal before stale recovery.
DEFAULT_STALE_SECONDS = 15 * 60
# Default wait for an async provider run inside RouteExecutor.
DEFAULT_STEP_WAIT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_status(status: str | None, *, error_code: str | None = None) -> str:
    """Map internal statuses onto the public terminal/active set."""
    raw = (status or "").strip().lower()
    code = (error_code or "").strip().lower()
    if code in {"approval_required"} or raw in {"paused", "paused_for_approval"}:
        return "paused_for_approval"
    if code in {"timeout", "timed_out"} or raw in {"timed_out", "timeout"}:
        return "timed_out"
    if raw in {"succeeded", "success", "completed"}:
        return "completed"
    if raw in {"cancelled", "canceled"}:
        return "cancelled"
    if raw in {"failed", "blocked", "unavailable", "error", "permission_denied"}:
        return "failed"
    if raw in {"queued", "running", "active"}:
        # "active" is a session in-progress marker — treat as running for polls.
        return "running" if raw == "active" else raw
    if raw in TERMINAL_STATUSES or raw in ACTIVE_STATUSES:
        return raw
    if not raw:
        return "failed"
    return "failed"


def is_terminal(status: str | None, *, error_code: str | None = None) -> bool:
    return normalize_status(status, error_code=error_code) in TERMINAL_STATUSES


def is_active(status: str | None, *, error_code: str | None = None) -> bool:
    return normalize_status(status, error_code=error_code) in ACTIVE_STATUSES


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
) -> bool:
    """True when a non-terminal row has exceeded the stale window."""
    if is_terminal(str(row.get("status") or ""), error_code=str(row.get("error_code") or "") or None):
        return False
    now = now or datetime.now(timezone.utc)
    started = parse_iso(str(row.get("started_at") or row.get("updated_at") or row.get("created_at") or ""))
    if started is None:
        return False
    age = (now - started).total_seconds()
    return age >= float(stale_seconds)


def log_lifecycle(
    *,
    event: str,
    status: str,
    step_id: str = "",
    provider_id: str = "",
    tool_ids: list[str] | None = None,
    started_at: str = "",
    finished_at: str = "",
    failure_reason: str = "",
    execution_id: str = "",
    session_id: str = "",
) -> None:
    logger.info(
        "airix_lifecycle event=%s status=%s step=%s provider=%s tools=%s "
        "start=%s end=%s execution=%s session=%s reason=%s",
        event,
        status,
        step_id or "-",
        provider_id or "-",
        ",".join(tool_ids or []) or "-",
        started_at or "-",
        finished_at or "-",
        execution_id or "-",
        session_id or "-",
        (failure_reason or "-")[:240],
    )


def unwrap_agent_run_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mirror dock JS: GET/POST /runs wrap the row as ``{"run": {...}}``."""
    if not isinstance(payload, dict):
        return None
    nested = payload.get("run")
    if isinstance(nested, dict) and (nested.get("id") or nested.get("status")):
        return nested
    if payload.get("id") or payload.get("status"):
        return payload
    return None


def consume_skip_routing_once(skip_routing_once: bool) -> tuple[bool, bool]:
    """
    One-shot Smart Routing bypass used by Choose Agent / manual override.

    Returns ``(should_skip_recommend, next_skip_flag)``.
    Skipping recommend must never disable child-run lifecycle polling.
    """
    if skip_routing_once:
        return True, False
    return False, False


def public_execution_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a status payload for API/UI consumers."""
    status = normalize_status(
        str(row.get("status") or ""),
        error_code=str(row.get("error_code") or "") or None,
    )
    out = dict(row)
    out["status"] = status
    out["terminal"] = status in TERMINAL_STATUSES
    if "finished_at" not in out and status in TERMINAL_STATUSES:
        out["finished_at"] = out.get("finished_at") or _utcnow()
    return out
