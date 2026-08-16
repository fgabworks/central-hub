"""Gemini provider contracts for AiriX / CLIMATE Chat v1."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.adapters import build_adapters, load_agent_descriptors
from hub.agent_center.adapters.gemini_api import GeminiApiAdapter
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.gemini_client import GeminiClient, response_text, response_usage
from hub.agent_center.gemini_runner import GeminiRunner
from hub.agent_center.gemini_settings import GeminiSettings, load_gemini_settings
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError


def settings(**overrides):
    base = {
        "enabled": True,
        "api_key": "test-key",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-test-flash",
        "allowed_models": None,
        "timeout_seconds": 30.0,
        "model_cache_ttl_seconds": 300.0,
        "max_output_tokens": 1024,
    }
    base.update(overrides)
    return GeminiSettings(**base)


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


class GeminiSettingsTests(unittest.TestCase):
    def test_google_key_precedence_and_environment_only_enablement(self):
        with mock.patch.dict(
            "os.environ",
            {
                "GEMINI_API_KEY": "gemini-key",
                "GOOGLE_API_KEY": "google-key",
                "GEMINI_ENABLED": "true",
            },
            clear=True,
        ):
            loaded = load_gemini_settings()
        self.assertEqual(loaded.api_key, "google-key")
        self.assertTrue(loaded.is_configured)


class GeminiClientTests(unittest.TestCase):
    def test_model_discovery_filters_to_generate_content_models(self):
        fake = FakeSession(
            model_response=FakeResponse(
                {
                    "models": [
                        {
                            "name": "models/gemini-test-flash",
                            "displayName": "Gemini Test Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/text-embedding-test",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                }
            )
        )
        client = GeminiClient(settings(), session=fake)
        rows, source = client.list_models()
        self.assertEqual(source, "discovered")
        self.assertEqual([row["id"] for row in rows], ["gemini-test-flash"])
        self.assertEqual(fake.get_calls[0][1]["headers"]["x-goog-api-key"], "test-key")

    def test_sse_stream_parses_text_and_exact_usage(self):
        events = [
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello "}]}}
                ]
            },
            {
                "candidates": [
                    {"content": {"parts": [{"text": "from Gemini"}]}}
                ],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 16,
                },
            },
        ]
        fake = FakeSession(
            stream_response=FakeResponse(
                lines=[f"data: {json.dumps(event)}" for event in events]
            )
        )
        client = GeminiClient(settings(), session=fake)
        streamed = list(
            client.stream_generate_content(
                model="gemini-test-flash",
                contents=[{"role": "user", "parts": [{"text": "Hello"}]}],
                system_instruction="You are AiriX.",
            )
        )
        self.assertEqual("".join(response_text(row) for row in streamed), "Hello from Gemini")
        self.assertEqual(response_usage(streamed[-1])["total_tokens"], 16)
        url, kwargs = fake.post_calls[0]
        self.assertIn("gemini-test-flash:streamGenerateContent?alt=sse", url)
        self.assertEqual(kwargs["json"]["systemInstruction"]["parts"][0]["text"], "You are AiriX.")


class GeminiAdapterTests(unittest.TestCase):
    def test_read_only_capabilities_and_exact_model_selection(self):
        adapter = GeminiApiAdapter(settings=settings())
        adapter.client = mock.Mock()
        adapter.client.list_models.return_value = (
            [
                {
                    "id": "gemini-test-flash",
                    "display_name": "Gemini Test Flash",
                    "availability": "available",
                }
            ],
            "discovered",
        )
        capabilities = adapter.capabilities()
        self.assertTrue(capabilities["read_only"])
        self.assertFalse(capabilities["file_write"])
        self.assertFalse(capabilities["command_execution"])
        self.assertFalse(capabilities["native_repository_investigation"])
        selected = adapter.resolve_run_model(
            mode="ask", requested_model="gemini-test-flash"
        )
        self.assertTrue(selected["ok"])
        rejected = adapter.resolve_run_model(
            mode="ask", requested_model="gemini-other"
        )
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["code"], "model_unavailable")
        self.assertEqual(
            adapter.resolve_run_model(mode="plan", requested_model=None)["code"],
            "mode_unsupported",
        )

    def test_config_builds_gemini_adapter(self):
        adapters = build_adapters(load_agent_descriptors())
        gemini = next(row for row in adapters if row.descriptor.id == "gemini")
        self.assertIsInstance(gemini, GeminiApiAdapter)
        self.assertEqual(gemini.descriptor.modes, ["ask"])


class GeminiRunnerTests(unittest.TestCase):
    def test_streaming_completion_reuses_bounded_same_provider_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentCenterStore(
                AgentCenterDb(Path(temp_dir) / "agent-center.db")
            )
            conversation = store.create_conversation(
                profile_id="okarun", title="Gemini chat"
            )
            prior = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "gemini",
                    "agent_label": "Gemini",
                    "model": "gemini-test-flash",
                    "repository_ids": ["work-repo"],
                    "prompt": "First question",
                    "packed_prompt": "First question",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )
            store.update_run(
                prior["id"], status="completed", answer="First answer"
            )
            current = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "gemini",
                    "agent_label": "Gemini",
                    "model": "gemini-test-flash",
                    "repository_ids": ["work-repo"],
                    "prompt": "Follow-up",
                    "packed_prompt": "Bounded follow-up context",
                    "profile_id": "okarun",
                    "conversation_id": conversation["id"],
                }
            )

            fake_client = mock.Mock()
            fake_client.stream_generate_content.return_value = iter(
                [
                    {
                        "candidates": [
                            {"content": {"parts": [{"text": "Answer "}]}}
                        ]
                    },
                    {
                        "candidates": [
                            {"content": {"parts": [{"text": "continued"}]}}
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 20,
                            "candidatesTokenCount": 3,
                            "totalTokenCount": 23,
                        },
                    },
                ]
            )
            runner = GeminiRunner(
                store, settings=settings(), client=fake_client
            )
            runner._run(
                run_id=current["id"],
                model="gemini-test-flash",
                packed_prompt="Bounded follow-up context",
                timeout_seconds=30.0,
                conversation_id=conversation["id"],
                agent_id="gemini",
            )

            completed = store.get_run(current["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["answer"], "Answer continued")
            self.assertEqual(completed["usage"]["total_tokens"], 23)
            self.assertTrue(completed["usage"]["session_reused"])
            sent = fake_client.stream_generate_content.call_args.kwargs
            self.assertEqual(sent["contents"][0]["parts"][0]["text"], "First question")
            self.assertEqual(sent["contents"][1]["parts"][0]["text"], "First answer")
            self.assertEqual(sent["contents"][-1]["parts"][0]["text"], "Bounded follow-up context")
            self.assertIn("You are AiriX", sent["system_instruction"])


class ClimateGeminiSafetyTests(unittest.TestCase):
    def test_climate_keeps_display_prompt_separate_from_provider_packet(self):
        center = mock.Mock()
        center.start_run.return_value = {
            "id": "run-1",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-test-flash",
            "conversation_id": "conversation-1",
        }
        adapter = ClimateCodingAdapter(center)
        adapter.availability = mock.Mock(
            return_value={"id": "gemini", "state": "connected"}
        )
        adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="gemini",
            model="gemini-test-flash",
            prompt="Bounded CLIMATE context packet",
            display_prompt="What does this logic do?",
            task_mode="ask",
        )
        payload = center.start_run.call_args.args[0]
        self.assertEqual(payload["display_prompt"], "What does this logic do?")
        self.assertIn("Bounded CLIMATE context packet", payload["prompt"])

    def test_climate_blocks_gemini_edit_mode_before_starting_run(self):
        center = mock.Mock()
        adapter = ClimateCodingAdapter(center)
        adapter.availability = mock.Mock(
            return_value={"id": "gemini", "state": "connected"}
        )
        with self.assertRaises(ClimateCodingError) as caught:
            adapter.execute(
                workspace="work",
                repository_id="work-repo",
                provider="gemini",
                model="gemini-test-flash",
                prompt="Fix this file",
                task_mode="edit",
            )
        self.assertEqual(caught.exception.code, "mode_unsupported")
        center.start_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
