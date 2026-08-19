"""Focused tests for OpenAI API adapter in Agent Center."""

from __future__ import annotations

import json
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
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_runner import OpenAIRunner
from hub.agent_center.openai_settings import OpenAISettings
from hub.agent_center.openai_tools import (
    ALLOWED_TOOLS,
    AgentToolsContext,
    execute_tool,
    tool_definitions,
)
from hub.agent_center.redact import redact_text
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.profiles import get_profile
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, RegistryDefaults, Repository


def _settings(**overrides) -> OpenAISettings:
    base = dict(
        enabled=True,
        api_key="sk-test-secret-key-value",
        default_model="gpt-5.6-terra",
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


class OpenAIAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.repo_path = self.tmp / "repo"
        self.repo_path.mkdir()
        (self.repo_path / "AGENTS.md").write_text("# Rules\nSafe only.\n", encoding="utf-8")
        (self.repo_path / "src").mkdir()
        (self.repo_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        (self.repo_path / ".env").write_text("OPENAI_API_KEY=sk-should-not-leak\n", encoding="utf-8")
        (self.repo_path / "secret.bin").write_bytes(b"\x00\x01\x02binary")
        self.repo = Repository(
            id="demo-repo",
            name="Demo",
            type="command",
            enabled=True,
            local_path=str(self.repo_path),
            working_directory=str(self.repo_path),
        )
        self.registry = Registry(repositories=[self.repo], defaults=RegistryDefaults())
        self.db = AgentCenterDb(self.tmp / "agent.db")
        self.store = AgentCenterStore(self.db)
        self.audits: list[dict] = []

        def audit(**kwargs):
            self.audits.append(kwargs)

        self.audit = audit

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_configuration_and_key_redaction(self):
        disabled = OpenAIApiAdapter(settings=_settings(enabled=False, api_key=None))
        av = disabled.availability()
        self.assertEqual(av.status, "disabled")

        missing = OpenAIApiAdapter(settings=_settings(api_key=None, default_model=""))
        self.assertEqual(missing.availability().status, "unavailable")
        self.assertIn("OPENAI_API_KEY", missing.availability().detail)

        pub = _settings().public_status()
        self.assertEqual(pub["api_key"], "set")
        self.assertNotIn("sk-", str(pub))

        redacted = redact_text("Authorization: Bearer sk-test-secret-key-value\nOPENAI_API_KEY=sk-abc")
        self.assertNotIn("sk-test-secret-key-value", redacted)
        self.assertIn("[redacted]", redacted)

    def test_model_loading_and_fallback(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (
            ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-unrelated"],
            "discovered",
        )
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        models, source = adapter.list_models()
        self.assertTrue(source.startswith("discovered") or source == "discovered")
        # The provider response is authoritative; the Hub no longer hardcodes a catalog.
        self.assertEqual(models, ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-unrelated"])

        client.list_model_ids.side_effect = OpenAIClientError("rate", code="rate_limit", status=429)
        adapter2 = OpenAIApiAdapter(
            settings=_settings(default_model="gpt-5.6-terra"),
            client=client,
        )
        models2, source2 = adapter2.list_models()
        # On list failure we must not advertise unverified catalog models.
        self.assertEqual(models2, [])
        self.assertEqual(source2, "error")

    def test_streaming_and_cancellation(self):
        settings = _settings()
        client = mock.Mock(spec=OpenAIClient)

        def stream_events(body, timeout=None, **_kwargs):
            yield {
                "type": "response.created",
                "response": {"id": "resp_1"},
            }
            yield {"type": "response.output_text.delta", "delta": "Hello "}
            # Simulate slow stream so cancel can land
            time.sleep(0.3)
            yield {"type": "response.output_text.delta", "delta": "world"}
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                    "output": [],
                },
            }

        client.create_response_stream.side_effect = stream_events
        runner = OpenAIRunner(self.store, settings=settings, client=client, audit=self.audit)
        run = self.store.create_run(
            {
                "status": "queued",
                "mode": "ask",
                "agent_id": "openai-api",
                "agent_label": "OpenAI API",
                "model": "gpt-5.6-terra",
                "repository_ids": ["demo-repo"],
                "prompt": "hi",
                "packed_prompt": "packed",
            }
        )
        ctx = AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"])
        runner.start(
            run_id=run["id"],
            model="gpt-5.6-terra",
            mode="ask",
            user_prompt="hi",
            packed_prompt="packed",
            tools_ctx=ctx,
            timeout_seconds=10,
        )
        time.sleep(0.05)
        runner.cancel(run["id"])
        deadline = time.time() + 5
        while time.time() < deadline:
            current = self.store.get_run(run["id"])
            if current["status"] in {"cancelled", "completed", "failed"}:
                break
            time.sleep(0.05)
        current = self.store.get_run(run["id"])
        self.assertIn(current["status"], {"cancelled", "completed"})
        # Cancel path preferred; if stream finished first, completed is acceptable.
        if current["status"] == "cancelled":
            self.assertTrue(current["cancel_requested"])

        # Completed streaming path
        client.create_response_stream.side_effect = stream_events
        run2 = self.store.create_run(
            {
                "status": "queued",
                "mode": "ask",
                "agent_id": "openai-api",
                "agent_label": "OpenAI API",
                "model": "gpt-5.6-terra",
                "repository_ids": ["demo-repo"],
                "prompt": "hi2",
                "packed_prompt": "packed2",
            }
        )
        runner.start(
            run_id=run2["id"],
            model="gpt-5.6-terra",
            mode="ask",
            user_prompt="hi2",
            packed_prompt="packed2",
            tools_ctx=AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"]),
            timeout_seconds=10,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            current = self.store.get_run(run2["id"])
            if current["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        current = self.store.get_run(run2["id"])
        self.assertEqual(current["status"], "completed", current.get("error"))
        self.assertIn("Hello", current["answer"])
        self.assertEqual(current["usage"].get("total_tokens"), 12)

    def test_repository_scope_and_secret_exclusion(self):
        ctx = AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"])
        # Outside scope
        other = execute_tool(
            "read_file",
            {"repo_id": "nope", "path": "AGENTS.md"},
            AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"]),
        )
        self.assertIn("not in run scope", other)

        denied = execute_tool(
            "read_file",
            {"repo_id": "demo-repo", "path": ".env"},
            ctx,
        )
        self.assertIn("excluded", denied.lower())
        self.assertNotIn("sk-should-not-leak", denied)

        binary = execute_tool(
            "read_file",
            {"repo_id": "demo-repo", "path": "secret.bin"},
            ctx,
        )
        self.assertIn("excluded", binary.lower())

        ok = json.loads(
            execute_tool(
                "read_file",
                {"repo_id": "demo-repo", "path": "src/app.py"},
                ctx,
            )
        )
        self.assertIn("print", ok["content"])
        self.assertTrue(any(a.name == "read_file" and a.ok for a in ctx.activity))

        # Path escape
        escape = execute_tool(
            "read_file",
            {"repo_id": "demo-repo", "path": "../outside.txt"},
            ctx,
        )
        self.assertIn("error", escape.lower())

    def test_function_tool_allowlist_and_no_writes(self):
        names = {t["name"] for t in tool_definitions()}
        self.assertEqual(names, set(ALLOWED_TOOLS))
        ctx = AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"])
        blocked = json.loads(execute_tool("run_terminal", {"cmd": "rm -rf /"}, ctx))
        self.assertIn("not allowlisted", blocked["error"].lower())
        self.assertNotIn("shell", ALLOWED_TOOLS)
        self.assertNotIn("sql_execute", ALLOWED_TOOLS)
        self.assertNotIn("edit_file", ALLOWED_TOOLS)

        # sql_lookup must not execute — only lookup message when store missing
        sql = json.loads(execute_tool("sql_lookup", {"search": "x"}, ctx))
        self.assertIn("not available", sql["error"].lower())

    def test_context_preview_and_instructions(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["gpt-5.6-terra"], "discovered")
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        svc = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=[adapter],
            audit=self.audit,
            openai_settings=_settings(),
        )
        preview = svc.preview_context(
            {"repository_ids": ["demo-repo"], "mode": "ask", "prompt": "app.py rules"}
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(any(i["path"] == "AGENTS.md" for i in preview["instructions"]))
        self.assertIn("repo_search", preview["tools"]["enabled"])
        self.assertIn("sql_execute", preview["tools"]["disabled"])

    def test_api_errors_timeouts_rate_limits(self):
        settings = _settings()
        client = mock.Mock(spec=OpenAIClient)
        client.create_response_stream.side_effect = OpenAIClientError(
            "OpenAI rate limit", code="rate_limit", status=429
        )
        runner = OpenAIRunner(self.store, settings=settings, client=client, audit=self.audit)
        run = self.store.create_run(
            {
                "status": "queued",
                "mode": "ask",
                "agent_id": "openai-api",
                "agent_label": "OpenAI API",
                "model": "gpt-5.6-terra",
                "repository_ids": ["demo-repo"],
                "prompt": "x",
                "packed_prompt": "x",
            }
        )
        runner.start(
            run_id=run["id"],
            model="gpt-5.6-terra",
            mode="ask",
            user_prompt="x",
            packed_prompt="x",
            tools_ctx=AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"]),
            timeout_seconds=5,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            current = self.store.get_run(run["id"])
            if current["status"] == "failed":
                break
            time.sleep(0.05)
        current = self.store.get_run(run["id"])
        self.assertEqual(current["status"], "failed")
        self.assertIn("rate limit", current["error"].lower())
        self.assertTrue(any(a.get("action") == "AGENT_RUN_FAILED" for a in self.audits))

        # Unavailable model rejected by service when discovered list present
        client.list_model_ids.return_value = (["gpt-5.6-terra"], "discovered")
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        svc = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=[adapter],
            openai_settings=_settings(),
        )
        with self.assertRaises(AgentCenterError):
            svc.start_run(
                {
                    "repository_ids": ["demo-repo"],
                    "mode": "ask",
                    "agent_id": "openai-api",
                    "model": "not-in-list",
                    "prompt": "hello",
                }
            )

    def test_usage_history_audit_and_no_cli_argv(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["gpt-5.6-terra"], "discovered")

        def stream_events(body, timeout=None, **_kwargs):
            tool_names = {t.get("name") for t in (body.get("tools") or [])}
            allowed = set(get_profile("okarun").allowed_tools)
            self.assertTrue(tool_names)
            self.assertTrue(tool_names.issubset(allowed))
            self.assertEqual(body.get("model"), "gpt-5.6-terra")
            yield {"type": "response.output_text.delta", "delta": "ok"}
            yield {
                "type": "response.completed",
                "response": {
                    "id": "r1",
                    "usage": {"total_tokens": 7},
                    "output": [],
                },
            }

        client.create_response_stream.side_effect = stream_events
        adapter = OpenAIApiAdapter(settings=_settings(), client=client)
        with self.assertRaises(ValueError):
            adapter.build_argv(mode="ask", prompt="x", model="gpt-5.6-terra", cwd=str(self.repo_path))

        svc = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=[adapter],
            audit=self.audit,
            openai_settings=_settings(),
        )
        svc.openai_runner.client = client
        run = svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "review",
                "agent_id": "openai-api",
                "model": "gpt-5.6-terra",
                "prompt": "summarize",
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
        self.assertEqual(current["usage"].get("total_tokens"), 7)
        hist = svc.history()
        self.assertTrue(any(h["id"] == run["id"] for h in hist))
        blob = json.dumps(self.audits)
        self.assertNotIn("sk-test-secret-key-value", blob)
        self.assertIn("AGENT_RUN_SUBMIT", {a.get("action") for a in self.audits})

    def test_repo_search_tool(self):
        ctx = AgentToolsContext(registry=self.registry, repository_ids=["demo-repo"])
        result = json.loads(execute_tool("repo_search", {"query": "app.py", "limit": 10}, ctx))
        self.assertTrue(result["matches"])
        self.assertTrue(all(m["repo_id"] == "demo-repo" for m in result["matches"]))


if __name__ == "__main__":
    unittest.main()
