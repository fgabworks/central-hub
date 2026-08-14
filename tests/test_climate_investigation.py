"""CLIMATE investigation targeting, search telemetry, and graph-hint eval."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.climate.coding import ClimateCodingAdapter
from hub.climate.context_resolver import _qualify_source_rows, resolve_climate_context
from hub.climate.domain_query import extract_domain_query, score_source
from hub.climate.investigation_metrics import (
    has_invalid_windows_search_glob,
    summarize_tool_activity,
)
from hub.climate.repo_graph import build_python_index, concept_file_hints
from hub.climate.token_efficiency import count_files_inspected
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter
from hub.climate.service import ClimateService


FIC_PROMPT = "What is Fully immunized Child"
ANC_PROMPT = "Give me the logic of the ANC"
PNC_PROMPT = "Give me the logic of the PNC"


def _write_fixture(root: Path) -> None:
    (root / "lookup" / "immunization").mkdir(parents=True)
    (root / "lookup" / "convergence").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "lookup" / "immunization" / "derive_fic.py").write_text(
        "CH_FIC = '1'\nFIC_STATUS = 'ok'\n\ndef derive_fic(ctx):\n    return ctx\n",
        encoding="utf-8",
    )
    (root / "lookup" / "child_age_correction.py").write_text(
        "def correct_child_age(member):\n    return member['age']\n",
        encoding="utf-8",
    )
    (root / "docs" / "child.md").write_text("Child household notes.\n", encoding="utf-8")
    (root / "lookup" / "convergence" / "derive_anc.py").write_text(
        "def derive_anc_score(ctx):\n    return ctx\n",
        encoding="utf-8",
    )
    (root / "lookup" / "convergence" / "derive_pnc.py").write_text(
        "def derive_pnc_four(ctx):\n    return ctx\n",
        encoding="utf-8",
    )
    (root / "lookup" / "immunization" / "classify.py").write_text(
        "from lookup.immunization.derive_fic import derive_fic\n\n"
        "def classify_fic(row):\n    return derive_fic(row)\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Agents\nCite repository files.\n", encoding="utf-8")


class DomainQueryTests(unittest.TestCase):
    def test_fic_acronym_and_aliases(self):
        query = extract_domain_query(FIC_PROMPT)
        self.assertIn("FIC", query.acronyms)
        self.assertTrue(any("CH_FIC" == item or item.endswith("CH_FIC") for item in query.aliases + query.acronyms))
        self.assertIn("CH_FIC", query.aliases)
        self.assertIn("FIC_STATUS", query.aliases)
        self.assertIn("immunized", query.strong)
        self.assertIn("immunization", query.strong + query.aliases)
        self.assertNotIn("child", query.strong)

    def test_generic_terms_score_lower_than_exact_concepts(self):
        query = extract_domain_query(FIC_PROMPT)
        fic = score_source(
            "lookup/immunization/derive_fic.py",
            "def derive_fic(ctx):\n    FIC_STATUS = 1\n",
            query,
        )
        child = score_source(
            "lookup/child_age_correction.py",
            "def correct_child_age(member):\n    return member\n",
            query,
        )
        self.assertGreater(fic, child)
        self.assertGreater(fic, child * 5)


class ResolverTargetingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "work"
        self.root.mkdir()
        _write_fixture(self.root)
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.root)),
        ])
        self.repo_service = RepositoryWorkspaceService(WorkspaceSettings())
        self.repo = self.registry.get("work-repo")

    def tearDown(self):
        self.temp.cleanup()

    def test_fic_prioritizes_immunization_over_generic_child_files(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt=FIC_PROMPT,
            provider="codex",
            model="m",
            repository_agent=True,
        )
        self.assertTrue(resolved.ok)
        authoritative = list(resolved.diagnostics.get("authoritative_sources") or [])
        self.assertTrue(any("derive_fic.py" in path for path in authoritative), authoritative)
        qualification_paths = [row["path"] for row in resolved.diagnostics["qualification"]]
        self.assertTrue(any("derive_fic.py" in path for path in qualification_paths))
        child_paths = [path for path in qualification_paths if "child_age" in path]
        if child_paths:
            child_rank = next(i for i, row in enumerate(resolved.diagnostics["qualification"]) if "child_age" in row["path"])
            fic_rank = next(i for i, row in enumerate(resolved.diagnostics["qualification"]) if "derive_fic.py" in row["path"])
            self.assertLess(fic_rank, child_rank)
            child_row = next(row for row in resolved.diagnostics["qualification"] if "child_age" in row["path"])
            self.assertFalse(child_row["accepted"])
        else:
            self.assertFalse(any("child_age" in path for path in authoritative))
        self.assertIn("FIC", resolved.diagnostics.get("domain_terms", {}).get("acronyms") or [])
        self.assertTrue(any("FIC" in str(q) or "immun" in str(q).lower() for q in resolved.diagnostics.get("resolver_queries") or []))

    def test_acronym_alias_matching_qualifies_fic_symbols(self):
        rows = [
            {"path": "lookup/child_age_correction.py", "content": "def correct_child_age(m): return m\n", "score": 20, "reason": "search:content"},
            {"path": "lookup/immunization/derive_fic.py", "content": "FIC_STATUS=1\ndef derive_fic(ctx): return ctx\n", "score": 4, "reason": "search:filename"},
        ]
        authoritative, diagnostics = _qualify_source_rows(rows, prompt=FIC_PROMPT)
        self.assertEqual(authoritative[0]["path"], "lookup/immunization/derive_fic.py")
        by_path = {row["path"]: row for row in diagnostics}
        self.assertTrue(by_path["lookup/immunization/derive_fic.py"]["accepted"])
        self.assertIn("derive_fic", by_path["lookup/immunization/derive_fic.py"]["functions"])

    def test_weak_resolver_still_invokes_codex_investigation(self):
        coding = FakeCodingAdapter()
        service = ClimateService(self.registry, self.repo_service, coding)
        result = service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Explain quantum foam topology xyzzy-no-match",
        )
        self.assertTrue(result.get("provider_invoked"))
        self.assertTrue(coding.calls[0].get("repository_investigation"))
        self.assertIn("independently search", coding.calls[0]["prompt"])


class SearchCommandTests(unittest.TestCase):
    def test_windows_search_commands_avoid_invalid_wildcards(self):
        self.assertTrue(has_invalid_windows_search_glob("rg -n FIC tests *.py"))
        self.assertTrue(has_invalid_windows_search_glob("rg -n FIC lookup/test_immunization*"))
        self.assertFalse(has_invalid_windows_search_glob("rg -n FIC --glob '*.py' lookup/immunization"))
        self.assertFalse(has_invalid_windows_search_glob("rg -n --glob '!**/test_*' FIC"))

    def test_failed_searches_are_recorded_and_not_inspections(self):
        summary = summarize_tool_activity([
            {
                "type": "command_execution",
                "name": "rg -n FIC lookup/test_immunization*",
                "status": "failed",
                "detail": "Cannot find path 'lookup/test_immunization*' because it does not exist.",
            },
            {
                "type": "command_execution",
                "name": "rg -n FIC --glob '*.py'",
                "status": "completed",
                "detail": "lookup/immunization/derive_fic.py:1:def derive_fic\nlookup/child_age_correction.py:4:child",
            },
            {
                "type": "command_execution",
                "name": "Get-Content lookup/immunization/derive_fic.py",
                "status": "completed",
                "detail": "def derive_fic(ctx):\n    return ctx\n",
            },
            {
                "type": "command_execution",
                "name": "Get-Content lookup/immunization/derive_fic.py",
                "status": "completed",
                "detail": "duplicate read",
            },
        ])
        self.assertEqual(summary.failed_searches, 1)
        self.assertEqual(summary.successful_searches, 1)
        self.assertGreaterEqual(summary.invalid_windows_globs, 1)
        self.assertEqual(summary.search_matched_files, 2)
        self.assertEqual(summary.files_inspected, 1)
        self.assertNotEqual(summary.search_matched_files, summary.files_inspected)
        self.assertEqual(summary.inspected_paths, ["lookup/immunization/derive_fic.py"])
        self.assertEqual(
            count_files_inspected([
                {
                    "type": "command_execution",
                    "name": "rg -n FIC lookup/a.py",
                    "status": "completed",
                    "detail": "lookup/a.py:1:x\nlookup/b.py:1:y",
                },
                {
                    "type": "command_execution",
                    "name": "Get-Content lookup/a.py",
                    "status": "completed",
                    "detail": "x",
                },
            ]),
            1,
        )


class GraphEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write_fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_local_graph_hints_fic_anc_pnc_without_graphify(self):
        index = build_python_index(self.root)
        fic = concept_file_hints(self.root, FIC_PROMPT, limit=6)
        anc = concept_file_hints(self.root, ANC_PROMPT, limit=6)
        pnc = concept_file_hints(self.root, PNC_PROMPT, limit=6)
        self.assertTrue(fic)
        self.assertTrue(any("derive_fic.py" in row["path"] for row in fic))
        self.assertTrue(any("derive_anc.py" in row["path"] for row in anc))
        self.assertTrue(any("derive_pnc.py" in row["path"] for row in pnc))
        self.assertTrue(index)
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("graphify", requirements.lower())
        self.assertNotIn("graphifyy", requirements.lower())


class InvestigationPromptTests(unittest.TestCase):
    def test_ask_prompt_includes_progressive_windows_safe_search(self):
        class StubCenter:
            def __init__(self):
                self.payload = None

            def start_run(self, payload):
                self.payload = payload
                return {
                    "id": "r1", "status": "running", "agent_id": "codex", "model": "m",
                    "answer": "", "logs": "", "usage": {},
                }

        center = StubCenter()
        adapter = ClimateCodingAdapter(center)
        adapter.availability = lambda provider=None, refresh=False: (  # type: ignore[method-assign]
            {
                "id": "codex", "state": "connected", "status": "Connected",
                "detail": "", "account_label": "",
                "capabilities": {"native_repository_investigation": True},
            } if provider else []
        )
        adapter.execute(
            workspace="work", repository_id="work-repo", provider="codex", model="m",
            prompt="CLIMATE context packet (ASK).\nTask:\nWhat is Fully immunized Child\nConfidence: low",
            task_mode="ask", repository_investigation=True,
        )
        packed = center.payload["prompt"]
        self.assertIn("Search progressively", packed)
        self.assertIn("tests *.py", packed)
        self.assertIn("failed command is not a successful inspection", packed)
        adapter.execute(
            workspace="work", repository_id="work-repo", provider="codex", model="m",
            prompt="Fix ANC Binary", task_mode="edit",
        )
        self.assertNotIn("Search progressively", center.payload["prompt"])
