"""CLIMATE API provider expansion: OpenAI, Anthropic, xAI, plus Gemini regression."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.adapters import build_adapters, load_agent_descriptors
from hub.agent_center.adapters.anthropic_api import AnthropicApiAdapter
from hub.agent_center.adapters.gemini_api import GeminiApiAdapter
from hub.agent_center.adapters.openai_api import OpenAIApiAdapter
from hub.agent_center.adapters.xai_api import XaiApiAdapter
from hub.agent_center.anthropic_client import AnthropicClient
from hub.agent_center.anthropic_runner import AnthropicRunner
from hub.agent_center.anthropic_settings import AnthropicSettings
from hub.agent_center.api_chat import AIRIX_SYSTEM_INSTRUCTION, DIRECT_SYSTEM_INSTRUCTION
from hub.agent_center.connections import API_CHAT_PROVIDER_IDS, CODING_CLI_PROVIDER_IDS
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_runner import OpenAIRunner
from hub.agent_center.openai_settings import OpenAISettings
from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.provider_secrets import set_secret
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import CODING_PROVIDERS, ClimateCodingAdapter, ClimateCodingError
from hub.registry.models import Registry, RegistryDefaults, Repository

from tests.test_openai_agent import _settings as openai_settings


SECRET = "hub-test-secret-KEY-9f3a2c1b"


class FakeResponse:
    def __init__(self, payload=None, *, status=200, lines=None, text=""):
        self.payload = payload or {}
        self.status_code = status
        self.content = json.dumps(self.payload).encode("utf-8")
        self.text = text or json.dumps(self.payload)
        self.lines = list(lines or [])
        self.closed = False

    def json(self):
        return self.payload

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        yield from self.lines

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *, model_response=None, stream_response=None):
        self.model_response = model_response or FakeResponse()
        self.stream_response = stream_response or FakeResponse()
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.model_response

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.stream_response


def _anthropic_settings(**overrides) -> AnthropicSettings:
    base = {
        "enabled": True,
        "api_key": "sk-ant-test-secret-key",
        "base_url": "https://api.anthropic.com",
        "api_version": "2023-06-01",
        "default_model": "claude-sonnet-test",
        "allowed_models": None,
        "timeout_seconds": 30.0,
        "model_cache_ttl_seconds": 300.0,
        "max_output_tokens": 1024,
    }
    base.update(overrides)
    return AnthropicSettings(**base)


def _registry() -> Registry:
    repo = Repository(
        id="demo-repo",
        name="Demo",
        type="command",
        enabled=True,
        local_path=".",
    )
    return Registry(repositories=[repo], defaults=RegistryDefaults())


def _tools_ctx(registry: Registry) -> AgentToolsContext:
    return AgentToolsContext(registry=registry, repository_ids=["demo-repo"])


class ProviderRegistryTests(unittest.TestCase):
    def test_config_builds_api_adapters(self):
        adapters = build_adapters(load_agent_descriptors())
        by_id = {row.descriptor.id: row for row in adapters}
        self.assertIsInstance(by_id["openai-api"], OpenAIApiAdapter)
        self.assertIsInstance(by_id["anthropic-api"], AnthropicApiAdapter)
        self.assertIsInstance(by_id["grok"], XaiApiAdapter)
        self.assertIsInstance(by_id["gemini"], GeminiApiAdapter)
        self.assertEqual(by_id["anthropic-api"].descriptor.modes, ["ask"])
        self.assertEqual(CODING_PROVIDERS, CODING_CLI_PROVIDER_IDS)
        for provider_id in API_CHAT_PROVIDER_IDS:
            self.assertIn(provider_id, CODING_CLI_PROVIDER_IDS)


class OpenAIClimateTests(unittest.TestCase):
    def test_secret_store_and_redaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            secrets = Path(temp_dir) / "ai_provider_secrets.env"
            with mock.patch.dict(
                os.environ,
                {"CENTRAL_HUB_AI_PROVIDER_SECRETS": str(secrets)},
                clear=False,
            ):
                set_secret("OPENAI_API_KEY", SECRET, allowlist={"OPENAI_API_KEY"})
                try:
                    stored = secrets.read_text(encoding="utf-8")
                    self.assertIn("OPENAI_API_KEY=", stored)
                    blob = redact_text(f"OPENAI_API_KEY={SECRET} Authorization: Bearer {SECRET}")
                    self.assertNotIn(SECRET, blob)
                    self.assertIn("[redacted]", blob)
                    public = openai_settings(api_key=SECRET).public_status()
                    self.assertEqual(public["api_key"], "set")
                    self.assertNotIn(SECRET, json.dumps(public))
                finally:
                    os.environ.pop("OPENAI_API_KEY", None)

    def test_connection_test_requires_successful_discovery(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["gpt-test"], "discovered")
        adapter = OpenAIApiAdapter(settings=openai_settings(), client=client)
        status = adapter.test_connection()
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "connected")
        self.assertIn("1 text models", status["detail"])

        missing = OpenAIApiAdapter(settings=openai_settings(api_key=None), client=client)
        failed = missing.test_connection()
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["state"], "authentication_required")
        self.assertNotEqual(failed["state"], "connected")

    def test_exact_model_no_silent_fallback(self):
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["gpt-test-a", "gpt-test-b"], "discovered")
        adapter = OpenAIApiAdapter(settings=openai_settings(), client=client)
        selected = adapter.resolve_run_model(mode="ask", requested_model="gpt-test-a")
        self.assertTrue(selected["ok"])
        self.assertEqual(selected["model"], "gpt-test-a")
        rejected = adapter.resolve_run_model(mode="ask", requested_model="gpt-missing")
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "model_unavailable")
        self.assertNotEqual(rejected.get("resolved_model"), "gpt-test-a")

    def test_streaming_cancel_airix_direct_and_restored_conversation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentCenterStore(AgentCenterDb(Path(temp_dir) / "agent.db"))
            conversation = store.create_conversation(profile_id="okarun", title="OpenAI chat")
            prior = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "openai-api",
                    "agent_label": "OpenAI API",
                    "model": "gpt-test",
                    "repository_ids": [],
                    "prompt": "First question",
                    "packed_prompt": "First question",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )
            store.update_run(prior["id"], status="completed", answer="First answer")
            current = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "openai-api",
                    "agent_label": "OpenAI API",
                    "model": "gpt-test",
                    "repository_ids": [],
                    "prompt": "Follow-up",
                    "packed_prompt": "Bounded follow-up",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )
            client = mock.Mock(spec=OpenAIClient)
            captured = {}

            def stream(body, timeout=None, **kwargs):
                captured["body"] = body
                captured["on_response"] = kwargs.get("on_response")
                yield {"type": "response.output_text.delta", "delta": "Hello "}
                yield {"type": "response.output_text.delta", "delta": "world"}
                yield {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                    },
                }

            client.create_response_stream.side_effect = stream
            runner = OpenAIRunner(store, settings=openai_settings(), client=client)
            ctx = _tools_ctx(_registry())
            runner._run_chat(
                run_id=current["id"],
                model="gpt-test",
                packed_prompt="Bounded follow-up",
                timeout_seconds=30.0,
                conversation_id=conversation["id"],
                agent_id="openai-api",
                tools_ctx=ctx,
            )
            completed = store.get_run(current["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["answer"], "Hello world")
            self.assertTrue(completed["usage"]["session_reused"])
            self.assertNotIn(SECRET, json.dumps(completed))
            messages = captured["body"]["input"]
            self.assertEqual(messages[0]["content"], "First question")
            self.assertEqual(messages[1]["content"], "First answer")
            self.assertEqual(messages[-1]["content"], "Bounded follow-up")
            self.assertIn("AiriX", captured["body"]["instructions"])
            self.assertNotIn("tools", captured["body"])

            runner._run_chat(
                run_id=current["id"],
                model="gpt-test",
                packed_prompt="Direct question",
                timeout_seconds=30.0,
                conversation_id=conversation["id"],
                agent_id="openai-api",
                direct_provider_chat=True,
                tools_ctx=ctx,
            )
            self.assertEqual(captured["body"]["instructions"], DIRECT_SYSTEM_INSTRUCTION)

            response = FakeResponse(lines=['data: {"type":"response.output_text.delta","delta":"Partial"}'])
            runner._set_stream(current["id"], response)
            runner.cancel(current["id"])
            self.assertTrue(response.closed)

    def test_provider_errors_are_redacted(self):
        leaked = "sk-leak-secret-key-value99"
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.side_effect = OpenAIClientError(
            f"OpenAI authentication failed: {leaked}", code="auth"
        )
        adapter = OpenAIApiAdapter(settings=openai_settings(api_key=leaked), client=client)
        details = adapter.list_model_details()
        self.assertEqual(details["models"], [])
        self.assertNotIn(leaked, json.dumps(details))
        status = adapter.test_connection()
        self.assertNotEqual(status["state"], "connected")
        self.assertNotIn(leaked, json.dumps(status))


class AnthropicClimateTests(unittest.TestCase):
    def test_secret_store_redaction_and_headers(self):
        blob = redact_text("ANTHROPIC_API_KEY=sk-ant-secretvalue Authorization: sk-ant-secretvalue")
        self.assertNotIn("sk-ant-secretvalue", blob)
        adapter = AnthropicApiAdapter(settings=_anthropic_settings())
        self.assertEqual(adapter.credential_type, "api_key")
        self.assertEqual(adapter.credential_storage, "Gitignored local server file or server environment")
        headers = AnthropicClient(_anthropic_settings())._headers()
        self.assertEqual(headers["x-api-key"], "sk-ant-test-secret-key")
        self.assertNotIn("sk-ant-test-secret-key", adapter.settings_help)

    def test_connection_and_model_discovery(self):
        fake = FakeSession(
            model_response=FakeResponse(
                {
                    "data": [
                        {"id": "claude-sonnet-test", "display_name": "Sonnet Test", "type": "model"},
                        {"id": "claude-embedding", "display_name": "Embed", "type": "model"},
                    ],
                    "has_more": False,
                }
            )
        )
        settings = _anthropic_settings()
        client = AnthropicClient(settings, session=fake)
        adapter = AnthropicApiAdapter(settings=settings, client=client)
        status = adapter.test_connection()
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "connected")
        details = adapter.list_model_details()
        self.assertIn("claude-sonnet-test", details["models"])
        self.assertNotIn("claude-embedding", details["models"])
        missing = adapter.resolve_run_model(mode="ask", requested_model="")
        self.assertEqual(missing["code"], "model_required")
        exact = adapter.resolve_run_model(mode="ask", requested_model="claude-sonnet-test")
        self.assertTrue(exact["ok"])
        self.assertEqual(exact["resolved_model"], "claude-sonnet-test")
        plan = adapter.resolve_run_model(mode="plan", requested_model="claude-sonnet-test")
        self.assertEqual(plan["code"], "mode_unsupported")
        offline = AnthropicApiAdapter(settings=_anthropic_settings(api_key=None))
        self.assertNotEqual(offline.test_connection()["state"], "connected")

    def test_streaming_cancel_modes_and_restored_conversation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentCenterStore(AgentCenterDb(Path(temp_dir) / "agent.db"))
            conversation = store.create_conversation(profile_id="okarun", title="Claude chat")
            prior = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "anthropic-api",
                    "agent_label": "Anthropic",
                    "model": "claude-sonnet-test",
                    "repository_ids": [],
                    "prompt": "First question",
                    "packed_prompt": "First question",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )
            store.update_run(prior["id"], status="completed", answer="First answer")
            current = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "anthropic-api",
                    "agent_label": "Anthropic",
                    "model": "claude-sonnet-test",
                    "repository_ids": [],
                    "prompt": "Follow-up",
                    "packed_prompt": "Bounded follow-up",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )
            fake_client = mock.Mock()
            fake_client.stream_messages.return_value = iter(
                [
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Answer "},
                    },
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "continued"},
                    },
                    {
                        "type": "message_delta",
                        "usage": {"output_tokens": 2},
                    },
                ]
            )
            runner = AnthropicRunner(
                store, settings=_anthropic_settings(), client=fake_client
            )
            runner._run(
                run_id=current["id"],
                model="claude-sonnet-test",
                packed_prompt="Bounded follow-up",
                timeout_seconds=30.0,
                conversation_id=conversation["id"],
                agent_id="anthropic-api",
            )
            completed = store.get_run(current["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["answer"], "Answer continued")
            sent = fake_client.stream_messages.call_args.kwargs
            self.assertEqual(sent["messages"][0]["content"], "First question")
            self.assertEqual(sent["messages"][1]["content"], "First answer")
            self.assertEqual(sent["messages"][-1]["content"], "Bounded follow-up")
            self.assertEqual(sent["system"], AIRIX_SYSTEM_INSTRUCTION)

            response = FakeResponse(lines=["data: {}"])
            runner._set_stream(current["id"], response)
            runner.cancel(current["id"])
            self.assertTrue(response.closed)

            runner._run(
                run_id=current["id"],
                model="claude-sonnet-test",
                packed_prompt="Direct question",
                timeout_seconds=30.0,
                conversation_id=conversation["id"],
                agent_id="anthropic-api",
                direct_provider_chat=True,
            )
            sent_direct = fake_client.stream_messages.call_args.kwargs
            self.assertEqual(sent_direct["system"], DIRECT_SYSTEM_INSTRUCTION)

    def test_http_errors_normalized(self):
        fake = FakeSession(model_response=FakeResponse(status=401, text="invalid x-api-key sk-ant-secretvalue"))
        client = AnthropicClient(_anthropic_settings(), session=fake)
        with self.assertRaises(Exception) as ctx:
            client.list_models(force_refresh=True)
        self.assertNotIn("sk-ant-secretvalue", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "auth")


class XaiClimateTests(unittest.TestCase):
    def test_secret_store_pattern_and_discovery(self):
        blob = redact_text("XAI_API_KEY=xai-secretvaluehere")
        self.assertNotIn("xai-secretvaluehere", blob)
        client = mock.Mock(spec=OpenAIClient)
        client.list_model_ids.return_value = (["grok-test"], "discovered")
        adapter = XaiApiAdapter()
        adapter.client = client
        adapter.settings = openai_settings(api_key="xai-test-key-value", base_url="https://api.x.ai/v1")
        status = adapter.test_connection()
        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "connected")
        exact = adapter.resolve_run_model(mode="ask", requested_model="grok-test")
        self.assertEqual(exact["model"], "grok-test")
        rejected = adapter.resolve_run_model(mode="ask", requested_model="grok-other")
        self.assertEqual(rejected["code"], "model_unavailable")
        self.assertEqual(adapter.credential_storage, "Gitignored local server file or server environment")

    def test_not_connected_without_key(self):
        adapter = XaiApiAdapter()
        adapter.settings = openai_settings(enabled=False, api_key=None)
        adapter.client = mock.Mock(spec=OpenAIClient)
        status = adapter.connection_status()
        self.assertEqual(status["state"], "authentication_required")
        self.assertFalse(status["available"])


class ClimateApiSurfaceTests(unittest.TestCase):
    def _execute(self, provider: str, **kwargs):
        center = mock.Mock()
        center.start_run.return_value = {
            "id": "run-1",
            "status": "queued",
            "agent_id": provider,
            "model": kwargs.get("model", "test-model"),
            "conversation_id": "conversation-1",
        }
        adapter = ClimateCodingAdapter(center)
        adapter.availability = mock.Mock(return_value={"id": provider, "state": "connected"})
        result = adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider=provider,
            model=kwargs.get("model", "test-model"),
            prompt=kwargs.get("prompt", "Explain this."),
            display_prompt=kwargs.get("display_prompt", "Explain this."),
            task_mode=kwargs.get("task_mode", "ask"),
            execution_mode=kwargs.get("execution_mode", "climate_assisted"),
            surface=kwargs.get("surface", "chat"),
            context_scope=kwargs.get("context_scope", "general"),
            conversation_id=kwargs.get("conversation_id", ""),
        )
        return result, center.start_run.call_args.args[0]

    def test_api_providers_are_ask_only_with_tools_disabled(self):
        for provider, model in (
            ("openai-api", "gpt-test"),
            ("anthropic-api", "claude-sonnet-test"),
            ("grok", "grok-test"),
            ("gemini", "gemini-test-flash"),
        ):
            result, payload = self._execute(provider, model=model)
            self.assertEqual(payload["agent_id"], provider)
            self.assertEqual(payload["model"], model)
            self.assertFalse(payload.get("tool_runtime"))
            self.assertTrue(payload.get("api_chat"))
            self.assertFalse(payload.get("repository_investigation"))
            self.assertEqual(payload["files"], {})
            self.assertEqual(result["model"], model)
            with self.assertRaises(ClimateCodingError) as ctx:
                self._execute(provider, model=model, task_mode="edit", prompt="edit app.py")
            self.assertEqual(ctx.exception.code, "mode_unsupported")

    def test_airix_direct_and_scopes_preserved(self):
        _, airix = self._execute("openai-api", model="gpt-test", execution_mode="climate_assisted")
        self.assertIn("AiriX · CLIMATE Chat", airix["prompt"])
        self.assertFalse(airix.get("direct_provider_chat"))
        _, direct = self._execute(
            "anthropic-api",
            model="claude-sonnet-test",
            execution_mode="direct",
            prompt="what is PMNP?",
            display_prompt="what is PMNP?",
        )
        self.assertTrue(direct.get("direct_provider_chat"))
        self.assertEqual(direct["prompt"], "what is PMNP?")
        _, scoped = self._execute(
            "grok",
            model="grok-test",
            context_scope="repository",
            surface="chat",
        )
        self.assertEqual(scoped["climate_execution"]["context_scope"], "repository")

    def test_missing_model_is_rejected(self):
        center = mock.Mock()
        adapter = ClimateCodingAdapter(center)
        adapter.availability = mock.Mock(return_value={"id": "openai-api", "state": "connected"})
        with self.assertRaises(ClimateCodingError) as ctx:
            adapter.execute(
                workspace="work",
                repository_id="",
                provider="openai-api",
                model="  ",
                prompt="Hello",
                surface="chat",
            )
        self.assertEqual(ctx.exception.code, "model_required")
        center.start_run.assert_not_called()

    def test_gemini_regression_identity(self):
        _, payload = self._execute("gemini", model="gemini-test-flash")
        self.assertEqual(payload["agent_id"], "gemini")
        self.assertFalse(payload.get("tool_runtime"))
        self.assertTrue(payload.get("api_chat"))
        self.assertIn("AiriX · CLIMATE Chat", payload["prompt"])


if __name__ == "__main__":
    unittest.main()
