"""UI-facing run status reconciliation.

Process liveness and HTTP health are separate facets. A dead tracked process
never shares a combined "Stopped · HTTP 200" display state. When the hub run is
stopped but its port still accepts connections, surface a port-orphan warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from hub.repository_workspace.ports import port_available, port_listeners
from hub.repository_workspace.process_manager import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ManagedRun,
)

DISPLAY_STARTING = "Starting"
DISPLAY_RUNNING_HEALTHY = "Running + Healthy"
DISPLAY_RUNNING_UNHEALTHY = "Running + Unhealthy"
DISPLAY_RUNNING = "Running"
DISPLAY_FAILED = "Failed"
DISPLAY_STOPPING = "Stopping"
DISPLAY_STOPPED = "Stopped"

HISTORY_DISPLAY_LIMIT = 5

TONE_AMBER = "amber"
TONE_GREEN = "green"
TONE_RED = "red"
TONE_GRAY = "gray"

DISPLAY_TONES = {
    DISPLAY_STARTING: TONE_AMBER,
    DISPLAY_RUNNING_HEALTHY: TONE_GREEN,
    DISPLAY_RUNNING_UNHEALTHY: TONE_AMBER,
    DISPLAY_RUNNING: TONE_GREEN,
    DISPLAY_FAILED: TONE_RED,
    DISPLAY_STOPPING: TONE_AMBER,
    DISPLAY_STOPPED: TONE_GRAY,
}

AUTO_REFRESH_DISPLAY = frozenset(
    {
        DISPLAY_STARTING,
        DISPLAY_RUNNING,
        DISPLAY_RUNNING_HEALTHY,
        DISPLAY_RUNNING_UNHEALTHY,
        DISPLAY_STOPPING,
    }
)


def format_uptime(elapsed_seconds: int | None) -> str:
    if elapsed_seconds is None:
        return "—"
    secs = max(0, int(elapsed_seconds))
    hours, rem = divmod(secs, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def process_state_from_status(status: str) -> str:
    """Map stored hub status → process-only facet (never healthy/unhealthy)."""
    status = (status or "").strip().lower()
    if status in {"healthy", "unhealthy", "running"}:
        return "running"
    if status in {"starting", "stopping", "stopped", "failed"}:
        return status
    if status in ACTIVE_STATUSES:
        return "running"
    return "stopped"


def health_state_from_run(run: ManagedRun | dict[str, Any], *, process_alive: bool) -> str:
    """HTTP health facet. Stale when process is not alive."""
    if isinstance(run, ManagedRun):
        health_url = run.health_url
        ok = run.last_health_ok
        detail = run.last_health_detail
        status = run.status
    else:
        health_url = run.get("health_url")
        ok = run.get("last_health_ok")
        detail = run.get("last_health_detail") or ""
        status = str(run.get("status") or "")

    if not health_url:
        return "none"
    if not process_alive:
        # Never treat a prior HTTP 200 as current health for a dead process.
        return "stale" if (ok is not None or detail) else "none"
    if status == "starting" and ok is not True:
        return "pending"
    if ok is True:
        return "healthy"
    if ok is False:
        return "unhealthy"
    return "unknown"


def display_status_for(*, process_state: str, health_state: str) -> str:
    if process_state == "starting":
        return DISPLAY_STARTING
    if process_state == "stopping":
        return DISPLAY_STOPPING
    if process_state == "failed":
        return DISPLAY_FAILED
    if process_state == "stopped":
        return DISPLAY_STOPPED
    if health_state == "healthy":
        return DISPLAY_RUNNING_HEALTHY
    if health_state == "unhealthy":
        return DISPLAY_RUNNING_UNHEALTHY
    if health_state == "pending":
        return DISPLAY_STARTING
    if health_state == "none":
        return DISPLAY_RUNNING
    if health_state == "unknown":
        return DISPLAY_RUNNING_UNHEALTHY
    return DISPLAY_RUNNING


def actions_for_display(display: str, *, has_local_url: bool, has_run_id: bool) -> list[str]:
    if display == DISPLAY_STOPPED:
        return ["start"]
    if display == DISPLAY_STARTING:
        return ["stop", "view_logs"] if has_run_id else ["view_logs"]
    if display in {
        DISPLAY_RUNNING,
        DISPLAY_RUNNING_HEALTHY,
        DISPLAY_RUNNING_UNHEALTHY,
    }:
        actions = ["stop", "restart", "view_logs"]
        if has_local_url:
            actions.insert(2, "open_app")
        return actions
    if display == DISPLAY_FAILED:
        return ["retry", "view_logs"] if has_run_id else ["start", "view_logs"]
    if display == DISPLAY_STOPPING:
        return ["view_logs"] if has_run_id else []
    return []


def detect_port_orphan(
    *,
    port: int | None,
    tracked_pid: int | None,
    process_alive: bool,
    port_is_available: Callable[[int], bool] | None = None,
    listeners_for: Callable[[int], list[int]] | None = None,
) -> dict[str, Any] | None:
    """When tracked process is dead but the port still responds."""
    if not port or int(port) <= 0:
        return None
    if process_alive:
        return None
    available_fn = port_is_available or port_available
    if available_fn(int(port)):
        return None
    listeners_fn = listeners_for or (lambda p: port_listeners([p]).get(int(p), []))
    try:
        pids = [int(x) for x in (listeners_fn(int(port)) or [])]
    except Exception:  # noqa: BLE001
        pids = []
    if tracked_pid and int(tracked_pid) in pids:
        return None
    return {
        "code": "port_orphan",
        "message": (
            f"Tracked process stopped — another process is using port {int(port)}"
        ),
        "port": int(port),
        "listener_pids": pids,
        "processes_anchor": "#repository-processes",
    }


@dataclass
class ReconciledRun:
    """Public view for Active Application / history rows."""

    run_id: str | None
    repo_id: str
    profile_id: str
    environment: str
    port: int | None
    pid: int | None
    local_url: str
    health_url: str | None
    started_at: str | None
    stopped_at: str | None
    elapsed_seconds: int | None
    uptime: str
    raw_status: str
    process_state: str
    health_state: str
    health_detail: str
    display_status: str
    display_tone: str
    actions: list[str] = field(default_factory=list)
    warning: str | None = None
    warning_code: str | None = None
    listener_pids: list[int] = field(default_factory=list)
    error: str = ""
    command_preview: list[str] = field(default_factory=list)
    cwd: str = ""
    is_active: bool = False
    auto_refresh: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo_id": self.repo_id,
            "profile_id": self.profile_id,
            "environment": self.environment,
            "port": self.port,
            "pid": self.pid,
            "local_url": self.local_url,
            "health_url": self.health_url,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "elapsed_seconds": self.elapsed_seconds,
            "uptime": self.uptime,
            "status": self.raw_status,
            "process_state": self.process_state,
            "health_state": self.health_state,
            "health_detail": self.health_detail,
            "display_status": self.display_status,
            "display_tone": self.display_tone,
            "actions": list(self.actions),
            "warning": self.warning,
            "warning_code": self.warning_code,
            "listener_pids": list(self.listener_pids),
            "error": self.error,
            "command_preview": list(self.command_preview),
            "cwd": self.cwd,
            "is_active": self.is_active,
            "auto_refresh": self.auto_refresh,
            "last_health_ok": (
                True
                if self.health_state == "healthy"
                else False
                if self.health_state == "unhealthy"
                else None
            ),
            "last_health_detail": (
                "" if self.health_state in {"stale", "none"} else self.health_detail
            ),
        }


def reconcile_run(
    run: ManagedRun,
    *,
    process_alive: bool | None = None,
    check_port: bool = True,
    port_is_available: Callable[[int], bool] | None = None,
    listeners_for: Callable[[int], list[int]] | None = None,
) -> ReconciledRun:
    public = run.to_public()
    if process_alive is not None:
        alive = bool(process_alive)
    elif run.status == "stopping":
        alive = True
    elif run.status in TERMINAL_STATUSES:
        alive = False
    else:
        alive = run.status in ACTIVE_STATUSES

    process_state = process_state_from_status(run.status)
    if process_alive is False:
        process_state = "failed" if run.status == "failed" else "stopped"

    health_alive = alive and process_state not in {"stopped", "failed"}
    health_state = health_state_from_run(run, process_alive=health_alive)
    display = display_status_for(process_state=process_state, health_state=health_state)
    tone = DISPLAY_TONES.get(display, TONE_GRAY)

    if health_state == "healthy":
        health_detail = run.last_health_detail or "HTTP OK"
    elif health_state == "unhealthy":
        health_detail = run.last_health_detail or "Unhealthy"
    elif health_state == "pending":
        health_detail = "Waiting for first health response"
    elif health_state in {"stale", "none"}:
        health_detail = "—" if health_state == "none" else ""
    else:
        health_detail = run.last_health_detail or "—"

    warning = None
    warning_code = None
    listener_pids: list[int] = []
    if check_port and process_state in {"stopped", "failed"}:
        orphan = detect_port_orphan(
            port=run.port,
            tracked_pid=run.pid,
            process_alive=False,
            port_is_available=port_is_available,
            listeners_for=listeners_for,
        )
        if orphan:
            warning = orphan["message"]
            warning_code = orphan["code"]
            listener_pids = list(orphan.get("listener_pids") or [])

    return ReconciledRun(
        run_id=run.run_id,
        repo_id=run.repo_id,
        profile_id=run.profile_id,
        environment=run.environment,
        port=run.port or None,
        pid=run.pid,
        local_url=run.local_url or "",
        health_url=run.health_url,
        started_at=run.started_at,
        stopped_at=run.stopped_at,
        elapsed_seconds=public.get("elapsed_seconds"),
        uptime=format_uptime(public.get("elapsed_seconds")),
        raw_status=run.status,
        process_state=process_state,
        health_state=health_state,
        health_detail=health_detail,
        display_status=display,
        display_tone=tone,
        actions=actions_for_display(
            display,
            has_local_url=bool(run.local_url),
            has_run_id=bool(run.run_id),
        ),
        warning=warning,
        warning_code=warning_code,
        listener_pids=listener_pids,
        error=run.error or "",
        command_preview=list(public.get("command_preview") or []),
        cwd=run.cwd or "",
        is_active=run.status in ACTIVE_STATUSES,
        auto_refresh=display in AUTO_REFRESH_DISPLAY,
    )


def idle_active_card(
    *,
    repo_id: str,
    profile_id: str = "",
    environment: str = "development",
    port: int | None = None,
    check_port: bool = True,
    port_is_available: Callable[[int], bool] | None = None,
    listeners_for: Callable[[int], list[int]] | None = None,
) -> ReconciledRun:
    """Active Application placeholder when no hub-managed run is active."""
    warning = None
    warning_code = None
    listener_pids: list[int] = []
    if check_port and port:
        orphan = detect_port_orphan(
            port=port,
            tracked_pid=None,
            process_alive=False,
            port_is_available=port_is_available,
            listeners_for=listeners_for,
        )
        if orphan:
            warning = orphan["message"]
            warning_code = orphan["code"]
            listener_pids = list(orphan.get("listener_pids") or [])
    return ReconciledRun(
        run_id=None,
        repo_id=repo_id,
        profile_id=profile_id,
        environment=environment,
        port=port,
        pid=None,
        local_url="",
        health_url=None,
        started_at=None,
        stopped_at=None,
        elapsed_seconds=None,
        uptime="—",
        raw_status="stopped",
        process_state="stopped",
        health_state="none",
        health_detail="—",
        display_status=DISPLAY_STOPPED,
        display_tone=TONE_GRAY,
        actions=["start"],
        warning=warning,
        warning_code=warning_code,
        listener_pids=listener_pids,
        is_active=False,
        auto_refresh=False,
    )


def pick_active_run(runs: list[ManagedRun]) -> ManagedRun | None:
    active = [r for r in runs if r.status in ACTIVE_STATUSES]
    if not active:
        return None
    active.sort(key=lambda r: r.started_at or "", reverse=True)
    return active[0]


def build_run_dashboard(
    runs: list[ManagedRun],
    *,
    repo_id: str,
    preferred_profile_id: str = "",
    preferred_environment: str = "development",
    preferred_port: int | None = None,
    check_ports: bool = True,
    port_is_available: Callable[[int], bool] | None = None,
    listeners_for: Callable[[int], list[int]] | None = None,
    history_limit: int = HISTORY_DISPLAY_LIMIT,
) -> dict[str, Any]:
    """Split active application vs history and attach reconciled views."""
    reconciled_all = [
        reconcile_run(
            r,
            check_port=check_ports,
            port_is_available=port_is_available,
            listeners_for=listeners_for,
        )
        for r in runs
    ]
    active_views = [v for v in reconciled_all if v.is_active]
    history_views = [v for v in reconciled_all if not v.is_active]
    history_views.sort(key=lambda v: v.started_at or "", reverse=True)
    active_views.sort(key=lambda v: v.started_at or "", reverse=True)
    limit = max(0, int(history_limit))
    history_display = history_views[:limit]

    if active_views:
        active = active_views[0].to_public()
    else:
        port = preferred_port
        profile = preferred_profile_id
        env = preferred_environment
        if history_views:
            last = history_views[0]
            port = port or last.port
            profile = profile or last.profile_id
            env = env or last.environment
            idle = idle_active_card(
                repo_id=repo_id,
                profile_id=profile,
                environment=env,
                port=port,
                check_port=check_ports,
                port_is_available=port_is_available,
                listeners_for=listeners_for,
            )
            if last.warning and not idle.warning:
                idle.warning = last.warning
                idle.warning_code = last.warning_code
                idle.listener_pids = list(last.listener_pids)
            active = idle.to_public()
        else:
            active = idle_active_card(
                repo_id=repo_id,
                profile_id=profile,
                environment=env,
                port=port,
                check_port=check_ports,
                port_is_available=port_is_available,
                listeners_for=listeners_for,
            ).to_public()

    return {
        "active": active,
        "history": [v.to_public() for v in history_display],
        "history_total": len(history_views),
        "runs": [v.to_public() for v in (active_views + history_views)],
        "auto_refresh": bool(active.get("auto_refresh")),
    }


def process_kind_label(process: dict[str, Any]) -> str:
    """Managed / External / Possible stale for Repository Processes table."""
    if process.get("managed_by_hub"):
        return "Managed"
    reasons = " ".join(str(r) for r in (process.get("detection_reasons") or [])).lower()
    confidence = str(process.get("confidence") or "")
    if confidence == "Low" or "stale" in reasons or "fingerprint" in reasons:
        return "Possible stale process"
    return "External"
