"""Process manager for approved repository run profiles.

Safety:
- shell=False launches only
- new process group / session per run
- track only hub-started runs (run_id fingerprint + image path)
- never kill unrelated processes
- stale PID / PID-reuse detection before stop/restart
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from hub.audit import actions as audit_actions
from hub.repository_workspace.logs import RunLogStore
from hub.repository_workspace.ports import find_available_port, port_available
from hub.repository_workspace.run_profiles import PreparedLaunch, RunProfileError
from hub.repository_workspace.security import WorkspaceSecurityError, redact_audit_detail
from hub.settings import ROOT_DIR

ACTIVE_STATUSES = frozenset({"starting", "running", "healthy", "unhealthy", "stopping"})
TERMINAL_STATUSES = frozenset({"stopped", "failed"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_runs_dir() -> Path:
    configured = (os.environ.get("REPO_WS_RUN_STATE_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "data" / "repository_runs" / "state"


@dataclass
class ManagedRun:
    run_id: str
    repo_id: str
    profile_id: str
    environment: str
    port: int
    status: str
    pid: int | None = None
    pgid: int | None = None
    executable_path: str = ""
    argv_redacted: list[str] = field(default_factory=list)
    cwd: str = ""
    local_url: str = ""
    health_url: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    last_health_at: str | None = None
    last_health_ok: bool | None = None
    last_health_detail: str = ""
    error: str = ""
    create_token: str = ""
    startup_timeout_seconds: float = 30.0

    def to_public(self) -> dict[str, Any]:
        elapsed = None
        if self.started_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                end = (
                    datetime.fromisoformat(self.stopped_at)
                    if self.stopped_at
                    else datetime.now(timezone.utc)
                )
                elapsed = max(0, int((end - start).total_seconds()))
            except ValueError:
                elapsed = None
        return {
            "run_id": self.run_id,
            "repo_id": self.repo_id,
            "profile_id": self.profile_id,
            "environment": self.environment,
            "port": self.port,
            "status": self.status,
            "pid": self.pid,
            "local_url": self.local_url,
            "health_url": self.health_url,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "elapsed_seconds": elapsed,
            "last_health_at": self.last_health_at,
            "last_health_ok": self.last_health_ok,
            "last_health_detail": self.last_health_detail,
            "error": self.error,
            "command_preview": [self.executable_path] + list(self.argv_redacted),
            "cwd": self.cwd,
        }


AuditFn = Callable[[str, str, str, bool], None]


class ProcessManager:
    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        logs: RunLogStore | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self.state_dir = state_dir or default_runs_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs = logs or RunLogStore()
        self.audit = audit
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._readers: dict[str, list[threading.Thread]] = {}
        self._reconcile_stale()

    # ---- persistence ----

    def _path(self, run_id: str) -> Path:
        return self.state_dir / f"{run_id}.json"

    def _save(self, run: ManagedRun) -> None:
        self._path(run.run_id).write_text(
            json.dumps(asdict(run), indent=2), encoding="utf-8"
        )

    def _load(self, run_id: str) -> ManagedRun | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ManagedRun(**raw)

    def list_runs(self, *, repo_id: str | None = None) -> list[ManagedRun]:
        runs: list[ManagedRun] = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                run = ManagedRun(**raw)
            except Exception:  # noqa: BLE001
                continue
            if repo_id and run.repo_id != repo_id:
                continue
            runs.append(run)
        return runs

    def get(self, run_id: str) -> ManagedRun | None:
        with self._lock:
            run = self._load(run_id)
            if run:
                self._refresh_status(run)
            return run

    def _audit(self, action: str, target: str, detail: str, ok: bool = True) -> None:
        if self.audit:
            self.audit(action, target, redact_audit_detail(detail), ok)

    # ---- PID / process identity ----

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            if os.name == "nt":
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
                )
                if not handle:
                    return False
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _process_image(self, pid: int) -> str | None:
        if pid <= 0:
            return None
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
                )
                if not handle:
                    return None
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        handle, 0, buf, ctypes.byref(size)
                    )
                    return buf.value if ok else None
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                return None
        try:
            exe = Path(f"/proc/{pid}/exe").resolve()
            return str(exe)
        except Exception:  # noqa: BLE001
            return None

    def _matches_fingerprint(self, run: ManagedRun) -> bool:
        """True only when PID is alive and appears to be the hub-started process."""
        if not run.pid:
            return False
        if not self._pid_alive(run.pid):
            return False
        # Prefer in-memory handle identity when available
        proc = self._procs.get(run.run_id)
        if proc is not None and proc.pid == run.pid and proc.poll() is None:
            return True
        image = self._process_image(run.pid)
        if not image or not run.executable_path:
            # Without a verifiable image, treat as stale (PID reuse protection).
            return False
        try:
            return Path(image).resolve() == Path(run.executable_path).resolve()
        except Exception:  # noqa: BLE001
            return False

    def _reconcile_stale(self) -> None:
        for run in self.list_runs():
            if run.status in TERMINAL_STATUSES:
                continue
            if not self._matches_fingerprint(run):
                run.status = "stopped"
                run.stopped_at = run.stopped_at or _utcnow()
                run.error = run.error or "Stale PID or process no longer matches hub fingerprint."
                self._save(run)

    def _refresh_status(self, run: ManagedRun) -> None:
        if run.status in TERMINAL_STATUSES:
            return
        proc = self._procs.get(run.run_id)
        if proc is not None:
            code = proc.poll()
            if code is not None:
                run.status = "failed" if code != 0 else "stopped"
                run.stopped_at = _utcnow()
                if code != 0:
                    run.error = f"Process exited with code {code}"
                self._procs.pop(run.run_id, None)
                self._save(run)
                return
        elif not self._matches_fingerprint(run):
            run.status = "stopped"
            run.stopped_at = _utcnow()
            run.error = run.error or "Process ended or PID no longer trusted."
            self._save(run)
            return

        # Optional health probe for running processes
        if run.health_url and run.status in {"starting", "running", "healthy", "unhealthy"}:
            prev_status = run.status
            ok, detail = self._probe_health(run.health_url)
            run.last_health_at = _utcnow()
            run.last_health_ok = ok
            run.last_health_detail = detail
            if run.status == "starting":
                # remain starting until timeout or first success
                if ok:
                    run.status = "healthy"
                else:
                    started = datetime.fromisoformat(run.started_at) if run.started_at else None
                    if started and (
                        datetime.now(timezone.utc) - started
                    ).total_seconds() > run.startup_timeout_seconds:
                        run.status = "unhealthy"
            elif ok:
                run.status = "healthy"
            else:
                run.status = "unhealthy"
            self._save(run)
            if prev_status != run.status:
                self._audit(
                    audit_actions.REPO_WS_RUN_HEALTH,
                    run.repo_id,
                    f"run={run.run_id} {prev_status}->{run.status} {detail}",
                    ok,
                )

    def _probe_health(self, url: str) -> tuple[bool, str]:
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=3) as resp:  # noqa: S310 — local health only
                code = getattr(resp, "status", 200)
                return (200 <= int(code) < 400), f"HTTP {code}"
        except Exception as exc:  # noqa: BLE001
            return False, redact_audit_detail(str(exc), limit=160)

    # ---- start / stop ----

    def occupied_ports(self) -> set[int]:
        ports: set[int] = set()
        for run in self.list_runs():
            if run.status in ACTIVE_STATUSES:
                ports.add(int(run.port))
        return ports

    def find_port(self, preferred: int) -> int | None:
        return find_available_port(preferred, exclude=self.occupied_ports())

    def start(
        self,
        *,
        repo_id: str,
        launch: PreparedLaunch,
    ) -> ManagedRun:
        with self._lock:
            # Duplicate protection: same repo/profile/port while active
            for existing in self.list_runs(repo_id=repo_id):
                if existing.status not in ACTIVE_STATUSES:
                    continue
                if (
                    existing.profile_id == launch.profile_id
                    and int(existing.port) == int(launch.port)
                ):
                    raise RunProfileError(
                        "A run with this repository/profile/port is already active.",
                        code="duplicate_run",
                    )
            if not port_available(launch.port):
                alt = self.find_port(launch.port)
                raise RunProfileError(
                    f"Port {launch.port} is occupied."
                    + (f" Suggested alternate: {alt}." if alt else ""),
                    code="port_occupied",
                )

            found = shutil.which(launch.executable)
            if found:
                exe = found
            else:
                candidate = Path(launch.executable)
                if candidate.exists():
                    exe = str(candidate.resolve())
                else:
                    raise RunProfileError(
                        f"Executable not found: {launch.executable}",
                        code="exe_missing",
                    )
            exe_path = Path(exe)

            run_id = uuid.uuid4().hex
            token = uuid.uuid4().hex
            env = dict(launch.env)
            env["CENTRAL_HUB_RUN_ID"] = run_id
            env["CENTRAL_HUB_RUN_TOKEN"] = token

            popen_kwargs: dict[str, Any] = {
                "args": [str(exe_path), *launch.argv],
                "cwd": str(launch.cwd),
                "env": env,
                "shell": False,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
            }
            if os.name == "nt":
                # CREATE_NEW_PROCESS_GROUP = 0x00000200
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
                )
            else:
                popen_kwargs["start_new_session"] = True

            try:
                proc = subprocess.Popen(**popen_kwargs)  # noqa: S603
            except OSError as exc:
                raise RunProfileError(
                    f"Failed to start process: {exc}", code="start_failed"
                ) from exc

            run = ManagedRun(
                run_id=run_id,
                repo_id=repo_id,
                profile_id=launch.profile_id,
                environment=launch.environment,
                port=launch.port,
                status="starting",
                pid=proc.pid,
                pgid=proc.pid,
                executable_path=str(Path(exe).resolve()) if Path(exe).exists() else str(exe),
                argv_redacted=list(launch.argv_redacted),
                cwd=str(launch.cwd),
                local_url=launch.local_url,
                health_url=launch.health_url,
                started_at=_utcnow(),
                create_token=token,
                startup_timeout_seconds=launch.startup_timeout_seconds,
            )
            self._procs[run_id] = proc
            self._save(run)
            self._start_log_readers(run_id, proc)
            self.logs.append(run_id, f"Started pid={proc.pid} port={launch.port}", stream="stdout")
            self._audit(
                audit_actions.REPO_WS_RUN_START,
                repo_id,
                f"profile={launch.profile_id} env={launch.environment} port={launch.port} run={run_id}",
                True,
            )
            # If no health URL, mark running shortly
            if not launch.health_url:
                run.status = "running"
                self._save(run)
            return run

    def _start_log_readers(self, run_id: str, proc: subprocess.Popen[str]) -> None:
        threads: list[threading.Thread] = []

        def _read(stream_name: str, stream: Any) -> None:
            try:
                for line in iter(stream.readline, ""):
                    if not line:
                        break
                    self.logs.append(run_id, line.rstrip("\n"), stream=stream_name)
            except Exception:  # noqa: BLE001
                pass

        if proc.stdout is not None:
            t = threading.Thread(
                target=_read, args=("stdout", proc.stdout), daemon=True, name=f"rw-out-{run_id}"
            )
            t.start()
            threads.append(t)
        if proc.stderr is not None:
            t = threading.Thread(
                target=_read, args=("stderr", proc.stderr), daemon=True, name=f"rw-err-{run_id}"
            )
            t.start()
            threads.append(t)
        self._readers[run_id] = threads

    def stop(self, run_id: str, *, reason: str = "stopped by user") -> ManagedRun:
        with self._lock:
            run = self._load(run_id)
            if run is None:
                raise WorkspaceSecurityError("Run not found.", code="not_found")
            if run.status in TERMINAL_STATUSES:
                return run
            if not self._matches_fingerprint(run):
                run.status = "stopped"
                run.stopped_at = _utcnow()
                run.error = "Refused to signal unverified PID (stale or reused)."
                self._save(run)
                self._audit(
                    audit_actions.REPO_WS_RUN_STOP,
                    run.repo_id,
                    f"run={run_id} stale_pid",
                    False,
                )
                return run

            run.status = "stopping"
            self._save(run)
            proc = self._procs.get(run_id)
            try:
                self._terminate_group(run, proc)
            except Exception as exc:  # noqa: BLE001
                run.error = redact_audit_detail(str(exc))
            # wait briefly, then force-kill the tracked group only
            if proc is not None:
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    self._kill_group(run, proc)
                    try:
                        proc.wait(timeout=3)
                    except Exception:  # noqa: BLE001
                        pass
            elif run.pid and self._matches_fingerprint(run):
                self._kill_group(run, None)

            still_alive = bool(run.pid and self._pid_alive(int(run.pid)) and self._matches_fingerprint(run))
            if still_alive:
                self._kill_group(run, proc)
                time.sleep(0.2)
                still_alive = bool(run.pid and self._pid_alive(int(run.pid)) and self._matches_fingerprint(run))

            self._close_proc_pipes(proc)
            self._procs.pop(run_id, None)
            run.status = "failed" if still_alive else "stopped"
            run.stopped_at = _utcnow()
            run.error = (
                "Stop signal sent but process still appears alive."
                if still_alive
                else reason
            )
            self._save(run)
            self.logs.append(run_id, f"Stopped: {run.error}", stream="stdout")
            self._audit(
                audit_actions.REPO_WS_RUN_STOP,
                run.repo_id,
                f"run={run_id} port={run.port}",
                not still_alive,
            )
            return run

    def _close_proc_pipes(self, proc: subprocess.Popen[str] | None) -> None:
        if proc is None:
            return
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def restart(self, run_id: str, prepare_again: Callable[[ManagedRun], PreparedLaunch]) -> ManagedRun:
        old = self.get(run_id)
        if old is None:
            raise WorkspaceSecurityError("Run not found.", code="not_found")
        self.stop(run_id, reason="restart")
        launch = prepare_again(old)
        new_run = self.start(repo_id=old.repo_id, launch=launch)
        self._audit(
            audit_actions.REPO_WS_RUN_RESTART,
            old.repo_id,
            f"old={run_id} new={new_run.run_id} port={new_run.port}",
            True,
        )
        return new_run

    def _terminate_group(self, run: ManagedRun, proc: subprocess.Popen[str] | None) -> None:
        if os.name == "nt":
            pid = run.pid or (proc.pid if proc else None)
            if not pid or not self._matches_fingerprint(run):
                return
            # Soft tree terminate (no /F). CTRL_BREAK alone is unreliable for many apps.
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                shell=False,
                capture_output=True,
                check=False,
                timeout=8,
            )
            return
        pgid = run.pgid or run.pid
        if pgid:
            os.killpg(pgid, signal.SIGTERM)

    def _kill_group(self, run: ManagedRun, proc: subprocess.Popen[str] | None) -> None:
        if os.name == "nt":
            pid = run.pid or (proc.pid if proc else None)
            if pid and self._matches_fingerprint(run):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    shell=False,
                    capture_output=True,
                    check=False,
                    timeout=8,
                )
            return
        pgid = run.pgid or run.pid
        if pgid and self._matches_fingerprint(run):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
