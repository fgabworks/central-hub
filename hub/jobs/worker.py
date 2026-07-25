"""Background job worker with cooperative cancel/pause (Phase 2–6)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from hub.jobs.db import utcnow
from hub.jobs.executor import JobCancelled, JobPaused, CapabilityExecutionError, run_capability
from hub.jobs.store import JobStore
from hub.registry.models import Registry

AuditFn = Callable[..., Any]


class JobWorker:
    """In-process worker loop — daemon threads, disk-backed job state."""

    def __init__(
        self,
        store: JobStore,
        *,
        registry_provider: Callable[[], Registry | None],
        max_concurrent: int = 2,
        audit: AuditFn | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self.store = store
        self.registry_provider = registry_provider
        self.max_concurrent = max(1, int(max_concurrent))
        self.audit = audit
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_lock = threading.Lock()
        self._active: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Mark orphaned running jobs from previous process.
        self.store.refresh_orphans()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hub-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def kick(self) -> None:
        """Wake-style hint: try claim immediately from caller thread if capacity."""
        self._maybe_start_jobs()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_start_jobs()
            except Exception:  # noqa: BLE001 — never kill the worker loop
                pass
            self._stop.wait(self.poll_seconds)

    def _maybe_start_jobs(self) -> None:
        with self._active_lock:
            running = len(self._active)
        while running < self.max_concurrent:
            job = self.store.claim_next()
            if not job:
                break
            job_id = job["id"]
            with self._active_lock:
                self._active.add(job_id)
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id,),
                name=f"hub-job-{job_id}",
                daemon=True,
            )
            thread.start()
            running += 1

    def _cancel_check(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            raise JobCancelled("Job missing")
        if job.get("cancel_requested"):
            raise JobCancelled()
        if job.get("pause_requested"):
            raise JobPaused()

    def _run_job(self, job_id: str) -> None:
        try:
            job = self.store.get(job_id)
            if not job:
                return
            self.store.append_log(job_id, "worker started")
            if self.audit:
                self.audit(
                    action="START_JOB",
                    target=job_id,
                    detail=f"Started {job['repository_id']}/{job['capability_id']}",
                    ok=True,
                )
            registry = self.registry_provider()
            if registry is None:
                raise CapabilityExecutionError("Repository registry is not loaded")
            repo = registry.get(job["repository_id"])
            if repo is None or not repo.enabled:
                raise CapabilityExecutionError(f"Repository unavailable: {job['repository_id']}")
            capability = next((c for c in repo.capabilities if c.id == job["capability_id"]), None)
            if capability is None:
                raise CapabilityExecutionError(f"Unknown capability: {job['capability_id']}")

            timeout = float(registry.defaults.job_timeout_seconds or 3600)
            # Cap individual capability run more tightly for sample demos.
            cap_timeout = float((capability.raw or {}).get("timeout_seconds") or min(timeout, 120))

            self.store.update(job_id, phase="running", percent=10, message="Executing capability")
            result = run_capability(
                repo,
                capability,
                dry_run=bool(job["dry_run"]),
                job_id=job_id,
                input_dir=Path(job["input_path"]),
                result_dir=Path(job["result_path"]),
                log_append=lambda line: self.store.append_log(job_id, line),
                cancel_check=lambda: self._cancel_check(job_id),
                timeout_seconds=cap_timeout,
            )
            self.store.update(
                job_id,
                status="completed",
                phase="finalizing",
                percent=100,
                message="Completed",
                finished_at=utcnow(),
                checkpoint={"result": result},
                metadata={**(job.get("metadata") or {}), "result": result},
            )
            self.store.append_log(job_id, f"completed ok artifacts={result.get('artifacts')}")
            if self.audit:
                self.audit(
                    action="JOB_COMPLETED",
                    target=job_id,
                    detail="Job completed successfully",
                    ok=True,
                    metadata={"dry_run": job["dry_run"]},
                )
        except JobPaused:
            self.store.update(
                job_id,
                status="paused",
                phase="paused",
                message="Paused by operator",
                resumable=True,
                pause_requested=False,
            )
            self.store.append_log(job_id, "paused")
            if self.audit:
                self.audit(action="JOB_PAUSED", target=job_id, detail="Job paused", ok=True)
        except JobCancelled:
            self.store.update(
                job_id,
                status="cancelled",
                phase="cancelled",
                percent=100,
                message="Cancelled",
                finished_at=utcnow(),
            )
            self.store.append_log(job_id, "cancelled")
            if self.audit:
                self.audit(action="JOB_CANCELLED", target=job_id, detail="Job cancelled", ok=True)
        except CapabilityExecutionError as exc:
            self.store.update(
                job_id,
                status="failed",
                phase="failed",
                percent=100,
                message=exc.message,
                error=exc.message,
                finished_at=utcnow(),
                resumable=True,
            )
            self.store.append_log(job_id, f"failed: {exc.message}")
            if self.audit:
                self.audit(
                    action="JOB_FAILED",
                    target=job_id,
                    detail=exc.message,
                    ok=False,
                )
        except Exception as exc:  # noqa: BLE001
            self.store.update(
                job_id,
                status="failed",
                phase="failed",
                percent=100,
                message=str(exc),
                error=str(exc),
                finished_at=utcnow(),
            )
            self.store.append_log(job_id, f"error: {exc}")
            if self.audit:
                self.audit(action="JOB_FAILED", target=job_id, detail=str(exc), ok=False)
        finally:
            with self._active_lock:
                self._active.discard(job_id)
