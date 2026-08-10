"""Inspect-mode Repository Intelligence attachment and diagnostics."""

from __future__ import annotations

import subprocess
import shutil
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.repository_context import resolve_repository_context
from hub.agent_center.repository_intelligence import RepositoryIntelligenceService
from hub.agent_center.routing.execution import RouteExecutor
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation, RoutingSettings
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


def _inspect_rec(**kwargs: Any) -> RouteRecommendation:
    signals = list(
        kwargs.get(
            "signals",
            ["deterministic_capable", "simple_lookup", "selected_repo", "project_lookup"],
        )
    )
    c = PromptClassification(
        task_type=kwargs.get("task_type", "architecture"),
        complexity=kwargs.get("complexity", 2),
        risk="low",
        estimated_scope_files=2,
        context_size="small",
        needs_coding=bool(kwargs.get("needs_coding", False)),
        needs_testing=False,
        needs_architecture=bool(kwargs.get("needs_architecture", True)),
        deterministic_capable=True,
        signals=signals,
    )
    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent="deterministic",
        recommended_label="Deterministic",
        recommended_tier="T0",
        alternative_agent="low-cost",
        alternative_label="Low-cost",
        confidence=0.9,
        reason="inspect",
        estimated_usage="Very Low",
        approval_required=False,
        classification=c,
    )


class InspectRepositoryIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path.cwd() / "data" / "test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_root = temp_root / f"inspect-ri-{uuid.uuid4().hex}"
        self.temp_root.mkdir()
        self.root = self.temp_root / "live-processing-local"
        self.root.mkdir()
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "tests@example.invalid")
        _git(self.root, "config", "user.name", "Inspect RI Tests")
        (self.root / "AGENTS.md").write_text(
            "# Live Processing\nUse adapters. Stage and Live stay isolated.\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# live-processing-local\nCoordinates batch intake and validation.\n",
            encoding="utf-8",
        )
        (self.root / "architecture.md").write_text(
            "# Architecture\nIntake workers push validated rows into the ledger.\n",
            encoding="utf-8",
        )
        (self.root / "intake.py").write_text(
            "def validate_batch(rows):\n    return [r for r in rows if r.get('ok')]\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "AGENTS.md", "README.md", "architecture.md", "intake.py")
        _git(self.root, "commit", "-m", "initial")

        self.repo = Repository(
            id="live-processing-local",
            name="Live Processing Local",
            type="command",
            enabled=True,
            local_path=str(self.root),
            working_directory=str(self.root),
            repository_group_id="pmnp-live-processing",
        )
        self.api_repo = Repository(
            id="live-processing",
            name="PMNP Live Processing",
            type="api",
            enabled=True,
            base_url="http://example.invalid",
            repository_group_id="pmnp-live-processing",
        )
        self.registry = Registry(repositories=[self.api_repo, self.repo])
        self.db_path = self.temp_root / "agent.db"
        self.center = AgentCenterService(
            self.registry,
            store=AgentCenterStore(AgentCenterDb(self.db_path)),
            adapters=[],
        )
        self.center.repository_intelligence.scan("live-processing-local")
        self.history = RoutingHistoryStore(AgentCenterDb(self.temp_root / "routing.db"))
        self.executor = RouteExecutor(self.center, history=self.history)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_selected_repo_id_resolves_for_deterministic_inspect(self) -> None:
        selectable = [
            {
                "id": "live-processing-local",
                "name": "Live Processing Local",
                "selectable": True,
                "path": str(self.root),
            },
            {"id": "other", "name": "Other", "selectable": True, "path": str(self.root)},
        ]
        resolved = resolve_repository_context(
            agent_id="deterministic",
            repository_ids=[],
            selected_repository_id="live-processing-local",
            repositories=selectable,
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["repository_ids"], ["live-processing-local"])
        self.assertEqual(resolved["source"], "persisted_selection")

    def test_current_profile_never_reports_not_learned(self) -> None:
        status = self.center.repository_intelligence.get_status("live-processing-local")
        self.assertEqual(status["status"], "current")
        retrieved = self.center.repository_intelligence.retrieve(
            ["live-processing-local"],
            "How does batch intake validation work in this repository?",
        )
        diag = retrieved["diagnostics"]
        self.assertTrue(diag["used"])
        self.assertEqual(diag["freshness"], "current")
        self.assertNotEqual(diag["freshness"], "not_learned")
        self.assertIn("live-processing-local", diag["repository_ids"])
        for row in diag["repositories"]:
            self.assertEqual(row["status"], "current")
            self.assertNotEqual(row.get("freshness"), "not_learned")

    def test_relevant_entries_for_matching_repo_question(self) -> None:
        retrieved = self.center.repository_intelligence.retrieve(
            ["live-processing-local"],
            "How does validate_batch intake validation work?",
        )
        self.assertGreater(retrieved["item_count"], 0)
        self.assertLessEqual(retrieved["item_count"], 6)
        self.assertFalse(retrieved["include_full_index"])
        paths = {item["path"] for item in retrieved["items"]}
        self.assertTrue(paths & {"intake.py", "architecture.md", "AGENTS.md", "README.md"})

    def test_learned_repo_inspect_uses_repository_intelligence(self) -> None:
        prompt = "How does batch intake validation work in live-processing-local?"
        out = self.executor.execute(
            prompt=prompt,
            recommendation=_inspect_rec(),
            settings=RoutingSettings(
                prefer_deterministic=True,
                require_approval_before_codex=False,
            ),
            agent_override="deterministic",
            repository_ids=["live-processing-local"],
            selected_repository_id="live-processing-local",
            interaction_mode="inspect",
            workspace="work",
            actor="owner",
        )
        diag = out.get("repository_intelligence_diagnostics") or (
            (out.get("telemetry") or {}).get("repository_intelligence") or {}
        )
        self.assertTrue(diag.get("used"), msg=diag)
        self.assertIn("live-processing-local", diag.get("repository_ids") or [])
        self.assertGreater(int(diag.get("knowledge_entries_used") or 0), 0)
        self.assertEqual(diag.get("freshness"), "current")
        self.assertNotEqual(diag.get("freshness"), "not_learned")
        packet = out.get("evidence_packet") or {}
        self.assertTrue(
            packet.get("usable")
            or any(
                str(src).startswith("repository_intelligence:")
                or str(src) == "tool:repository_intelligence"
                for src in (packet.get("sources") or [])
            ),
            msg=packet,
        )

    def test_diagnostics_match_actual_execution_events(self) -> None:
        prompt = "Where is validate_batch defined for intake?"
        out = self.executor.execute(
            prompt=prompt,
            recommendation=_inspect_rec(
                task_type="lookup",
                needs_architecture=False,
                signals=[
                    "deterministic_capable",
                    "selected_repo",
                    "project_lookup",
                    "code",
                ],
            ),
            settings=RoutingSettings(
                prefer_deterministic=True,
                require_approval_before_codex=False,
            ),
            agent_override="deterministic",
            repository_ids=["live-processing-local"],
            selected_repository_id="live-processing-local",
            interaction_mode="inspect",
            context_sources=["files"],
            workspace="work",
            actor="owner",
        )
        enriched = attach_execution_telemetry(dict(out))
        tel = enriched["telemetry"]
        tools = list(tel.get("tools_used") or [])
        self.assertIn("repository_intelligence", tools)
        # Planned tool_ids alone must not invent phantom tools; RI event must be real.
        self.assertTrue(
            any(
                (isinstance(item, dict) and item.get("tool") == "repository_intelligence")
                for item in (out.get("tool_results") or [])
            )
            or any(
                str(src).startswith("tool:repository_intelligence")
                or str(src).startswith("repository_intelligence:")
                for src in ((out.get("evidence_packet") or {}).get("sources") or [])
            )
        )
        g = out.get("grounding") or {}
        self.assertIn(g.get("evidence_found_label"), {"Yes", "No"})
        self.assertNotEqual(g.get("evidence_found_label"), "Unknown")
        self.assertIn(g.get("task_solved_label"), {"Yes", "No", None})
        if g.get("task_solved_label") is not None:
            self.assertNotEqual(g.get("task_solved_label"), "Unknown")
        ri = tel.get("repository_intelligence") or {}
        self.assertTrue(ri.get("used"))
        self.assertEqual(ri.get("freshness"), "current")
        self.assertGreater(int(ri.get("knowledge_entries_used") or 0), 0)

    def test_grouped_inspect_explanation_attaches_ri_then_uses_bounded_ai(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_start_run(payload: dict[str, Any]) -> dict[str, Any]:
            captured.append(payload)
            knowledge = payload.get("repository_intelligence") or {}
            return {
                "id": "ai-synthesis-1",
                "status": "completed",
                "agent_id": "openai-api",
                "model": "low-cost-test-model",
                "answer": (
                    "The intake path validates each row before it reaches the ledger; "
                    "the repository evidence ties that behavior to intake.py and its architecture guidance."
                ),
                "usage": {
                    "input_tokens": 90,
                    "output_tokens": 32,
                    "cached_tokens": 0,
                    "total_tokens": 122,
                    "usage_source": "actual",
                },
                "context": {
                    "repository_intelligence": knowledge,
                    "evidence_packet": payload.get("evidence_packet") or {},
                    "grounding": {
                        "grounded": True,
                        "grounded_label": "Yes",
                        "task_solved": True,
                        "task_solved_label": "Yes",
                        "answer_grounded": True,
                        "evidence_found": True,
                        "evidence_found_label": "Yes",
                        "policy_violation": False,
                    },
                },
            }

        self.center.start_run = fake_start_run  # type: ignore[method-assign]
        self.executor._availability_loader = lambda: {
            "openai-api": {"status": "available", "runnable": True}
        }
        packet = {
            "usable": True,
            "hits": [{
                "source": "repo_search",
                "repository_id": "live-processing-local",
                "path": "intake.py",
                "summary": "validate_batch filters invalid intake rows before ledger writes",
            }],
            "sources": ["tool:repo_search", "repository:live-processing-local:intake.py"],
            "tool_results": [{"tool": "repo_search", "ok": True, "result": {}}],
            "errors": [],
            "summary": "bounded repository evidence",
        }
        prompt = "Explain how batch intake validation works in PMNP Live Processing."
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet", return_value=packet
        ), patch("hub.agent_center.grounding.answer_from_evidence", return_value=None):
            out = self.executor.execute(
                prompt=prompt,
                recommendation=_inspect_rec(),
                settings=RoutingSettings(
                    prefer_deterministic=True,
                    require_approval_before_codex=False,
                ),
                agent_override="deterministic",
                repository_ids=["live-processing"],
                selected_repository_id="live-processing",
                interaction_mode="inspect",
                context_sources=["files"],
                workspace="work",
                actor="owner",
            )

        self.assertEqual(out.get("status"), "completed", msg=out)
        self.assertEqual(out.get("resolved_provider"), "openai-api")
        self.assertEqual(out.get("resolved_model"), "low-cost-test-model")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].get("repository_ids"), ["live-processing-local"])
        self.assertTrue(captured[0].get("bounded_evidence_only"))
        self.assertLessEqual(
            len((captured[0].get("repository_intelligence") or {}).get("items") or []), 6
        )
        evidence = out.get("evidence_packet") or {}
        self.assertIn("tool:repository_intelligence", evidence.get("sources") or [])
        ri = (out.get("telemetry") or {}).get("repository_intelligence") or {}
        self.assertTrue(ri.get("used"), msg=ri)
        self.assertEqual(ri.get("repository_ids"), ["live-processing-local"])
        self.assertGreater(int(ri.get("knowledge_entries_used") or 0), 0)
        self.assertGreater(int(ri.get("context_chars_contributed") or 0), 0)
        self.assertTrue(out.get("context_items"))
        self.assertTrue((out.get("grounding") or {}).get("task_solved"))
        self.assertTrue((out.get("grounding") or {}).get("answer_grounded"))
        self.assertTrue((out.get("telemetry") or {}).get("llm_invoked"))


class FilesContextDoesNotDisableRiTests(unittest.TestCase):
    def test_files_context_adds_search_tools_and_keeps_ri(self) -> None:
        from hub.agent_center.routing.context import tools_for_repository_knowledge

        knowledge = {
            "profiles": [{"repository_id": "live-processing-local"}],
            "items": [
                {
                    "repository_id": "live-processing-local",
                    "path": "intake.py",
                    "category": "business_logic",
                    "summary": "validate_batch",
                }
            ],
        }
        tools = tools_for_repository_knowledge(["org_unit_lookup"], knowledge)
        self.assertIn("repo_search", tools)
        self.assertIn("read_file", tools)


if __name__ == "__main__":
    unittest.main()
