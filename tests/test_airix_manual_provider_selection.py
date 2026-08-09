"""Manual provider selection must never silently fall back to Hub Simulator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.context import provider_to_adapter_id
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.service import AgentCenterError


class _FakeAgentCenter:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self._n = 0
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

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._n += 1
        run_id = f"run-{self._n}"
        agent_id = str(payload.get("agent_id") or "")
        if agent_id == "hub-simulator" and not getattr(self, "allow_simulator", False):
            raise AssertionError("Hub Simulator must not start for this test")
        row = {
            "id": run_id,
            "status": "completed",
            "answer": f"ok via {agent_id}",
            "agent_id": agent_id,
            "model": payload.get("model") or "default-model",
            "prompt": payload.get("prompt"),
            "finished_at": "2026-08-10T00:00:00+00:00",
            "context": {},
        }
        self.started.append(payload)
        self.runs[run_id] = row
        return dict(row)

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        if run_id not in self.runs:
            raise AgentCenterError("not found", code="not_found")
        return dict(self.runs[run_id])

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        row = self.get_run(run_id, profile_id=profile_id)
        row["status"] = "cancelled"
        self.runs[run_id] = row
        return row

    def get_agent(self, agent_id: str) -> Any:
        return MagicMock()

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return [
            {
                "id": "sample-cli",
                "name": "sample-cli",
                "selectable": True,
                "path": str(Path(tempfile.gettempdir())),
            }
        ]


_REPO_OK = {
    "ok": True,
    "repository_ids": ["sample-cli"],
    "primary_id": "sample-cli",
    "code": "",
    "error": "",
    "source": "explicit",
}


class ManualProviderNoSimulatorFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = AgentCenterDb(root / "agent.db")
        self.history = RoutingHistoryStore(self.db)
        self.fake = _FakeAgentCenter()
        self.router = AgentRouterService(
            availability_loader=lambda: {
                "codex": {"id": "codex", "status": "available", "runnable": True},
                "grok": {"id": "grok", "status": "available", "runnable": True},
                "openai-api": {"id": "openai-api", "status": "available", "runnable": True},
                "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
                "claude-code": {"id": "claude-code", "status": "available", "runnable": True},
                "cursor-agent": {"id": "cursor-agent", "status": "available", "runnable": True},
            },
            agent_center=self.fake,  # type: ignore[arg-type]
            history=self.history,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manual_codex_executes_codex(self) -> None:
        with patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value=dict(_REPO_OK),
        ):
            result = self.router.execute_route(
                "Explain this Python function and suggest a cleaner refactor",
                orchestrate=False,
                agent_override="codex",
                approve_codex=True,
                model="gpt-5.3-codex",
                repository_ids=["sample-cli"],
            )
        ex = result["execution"]
        self.assertEqual(ex.get("status"), "completed", msg=ex.get("error"))
        self.assertTrue(ex.get("manual_override"))
        self.assertEqual(ex.get("provider_id"), "codex")
        self.assertEqual(ex.get("adapter_id"), "codex")
        self.assertEqual(ex.get("resolved_provider"), "codex")
        self.assertEqual(ex.get("selected_provider"), "codex")
        self.assertNotEqual(ex.get("adapter_id"), "hub-simulator")
        self.assertEqual(provider_to_adapter_id(ex.get("provider_id") or ""), "codex")
        self.assertEqual(len(self.fake.started), 1)
        self.assertEqual(self.fake.started[0].get("agent_id"), "codex")
        self.assertEqual(self.fake.started[0].get("model"), "gpt-5.3-codex")

    def test_manual_codex_unavailable_clear_error_no_simulator(self) -> None:
        assert self.router.executor is not None
        self.router.executor._availability_loader = lambda: {
            "codex": {
                "id": "codex",
                "status": "unavailable",
                "runnable": False,
                "detail": "not authenticated",
            },
            "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
            "grok": {"id": "grok", "status": "available", "runnable": True},
        }
        with patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value=dict(_REPO_OK),
        ):
            result = self.router.execute_route(
                "Explain this Python function and suggest a cleaner refactor",
                orchestrate=False,
                agent_override="codex",
                approve_codex=True,
                repository_ids=["sample-cli"],
            )
        ex = result["execution"]
        self.assertEqual(ex.get("status"), "failed")
        self.assertIn("unavailable", (ex.get("error") or "").lower())
        self.assertIn("no automatic fallback", (ex.get("error") or "").lower())
        self.assertEqual(ex.get("error_code"), "unavailable")
        self.assertNotEqual(ex.get("adapter_id"), "hub-simulator")
        self.assertEqual(ex.get("fallback_reason"), "provider_unavailable_no_auto_fallback")
        self.assertEqual(len(self.fake.started), 0)

    def test_manual_model_preserved(self) -> None:
        result = self.router.execute_route(
            "Refactor this helper for clarity",
            orchestrate=False,
            agent_override="grok",
            model="grok-4-1-fast-reasoning",
            repository_ids=["sample-cli"],
        )
        ex = result["execution"]
        self.assertEqual(ex.get("status"), "completed", msg=ex.get("error"))
        self.assertEqual(ex.get("selected_model"), "grok-4-1-fast-reasoning")
        self.assertEqual(self.fake.started[0].get("model"), "grok-4-1-fast-reasoning")
        self.assertEqual(
            ex.get("resolved_model") or self.fake.started[0].get("model"),
            "grok-4-1-fast-reasoning",
        )

    def test_recommendation_does_not_override_manual_choice(self) -> None:
        with patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value=dict(_REPO_OK),
        ):
            result = self.router.execute_route(
                "hi",
                orchestrate=False,
                agent_override="codex",
                approve_codex=True,
                model="gpt-5.3-codex",
                repository_ids=["sample-cli"],
            )
        rec = result["recommendation"]
        self.assertEqual(result["execution"].get("status"), "completed", msg=result["execution"].get("error"))
        self.assertEqual(result["execution"].get("provider_id"), "codex")
        self.assertEqual(result["execution"].get("adapter_id"), "codex")
        self.assertTrue(result["execution"].get("manual_override"))
        self.assertEqual(result["execution"].get("selected_provider"), "codex")
        if rec.get("recommended_agent") != "codex":
            self.assertEqual(
                result["execution"].get("recommended_provider"),
                rec.get("recommended_agent"),
            )
        self.assertEqual(self.fake.started[0]["agent_id"], "codex")

    def test_retry_preserves_provider_and_model(self) -> None:
        first = self.router.execute_route(
            "Refactor this helper for clarity",
            orchestrate=False,
            agent_override="grok",
            model="grok-4-1-fast-reasoning",
            repository_ids=["sample-cli"],
        )
        run_id = first["execution"].get("agent_run_id")
        self.assertTrue(run_id)
        prior = self.fake.get_run(str(run_id))
        retried = self.fake.start_run(
            {
                "agent_id": prior.get("agent_id"),
                "model": prior.get("model"),
                "prompt": prior.get("prompt"),
                "repository_ids": ["sample-cli"],
            }
        )
        self.assertEqual(retried.get("agent_id"), "grok")
        self.assertEqual(retried.get("model"), "grok-4-1-fast-reasoning")
        self.assertEqual(self.fake.started[-1].get("agent_id"), "grok")
        self.assertNotEqual(self.fake.started[-1].get("agent_id"), "hub-simulator")

    def test_hub_simulator_only_when_explicitly_selected(self) -> None:
        self.fake.allow_simulator = True
        result = self.router.execute_route(
            "Say hello in one sentence",
            orchestrate=False,
            agent_override="hub-simulator",
            approve_codex=False,
        )
        self.assertEqual(result["execution"].get("adapter_id"), "hub-simulator")
        self.assertTrue(result["execution"].get("manual_override"))
        self.assertEqual(self.fake.started[-1].get("agent_id"), "hub-simulator")

        assert self.router.executor is not None
        self.router.executor._availability_loader = lambda: {
            "codex": {"id": "codex", "status": "unavailable", "runnable": False},
            "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
        }
        before = len(self.fake.started)
        with patch(
            "hub.agent_center.repository_context.resolve_repository_context",
            return_value=dict(_REPO_OK),
        ):
            failed = self.router.execute_route(
                "Refactor this module carefully",
                orchestrate=False,
                agent_override="codex",
                approve_codex=True,
                repository_ids=["sample-cli"],
            )
        self.assertEqual(failed["execution"].get("status"), "failed")
        self.assertEqual(len(self.fake.started), before)


if __name__ == "__main__":
    unittest.main()
