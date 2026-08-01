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

from hub.agent_center.codex_jsonl import CodexJsonlAccumulator
from hub.agent_center.codex_safety import assert_git_unchanged, git_status_snapshot
from hub.agent_center.models import DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
from hub.agent_center.redact import classify_provider_error, redact_text
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
        stdin_path: str | None = None,
        jsonl: bool = False,
        safety_repo: str | None = None,
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
                "stdin_path": stdin_path,
                "jsonl": jsonl,
                "safety_repo": safety_repo,
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
        stdin_path: str | None = None,
        jsonl: bool = False,
        safety_repo: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store.update_run(run_id, status="running", started_at=now)
        if self.audit:
            self.audit(action="AGENT_RUN_START", detail={"run_id": run_id, "argv0": argv[:1]})

        child_env = dict(os.environ)
        if env:
            child_env.update(env)
        for key in list(child_env):
            upper = key.upper()
            if any(s in upper for s in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE_KEY", "COOKIE")):
                child_env.pop(key, None)

        before = git_status_snapshot(Path(safety_repo)) if safety_repo else None
        stdin_handle = None
        try:
            if stdin_path:
                stdin_handle = open(stdin_path, "r", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                stdin=stdin_handle if stdin_handle else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
        except OSError as exc:
            if stdin_handle:
                stdin_handle.close()
            classified = classify_provider_error(str(exc))
            self.store.update_run(
                run_id,
                status="failed",
                error=classified["detail"],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            if self.audit:
                self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "error": classified["code"]})
            return

        with self._lock:
            self._procs[run_id] = proc
        self.store.update_run(run_id, pid=proc.pid)

        chunks: list[str] = []
        accumulator = CodexJsonlAccumulator() if jsonl else None
        deadline = time.monotonic() + timeout_seconds
        assert proc.stdout is not None
        try:
            while True:
                run = self.store.get_run(run_id)
                if run and run.get("cancel_requested"):
                    self._terminate(proc)
                    self.store.append_log(run_id, "\n[cancelled]\n")
                    answer = accumulator.final_answer() if accumulator else redact_text("".join(chunks))
                    self.store.update_run(
                        run_id,
                        status="cancelled",
                        answer=answer,
                        tool_activity=(accumulator.tool_activity if accumulator else None),
                        usage=(accumulator.usage if accumulator else None),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if self.audit:
                        self.audit(action="AGENT_RUN_CANCELLED", detail={"run_id": run_id})
                    return

                if time.monotonic() > deadline:
                    self._terminate(proc)
                    self.store.append_log(run_id, "\n[timeout]\n")
                    answer = accumulator.final_answer() if accumulator else redact_text("".join(chunks))
                    self.store.update_run(
                        run_id,
                        status="failed",
                        error=classify_provider_error(f"Timed out after {int(timeout_seconds)}s")["detail"],
                        answer=answer,
                        tool_activity=(accumulator.tool_activity if accumulator else None),
                        usage=(accumulator.usage if accumulator else None),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                    if self.audit:
                        self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "error": "timeout"})
                    return

                line = proc.stdout.readline()
                if line:
                    if accumulator is not None:
                        chunk = accumulator.feed(line)
                        if chunk:
                            chunks.append(chunk)
                            self.store.append_log(run_id, chunk)
                            if accumulator.tool_activity:
                                self.store.update_run(run_id, tool_activity=accumulator.tool_activity)
                            if accumulator.usage:
                                self.store.update_run(run_id, usage=accumulator.usage)
                    else:
                        chunks.append(line)
                        self.store.append_log(run_id, line)
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.05)

            rest = proc.stdout.read() or ""
            if rest:
                if accumulator is not None:
                    for rest_line in rest.splitlines(keepends=True):
                        chunk = accumulator.feed(rest_line)
                        if chunk:
                            chunks.append(chunk)
                            self.store.append_log(run_id, chunk)
                else:
                    chunks.append(rest)
                    self.store.append_log(run_id, rest)

            code = proc.wait()
            answer = accumulator.final_answer() if accumulator else redact_text("".join(chunks))
            if not answer and chunks:
                answer = redact_text("".join(chunks))
            finished = datetime.now(timezone.utc).isoformat()
            run = self.store.get_run(run_id)
            if run and run.get("cancel_requested"):
                self.store.update_run(
                    run_id,
                    status="cancelled",
                    answer=answer,
                    tool_activity=(accumulator.tool_activity if accumulator else None),
                    usage=(accumulator.usage if accumulator else None),
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_CANCELLED", detail={"run_id": run_id})
                return

            safety_error = ""
            if before is not None and safety_repo:
                after = git_status_snapshot(Path(safety_repo))
                try:
                    assert_git_unchanged(before, after)
                except RuntimeError as exc:
                    safety_error = str(exc)

            if safety_error:
                self.store.update_run(
                    run_id,
                    status="failed",
                    answer=answer,
                    error=safety_error,
                    tool_activity=(accumulator.tool_activity if accumulator else None),
                    usage=(accumulator.usage if accumulator else None),
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "error": "read_only_violation"})
                return

            if code == 0:
                self.store.update_run(
                    run_id,
                    status="completed",
                    answer=answer,
                    tool_activity=(accumulator.tool_activity if accumulator else None),
                    usage=(accumulator.usage if accumulator else None),
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_COMPLETED", detail={"run_id": run_id})
            else:
                err = ""
                if accumulator and accumulator.errors:
                    err = accumulator.error_summary()
                if not err:
                    err = classify_provider_error(f"Agent exited with code {code}")["detail"]
                self.store.update_run(
                    run_id,
                    status="failed",
                    answer=answer,
                    error=err,
                    tool_activity=(accumulator.tool_activity if accumulator else None),
                    usage=(accumulator.usage if accumulator else None),
                    finished_at=finished,
                )
                if self.audit:
                    self.audit(action="AGENT_RUN_FAILED", detail={"run_id": run_id, "code": code})
        finally:
            if stdin_handle:
                try:
                    stdin_handle.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._procs.pop(run_id, None)
                self._threads.pop(run_id, None)

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except OSError:
            pass
