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
from hub.agent_center.gemini_runner import (
    AIRIX_SYSTEM_INSTRUCTION,
    DIRECT_SYSTEM_INSTRUCTION,
    GeminiRunner,
    gemini_system_instruction,
)
from hub.agent_center.gemini_settings import GeminiSettings, load_gemini_settings
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


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


class GeminiSystemInstructionTests(unittest.TestCase):
    def test_direct_skips_evidence_bound_airix_instruction(self):
        direct = gemini_system_instruction(direct_provider_chat=True)
        airix = gemini_system_instruction(direct_provider_chat=False)
        self.assertEqual(direct, DIRECT_SYSTEM_INSTRUCTION)
        self.assertEqual(airix, AIRIX_SYSTEM_INSTRUCTION)
        self.assertNotIn("bounded repository context", direct.lower())
        self.assertNotIn("evidence packet", direct.lower())
        self.assertNotIn("cannot verify", direct.lower())
        self.assertIn("bounded repository context", airix.lower())
        self.assertIn("You are AiriX", airix)


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
        missing = adapter.resolve_run_model(mode="ask", requested_model="")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["code"], "model_required")
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

    def test_cancel_closes_active_stream_and_keeps_partial_answer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AgentCenterStore(
                AgentCenterDb(Path(temp_dir) / "agent-center.db")
            )
            current = store.create_run(
                {
                    "mode": "ask",
                    "agent_id": "gemini",
                    "agent_label": "Gemini",
                    "model": "gemini-test-flash",
                    "repository_ids": ["work-repo"],
                    "prompt": "Summarize",
                    "packed_prompt": "Summarize",
                    "profile_id": "okarun",
                    "conversation_id": "",
                }
            )
            response = FakeResponse(
                lines=[
                    'data: {"candidates":[{"content":{"parts":[{"text":"Partial"}]}}]}'
                ]
            )

            class StreamClient:
                def stream_generate_content(self, **kwargs):
                    kwargs["on_response"](response)
                    store.request_cancel(current["id"])
                    if kwargs["should_cancel"]():
                        return iter(())
                    yield from ()

            runner = GeminiRunner(
                store, settings=settings(), client=StreamClient()
            )
            runner._set_stream(current["id"], response)
            cancelled = runner.cancel(current["id"])
            self.assertTrue(response.closed)
            self.assertTrue((cancelled or store.get_run(current["id"])).get("cancel_requested"))


class GeminiRedactionTests(unittest.TestCase):
    def test_google_and_gemini_keys_are_redacted(self):
        blob = redact_text(
            "header AIzaSyDummyGoogleApiKeyValue0000001 and GEMINI_API_KEY=secret-value"
        )
        self.assertNotIn("AIzaSyDummyGoogleApiKeyValue0000001", blob)
        self.assertNotIn("secret-value", blob)
        self.assertIn("[redacted]", blob)


class ClimateGeminiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name) / "work"
        self.work.mkdir()
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.work / "AGENTS.md").write_text("# Agents\nUse files.\n", encoding="utf-8")
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work)),
        ])
        self.coding = FakeCodingAdapter()
        self.service = ClimateService(
            self.registry, RepositoryWorkspaceService(WorkspaceSettings()), self.coding
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_work_gemini_packs_explicit_file_only(self):
        self.service.execute(
            "work",
            "work-repo",
            provider="gemini",
            model="gemini-test-flash",
            prompt="Summarize the purpose of the provided file in 3 bullets. Use only the supplied context and do not infer information from elsewhere.",
            display_prompt="Summarize the purpose of the provided file in 3 bullets. Use only the supplied context and do not infer information from elsewhere.",
            current_file="app.py",
            selected_files=[],
            task_mode="ask",
        )
        call = self.coding.calls[-1]
        self.assertIn("Selected file app.py", call["selection"])
        self.assertIn("value = 1", call["selection"])
        self.assertEqual(call["display_prompt"].startswith("Summarize the purpose"), True)

    def test_gemini_selected_file_bypasses_empty_evidence_gate(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="gemini",
            model="gemini-test-flash",
            prompt="Explain quantum foam topology xyzzy-no-match",
            current_file="app.py",
            selected_files=[],
            task_mode="ask",
        )
        self.assertTrue(result.get("provider_invoked"))
        self.assertEqual(self.coding.calls[-1]["provider"], "gemini")
        self.assertIn("value = 1", self.coding.calls[-1]["selection"])


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

    def test_gemini_ask_keeps_bounded_context_and_omits_resolver_hits(self):
        center = mock.Mock()
        center.start_run.return_value = {
            "id": "run-2",
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
            prompt="CLIMATE context packet (ASK).\nLikely source: app.py",
            display_prompt="Summarize the provided file in 3 bullets.",
            task_mode="ask",
            current_file="app.py",
            selected_files=["app.py"],
            selection="Selected file app.py:\nvalue = 1\n",
            evidence_packet={"hits": [{"source": "climate_context_resolver", "path": "app.py"}]},
        )
        payload = center.start_run.call_args.args[0]
        self.assertEqual(payload["display_prompt"], "Summarize the provided file in 3 bullets.")
        self.assertTrue(payload["bounded_evidence_only"])
        self.assertTrue(payload["tool_runtime_lean_context"])
        self.assertFalse(payload["repository_investigation"])
        self.assertEqual(payload["files"], {})
        self.assertNotIn("evidence_packet", payload)
        self.assertIn("Selected file app.py", payload["prompt"])
        self.assertIn("value = 1", payload["prompt"])

    def test_general_chat_omits_repository_and_keeps_display_prompt(self):
        center = mock.Mock()
        center.start_run.return_value = {
            "id": "run-chat",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-test-flash",
            "conversation_id": "conversation-chat",
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
            prompt="Reply with exactly: AiriX Gemini connection successful.",
            display_prompt="Reply with exactly: AiriX Gemini connection successful.",
            task_mode="ask",
            surface="chat",
            include_repo_context=True,
            current_file="app.py",
            selected_files=["README.md"],
        )
        payload = center.start_run.call_args.args[0]
        self.assertEqual(
            payload["display_prompt"],
            "Reply with exactly: AiriX Gemini connection successful.",
        )
        self.assertEqual(payload["repository_ids"], [])
        self.assertEqual(payload["files"], {})
        self.assertFalse(payload.get("inherit_repository_scope"))
        self.assertIsNone(payload.get("active_repository_id"))
        self.assertFalse(payload["repository_investigation"])
        self.assertNotIn("Repository context:", payload["prompt"])
        self.assertNotIn("CLIMATE coding request", payload["prompt"])
        self.assertIn("AiriX · CLIMATE Chat", payload["prompt"])
        self.assertIn("Reply with exactly: AiriX Gemini connection successful.", payload["prompt"])


if __name__ == "__main__":
    unittest.main()
