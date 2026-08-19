"""CLIMATE execution mode: Assisted retrieval vs Direct Provider, safety preserved."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.execution_mode import (
    CLIMATE_ASSISTED,
    DIRECT,
    coerce_execution_mode,
    normalize_execution_mode,
)
from hub.climate.preflight import GATE_MESSAGE
from hub.climate.retrieval_policy import ASK_INVESTIGATION_CONSTRAINTS
from hub.climate.service import ClimateService, resolve_climate_context
from hub.climate.token_efficiency import TokenEfficiencyService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


class ExecutionModeNormalizeTests(unittest.TestCase):
    def test_aliases_and_default(self):
        self.assertEqual(normalize_execution_mode(""), CLIMATE_ASSISTED)
        self.assertEqual(normalize_execution_mode("assisted"), CLIMATE_ASSISTED)
        self.assertEqual(normalize_execution_mode("airix"), CLIMATE_ASSISTED)
        self.assertEqual(normalize_execution_mode("direct_provider"), DIRECT)
        self.assertEqual(normalize_execution_mode("Direct Codex"), CLIMATE_ASSISTED)
        self.assertEqual(normalize_execution_mode("smart"), CLIMATE_ASSISTED)

    def test_coerce_blank_is_airix_unknown_raises(self):
        self.assertEqual(coerce_execution_mode(""), CLIMATE_ASSISTED)
        self.assertEqual(coerce_execution_mode("airix"), CLIMATE_ASSISTED)
        self.assertEqual(coerce_execution_mode("direct"), DIRECT)
        with self.assertRaises(ValueError):
            coerce_execution_mode("smart")


class ExecutionModeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / "work"
        self.personal = root / "personal"
        self.work.mkdir()
        self.personal.mkdir()
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.work / "AGENTS.md").write_text("# Agents\nUse repository files.\n", encoding="utf-8")
        (self.work / "SKILLS.md").write_text("# Skills\n\n## ANC Binary\nVisit thresholds.\n", encoding="utf-8")
        (self.work / "docs").mkdir()
        (self.work / "docs" / "anc.md").write_text("ANC Binary is derived from visit thresholds.\n", encoding="utf-8")
        (self.personal / "note.md").write_text("private\n", encoding="utf-8")
        self._git_init(self.work)
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work)),
            Repository(
                id="personal-repo",
                name="Personal",
                type="command",
                enabled=True,
                local_path=str(self.personal),
                tags=["arctic"],
            ),
        ])
        self.repo_service = RepositoryWorkspaceService(WorkspaceSettings())
        self.coding = FakeCodingAdapter()
        self.service = ClimateService(self.registry, self.repo_service, self.coding)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _git_init(root: Path):
        try:
            for args in (
                ("init",),
                ("config", "user.email", "test@example.com"),
                ("config", "user.name", "Test"),
                ("add", "app.py"),
                ("commit", "-m", "init"),
            ):
                subprocess.run(["git", *args], cwd=root, check=True, shell=False, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            pass

    def test_bootstrap_exposes_generic_modes_only(self):
        boot = self.service.bootstrap("work")
        ids = [row["id"] for row in boot["execution_modes"]]
        self.assertEqual(ids, [CLIMATE_ASSISTED, DIRECT])
        labels = [row["label"] for row in boot["execution_modes"]]
        self.assertEqual(labels, ["AiriX", "Direct"])
        blob = str(boot["execution_modes"])
        self.assertNotIn("Direct Codex", blob)
        self.assertNotIn("smart", blob.lower())

    def test_assisted_uses_resolver_packet(self):
        with mock.patch("hub.climate.service.resolve_climate_context", wraps=resolve_climate_context) as resolver:
            result = self.service.execute(
                "work",
                "work-repo",
                provider="codex",
                model="gpt-exact",
                prompt="Explain how ANC Binary is derived",
                current_file="app.py",
            )
        resolver.assert_called()
        call = self.coding.calls[0]
        self.assertIn("CLIMATE context packet", call["prompt"])
        self.assertEqual(call["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(result["execution_mode"], CLIMATE_ASSISTED)
        self.assertIsInstance(call.get("evidence_packet"), dict)

    def test_direct_never_builds_or_sends_resolver_packet(self):
        prompt = "Explain how ANC Binary is derived"
        with mock.patch("hub.climate.service.resolve_climate_context") as resolver:
            result = self.service.execute(
                "work",
                "work-repo",
                provider="codex",
                model="gpt-exact",
                prompt=prompt,
                current_file="app.py",
                selected_files=["docs/anc.md"],
                execution_mode="direct",
            )
        resolver.assert_not_called()
        call = self.coding.calls[0]
        self.assertEqual(call["prompt"], prompt)
        self.assertIsNone(call.get("evidence_packet"))
        self.assertEqual(call["execution_mode"], DIRECT)
        self.assertEqual(call["provider"], "codex")
        self.assertEqual(call["model"], "gpt-exact")
        self.assertEqual(call["repository_id"], "work-repo")
        self.assertTrue(call.get("repository_investigation"))
        self.assertIn("[climate_execution_mode]", call.get("preflight_log") or "")
        self.assertIn("Context Resolver skipped", call.get("preflight_log") or "")
        self.assertNotIn("climate_context_resolver", str(call))
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertTrue((result.get("preflight") or {}).get("diagnostics", {}).get("resolver_skipped"))
        self.assertEqual((result.get("preflight") or {}).get("source_files"), [])
        self.assertEqual((result.get("preflight") or {}).get("context_tokens_est"), 0)
        te = result.get("token_efficiency") or {}
        self.assertEqual(te.get("execution_mode"), DIRECT)
        self.assertEqual(te.get("compare_label"), "Compare with CLIMATE")

    def test_direct_omitted_mode_defaults_to_assisted(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Explain how ANC Binary is derived",
        )
        self.assertEqual(result["execution_mode"], CLIMATE_ASSISTED)
        self.assertIn("CLIMATE context packet", self.coding.calls[0]["prompt"])

    def test_direct_still_enforces_workspace_isolation(self):
        with self.assertRaises(ClimateCodingError) as ctx:
            self.service.execute(
                "personal",
                "work-repo",
                provider="claude-code",
                model="claude-exact",
                prompt="inspect",
                execution_mode="direct",
            )
        self.assertEqual(ctx.exception.code, "workspace_isolation")
        self.assertEqual(self.coding.calls, [])

    def test_direct_bypasses_retrieval_gate_not_safety_boundary(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="claude-code",
            model="m",
            prompt="Explain quantum foam topology xyzzy-no-match",
            execution_mode="direct",
        )
        self.assertTrue(result.get("provider_invoked"))
        self.assertEqual(len(self.coding.calls), 1)
        self.assertNotIn(GATE_MESSAGE, str(result.get("answer") or ""))
        self.assertEqual(self.coding.calls[0]["repository_id"], "work-repo")

    def test_assisted_packet_only_gate_is_unchanged(self):
        before = len(self.coding.calls)
        result = self.service.execute(
            "work",
            "work-repo",
            provider="claude-code",
            model="m",
            prompt="Explain quantum foam topology xyzzy-no-match",
        )
        self.assertEqual(len(self.coding.calls), before)
        self.assertFalse(result.get("provider_invoked"))
        self.assertIn(GATE_MESSAGE, result["answer"])

    def test_provider_model_switching_works_in_both_modes(self):
        assisted = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="gpt-exact",
            prompt="inspect",
            execution_mode="climate_assisted",
        )
        direct = self.service.execute(
            "work",
            "work-repo",
            provider="cursor-agent",
            model="cursor-exact",
            prompt="review",
            execution_mode="direct",
        )
        self.assertEqual((assisted["provider"], assisted["model"]), ("codex", "gpt-exact"))
        self.assertEqual((direct["provider"], direct["model"]), ("cursor-agent", "cursor-exact"))
        self.assertEqual(self.coding.calls[0]["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(self.coding.calls[1]["execution_mode"], DIRECT)
        self.assertEqual(self.coding.calls[1]["model"], "cursor-exact")

    def test_direct_edit_still_uses_task_mode_edit(self):
        self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Fix ANC Binary in app.py",
            execution_mode="direct",
            task_mode="edit",
        )
        self.assertEqual(self.coding.calls[0]["task_mode"], "edit")
        self.assertEqual(self.coding.calls[0]["prompt"], "Fix ANC Binary in app.py")

    def test_chat_airix_and_direct_do_not_require_a_repository(self):
        airix = self.service.execute_chat(
            "work", provider="codex", model="m", prompt="hello there",
        )
        direct = self.service.execute_chat(
            "work", provider="codex", model="m", prompt="hello there", execution_mode="direct",
        )
        self.assertEqual(airix["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(direct["execution_mode"], DIRECT)
        self.assertEqual(self.coding.calls[0]["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(self.coding.calls[1]["execution_mode"], DIRECT)
        self.assertEqual(self.coding.calls[0]["repository_id"], "")
        self.assertEqual(self.coding.calls[1]["repository_id"], "")
        self.assertEqual(self.coding.calls[0]["surface"], "chat")
        self.assertEqual(self.coding.calls[0]["context_scope"], "general")
        self.assertIn("CLIMATE connected repositories", self.coding.calls[0]["selection"])
        self.assertFalse(self.coding.calls[1]["selection"])

    def test_chat_explicit_repo_uses_resolver_only_for_airix(self):
        packet = mock.Mock(ok=True, packet="CLIMATE context packet (ASK).\nhello")
        with mock.patch("hub.climate.service.resolve_climate_context", return_value=packet) as resolver:
            self.service.execute_chat(
                "work",
                provider="codex",
                model="m",
                prompt="hello",
                repository_id="work-repo",
                include_repo_context=True,
                execution_mode="climate_assisted",
            )
        resolver.assert_called()
        self.assertEqual(self.coding.calls[0]["prompt"], packet.packet)
        self.assertEqual(self.coding.calls[0]["repository_id"], "")
        with mock.patch("hub.climate.service.resolve_climate_context") as resolver:
            self.service.execute_chat(
                "work",
                provider="codex",
                model="m",
                prompt="hello",
                repository_id="work-repo",
                include_repo_context=True,
                execution_mode="direct",
            )
        resolver.assert_not_called()
        self.assertEqual(self.coding.calls[1]["execution_mode"], DIRECT)
        self.assertEqual(self.coding.calls[1]["context_scope"], "repository")
        self.assertIn("Explicit repository context", self.coding.calls[1]["selection"])

    def test_chat_all_repositories_uses_bounded_selection(self):
        result = self.service.execute_chat(
            "work",
            provider="codex",
            model="m",
            prompt="where is execute_chat?",
            context_scope="all",
        )
        self.assertEqual(result["context_scope"], "all")
        self.assertEqual(self.coding.calls[0]["context_scope"], "all")
        self.assertIn("CLIMATE connected repositories", self.coding.calls[0]["selection"])
        self.assertNotIn("execute_chat handles", self.coding.calls[0]["selection"])

    def test_workspace_general_does_not_require_a_repository(self):
        result = self.service.execute(
            "work",
            "",
            provider="codex",
            model="m",
            prompt="hello there",
            context_scope="general",
        )
        self.assertEqual(result["context_scope"], "general")
        self.assertEqual(self.coding.calls[0]["repository_id"], "")
        self.assertEqual(self.coding.calls[0]["context_scope"], "general")
        self.assertEqual(self.coding.calls[0]["surface"], "workspace")
        self.assertIn("CLIMATE connected repositories", self.coding.calls[0]["selection"])

    def test_workspace_all_repositories_uses_bounded_selection(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="hello",
            context_scope="all",
            execution_mode="direct",
        )
        self.assertEqual(result["context_scope"], "all")
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertEqual(self.coding.calls[0]["repository_id"], "")
        self.assertIn("CLIMATE connected repositories", self.coding.calls[0]["selection"])

    def test_workspace_attached_files_reach_provider(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="explain this",
            execution_mode="direct",
            attached_files=[
                {"repository_id": "work-repo", "path": "app.py", "start_line": 1, "end_line": 1},
                {"repository_id": "work-repo", "path": "docs/anc.md"},
            ],
        )
        call = self.coding.calls[0]
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertIn("Explicit attached file context", call["selection"])
        self.assertIn("app.py", call["selection"])
        self.assertIn("docs/anc.md", call["selection"])
        self.assertIn("value = 1", call["selection"])
        self.assertIn("explain this", call["prompt"])
        self.assertEqual(call["selected_files"], ["app.py", "docs/anc.md"])

    def test_workspace_specific_rejects_foreign_attachment(self):
        with self.assertRaises(ClimateCodingError) as caught:
            self.service.execute(
                "work",
                "work-repo",
                provider="codex",
                model="m",
                prompt="hello",
                attached_files=[{"repository_id": "personal-repo", "path": "note.md"}],
            )
        self.assertEqual(caught.exception.code, "workspace_isolation")
        self.assertEqual(self.coding.calls, [])


class ExecutionModeCodingAdapterTests(unittest.TestCase):
    def setUp(self):
        class StubCenter:
            def __init__(self):
                self.payload = None

            def start_run(self, payload):
                self.payload = payload
                return {
                    "id": "r1",
                    "status": "running",
                    "agent_id": "codex",
                    "model": "m",
                    "answer": "",
                    "logs": "",
                    "usage": {},
                }

        self.center = StubCenter()
        self.adapter = ClimateCodingAdapter(self.center)
        self.adapter.availability = lambda provider=None, refresh=False: (  # type: ignore[method-assign]
            {
                "id": "codex",
                "state": "connected",
                "status": "Connected",
                "detail": "",
                "capabilities": {"native_repository_investigation": True},
            }
            if provider
            else []
        )

    def test_direct_ask_sends_raw_prompt_with_safety_no_packet(self):
        result = self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="Explain how ANC Binary is derived",
            task_mode="ask",
            repository_investigation=True,
            execution_mode="direct",
        )
        packed = self.center.payload["prompt"]
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertIn("Execution mode: Direct Provider.", packed)
        self.assertIn("User prompt:", packed)
        self.assertIn("Explain how ANC Binary is derived", packed)
        self.assertNotIn("CLIMATE context packet", packed)
        self.assertNotIn("climate_context_resolver", packed)
        self.assertIn("no candidate-source list", packed.lower())
        self.assertIn("Do not modify files", packed)
        self.assertIn(ASK_INVESTIGATION_CONSTRAINTS[:40], packed)
        self.assertFalse(self.center.payload["bounded_evidence_only"])
        self.assertFalse(self.center.payload["tool_runtime_lean_context"])
        self.assertTrue(self.center.payload["repository_investigation"])
        self.assertEqual(self.center.payload["repository_ids"], ["work-repo"])
        self.assertNotIn("evidence_packet", self.center.payload)
        self.assertNotIn("execution_mode", self.center.payload)

    def test_direct_edit_stays_propose_only(self):
        self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="Fix ANC Binary",
            task_mode="edit",
            execution_mode="direct",
        )
        packed = self.center.payload["prompt"]
        self.assertIn("EDIT mode", packed)
        self.assertIn("Stay read-only at runtime", packed)
        self.assertIn('"edits"', packed)
        self.assertIn("Do not apply edits", packed)
        self.assertNotIn("Use only the bounded context packet", packed)
        self.assertNotIn("CLIMATE context packet", packed)

    def test_assisted_ask_still_uses_packet_language(self):
        self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="CLIMATE context packet (ASK).\nLikely source: app.py",
            task_mode="ask",
            repository_investigation=True,
        )
        packed = self.center.payload["prompt"]
        self.assertIn("independently search", packed)
        self.assertIn("CLIMATE context packet (ASK)", packed)
        self.assertNotIn("Execution mode: Direct Provider.", packed)
        self.assertTrue(self.center.payload["bounded_evidence_only"])

    def test_chat_airix_wraps_prompt_without_repository(self):
        result = self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="hello there",
            surface="chat",
            include_repo_context=True,
            execution_mode="climate_assisted",
        )
        packed = self.center.payload["prompt"]
        self.assertEqual(result["execution_mode"], CLIMATE_ASSISTED)
        self.assertIn("AiriX · CLIMATE Chat", packed)
        self.assertIn("User prompt:", packed)
        self.assertIn("hello there", packed)
        self.assertEqual(self.center.payload["repository_ids"], [])
        self.assertEqual(self.center.payload["files"], {})

    def test_chat_direct_uses_minimal_wrapping_without_repository(self):
        result = self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="what is PMNP?",
            surface="chat",
            include_repo_context=True,
            execution_mode="direct",
        )
        packed = self.center.payload["prompt"]
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertEqual(packed, "what is PMNP?")
        self.assertNotIn("AiriX · CLIMATE Chat", packed)
        self.assertNotIn("User prompt:", packed)
        self.assertNotIn("cannot verify", packed.lower())
        self.assertNotIn("evidence packet", packed.lower())
        self.assertTrue(self.center.payload.get("direct_provider_chat"))
        self.assertTrue(self.center.payload.get("allow_general_knowledge"))
        self.assertFalse(self.center.payload.get("bounded_evidence_only"))
        self.assertFalse(self.center.payload.get("tool_runtime"))
        self.assertEqual(self.center.payload["repository_ids"], [])
        self.assertEqual(self.center.payload["files"], {})

    def test_chat_direct_includes_explicit_attached_context(self):
        result = self.adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="explain this file",
            surface="chat",
            execution_mode="direct",
            selection="Selected file app.py:\nvalue = 1\n",
        )
        packed = self.center.payload["prompt"]
        self.assertEqual(result["execution_mode"], DIRECT)
        self.assertIn("Attached context:", packed)
        self.assertIn("Selected file app.py:", packed)
        self.assertIn("value = 1", packed)
        self.assertIn("explain this file", packed)
        self.assertNotIn("cannot verify", packed.lower())
        self.assertNotIn("evidence packet", packed.lower())
        self.assertNotIn("AiriX · CLIMATE Chat", packed)
        self.assertTrue(self.center.payload.get("direct_provider_chat"))

    def test_chat_airix_keeps_climate_orchestration_language(self):
        result = self.adapter.execute(
            workspace="work",
            repository_id="",
            provider="codex",
            model="m",
            prompt="what is PMNP?",
            surface="chat",
            execution_mode="climate_assisted",
        )
        packed = self.center.payload["prompt"]
        self.assertEqual(result["execution_mode"], CLIMATE_ASSISTED)
        self.assertIn("AiriX · CLIMATE Chat", packed)
        self.assertIn("Use only the user prompt and any supplied bounded context.", packed)
        self.assertIn("what is PMNP?", packed)
        self.assertFalse(self.center.payload.get("direct_provider_chat"))
        self.assertNotEqual(packed, "what is PMNP?")


class ExecutionModeUiContractTests(unittest.TestCase):
    def test_selector_persists_separately_from_provider(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "climate.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        self.assertIn('id="climate-execution-mode"', template)
        self.assertIn("climate-mode-switch", template)
        self.assertIn(">AiriX<", template)
        self.assertIn(">Direct<", template)
        self.assertIn("climate-chat-pill-mode", template)
        self.assertNotIn("CLIMATE Assisted", template)
        self.assertNotIn("Direct Codex", template)
        self.assertNotIn("Smart mode", template)
        self.assertNotIn("smart_mode", script)
        self.assertIn("applyExecutionMode", script)
        self.assertIn("syncExecutionModeSwitch", script)
        self.assertIn("executionMode: currentExecutionMode()", script)
        self.assertIn("execution_mode: currentExecutionMode()", script)
        self.assertIn("Compare with Direct", script)
        self.assertIn("Compare with CLIMATE", script)
        self.assertNotIn("Evaluate Token Savings", script)
        self.assertIn("AiriX — CLIMATE orchestration, then the selected provider/model.", script)
        self.assertIn("Direct — send the prompt to the selected provider/model with minimal CLIMATE orchestration.", script)
        save = script.split("function savePrefs()", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("executionMode: currentExecutionMode()", save)
        send = script.split("function sendRun", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("execution_mode: currentExecutionMode()", send)
        self.assertIn("session.executionMode = currentExecutionMode()", send)


class ExecutionModeTokenEfficiencyTests(unittest.TestCase):
    def test_public_payload_records_mode_and_compare_label(self):
        svc = TokenEfficiencyService(persist_root=Path(tempfile.mkdtemp()))
        assisted = {
            "status": "Not measured",
            "snapshot": {
                "provider": "codex",
                "user_prompt": "Explain ANC",
                "execution_mode": CLIMATE_ASSISTED,
                "climate_usage": {"total_tokens": 100},
            },
        }
        public = svc.public(assisted)
        self.assertEqual(public["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(public["compare_label"], "Compare with Direct")
        self.assertEqual(public["snapshot"]["execution_mode"], CLIMATE_ASSISTED)

        direct = {
            "status": "Not measured",
            "snapshot": {
                "provider": "codex",
                "user_prompt": "Explain ANC",
                "execution_mode": DIRECT,
                "climate_usage": {"total_tokens": 80, "input_tokens": 60, "output_tokens": 20, "source": "provider"},
            },
        }
        public_direct = svc.public(direct)
        self.assertEqual(public_direct["execution_mode"], DIRECT)
        self.assertEqual(public_direct["compare_label"], "Compare with CLIMATE")
        self.assertEqual(public_direct["direct"]["usage"]["total_tokens"], 80)
        self.assertIsNone(public_direct["climate"]["total"])
