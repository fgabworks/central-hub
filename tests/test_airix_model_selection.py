"""AiriX / Agent Center model selection end-to-end tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.adapters.openai_api import OpenAIApiAdapter
from hub.agent_center.adapters.xai_api import XaiApiAdapter
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.model_selection import resolve_model_for_run
from hub.agent_center.openai_client import OpenAIClient, _is_text_model_candidate
from hub.agent_center.openai_settings import OpenAISettings
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.store import AgentCenterStore


def _openai_settings(**overrides: Any) -> OpenAISettings:
    base = dict(
        enabled=True,
        api_key="sk-test",
        default_model="gpt-4.1-mini",
        allowed_models=None,
        model_cache_ttl_seconds=60.0,
        pro_model_timeout_seconds=600.0,
        base_url="https://api.openai.com/v1",
        timeout_seconds=30.0,
        max_output_tokens=1024,
        max_tool_rounds=3,
        max_tool_result_chars=4000,
    )
    base.update(overrides)
    return OpenAISettings(**base)


class _FakeOpenAIClient:
    def __init__(self, ids: list[str]) -> None:
        self._ids = list(ids)

    def list_model_ids(self, *, force_refresh: bool = False) -> tuple[list[str], str]:
        return list(self._ids), "discovered"

    def clear_model_cache(self) -> None:
        return None


class _CapturingAgentCenter:
    """Records start_run payloads for routing model-pass-through tests."""

    def __init__(self, *, models: dict[str, list[str]] | None = None) -> None:
        self.started: list[dict[str, Any]] = []
        self.models = models or {
            "openai-api": ["gpt-4.1-mini", "gpt-4o", "gpt-5.4-mini"],
            "grok": ["grok-3", "grok-3-mini"],
        }
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
        agent = str(payload.get("agent_id") or "")
        selected = str(payload.get("model") or "").strip()
        available = self.models.get(agent, [])
        if selected and available and selected not in available:
            raise AgentCenterError(
                f"Model {selected!r} is not offered by provider {agent}",
                code="model_invalid",
            )
        model = selected or (available[0] if available else "")
        row = {
            "id": f"run-{self._n}",
            "status": "completed",
            "answer": f"ok:{agent}:{model}",
            "agent_id": agent,
            "model": model,
            "prompt": payload.get("prompt"),
            "error": "",
            "context": {
                "selected_model": selected,
                "resolved_model": model,
            },
        }
        self.started.append({"payload": dict(payload), "run": row})
        return dict(row)

    def get_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        for item in self.started:
            if item["run"]["id"] == run_id:
                return dict(item["run"])
        raise AgentCenterError("not found", code="not_found")

    def cancel_run(self, run_id: str, *, profile_id: str = "okarun") -> dict[str, Any]:
        return {"id": run_id, "status": "cancelled"}

    def get_agent(self, agent_id: str) -> Any:
        return MagicMock() if agent_id else None

    def repositories(self, profile_id: str = "okarun") -> list[dict[str, Any]]:
        return [{"id": "sample-cli", "name": "sample-cli", "selectable": True}]


def _availability() -> dict[str, dict[str, Any]]:
    return {
        "grok": {"id": "grok", "status": "available", "runnable": True},
        "openai-api": {"id": "openai-api", "status": "available", "runnable": True},
        "hub-simulator": {"id": "hub-simulator", "status": "available", "runnable": True},
        "codex": {"id": "codex", "status": "available", "runnable": True},
    }


class LegacyModelFilterTests(unittest.TestCase):
    def test_excludes_legacy_completion_models(self) -> None:
        self.assertFalse(_is_text_model_candidate("babbage-002"))
        self.assertFalse(_is_text_model_candidate("davinci-002"))
        self.assertFalse(_is_text_model_candidate("text-davinci-003"))
        self.assertFalse(_is_text_model_candidate("ada"))
        self.assertTrue(_is_text_model_candidate("gpt-4.1-mini"))
        self.assertTrue(_is_text_model_candidate("gpt-4o"))
        self.assertTrue(_is_text_model_candidate("grok-3"))


class OpenAIModelSelectionTests(unittest.TestCase):
    def test_multiple_openai_models_user_selection_honored(self) -> None:
        ids = ["babbage-002", "gpt-4.1-mini", "gpt-4o", "gpt-5.4-mini"]
        # Simulate post-filter list (adapter uses client which filters).
        filtered = [i for i in ids if _is_text_model_candidate(i)]
        adapter = OpenAIApiAdapter(
            settings=_openai_settings(default_model="gpt-4o"),
            client=_FakeOpenAIClient(filtered),  # type: ignore[arg-type]
        )
        for model in ("gpt-4.1-mini", "gpt-4o", "gpt-5.4-mini"):
            res = resolve_model_for_run(
                adapter, agent_id="openai-api", mode="ask", selected_model=model
            )
            self.assertTrue(res.ok, res.error)
            self.assertEqual(res.selected_model, model)
            self.assertEqual(res.resolved_model, model)
            self.assertEqual(res.reason, "user_selected")

    def test_provider_default_when_no_selection(self) -> None:
        adapter = OpenAIApiAdapter(
            settings=_openai_settings(default_model="gpt-4.1-mini"),
            client=_FakeOpenAIClient(["gpt-4o", "gpt-4.1-mini"]),  # type: ignore[arg-type]
        )
        res = resolve_model_for_run(
            adapter, agent_id="openai-api", mode="ask", selected_model=""
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.resolved_model, "gpt-4.1-mini")
        self.assertIn(res.reason, {"configured_default", "provider_default", "provider_configured_default"})

    def test_unavailable_model_errors_without_silent_substitute(self) -> None:
        adapter = OpenAIApiAdapter(
            settings=_openai_settings(default_model="gpt-4.1-mini"),
            client=_FakeOpenAIClient(["gpt-4.1-mini", "gpt-4o"]),  # type: ignore[arg-type]
        )
        res = resolve_model_for_run(
            adapter,
            agent_id="openai-api",
            mode="ask",
            selected_model="gpt-does-not-exist",
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.code, "model_unavailable")
        self.assertEqual(res.resolved_model, "")
        self.assertIn("gpt-does-not-exist", res.error)

    def test_no_stale_legacy_default_from_sorted_list(self) -> None:
        raw = ["babbage-002", "ada-002", "gpt-4.1-mini", "gpt-4o"]
        client = OpenAIClient(_openai_settings(default_model=""))
        client._cached_ids = sorted(i for i in raw if _is_text_model_candidate(i))
        client._cached_at = 10**12
        client._cached_source = "discovered"
        ids, _ = client.list_model_ids()
        self.assertNotIn("babbage-002", ids)
        self.assertEqual(ids[0], "gpt-4.1-mini")


class GrokModelSelectionTests(unittest.TestCase):
    def test_grok_user_selection(self) -> None:
        adapter = XaiApiAdapter()
        adapter.settings = _openai_settings(  # reuse shape; xAI uses same OpenAISettings fields
            api_key="xai-test",
            default_model="grok-3",
            base_url="https://api.x.ai/v1",
        )
        adapter.client = _FakeOpenAIClient(["grok-3", "grok-3-mini"])  # type: ignore[assignment]
        res = resolve_model_for_run(
            adapter, agent_id="grok", mode="ask", selected_model="grok-3-mini"
        )
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.resolved_model, "grok-3-mini")
        self.assertEqual(res.reason, "user_selected")

    def test_grok_default_and_unavailable(self) -> None:
        adapter = XaiApiAdapter()
        adapter.settings = _openai_settings(
            api_key="xai-test",
            default_model="grok-3",
            base_url="https://api.x.ai/v1",
        )
        adapter.client = _FakeOpenAIClient(["grok-3", "grok-3-mini"])  # type: ignore[assignment]
        ok = resolve_model_for_run(adapter, agent_id="grok", mode="ask", selected_model="")
        self.assertTrue(ok.ok)
        self.assertEqual(ok.resolved_model, "grok-3")
        bad = resolve_model_for_run(
            adapter, agent_id="grok", mode="ask", selected_model="missing-model"
        )
        self.assertFalse(bad.ok)
        self.assertEqual(bad.code, "model_unavailable")


class ProviderFallbackModelTests(unittest.TestCase):
    def test_provider_change_drops_prior_model(self) -> None:
        adapter = OpenAIApiAdapter(
            settings=_openai_settings(default_model="gpt-4.1-mini"),
            client=_FakeOpenAIClient(["gpt-4.1-mini", "gpt-4o"]),  # type: ignore[arg-type]
        )
        res = resolve_model_for_run(
            adapter,
            agent_id="openai-api",
            mode="ask",
            selected_model="grok-3-mini",
            provider_changed=True,
            previous_provider="grok",
        )
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.selected_model, "")
        self.assertEqual(res.resolved_model, "gpt-4.1-mini")
        self.assertIn("provider_fallback", res.fallback_reason)


class RoutingAndRetryModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = RoutingHistoryStore(AgentCenterDb(Path(self.tmp.name) / "agent.db"))

    def _router(self, fake: _CapturingAgentCenter) -> AgentRouterService:
        router = AgentRouterService(
            availability_loader=_availability,
            agent_center=fake,  # type: ignore[arg-type]
            history=self.history,
        )
        assert router.executor is not None
        return router

    def test_smart_routing_passes_selected_model(self) -> None:
        fake = _CapturingAgentCenter()
        router = self._router(fake)
        result = router.execute_route(
            "Debug why the analytics SQL join returns empty rows in this module",
            orchestrate=False,
            agent_override="grok",
            model="grok-3-mini",
        )
        self.assertEqual(result["execution"]["status"], "completed")
        payload = fake.started[0]["payload"]
        self.assertEqual(payload["agent_id"], "grok")
        self.assertEqual(payload["model"], "grok-3-mini")
        self.assertEqual(fake.started[0]["run"]["model"], "grok-3-mini")

    def test_manual_override_openai_model(self) -> None:
        fake = _CapturingAgentCenter()
        router = self._router(fake)
        result = router.execute_route(
            "Write a short summary",
            orchestrate=False,
            agent_override="openai-api",
            model="gpt-4.1-mini",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(fake.started[0]["payload"]["model"], "gpt-4.1-mini")
        self.assertEqual(fake.started[0]["run"]["model"], "gpt-4.1-mini")

    def test_unavailable_model_via_routing(self) -> None:
        fake = _CapturingAgentCenter()
        router = self._router(fake)
        result = router.execute_route(
            "Write a short summary",
            orchestrate=False,
            agent_override="openai-api",
            model="babbage-002",
        )
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertIn("babbage-002", result["execution"].get("error") or "")
        self.assertFalse(fake.started)

    def test_retry_preserves_prior_model(self) -> None:
        tmp = Path(self.tmp.name)
        store = AgentCenterStore(AgentCenterDb(tmp / "runs.db"))
        prior = store.create_run(
            {
                "status": "failed",
                "mode": "ask",
                "agent_id": "openai-api",
                "agent_label": "OpenAI",
                "model": "gpt-4.1-mini",
                "repository_ids": [],
                "prompt": "hello",
                "packed_prompt": "hello",
                "context": {"tools": {"enabled": []}},
                "referenced_files": [],
                "profile_id": "okarun",
                "conversation_id": "",
            }
        )
        # Lightweight service stub: only retry_run path needed.
        svc = AgentCenterService.__new__(AgentCenterService)
        svc.store = store
        captured: dict[str, Any] = {}

        def _start(payload: dict[str, Any]) -> dict[str, Any]:
            captured.update(payload)
            return {"id": "retry-1", "status": "queued", "model": payload.get("model")}

        svc.start_run = _start  # type: ignore[method-assign]
        out = AgentCenterService.retry_run(svc, prior["id"], profile_id="okarun")
        self.assertEqual(captured.get("model"), "gpt-4.1-mini")
        self.assertEqual(captured.get("agent_id"), "openai-api")
        self.assertEqual(out["model"], "gpt-4.1-mini")


if __name__ == "__main__":
    unittest.main()
