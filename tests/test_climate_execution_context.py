"""CLIMATE Chat and Code Workspace executed-configuration reliability."""

from __future__ import annotations

from contextlib import nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import ClimateCodingAdapter
from hub.climate.execution_mode import CLIMATE_ASSISTED, DIRECT
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


def _assert_identity(
    test: unittest.TestCase,
    row: dict,
    *,
    surface: str,
    mode: str,
    scope: str,
    provider: str,
    model: str,
    repository_id: str = "",
    repository_name: str = "",
) -> None:
    test.assertEqual(row.get("surface") or row.get("climate_execution", {}).get("surface"), surface)
    test.assertEqual(row.get("execution_mode"), mode)
    test.assertEqual(row.get("context_scope"), scope)
    test.assertEqual(row.get("provider"), provider)
    test.assertEqual(row.get("model"), model)
    test.assertEqual(row.get("repository_id") or "", repository_id)
    if repository_name:
        test.assertEqual(row.get("repository_name") or "", repository_name)
    if scope == "general":
        test.assertEqual(row.get("repository_id") or "", "")
    if scope == "repository":
        test.assertEqual(row.get("repository_id"), repository_id)


class ChatExecutionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentCenterStore(AgentCenterDb(Path(self.tmp.name) / "agent-center.db"))
        self.center = mock.Mock()
        self.center.store = self.store
        self.center.start_run.return_value = {
            "id": "run-chat",
            "status": "queued",
            "agent_id": "gemini",
            "agent_label": "Gemini",
            "model": "gemini-exact",
            "conversation_id": "conversation-chat",
            "repository_ids": [],
        }
        intel = mock.Mock()
        intel.retrieve.return_value = {
            "items": [
                {
                    "repository_id": "work-repo",
                    "path": "hub/climate/service.py",
                    "summary": "execute_chat stamps identity",
                }
            ]
        }
        self.center.repository_intelligence = intel
        self.adapter = ClimateCodingAdapter(self.center)
        self.adapter.availability = mock.Mock(
            return_value={"id": "gemini", "state": "connected", "status": "Connected"}
        )
        repo = Repository(id="work-repo", name="Work", type="command", enabled=True)
        workspace = mock.Mock()
        workspace.availability.return_value = {"available": True}
        workspace.preview.return_value = {"content": "print(1)\n", "binary": False}
        self.service = ClimateService(
            registry=Registry(repositories=[repo]),
            repository_workspace=workspace,
            coding=self.adapter,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _payload(self) -> dict:
        return self.center.start_run.call_args.args[0]

    def _run_chat(self, **kwargs):
        defaults = {
            "provider": "gemini",
            "model": "gemini-exact",
            "prompt": "hello execution context",
        }
        defaults.update(kwargs)
        return self.service.execute_chat("work", **defaults)

    def test_chat_mode_scope_matrix_persists_executed_configuration(self) -> None:
        cases = [
            ("climate_assisted", "general", "", ""),
            ("direct", "general", "", ""),
            ("climate_assisted", "all", "", ""),
            ("direct", "all", "", ""),
            ("climate_assisted", "repository", "work-repo", "Work"),
            ("direct", "repository", "work-repo", "Work"),
        ]
        packet = mock.Mock(
            ok=True,
            packet="CLIMATE context packet (ASK).\nhello execution context",
            source_files=["docs/anc.md"],
        )
        for mode, scope, repo_id, repo_name in cases:
            with self.subTest(mode=mode, scope=scope):
                self.center.start_run.reset_mock()
                ctx = (
                    mock.patch("hub.climate.service.resolve_climate_context", return_value=packet)
                    if mode == "climate_assisted" and scope == "repository"
                    else nullcontext()
                )
                with ctx:
                    result = self._run_chat(
                        execution_mode=mode,
                        context_scope=scope,
                        repository_id=repo_id,
                        selected_files=["app.py"] if scope == "repository" else [],
                    )
                payload = self._payload()
                exec_meta = payload["climate_execution"]
                _assert_identity(
                    self,
                    result,
                    surface="chat",
                    mode=mode,
                    scope=scope,
                    provider="gemini",
                    model="gemini-exact",
                    repository_id=repo_id,
                    repository_name=repo_name,
                )
                _assert_identity(
                    self,
                    {
                        **exec_meta,
                        "execution_mode": exec_meta["execution_mode"],
                        "provider": exec_meta["provider"],
                    },
                    surface="chat",
                    mode=mode,
                    scope=scope,
                    provider="gemini",
                    model="gemini-exact",
                    repository_id=repo_id,
                    repository_name=repo_name,
                )
                self.assertEqual(payload["model"], "gemini-exact")
                self.assertEqual(payload["repository_ids"], [])
                self.assertFalse(payload.get("repository_investigation"))
                if scope == "general":
                    self.assertEqual(result["attached_files"], [])
                    self.assertEqual(result["retrieved_files"], [])
                    self.assertEqual(exec_meta["repository_id"], "")
                if scope == "all":
                    self.assertIn("hub/climate/service.py", " ".join(result.get("retrieved_files") or []))
                    self.assertEqual(result["repository_id"], "")
                    self.assertNotIn("print(1)", payload.get("prompt") or "")
                if scope == "repository":
                    self.assertEqual(exec_meta["repository_id"], "work-repo")
                    self.assertEqual(exec_meta["repository_name"], "Work")
                    self.assertIn("app.py", result.get("attached_files") or [])
                if mode == "direct" and scope == "general":
                    self.assertTrue(payload.get("direct_provider_chat"))

    def test_chat_airix_specific_uses_resolver_direct_does_not(self) -> None:
        packet = mock.Mock(ok=True, packet="CLIMATE context packet (ASK).\nhello", source_files=["docs/anc.md"])
        with mock.patch("hub.climate.service.resolve_climate_context", return_value=packet) as resolver:
            airix = self._run_chat(
                execution_mode="climate_assisted",
                context_scope="repository",
                repository_id="work-repo",
            )
        resolver.assert_called()
        self.assertEqual(resolver.call_args.kwargs["repo"].id, "work-repo")
        self.assertEqual(airix["retrieved_files"], ["docs/anc.md"])
        self.center.start_run.reset_mock()
        with mock.patch("hub.climate.service.resolve_climate_context") as resolver:
            direct = self._run_chat(
                execution_mode="direct",
                context_scope="repository",
                repository_id="work-repo",
                selected_files=["app.py"],
            )
        resolver.assert_not_called()
        self.assertEqual(direct["attached_files"], ["app.py"])
        self.assertEqual(direct["retrieved_files"], [])
        self.assertFalse(self._payload().get("repository_investigation"))

    def test_chat_conversation_restores_full_execution_record(self) -> None:
        convo = self.store.create_conversation(profile_id="okarun", title="Chat restore")
        self.store.create_run({
            "mode": "ask",
            "agent_id": "gemini",
            "agent_label": "Gemini",
            "model": "gemini-exact",
            "repository_ids": [],
            "prompt": "hello",
            "profile_id": "okarun",
            "conversation_id": convo["id"],
            "context": {
                "climate_execution": {
                    "execution_mode": DIRECT,
                    "context_scope": "all",
                    "repository_id": "",
                    "repository_name": "",
                    "surface": "chat",
                    "provider": "gemini",
                    "model": "gemini-exact",
                    "attached_files": [],
                    "retrieved_files": ["work-repo:hub/climate/service.py"],
                    "inspected_files": [],
                }
            },
            "tool_activity": [
                {"kind": "read", "path": "hub/climate/coding.py", "ok": True},
            ],
        })
        run = self.service.conversation("work", convo["id"], surface="chat")["runs"][0]
        self.assertEqual(run["surface"], "chat")
        self.assertEqual(run["execution_mode"], DIRECT)
        self.assertEqual(run["context_scope"], "all")
        self.assertEqual(run["repository_id"], "")
        self.assertEqual(run["model"], "gemini-exact")
        self.assertEqual(run["retrieved_files"], ["work-repo:hub/climate/service.py"])
        self.assertIn("All Repositories", run["execution_summary"])


class WorkspaceExecutionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / "work"
        self.work.mkdir()
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.work / "docs").mkdir()
        (self.work / "docs" / "anc.md").write_text("ANC Binary\n", encoding="utf-8")
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work)),
        ])
        self.coding = FakeCodingAdapter()
        intel = mock.Mock()
        intel.retrieve.return_value = {
            "items": [{"repository_id": "work-repo", "path": "app.py", "summary": "value = 1"}]
        }
        self.service = ClimateService(
            self.registry,
            RepositoryWorkspaceService(WorkspaceSettings()),
            self.coding,
        )
        self.service._repository_intelligence = lambda: intel

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_workspace_mode_scope_matrix_persists_executed_configuration(self) -> None:
        cases = [
            ("climate_assisted", "general", "", ""),
            ("direct", "general", "", ""),
            ("climate_assisted", "all", "", ""),
            ("direct", "all", "", ""),
            ("climate_assisted", "repository", "work-repo", "Work"),
            ("direct", "repository", "work-repo", "Work"),
        ]
        for mode, scope, repo_id, repo_name in cases:
            with self.subTest(mode=mode, scope=scope):
                self.coding.calls.clear()
                kwargs = {
                    "provider": "codex",
                    "model": "codex-exact",
                    "prompt": "explain execution context",
                    "execution_mode": mode,
                    "context_scope": scope,
                    "surface": "workspace",
                }
                if scope == "repository":
                    kwargs["current_file"] = "app.py"
                    kwargs["attached_files"] = [
                        {"repository_id": "work-repo", "path": "docs/anc.md"}
                    ]
                packet = mock.Mock(
                    ok=True,
                    packet="CLIMATE context packet (ASK).\nexplain execution context",
                    source_files=["app.py"],
                    activity=["resolver"],
                    instruction_files=[],
                    skills_used=[],
                    context_chars=12,
                    context_tokens_est=3,
                    confidence="high",
                    diagnostics={"qualification": [], "authoritative_sources": []},
                    task_mode="ask",
                )
                packet.activity_log.return_value = ""
                ctx = (
                    mock.patch("hub.climate.service.resolve_climate_context", return_value=packet)
                    if mode == "climate_assisted" and scope == "repository"
                    else nullcontext()
                )
                with ctx:
                    result = self.service.execute("work", repo_id or "work-repo", **kwargs)
                call = self.coding.calls[0]
                _assert_identity(
                    self,
                    result,
                    surface="workspace",
                    mode=mode,
                    scope=scope,
                    provider="codex",
                    model="codex-exact",
                    repository_id=repo_id,
                    repository_name=repo_name,
                )
                self.assertEqual(call["model"], "codex-exact")
                self.assertEqual(call["execution_mode"], mode)
                self.assertEqual(call["context_scope"], scope)
                self.assertEqual(call["surface"], "workspace")
                if scope in {"general", "all"}:
                    self.assertEqual(call["repository_id"], "")
                    self.assertEqual(result["repository_id"], "")
                    self.assertFalse(call.get("repository_investigation"))
                if scope == "all":
                    self.assertIn("app.py", " ".join(result.get("retrieved_files") or []))
                    self.assertIn("Bounded relevant repository hits", call.get("selection") or "")
                    self.assertNotIn("value = 1\nvalue = 1\nvalue = 1", call.get("selection") or "")
                if scope == "repository":
                    self.assertEqual(call["repository_id"], "work-repo")
                    self.assertIn("docs/anc.md", result.get("attached_files") or [])
                    if mode == "direct":
                        self.assertFalse(call.get("repository_investigation"))
                        self.assertIsNone(call.get("evidence_packet"))
                    else:
                        self.assertIn("CLIMATE context packet", call.get("prompt") or "")

    def test_workspace_direct_specific_packs_explicit_files_only(self) -> None:
        with mock.patch("hub.climate.service.resolve_climate_context") as resolver:
            result = self.service.execute(
                "work",
                "work-repo",
                provider="codex",
                model="codex-exact",
                prompt="explain this file",
                execution_mode="direct",
                context_scope="repository",
                current_file="app.py",
                selected_files=["docs/anc.md"],
                surface="workspace",
            )
        resolver.assert_not_called()
        call = self.coding.calls[0]
        self.assertFalse(call.get("repository_investigation"))
        self.assertIn("app.py", call.get("selection") or "")
        self.assertIn("docs/anc.md", call.get("selection") or "")
        self.assertEqual(sorted(result["attached_files"]), ["app.py", "docs/anc.md"])
        self.assertEqual(result["retrieved_files"], [])
        self.assertEqual(result["repository_name"], "Work")

    def test_workspace_conversation_restores_full_execution_record(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AgentCenterStore(AgentCenterDb(Path(tmp.name) / "agent-center.db"))
        center = mock.Mock()
        center.store = store
        adapter = ClimateCodingAdapter(center)
        adapter.availability = mock.Mock(return_value={"id": "codex", "state": "connected"})
        service = ClimateService(
            registry=self.registry,
            repository_workspace=RepositoryWorkspaceService(WorkspaceSettings()),
            coding=adapter,
        )
        convo = store.create_conversation(profile_id="okarun", title="Workspace restore")
        store.create_run({
            "mode": "ask",
            "agent_id": "codex",
            "agent_label": "Codex",
            "model": "codex-exact",
            "repository_ids": ["work-repo"],
            "prompt": "hello",
            "profile_id": "okarun",
            "conversation_id": convo["id"],
            "context": {
                "climate_execution": {
                    "execution_mode": CLIMATE_ASSISTED,
                    "context_scope": "repository",
                    "repository_id": "work-repo",
                    "repository_name": "Work",
                    "surface": "workspace",
                    "provider": "codex",
                    "model": "codex-exact",
                    "attached_files": ["docs/anc.md"],
                    "retrieved_files": ["app.py"],
                    "inspected_files": ["hub/climate/coding.py"],
                }
            },
        })
        run = service.conversation("work", convo["id"], surface="workspace")["runs"][0]
        self.assertEqual(run["surface"], "workspace")
        self.assertEqual(run["execution_mode"], CLIMATE_ASSISTED)
        self.assertEqual(run["context_scope"], "repository")
        self.assertEqual(run["repository_id"], "work-repo")
        self.assertEqual(run["repository_name"], "Work")
        self.assertEqual(run["model"], "codex-exact")
        self.assertEqual(run["attached_files"], ["docs/anc.md"])
        self.assertEqual(run["retrieved_files"], ["app.py"])
        self.assertEqual(run["inspected_files"], ["hub/climate/coding.py"])
        self.assertIn("AiriX · Codex · codex-exact · Work", run["execution_summary"])

    def test_workspace_general_does_not_inherit_explorer_repository(self) -> None:
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="codex-exact",
            prompt="hello",
            context_scope="general",
            surface="workspace",
        )
        self.assertEqual(result["repository_id"], "")
        self.assertEqual(result["context_scope"], "general")
        self.assertEqual(self.coding.calls[0]["repository_id"], "")
        self.assertIn("General", result["execution_summary"])


class AgentCenterPersistTests(unittest.TestCase):
    def test_merge_persists_file_lists_and_repository_name(self) -> None:
        from hub.agent_center.service import _merge_climate_execution

        merged = _merge_climate_execution(
            {},
            {
                "agent_id": "gemini",
                "model": "gemini-exact",
                "climate_execution": {
                    "execution_mode": "direct",
                    "context_scope": "repository",
                    "repository_id": "work-repo",
                    "repository_name": "Work",
                    "surface": "chat",
                    "provider": "gemini",
                    "model": "gemini-exact",
                    "attached_files": [{"repository_id": "work-repo", "path": "app.py"}],
                    "retrieved_files": ["docs/anc.md"],
                    "inspected_files": ["hub/climate/coding.py"],
                    "current_file": "app.py",
                },
            },
        )
        record = merged["climate_execution"]
        self.assertEqual(record["surface"], "chat")
        self.assertEqual(record["repository_name"], "Work")
        self.assertEqual(record["attached_files"], ["work-repo:app.py"])
        self.assertEqual(record["retrieved_files"], ["docs/anc.md"])
        self.assertEqual(record["inspected_files"], ["hub/climate/coding.py"])
        self.assertEqual(record["current_file"], "app.py")
        self.assertEqual(record["model"], "gemini-exact")


if __name__ == "__main__":
    unittest.main()
