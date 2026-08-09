"""AiriX Smart Routing — execution lifecycle / stuck-running fixes."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.lifecycle import (
    is_terminal,
    normalize_status,
    public_execution_fields,
)
from hub.agent_center.service import AgentCenterError


class _AsyncFakeAgentCenter:
    """Simulates async provider runs that finish on get_run poll."""

    def __init__(self, *, mode: str = "succeed") -> None:
        self.mode = mode
        self.started: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.registry = MagicMock()
        self.notebook = None
        self.sql_store = None
        self.uid_index = None
        self.email = None
        self.calendar = None
        self.job_store = None
        self.audit_store = None
        self.dhis2_reports = None
        self.notepad_factory = None
        self._n = 0

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._n += 1
        run_id = f"run-{self._n}"
        row = {
            "id": run_id,
            "status": "running",
            "answer": "",
            "agent_id": payload.get("agent_id"),
            "prompt": payload.get("prompt"),
            "tool_ids": list(payload.get("tool_ids") or []),
            "error": "",
        }
        if self.mode == "unavailable":
            row["status"] = "unavailable"
            row["error"] = "provider down"
        elif self.mode == "fail_immediate":
            row["status"] = "failed"
            row["error"] = "boom"
        self.started.append(payload)
        self.runs[run_id] = row
        self.polls[run_id] = 0
        return dict(row)

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        if run_id not in self.runs:
            raise AgentCenterError("not found", code="not_found")
        self.polls[run_id] = self.polls.get(run_id, 0) + 1
        row = dict(self.runs[run_id])
        if self.mode == "succeed" and self.polls[run_id] >= 2:
            row["status"] = "succeeded"
            row["answer"] = f"Grok finished for {row.get('agent_id')}"
            row["finished_at"] = "2026-08-10T00:00:05+00:00"
            row["usage"] = {"total_tokens": 42}
            self.runs[run_id] = row
        elif self.mode == "fail_later" and self.polls[run_id] >= 2:
            row["status"] = "failed"
            row["error"] = "provider failed mid-run"
            row["finished_at"] = "2026-08-10T00:00:05+00:00"
            self.runs[run_id] = row
        elif self.mode == "hang":
            row["status"] = "running"
        return dict(row)

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        self.cancelled.append(run_id)
        if run_id in self.runs:
            self.runs[run_id]["status"] = "cancelled"
            self.runs[run_id]["error"] = "Cancelled by user"
        return {"id": run_id, "status": "cancelled"}

    def get_agent(self, agent_id: str) -> Any:
        return MagicMock() if agent_id else None

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return [{"id": "sample-cli", "name": "sample-cli", "selectable": True}]


def _availability() -> dict[str, dict[str, Any]]:
    return {
        "grok": {"id": "grok", "status": "available", "runnable": True},
        "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
        "codex": {"id": "codex", "status": "available", "runnable": True},
    }


class LifecycleHelperTests(unittest.TestCase):
    def test_normalize_terminal_statuses(self) -> None:
        self.assertEqual(normalize_status("paused"), "paused_for_approval")
        self.assertEqual(normalize_status("blocked"), "failed")
        self.assertEqual(normalize_status("unavailable"), "failed")
        self.assertEqual(normalize_status("active"), "running")
        self.assertTrue(is_terminal("paused_for_approval"))
        self.assertTrue(is_terminal("timed_out"))
        self.assertFalse(is_terminal("running"))
        pub = public_execution_fields({"status": "paused", "id": "x"})
        self.assertEqual(pub["status"], "paused_for_approval")
        self.assertTrue(pub["terminal"])


class AirixLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = RoutingHistoryStore(AgentCenterDb(Path(self.tmp.name) / "agent.db"))

    def _router(self, fake: _AsyncFakeAgentCenter) -> AgentRouterService:
        router = AgentRouterService(
            availability_loader=_availability,
            agent_center=fake,  # type: ignore[arg-type]
            history=self.history,
        )
        assert router.executor is not None
        router.executor.step_wait_seconds = 2.0
        router.executor.poll_interval_seconds = 0.05
        return router

    def test_t0_completion_terminal(self) -> None:
        fake = _AsyncFakeAgentCenter()
        router = self._router(fake)
        result = router.execute_route(
            "Look up the UID for Philippines and show me the status of recent jobs",
            orchestrate=True,
        )
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertEqual(result["orchestration"]["status"], "completed")
        self.assertIn("Solved by deterministic", result["orchestration"].get("stopped_reason") or "")
        sess = router.get_session(result["execution"]["id"], actor="owner")
        self.assertIsNotNone(sess)
        assert sess is not None
        self.assertEqual(normalize_status(str(sess.get("status"))), "completed")

    def test_grok_async_completion(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="succeed")
        router = self._router(fake)
        result = router.execute_route(
            "Debug why the analytics SQL join returns empty rows in this module",
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertIn("Grok finished", result["execution"].get("answer") or "")
        self.assertGreaterEqual(sum(fake.polls.values()), 2)

    def test_grok_async_failure(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="fail_later")
        router = self._router(fake)
        result = router.execute_route(
            "Debug why the analytics SQL join returns empty rows in this module",
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertIn("failed", (result["execution"].get("error") or "").lower())

    def test_unavailable_provider_finalizes(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="unavailable")
        router = self._router(fake)

        def _none_available() -> dict[str, dict[str, Any]]:
            return {
                "grok": {"id": "grok", "status": "unavailable", "runnable": False},
                "hub-simulator": {"id": "hub-simulator", "status": "unavailable", "runnable": False},
                "codex": {"id": "codex", "status": "unavailable", "runnable": False},
            }

        router._availability_loader = _none_available  # type: ignore[method-assign]
        assert router.executor is not None
        router.executor._availability_loader = _none_available
        result = router.execute_route(
            "Investigate DHIS2 analytics SQL join for program indicators",
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertTrue(result["execution"].get("terminal"))

    def test_timeout_finalizes(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="hang")
        router = self._router(fake)
        assert router.executor is not None
        router.executor.step_wait_seconds = 0.2
        router.executor.poll_interval_seconds = 0.05
        result = router.execute_route(
            "Investigate DHIS2 analytics SQL join for program indicators",
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "timed_out")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertTrue(fake.cancelled)

    def test_cancel_terminates_parent_session(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="succeed")
        router = self._router(fake)
        # Seed an active orchestration session and a running child step.
        sess = self.history.save_session(
            {
                "workspace": "work",
                "actor": "owner",
                "prompt_fingerprint": "abc",
                "prompt_preview": "Investigate",
                "status": "active",
                "completed_steps": [],
            }
        )
        # Start a running executor row linked loosely; cancel by session id.
        assert router.executor is not None
        with router.executor._lock:
            router.executor._active["child-1"] = {
                "id": "child-1",
                "status": "running",
                "workspace": "work",
                "actor": "owner",
                "provider_id": "grok",
                "agent_run_id": "run-x",
                "history_recorded": False,
            }
        fake.runs["run-x"] = {"id": "run-x", "status": "running"}
        cancelled = router.cancel_execution(sess["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled.get("terminal"))
        refreshed = router.get_session(sess["id"], actor="owner")
        assert refreshed is not None
        self.assertEqual(normalize_status(str(refreshed.get("status"))), "cancelled")

    def test_codex_approval_pause_not_running(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="succeed")
        router = self._router(fake)
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        result = router.execute_route(prompt, orchestrate=True, approve_codex=False)
        self.assertEqual(result["execution"]["status"], "paused_for_approval")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertNotEqual(result["execution"]["status"], "running")
        self.assertEqual(result["orchestration"]["status"], "paused_for_approval")

    def test_stale_session_recovery(self) -> None:
        fake = _AsyncFakeAgentCenter()
        router = self._router(fake)
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        sess = self.history.save_session(
            {
                "workspace": "work",
                "actor": "owner",
                "prompt_fingerprint": "stale-fp",
                "prompt_preview": "old run",
                "status": "active",
                "created_at": old,
                "updated_at": old,
                "completed_steps": [],
            }
        )
        # Force updated_at/created_at old via direct SQL for stale check.
        with self.history.db.connect() as conn:
            conn.execute(
                "UPDATE airix_routing_sessions SET created_at=?, updated_at=? WHERE id=?",
                (old, old, sess["id"]),
            )
        status = router.execution_status(sess["id"])
        assert status is not None
        self.assertEqual(status["status"], "timed_out")
        self.assertTrue(status.get("terminal"))


if __name__ == "__main__":
    unittest.main()
