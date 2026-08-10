"""Focused Repository Intelligence persistence and freshness contracts."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from hub.agent_center.context_builder import build_context_preview
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.repository_intelligence import RepositoryIntelligenceService
from hub.agent_center.routes import register_agent_center_routes
from hub.agent_center.routing.telemetry import attach_execution_telemetry
from hub.agent_center.service import AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, Repository


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return proc.stdout.strip()


class RepositoryIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "learned-repo"
        self.root.mkdir()
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "tests@example.invalid")
        _git(self.root, "config", "user.name", "Repository Intelligence Tests")
        (self.root / "AGENTS.md").write_text(
            "# Instructions\nUse adapters only. Keep Stage and Live isolated.\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# Example Hub\nCoordinates repository integrations and tools.\n",
            encoding="utf-8",
        )
        (self.root / "architecture.md").write_text(
            "# Architecture\nThe routing service selects providers and context.\n",
            encoding="utf-8",
        )
        (self.root / "payments.py").write_text(
            "def reconcile_invoice():\n    return 'invoice-ledger-marker'\n",
            encoding="utf-8",
        )
        (self.root / "unrelated.py").write_text(
            "UNRELATED_CONTEXT_MARKER = 'never pack by default'\n",
            encoding="utf-8",
        )
        (self.root / ".env").write_text("TOKEN=never-index\n", encoding="utf-8")
        _git(self.root, "add", "AGENTS.md", "README.md", "architecture.md", "payments.py", "unrelated.py")
        _git(self.root, "commit", "-m", "initial")
        self.repo = Repository(
            id="example",
            name="Example",
            type="command",
            enabled=True,
            local_path=str(self.root),
            working_directory=str(self.root),
        )
        self.registry = Registry(repositories=[self.repo])
        self.db_path = Path(self.temp.name) / "agent.db"
        self.service = RepositoryIntelligenceService(AgentCenterDb(self.db_path), self.registry)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_initial_scan_persists_profile_index_and_commit(self) -> None:
        self.assertEqual(self.service.get_status("example")["status"], "not_learned")
        status = self.service.scan("example")
        self.assertEqual(status["status"], "current")
        self.assertEqual(status["indexed_commit"], _git(self.root, "rev-parse", "HEAD"))
        self.assertIn("guidance", status["categories"])
        self.assertGreaterEqual(status["profile"]["file_count"], 5)
        telemetry = status["last_scan_telemetry"]
        self.assertEqual(telemetry["execution_type"], "Deterministic")
        self.assertFalse(telemetry["llm_invoked"])
        self.assertIsNone(telemetry["provider"])
        self.assertIsNone(telemetry["model"])
        self.assertEqual(
            [telemetry[key] for key in ("input_tokens", "output_tokens", "cached_tokens", "total_ai_tokens")],
            [0, 0, 0, 0],
        )
        self.assertGreaterEqual(telemetry["files_scanned"], telemetry["files_indexed"])
        self.assertEqual(telemetry["indexed_commit"], status["indexed_commit"])

        reopened = RepositoryIntelligenceService(AgentCenterDb(self.db_path), self.registry)
        knowledge = reopened.knowledge("example")
        self.assertEqual(knowledge["status"]["status"], "current")
        self.assertIn("AGENTS.md", {row["path"] for row in knowledge["entries"]})
        self.assertNotIn(".env", {row["path"] for row in knowledge["entries"]})
        self.assertEqual(len(knowledge["scan_history"]), 1)

    def test_deep_ai_analysis_is_future_ready_but_disabled(self) -> None:
        status = self.service.get_status("example")
        self.assertFalse(status["analysis_modes"]["deep_ai"]["enabled"])
        self.assertFalse(status["analysis_modes"]["deep_ai"]["implemented"])
        with self.assertRaisesRegex(ValueError, "disabled and not implemented"):
            self.service.scan("example", analysis_mode="deep_ai")

    def test_scan_refresh_and_view_api_contract(self) -> None:
        class Audit:
            def __init__(self) -> None:
                self.actions: list[str] = []

            def append(self, *, action: str, detail: dict) -> None:
                self.actions.append(action)

        audit = Audit()
        app = Flask(__name__)
        app.secret_key = "repository-intelligence-tests"
        app.config["AGENT_CENTER"] = SimpleNamespace(repository_intelligence=self.service)
        app.config["AUDIT"] = audit
        register_agent_center_routes(app)
        client = app.test_client()

        scanned = client.post("/api/repositories/example/intelligence/scan")
        self.assertEqual(scanned.status_code, 200)
        self.assertTrue(scanned.get_json()["ok"])
        viewed = client.get("/api/repositories/example/intelligence")
        self.assertEqual(viewed.status_code, 200)
        self.assertGreater(viewed.get_json()["entry_count"], 0)
        refreshed = client.post("/api/repositories/example/intelligence/refresh")
        self.assertEqual(refreshed.status_code, 200)
        history = self.service.scan_history("example")
        self.assertEqual(len(history), 2)
        self.assertEqual([row["trigger"] for row in history], ["manual_refresh", "manual_scan"])
        self.assertTrue(all(not row["llm_invoked"] for row in history))
        self.assertTrue(all(row["total_ai_tokens"] == 0 for row in history))
        self.assertIn("REPOSITORY_INTELLIGENCE_SCAN", audit.actions)
        self.assertIn("REPOSITORY_INTELLIGENCE_REFRESH", audit.actions)

    def test_retrieval_and_prompt_pack_are_task_relevant_and_bounded(self) -> None:
        self.service.scan("example")
        retrieved = self.service.retrieve(["example"], "How is invoice reconciliation routed?")
        self.assertFalse(retrieved["include_full_index"])
        self.assertLessEqual(retrieved["item_count"], 6)
        self.assertIn("payments.py", {item["path"] for item in retrieved["items"]})

        preview = build_context_preview(
            self.registry,
            repository_ids=["example"],
            mode="ask",
            prompt="How is invoice reconciliation routed?",
            repository_knowledge=retrieved,
        )
        packed = preview["packed_prompt"]
        self.assertIn("Profile example", packed)
        self.assertIn("Runtime database and DHIS2 results override", packed)
        self.assertIn("payments.py", packed)
        self.assertNotIn("UNRELATED_CONTEXT_MARKER", packed)
        self.assertLess(len(packed), 20_000)
        diagnostics = preview["repository_intelligence"]["diagnostics"]
        self.assertTrue(diagnostics["used"])
        self.assertEqual(diagnostics["knowledge_entries_used"], retrieved["item_count"])
        self.assertGreater(diagnostics["context_chars_contributed"], 0)
        self.assertFalse(diagnostics["full_index_included"])

    def test_selected_learned_repo_is_loaded_by_existing_agent_context_path(self) -> None:
        store = AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "integrated-agent.db"))
        center = AgentCenterService(self.registry, store=store, adapters=[])
        center.repository_intelligence.scan("example")
        preview = center.preview_context(
            {
                "profile_id": "okarun",
                "repository_ids": ["example"],
                "mode": "ask",
                "prompt": "How does invoice reconciliation work?",
            }
        )
        knowledge = preview["repository_intelligence"]
        self.assertTrue(knowledge["diagnostics"]["used"])
        self.assertIn("payments.py", {item["path"] for item in knowledge["items"]})
        self.assertLessEqual(len(knowledge["items"]), 6)
        self.assertTrue(preview["grounding"]["usable"])
        self.assertTrue(any(
            str(source).startswith("repository_intelligence:example:")
            for source in preview["evidence_packet"]["sources"]
        ))

    def test_no_learned_repo_preserves_normal_context_behavior(self) -> None:
        retrieved = self.service.retrieve(["example"], "How does invoice reconciliation work?")
        self.assertEqual(retrieved["profiles"], [])
        self.assertEqual(retrieved["items"], [])
        self.assertFalse(retrieved["diagnostics"]["used"])
        preview = build_context_preview(
            self.registry,
            repository_ids=["example"],
            mode="ask",
            prompt="How does invoice reconciliation work?",
            evidence_packet_text="AUTHORITATIVE_RUNTIME_VALUE=42",
            repository_knowledge=retrieved,
        )
        self.assertNotIn("# Repository Intelligence", preview["packed_prompt"])
        self.assertIn("AUTHORITATIVE_RUNTIME_VALUE=42", preview["packed_prompt"])

    def test_runtime_evidence_override_contract_precedes_cached_intelligence(self) -> None:
        self.service.scan("example")
        retrieved = self.service.retrieve(["example"], "invoice reconciliation")
        preview = build_context_preview(
            self.registry,
            repository_ids=["example"],
            mode="ask",
            prompt="invoice reconciliation",
            evidence_packet_text="AUTHORITATIVE_RUNTIME_VALUE=42",
            repository_knowledge=retrieved,
        )
        packed = preview["packed_prompt"]
        self.assertLess(packed.index("AUTHORITATIVE_RUNTIME_VALUE=42"), packed.index("# Repository Intelligence"))
        self.assertIn("Runtime database and DHIS2 results override", packed)

        center = AgentCenterService(
            self.registry,
            store=AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "authority-agent.db")),
            adapters=[],
        )
        unchanged = center._grounding_with_repository_intelligence(
            {"usable": False, "evidence_packet": {"usable": False, "hits": [], "sources": []}},
            retrieved,
            prompt="How many DHIS2 beneficiaries are in the live database?",
            repository_ids=["example"],
        )
        self.assertFalse(unchanged["usable"])
        self.assertEqual(unchanged["evidence_packet"]["hits"], [])

    def test_airix_run_telemetry_exposes_repository_intelligence_diagnostics(self) -> None:
        row = attach_execution_telemetry(
            {
                "mode": "deterministic",
                "provider_id": "deterministic",
                "context": {
                    "repository_intelligence": {
                        "diagnostics": {
                            "used": True,
                            "repository_ids": ["example"],
                            "repositories": [{
                                "repository_id": "example",
                                "indexed_commit": "abc123",
                                "current_commit": "abc123",
                                "freshness": "current",
                                "knowledge_entries_used": 2,
                            }],
                            "knowledge_entries_used": 2,
                            "freshness": "current",
                            "context_chars_contributed": 640,
                            "full_index_included": False,
                        }
                    }
                },
            }
        )
        telemetry = row["telemetry"]
        self.assertEqual(telemetry["total_ai_tokens"], 0)
        self.assertTrue(telemetry["repository_intelligence"]["used"])
        self.assertEqual(telemetry["repository_intelligence"]["knowledge_entries_used"], 2)

    def test_git_change_detection_and_incremental_refresh(self) -> None:
        original = self.service.scan("example")
        (self.root / "payments.py").write_text(
            "def reconcile_invoice():\n    return 'updated-ledger-workflow'\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "payments.py")
        _git(self.root, "commit", "-m", "update payment flow")
        available = self.service.get_status("example")
        self.assertEqual(available["status"], "update_available")
        self.assertEqual(available["changed_files"], ["payments.py"])

        retrieved = self.service.retrieve(["example"], "updated ledger workflow")
        self.assertEqual(self.service.get_status("example")["status"], "current")
        self.assertNotEqual(original["indexed_commit"], retrieved["profiles"][0]["indexed_commit"])
        self.assertIn("updated-ledger-workflow", retrieved["items"][0]["summary"])
        telemetry = self.service.get_status("example")["last_scan_telemetry"]
        self.assertEqual(telemetry["trigger"], "automatic_refresh")
        self.assertEqual(telemetry["files_scanned"], 1)
        self.assertEqual(telemetry["files_changed"], 1)
        self.assertEqual(retrieved["diagnostics"]["freshness"], "refreshed")

    def test_instruction_change_invalidates_immediately_and_refreshes_siblings(self) -> None:
        self.service.scan("example")
        (self.root / "AGENTS.md").write_text(
            "# Instructions\nNew guidance marker: VERIFY_RUNTIME_FIRST.\n",
            encoding="utf-8",
        )
        (self.root / "payments.py").write_text("NEW_SIBLING_MARKER = True\n", encoding="utf-8")
        _git(self.root, "add", "AGENTS.md", "payments.py")
        _git(self.root, "commit", "-m", "change instructions and code")

        status = self.service.get_status("example")
        self.assertEqual(status["status"], "current")
        entries = {row["path"]: row["summary"] for row in self.service.knowledge("example")["entries"]}
        self.assertIn("VERIFY_RUNTIME_FIRST", entries["AGENTS.md"])
        self.assertIn("NEW_SIBLING_MARKER", entries["payments.py"])
        telemetry = status["last_scan_telemetry"]
        self.assertEqual(telemetry["trigger"], "instruction_refresh")
        self.assertEqual(telemetry["files_changed"], 2)

    def test_deleted_file_is_removed_and_stale_knowledge_cannot_be_retrieved(self) -> None:
        self.service.scan("example")
        (self.root / "payments.py").unlink()
        _git(self.root, "add", "payments.py")
        _git(self.root, "commit", "-m", "remove payments")
        retrieved = self.service.retrieve(["example"], "invoice ledger marker")
        self.assertNotIn("payments.py", {item["path"] for item in retrieved["items"]})
        self.assertNotIn(
            "payments.py",
            {row["path"] for row in self.service.knowledge("example")["entries"]},
        )


if __name__ == "__main__":
    unittest.main()
