"""Tests for curated OpenAI model catalog and selection."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.openai_api import OpenAIApiAdapter
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.openai_catalog import (
    MODE_RECOMMENDATIONS,
    OPENAI_MODEL_CATALOG,
    catalog_ids,
    get_spec,
    intersect_accessible,
    normalize_reasoning_effort,
    parse_allowed_models,
    recommend_model_id,
)
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_settings import OpenAISettings
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, RegistryDefaults, Repository


def _settings(**overrides) -> OpenAISettings:
    base = dict(
        enabled=True,
        api_key="sk-test-secret-key-value",
        default_model="",
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


ALL_IDS = list(catalog_ids())


class OpenAICatalogTests(unittest.TestCase):
    def test_all_supported_model_metadata(self):
        expected = {
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
        }
        self.assertEqual(set(catalog_ids()), expected)
        for spec in OPENAI_MODEL_CATALOG:
            self.assertTrue(spec.display_name)
            self.assertTrue(spec.description)
            self.assertTrue(spec.tier)
            self.assertTrue(spec.group)
            self.assertTrue(spec.recommended_uses)
            pub = spec.public_dict()
            self.assertNotIn("price", str(pub).lower())
            self.assertNotIn("usd", str(pub).lower())

    def test_api_key_availability_filtering_and_unavailable_removal(self):
        api = ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-unrelated", "whisper-1"]
        specs = intersect_accessible(api)
        ids = [s.id for s in specs]
        self.assertEqual(ids, ["gpt-5.6-terra", "gpt-5.6-luna"])
        self.assertNotIn("gpt-5.6-sol", ids)  # unavailable for key — omitted, no failure

        allowed = parse_allowed_models("gpt-5.6-luna,gpt-5.4-nano")
        specs2 = intersect_accessible(api, allowed=allowed)
        self.assertEqual([s.id for s in specs2], ["gpt-5.6-luna"])

    def test_mode_recommendations_and_fallback(self):
        self.assertEqual(MODE_RECOMMENDATIONS["find"][0], "gpt-5.6-luna")
        self.assertEqual(MODE_RECOMMENDATIONS["ask"][0], "gpt-5.6-terra")
        self.assertEqual(MODE_RECOMMENDATIONS["plan"][0], "gpt-5.6-terra")
        self.assertEqual(MODE_RECOMMENDATIONS["review"][0], "gpt-5.6-sol")

        mid, reason = recommend_model_id("ask", ["gpt-5.6-terra", "gpt-5.6-sol"])
        self.assertEqual(mid, "gpt-5.6-terra")
        self.assertEqual(reason, "recommended")

        mid2, reason2 = recommend_model_id("ask", ["gpt-5.6-sol", "gpt-5.4"])
        self.assertEqual(mid2, "gpt-5.4")
        self.assertEqual(reason2, "fallback")

        mid3, reason3 = recommend_model_id(
            "find",
            ["gpt-5.4-mini"],
            default_model="gpt-5.4-mini",
        )
        self.assertEqual(mid3, "gpt-5.4-mini")
        self.assertIn(reason3, {"fallback", "default", "recommended"})

    def test_user_model_override(self):
        mid, reason = recommend_model_id(
            "ask",
            ["gpt-5.6-terra", "gpt-5.6-sol"],
            user_override="gpt-5.6-sol",
        )
        self.assertEqual(mid, "gpt-5.6-sol")
        self.assertEqual(reason, "user_override")

        mid2, reason2 = recommend_model_id(
            "ask",
            ["gpt-5.6-terra"],
            user_override="gpt-5.6-sol",
        )
        self.assertIsNone(mid2)
        self.assertEqual(reason2, "override_unavailable")

    def test_reasoning_effort_compatibility(self):
        sol = get_spec("gpt-5.6-sol")
        luna = get_spec("gpt-5.6-luna")
        self.assertTrue(sol.supports_reasoning_effort)
        self.assertFalse(luna.supports_reasoning_effort)
        self.assertEqual(normalize_reasoning_effort("high", supported=True), "high")
        self.assertIsNone(normalize_reasoning_effort("high", supported=False))
        self.assertIsNone(normalize_reasoning_effort("extreme", supported=True))
        self.assertEqual(normalize_reasoning_effort("", supported=True), "medium")

    def test_pro_background_and_timeout(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (ALL_IDS, "discovered")
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        resolved = adapter.resolve_run_model(mode="review", requested_model="gpt-5.5-pro")
        self.assertTrue(resolved["ok"])
        self.assertTrue(resolved["is_pro"])
        self.assertTrue(resolved["background"])
        self.assertEqual(resolved["timeout_seconds"], 600.0)

        resolved2 = adapter.resolve_run_model(mode="find", requested_model="gpt-5.6-luna")
        self.assertTrue(resolved2["ok"])
        self.assertFalse(resolved2["background"])
        self.assertEqual(resolved2["timeout_seconds"], 30.0)

    def test_model_cache_refresh(self):
        settings = _settings(model_cache_ttl_seconds=60)
        session = mock.Mock()
        resp = mock.Mock()
        resp.status_code = 200
        resp.content = b'{"data":[{"id":"gpt-5.6-terra"},{"id":"gpt-5.6-luna"}]}'
        resp.json.return_value = {"data": [{"id": "gpt-5.6-terra"}, {"id": "gpt-5.6-luna"}]}
        session.get.return_value = resp
        client = OpenAIClient(settings, session=session)
        a, src_a = client.list_model_ids()
        b, src_b = client.list_model_ids()
        self.assertEqual(session.get.call_count, 1)
        self.assertTrue(src_b.startswith("cache:"))
        client.list_model_ids(force_refresh=True)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(a, b)

    def test_deprecated_and_unauthorized_errors(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.side_effect = OpenAIClientError(
            "OpenAI authorization failed", code="unauthorized", status=403
        )
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        details = adapter.list_model_details(mode="ask")
        self.assertEqual(details["models"], [])
        self.assertEqual(details["models_source"], "error")
        self.assertIn("authorization", details["error"].lower())

        # Run-time model errors mapped clearly
        err = OpenAIClientError("deprecated model", code="model_deprecated", status=400)
        self.assertEqual(err.code, "model_deprecated")
        err2 = OpenAIClientError("invalid model", code="model_invalid", status=400)
        self.assertEqual(err2.code, "model_invalid")

    def test_history_audit_model_id_and_scope_secrets_intact(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo_path = root / "repo"
        repo_path.mkdir()
        (repo_path / "AGENTS.md").write_text("# ok\n", encoding="utf-8")
        (repo_path / ".env").write_text("OPENAI_API_KEY=sk-should-not-leak\n", encoding="utf-8")
        repo = Repository(
            id="demo-repo",
            name="Demo",
            type="command",
            enabled=True,
            local_path=str(repo_path),
            working_directory=str(repo_path),
        )
        registry = Registry(repositories=[repo], defaults=RegistryDefaults())
        store = AgentCenterStore(AgentCenterDb(root / "a.db"))
        audits: list[dict] = []

        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["gpt-5.6-terra", "gpt-5.6-sol"], "discovered")

        def stream(body, timeout=None):
            self.assertEqual(body.get("model"), "gpt-5.6-sol")
            self.assertEqual((body.get("reasoning") or {}).get("effort"), "high")
            yield {"type": "response.output_text.delta", "delta": "done"}
            yield {
                "type": "response.completed",
                "response": {"id": "r1", "usage": {"total_tokens": 3}, "output": []},
            }

        client.create_response_stream.side_effect = stream
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        svc = AgentCenterService(
            registry,
            store=store,
            adapters=[adapter],
            audit=lambda **kw: audits.append(kw),
            openai_settings=_settings(),
        )
        svc.openai_runner.client = client

        # Override recommended ask model
        run = svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "openai-api",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "prompt": "hello",
            }
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            current = svc.get_run(run["id"])
            if current["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        current = svc.get_run(run["id"])
        self.assertEqual(current["status"], "completed", current.get("error"))
        self.assertEqual(current["model"], "gpt-5.6-sol")
        submit = next(a for a in audits if a.get("action") == "AGENT_RUN_SUBMIT")
        self.assertEqual(submit["detail"]["model"], "gpt-5.6-sol")
        blob = str(audits)
        self.assertNotIn("sk-test-secret-key-value", blob)
        self.assertNotIn("sk-should-not-leak", blob)

        # Secret still excluded from tools / preview
        preview = svc.preview_context(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "prompt": "x",
                "files": {"demo-repo": [".env"]},
            }
        )
        self.assertTrue(any(".env" in x for x in preview.get("excluded_secrets") or []))

        with self.assertRaises(AgentCenterError) as ctx:
            svc.start_run(
                {
                    "repository_ids": ["demo-repo"],
                    "mode": "ask",
                    "agent_id": "openai-api",
                    "model": "gpt-5.5-pro",
                    "prompt": "nope",
                }
            )
        self.assertEqual(ctx.exception.code, "model_unavailable")


if __name__ == "__main__":
    unittest.main()
