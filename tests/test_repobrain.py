"""RepoBrain Phase 1 persistence, refresh, bounds, and resolver integration."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.repobrain import RepoBrainService, RepoBrainSettings
from hub.agent_center.repository_intelligence import RepositoryIntelligenceService
from hub.climate.context_registry import ContextRequest, build_default_context_resolver
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


class _Workspace:
    def availability(self, repo):
        return {"available": True}


class _CapturingIntelligence:
    def __init__(self) -> None:
        self.repository_ids: list[str] = []

    def retrieve(self, repository_ids, query, **_kwargs):
        self.repository_ids = list(repository_ids)
        first = self.repository_ids[0] if self.repository_ids else ""
        return {"items": [{
            "repository_id": first,
            "path": "services/billing.py",
            "summary": f"Live exact evidence for {query}",
            "score": 9.0,
        }]}


class _BrokenRanker:
    def rank_repositories(self, _repository_ids, _query):
        raise RuntimeError("snapshot store unavailable")


class RepoBrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "services").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "node_modules").mkdir()
        (self.root / "dist").mkdir()
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "repobrain@example.invalid")
        _git(self.root, "config", "user.name", "RepoBrain Tests")
        (self.root / "README.md").write_text(
            "# Billing Hub\nRoutes invoice workflows through BillingService.\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text(
            "from services.billing import BillingService\n\n"
            "def create_app():\n    return BillingService()\n\n"
            "if __name__ == '__main__':\n    create_app()\n",
            encoding="utf-8",
        )
        (self.root / "services" / "billing.py").write_text(
            "from data.store import InvoiceStore\n\n"
            "class BillingService:\n"
            "    def reconcile_invoice(self):\n"
            "        return InvoiceStore().query()\n",
            encoding="utf-8",
        )
        (self.root / "tests" / "test_billing.py").write_text(
            "def test_reconcile_invoice():\n    assert True\n", encoding="utf-8"
        )
        (self.root / "node_modules" / "vendor.js").write_text(
            "function SecretVendorBundle() {}\n", encoding="utf-8"
        )
        (self.root / "dist" / "bundle.js").write_text(
            "function GeneratedBundle() {}\n", encoding="utf-8"
        )
        _git(self.root, "add", "README.md", "app.py", "services/billing.py", "tests/test_billing.py")
        _git(self.root, "commit", "-m", "initial architecture")
        self.repo = Repository(
            id="billing", name="Billing", type="command", enabled=True,
            description="Invoice billing control center", local_path=str(self.root),
            working_directory=str(self.root), tags=["work"],
        )
        self.registry = Registry([self.repo])
        self.db_path = Path(self.temp.name) / "agent.db"
        self.db = AgentCenterDb(self.db_path)
        self.intelligence = RepositoryIntelligenceService(self.db, self.registry)
        self.service = RepoBrainService(self.db, self.registry, self.intelligence)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commit_billing_change(self) -> str:
        path = self.root / "services" / "billing.py"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nclass InvoicePolicy:\n    pass\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "services/billing.py")
        _git(self.root, "commit", "-m", "add invoice policy")
        return _git(self.root, "rev-parse", "HEAD")

    def test_initial_build_and_persistence(self) -> None:
        built = self.service.build("billing")
        snapshot = built["snapshot"]
        self.assertEqual(built["version"], 1)
        self.assertEqual(built["git_commit"], _git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(snapshot["repository_id"], "billing")
        self.assertTrue(snapshot["modules"])
        self.assertIn("app.py", {row["path"] for row in snapshot["entry_points"]})
        self.assertIn("BillingService", {row["name"] for row in snapshot["symbols"]})
        self.assertTrue(snapshot["dependencies"])
        self.assertTrue(snapshot["business_logic_topics"])
        self.assertIn("tests/test_billing.py", {row["path"] for row in snapshot["test_map"]})
        self.assertTrue(snapshot["source_references"])

        reopened = RepoBrainService(
            AgentCenterDb(self.db_path), self.registry,
            RepositoryIntelligenceService(AgentCenterDb(self.db_path), self.registry),
        )
        self.assertEqual(reopened.latest("billing")["id"], built["id"])
        self.assertEqual(len(reopened.history("billing")), 1)

    def test_head_change_detection_and_stale_snapshot(self) -> None:
        built = self.service.build("billing")
        new_head = self._commit_billing_change()
        stale = self.service.get_snapshot("billing", refresh=False)
        self.assertEqual(stale["id"], built["id"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["current_git_commit"], new_head)
        self.assertIn("services/billing.py", stale["pending_changed_files"])

    def test_incremental_refresh_reuses_unaffected_file_analysis(self) -> None:
        first = self.service.build("billing")
        self._commit_billing_change()
        refreshed = self.service.get_snapshot("billing", refresh=True)
        self.assertEqual(refreshed["version"], 2)
        self.assertEqual(refreshed["build_mode"], "incremental")
        self.assertEqual(refreshed["reused_snapshot_id"], first["id"])
        self.assertEqual(refreshed["refresh"]["files_analyzed"], 1)
        self.assertGreater(refreshed["refresh"]["files_reused"], 0)
        self.assertIn(
            "InvoicePolicy",
            {row["name"] for row in refreshed["snapshot"]["symbols"]},
        )

    def test_full_rebuild_and_unchanged_reuse(self) -> None:
        first = self.service.build("billing")
        reused = self.service.build("billing")
        self.assertEqual(reused["id"], first["id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["refresh"]["mode"], "reuse")

        rebuilt = self.service.full_rebuild("billing")
        self.assertEqual(rebuilt["version"], 2)
        self.assertEqual(rebuilt["build_mode"], "full")
        self.assertGreater(rebuilt["refresh"]["files_analyzed"], 0)

    def test_excluded_directories_do_not_affect_or_enter_snapshot(self) -> None:
        built = self.service.build("billing")
        serialized = json.dumps(built["snapshot"])
        self.assertNotIn("SecretVendorBundle", serialized)
        self.assertNotIn("GeneratedBundle", serialized)
        self.assertFalse(any(path.startswith(("node_modules/", "dist/")) for path in built["source_references"]))
        (self.root / "node_modules" / "vendor.js").write_text("changed", encoding="utf-8")
        unchanged = self.service.get_snapshot("billing", refresh=False)
        self.assertFalse(unchanged["stale"])

    def test_snapshot_and_context_are_bounded(self) -> None:
        bounded = RepoBrainService(
            self.db, self.registry, self.intelligence,
            settings=RepoBrainSettings(max_snapshot_chars=8_000, max_context_chars=1_200),
        )
        built = bounded.build("billing")
        self.assertLessEqual(len(json.dumps(built["snapshot"], separators=(",", ":"))), 8_000)
        context = bounded.context("billing", "invoice billing architecture")
        self.assertLessEqual(len(context["content"]), 1_200)
        self.assertNotIn("file_analysis", context)

    def test_specific_repository_uses_orientation_and_live_verification(self) -> None:
        self.service.build("billing")
        resolver = build_default_context_resolver(
            registry=self.registry,
            repository_workspace=_Workspace(),
            intelligence_loader=lambda: self.intelligence,
            repobrain_loader=lambda: self.service,
            context_loader=lambda *_args, **_kwargs: SimpleNamespace(
                ok=True,
                packet="LIVE_EXACT_BILLING_EVIDENCE",
                source_files=["services/billing.py"],
            ),
        )
        result = resolver.resolve(ContextRequest(
            "invoice billing service", "work", scope="repository", repository_id="billing"
        ))
        self.assertIn("repobrain", result.sources_used)
        self.assertIn("repositories", result.sources_used)
        self.assertEqual(result.repository_evidence_origin, "both")
        self.assertIn("RepoBrain snapshot", result.packet)
        self.assertIn("LIVE_EXACT_BILLING_EVIDENCE", result.packet)

    def test_all_repositories_uses_repobrain_ranking_before_live_retrieval(self) -> None:
        other_root = Path(self.temp.name) / "nutrition"
        other_root.mkdir()
        _git(other_root, "init")
        _git(other_root, "config", "user.email", "repobrain@example.invalid")
        _git(other_root, "config", "user.name", "RepoBrain Tests")
        (other_root / "README.md").write_text("# Nutrition survey registry\n", encoding="utf-8")
        _git(other_root, "add", "README.md")
        _git(other_root, "commit", "-m", "nutrition")
        other = Repository(
            id="nutrition", name="Nutrition", type="command", enabled=True,
            description="Nutrition survey registry", local_path=str(other_root),
            working_directory=str(other_root), tags=["work"],
        )
        registry = Registry([other, self.repo])
        db = AgentCenterDb(Path(self.temp.name) / "ranking.db")
        ri = RepositoryIntelligenceService(db, registry)
        brain = RepoBrainService(db, registry, ri)
        brain.build("nutrition")
        brain.build("billing")
        live = _CapturingIntelligence()
        resolver = build_default_context_resolver(
            registry=registry,
            repository_workspace=_Workspace(),
            intelligence_loader=lambda: live,
            repobrain_loader=lambda: brain,
        )
        result = resolver.resolve(ContextRequest("invoice billing", "work", scope="all"))
        self.assertEqual(live.repository_ids[0], "billing")
        self.assertIn("repobrain", result.sources_used)
        self.assertIn("repositories", result.sources_used)
        self.assertEqual(
            result.repository_evidence_origin,
            "repobrain_snapshot+repobrain_cross_repository+live_repository_retrieval",
        )

    def test_repobrain_ranking_failure_does_not_block_live_retrieval(self) -> None:
        live = _CapturingIntelligence()
        resolver = build_default_context_resolver(
            registry=self.registry,
            repository_workspace=_Workspace(),
            intelligence_loader=lambda: live,
            repobrain_loader=lambda: _BrokenRanker(),
        )
        result = resolver.resolve(ContextRequest("invoice billing", "work", scope="all"))
        self.assertEqual(live.repository_ids, ["billing"])
        self.assertIn("repositories", result.sources_used)
        self.assertEqual(result.repository_evidence_origin, "live_repository_retrieval")


if __name__ == "__main__":
    unittest.main()
