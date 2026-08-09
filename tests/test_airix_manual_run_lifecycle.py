"""AiriX manual AgentCenter run + T0 direct / skipRoutingOnce contracts.

Covers the stuck-"Running" dock path: wrapped ``{run: ...}`` payloads, terminal
``completed`` (not only ``succeeded``), T0 direct execute, Grok complete/fail,
cancel, timeout, and one-shot skipRoutingOnce (recommend only).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.lifecycle import (
    TERMINAL_STATUSES,
    consume_skip_routing_once,
    is_terminal,
    normalize_status,
    unwrap_agent_run_payload,
)
from hub.agent_center.routes import _public_run
from hub.agent_center.service import AgentCenterError


REPRO_PROMPT = "Show recent DHIS2 logs and statuses"


class _AsyncFakeAgentCenter:
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
        if self.mode == "fail_immediate":
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
            row["status"] = "completed"
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


class ManualRunContractTests(unittest.TestCase):
    def test_public_run_wrapper_uses_completed_not_succeeded(self) -> None:
        """Dock must unwrap ``{run: ...}`` and treat ``completed`` as terminal."""
        raw = {
            "id": "run-abc",
            "status": "completed",
            "answer": "ok",
            "error": "",
            "context": {"included_sources": ["jobs"]},
            "tool_activity": [],
            "usage": {},
        }
        wrapped = {"run": _public_run(raw, include_body=True)}
        run = unwrap_agent_run_payload(wrapped)
        assert run is not None
        self.assertEqual(run["id"], "run-abc")
        self.assertEqual(run["status"], "completed")
        self.assertTrue(is_terminal(run["status"]))
        self.assertNotEqual(run["status"], "succeeded")
        # Bare polling of the wrapper (bug) never sees status.
        self.assertIsNone(wrapped.get("status"))

    def test_unwrap_accepts_bare_run_object(self) -> None:
        run = unwrap_agent_run_payload({"id": "r1", "status": "failed", "error": "x"})
        assert run is not None
        self.assertEqual(normalize_status(str(run["status"])), "failed")

    def test_terminal_set_matches_dock_contract(self) -> None:
        for status in (
            "completed",
            "failed",
            "cancelled",
            "paused_for_approval",
            "timed_out",
        ):
            self.assertIn(status, TERMINAL_STATUSES)
            self.assertTrue(is_terminal(status))
        self.assertEqual(normalize_status("succeeded"), "completed")
        self.assertFalse(is_terminal("running"))

    def test_skip_routing_once_is_one_shot(self) -> None:
        skip_now, next_flag = consume_skip_routing_once(True)
        self.assertTrue(skip_now)
        self.assertFalse(next_flag)
        # Second prompt after consume must recommend again.
        skip_now2, next_flag2 = consume_skip_routing_once(next_flag)
        self.assertFalse(skip_now2)
        self.assertFalse(next_flag2)
        # Skipping recommend never implies skipping lifecycle terminal handling.
        self.assertTrue(is_terminal("completed"))


class T0DirectAndProviderLifecycleTests(unittest.TestCase):
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

    def test_repro_prompt_routes_t0_and_executes_deterministic(self) -> None:
        fake = _AsyncFakeAgentCenter()
        router = self._router(fake)
        rec = router.recommend_route(REPRO_PROMPT)
        self.assertEqual(rec.recommended_agent, "deterministic")
        self.assertEqual(rec.recommended_tier, "T0")
        result = router.execute_route(REPRO_PROMPT, orchestrate=True)
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertEqual(result["execution"].get("provider_id") or result["execution"].get("mode"), "deterministic")
        # No Grok child when T0 tools solve it.
        self.assertFalse(any(p.get("agent_id") == "grok" for p in fake.started))

    def test_grok_manual_override_completes(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="succeed")
        router = self._router(fake)
        result = router.execute_route(
            REPRO_PROMPT,
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "completed")
        self.assertTrue(result["execution"].get("terminal"))
        self.assertIn("Grok finished", result["execution"].get("answer") or "")
        child_id = result["execution"].get("agent_run_id")
        self.assertTrue(child_id)
        # Poll contract: wrapped public run is terminal completed.
        child = fake.get_run(str(child_id))
        wrapped = {"run": _public_run(child, include_body=True)}
        run = unwrap_agent_run_payload(wrapped)
        assert run is not None
        self.assertTrue(is_terminal(normalize_status(str(run["status"]))))

    def test_grok_manual_override_fails(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="fail_later")
        router = self._router(fake)
        result = router.execute_route(
            "Investigate DHIS2 analytics SQL join for program indicators",
            orchestrate=False,
            agent_override="grok",
        )
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertTrue(result["execution"].get("terminal"))

    def test_cancel_is_terminal(self) -> None:
        fake = _AsyncFakeAgentCenter(mode="succeed")
        router = self._router(fake)
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
        cancelled = router.cancel_execution("child-1")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled.get("terminal"))
        wrapped = {"run": _public_run(fake.runs["run-x"], include_body=True)}
        run = unwrap_agent_run_payload(wrapped)
        assert run is not None
        self.assertEqual(normalize_status(str(run["status"])), "cancelled")

    def test_timeout_is_terminal(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
