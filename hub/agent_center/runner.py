"""Background runner for agent CLI invocations (shell=False, cancellable)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.agent_center.models import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore

AuditFn = Callable[..., None]


class AgentRunner:
    def __init__(self, store: AgentCenterStore, *, audit: AuditFn | None = None) -> None:
        self.store = store
        self.audit = audit
        self._threads: dict[str, threading.Thread] = {}
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    def start(
        self,
        *,
        run_id: str,
        argv: list[str],
        cwd: Path,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> None:
        timeout_seconds = max(5.0, min(float(timeout_seconds), float(MAX_TIMEOUT_SECONDS)))
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "run_id": run_id,
                "argv": list(argv),
                "cwd": str(cwd),
                "timeout_seconds": timeout_seconds,
                "env": env,
            },
            daemon=True,
            name=f"agent-run-{run_id[:8]}",
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def cancel(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.request_cancel(run_id)
        with self._lock:
            proc = self._procs.get(run_id)
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
            except OSError:
                pass
        return run

    def _run(
        self,
        *,
        run_id: str,
        argv: list[str],
        cwd: str,
        timeout_seconds: float,
        env: dict[str, str] | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.update_run(run_id, status="running", started_at=now)
        if self.audit:
            self.audit(action="AGENT_RUN_START", detail={"run_id": run_id, "argv0": argv[:1]})

        # Never inherit secrets from hub .env into child unless already on process env;
        # still strip known secret keys from a copy.
        child_env = dict(os.environ)
        if env:
            child_env.update(env)
        for key in list(child_env):
            upper = key.upper()
            if any(s in upper for s in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE")):
                # Keep PATH etc.; only drop obvious secret-bearing vars we control.
                if upper.startswith(("DHIS2_", "SQL_WS_", "GMAIL_", "CENTRAL_HUB_OWNER", "LIVE_", "STAGE_")):
                    child_env.pop(key, None)

        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
        except OSError as exc:
            self.store.update_run(
                run_id,
                status="failed",
                error=f"Failed to start agent: {exc}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            if self.audit:
                self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "error": str(exc)})
            return

        with self._lock:
            self._procs[run_id] = proc
        self.store.update_run(run_id, pid=proc.pid)

        chunks: list[str] = []
        deadline = time.monotonic() + timeout_seconds
        assert proc.stdout is not None
        try:
            while True:
                run = self.store.get_run(run_id)
                if run and run.get("cancel_requested"):
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    self.store.append_log(run_id, "\n[cancelled]\n")
                    self.store.update_run(
                        run_id,
                        status="cancelled",
                        answer=redact_text("".join(chunks)),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if self.audit:
                        self.audit(action="AGENT_RUN_CANCELLED", detail={"run_id": run_id})
                    return

                if time.monotonic() > deadline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    self.store.append_log(run_id, "\n[timeout]\n")
                    self.store.update_run(
                        run_id,
                        status="failed",
                        error=f"Timed out after {int(timeout_seconds)}s",
                        answer=redact_text("".join(chunks)),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if self.audit:
                        self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "error": "timeout"})
                    return

                line = proc.stdout.readline()
                if line:
                    chunks.append(line)
                    self.store.append_log(run_id, line)
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.05)

            # Drain remainder
            rest = proc.stdout.read() or ""
            if rest:
                chunks.append(rest)
                self.store.append_log(run_id, rest)

            code = proc.wait()
            answer = redact_text("".join(chunks))
            finished = datetime.now(timezone.utc).isoformat()
            run = self.store.get_run(run_id)
            if run and run.get("cancel_requested"):
                self.store.update_run(
                    run_id,
                    status="cancelled",
                    answer=answer,
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_CANCELLED", detail={"run_id": run_id})
                return
            if code == 0:
                self.store.update_run(
                    run_id,
                    status="completed",
                    answer=answer,
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_COMPLETED", detail={"run_id": run_id})
            else:
                self.store.update_run(
                    run_id,
                    status="failed",
                    answer=answer,
                    error=f"Agent exited with code {code}",
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "code": code})
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._procs.pop(run_id, None)
                self._threads.pop(run_id, None)
