"""RepoBrain Phase 2 cross-repository persistence and AiriX integration."""

from __future__ import annotations

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
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, shell=False,
    )
    return result.stdout.strip()


class _Workspace:
    def availability(self, _repo):
        return {"available": True}


class _LiveIntelligence:
    def __init__(self) -> None:
        self.repository_ids: list[str] = []

    def retrieve(self, repository_ids, query, **_kwargs):
        self.repository_ids = list(repository_ids)
        return {"items": [{
            "repository_id": repository_id,
            "path": "live_exact.py",
            "summary": f"Exact live evidence for {query}",
            "score": 8.0,
        } for repository_id in self.repository_ids[:2]]}


class RepoBrainCrossRepositoryTests(unittest.TestCase):
    UID = "AbCdEf12345"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.roots = {
            "processing": base / "processing",
            "reporting": base / "reporting",
            "data_script": base / "data-script",
        }
        files = {
            "processing": {
                "README.md": "# Processing\nProduces convergence results for downstream reporting.\n",
                "convergence_processing.py": (
                    "import os\nDHIS2_UID = 'AbCdEf12345'\n"
                    "CONFIG = os.getenv('PMNP_CONVERGENCE_MODE')\n"
                    "class ConvergenceService:\n"
                    "    def derive_convergence(self):\n        return DHIS2_UID\n"
                ),
                "tests/test_convergence.py": "def test_convergence_service():\n    assert True\n",
            },
            "reporting": {
                "README.md": "# Report Template\nReports convergence results from processing.\n",
                "reports/convergence_report.py": (
                    "import os\nDHIS2_UID = 'AbCdEf12345'\n"
                    "CONFIG = os.environ.get('PMNP_CONVERGENCE_MODE')\n"
                    "class ConvergenceReport:\n"
                    "    def render_indicator(self):\n        return DHIS2_UID\n"
                ),
            },
            "data_script": {
                "README.md": "# Data Script\nTransforms nutrition extracts for downstream queries.\n",
                "transform/convergence_transform.py": (
                    "DHIS2_UID = 'AbCdEf12345'\n"
                    "def transform_convergence_export():\n    return DHIS2_UID\n"
                ),
                "queries/convergence_query.sql": "SELECT 'AbCdEf12345' AS convergence_uid;\n",
            },
        }
        repositories = []
        for repository_id, root in self.roots.items():
            root.mkdir()
            _git(root, "init")
            _git(root, "config", "user.email", "cross@example.invalid")
            _git(root, "config", "user.name", "Cross RepoBrain Tests")
            for relative, content in files[repository_id].items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "initial")
            repositories.append(Repository(
                id=repository_id,
                name=repository_id.replace("_", " ").title(),
                type="command",
                enabled=True,
                description=files[repository_id]["README.md"],
                local_path=str(root),
                working_directory=str(root),
                tags=["work"],
            ))
        self.registry = Registry(repositories)
        self.db_path = base / "agent.db"
        self.db = AgentCenterDb(self.db_path)
        self.intelligence = RepositoryIntelligenceService(self.db, self.registry)
        self.service = RepoBrainService(self.db, self.registry, self.intelligence)
        for repository_id in self.roots:
            self.service.build(repository_id)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _change_processing(self) -> None:
        path = self.roots["processing"] / "convergence_processing.py"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\ndef calculate_convergence_score():\n    return 1\n",
            encoding="utf-8",
        )
        _git(self.roots["processing"], "add", "convergence_processing.py")
        _git(self.roots["processing"], "commit", "-m", "change convergence score")

    def test_relationship_creation_schema_and_persistence(self) -> None:
        built = self.service.build_cross_snapshot()
        self.assertEqual(built["version"], 1)
        self.assertEqual(built["build_mode"], "full")
        relationships = built["relationships"]
        self.assertTrue(relationships)
        types = {row["relationship_type"] for row in relationships}
        self.assertIn("shares_identifier", types)
        self.assertIn("shares_config", types)
        self.assertIn("reports_on", types)
        self.assertIn("transforms", types)
        relation = next(row for row in relationships if row["relationship_type"] == "shares_identifier")
        for key in (
            "source_repository", "target_repository", "source_files", "source_symbols",
            "target_files", "target_symbols", "relationship_type", "business_concept",
            "confidence", "source_references", "snapshot_versions",
        ):
            self.assertIn(key, relation)
        self.assertEqual(relation["business_concept"], self.UID)

        reopened = RepoBrainService(
            AgentCenterDb(self.db_path), self.registry,
            RepositoryIntelligenceService(AgentCenterDb(self.db_path), self.registry),
        )
        self.assertEqual(reopened.latest_cross_snapshot()["id"], built["id"])
        self.assertEqual(len(reopened.cross_history()), 1)

    def test_unchanged_reuse_and_incremental_recompute(self) -> None:
        first = self.service.build_cross_snapshot()
        reused = self.service.build_cross_snapshot()
        self.assertEqual(reused["id"], first["id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["refresh"]["relationships_recomputed"], 0)

        self._change_processing()
        refreshed = self.service.build_cross_snapshot()
        self.assertEqual(refreshed["version"], 2)
        self.assertEqual(refreshed["build_mode"], "incremental")
        self.assertEqual(refreshed["affected_repositories"], ["processing"])
        self.assertGreater(refreshed["refresh"]["relationships_recomputed"], 0)
        self.assertGreater(refreshed["refresh"]["relationships_reused"], 0)

        rebuilt = self.service.full_rebuild_cross_snapshot()
        self.assertEqual(rebuilt["version"], 3)
        self.assertEqual(rebuilt["build_mode"], "full")
        self.assertEqual(set(rebuilt["affected_repositories"]), set(self.roots))
        self.assertEqual(rebuilt["refresh"]["relationships_reused"], 0)

    def test_stale_relationship_detection(self) -> None:
        first = self.service.build_cross_snapshot()
        self._change_processing()
        stale = self.service.get_cross_snapshot(refresh=False)
        self.assertEqual(stale["id"], first["id"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["status"], "stale")

    def test_multi_repository_ranking_uses_relationships(self) -> None:
        self.service.build_cross_snapshot()
        ranked = self.service.rank_repositories_cross(
            list(self.roots), "Which reporting logic represents convergence processing?"
        )
        ids = [row["repository_id"] for row in ranked]
        self.assertIn("processing", ids[:2])
        self.assertIn("reporting", ids[:2])
        self.assertTrue(any(row["cross_repository_score"] > 0 for row in ranked))

    def test_specific_scope_relationships_are_orientation_only(self) -> None:
        self.service.build_cross_snapshot()
        resolver = build_default_context_resolver(
            registry=self.registry,
            repository_workspace=_Workspace(),
            intelligence_loader=lambda: self.intelligence,
            repobrain_loader=lambda: self.service,
            context_loader=lambda **kwargs: SimpleNamespace(
                ok=True,
                packet=f"LIVE_ONLY:{kwargs['repo'].id}",
                source_files=["convergence_processing.py"],
            ),
        )
        result = resolver.resolve(ContextRequest(
            "convergence report", "work", scope="repository", repository_id="processing"
        ))
        self.assertIn("repobrain_cross", result.sources_used)
        self.assertIn("repositories", result.sources_used)
        self.assertIn("Related repositories are orientation only", result.packet)
        self.assertIn("LIVE_ONLY:processing", result.packet)
        cross_ref = next(
            row for row in result.evidence_references if row["source_id"] == "repobrain_cross"
        )
        self.assertTrue(cross_ref["metadata"]["orientation_only"])
        self.assertEqual(cross_ref["metadata"]["anchor_repository_id"], "processing")
        self.assertIn("repobrain_cross_repository", result.repository_evidence_origins)

    def test_all_scope_cross_orientation_precedes_live_verification(self) -> None:
        self.service.build_cross_snapshot()
        live = _LiveIntelligence()
        resolver = build_default_context_resolver(
            registry=self.registry,
            repository_workspace=_Workspace(),
            intelligence_loader=lambda: live,
            repobrain_loader=lambda: self.service,
        )
        result = resolver.resolve(ContextRequest(
            "convergence reporting processing", "work", scope="all"
        ))
        self.assertIn("processing", live.repository_ids[:2])
        self.assertIn("reporting", live.repository_ids[:2])
        self.assertIn("repobrain_cross", result.sources_used)
        self.assertIn("repositories", result.sources_used)
        self.assertIn("live_repository_retrieval", result.repository_evidence_origins)
        self.assertIn("repobrain_cross_repository", result.repository_evidence_origins)

    def test_cross_context_output_is_bounded(self) -> None:
        bounded = RepoBrainService(
            self.db, self.registry, self.intelligence,
            settings=RepoBrainSettings(max_cross_context_chars=700),
        )
        bounded.build_cross_snapshot(full_rebuild=True)
        context = bounded.cross_context("convergence", refresh=False)
        self.assertLessEqual(len(context["content"]), 700)
        self.assertLessEqual(len(context["relationships"]), 24)


if __name__ == "__main__":
    unittest.main()
