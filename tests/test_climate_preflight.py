"""Focused tests for CLIMATE deterministic Context Resolver."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.climate.context_resolver import (
    GATE_MESSAGE,
    MAX_PACKET_CHARS,
    _confidence,
    _qualify_source_rows,
    resolve_climate_context,
    select_applicable_instructions,
    select_relevant_skill_sections,
)
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


class SkillAndInstructionSelectionTests(unittest.TestCase):
    def test_implementation_ranking_qualifies_code_not_docs_or_tests(self):
        rows = [
            {"path": "docs/anc.md", "content": "ANC logic and derive_anc_score", "score": 30, "reason": "search:content"},
            {"path": "lookup/test_anc.py", "content": "def test_anc_score(): pass", "score": 40, "reason": "search:content"},
            {"path": "lookup/derive.py", "content": "def derive_anc_score(ctx):\n    return ctx\n", "score": 8, "reason": "expand:content"},
        ]
        authoritative, diagnostics = _qualify_source_rows(
            rows,
            prompt="Give me the ANC implementation logic and exact function",
        )
        self.assertEqual([row["path"] for row in authoritative], ["lookup/derive.py"])
        by_path = {row["path"]: row for row in diagnostics}
        self.assertEqual(by_path["docs/anc.md"]["reason"], "documentation_support_only")
        self.assertEqual(by_path["lookup/test_anc.py"]["reason"], "test_support_only")
        self.assertTrue(by_path["lookup/derive.py"]["accepted"])
        self.assertEqual(_confidence(authoritative_rows=authoritative), "medium")

    def test_agents_and_provider_instructions_selected(self):
        items = [
            {"path": "AGENTS.md", "content": "Always cite repository files."},
            {"path": "CODEX.md", "content": "Codex-specific notes about sandbox."},
            {"path": "CLAUDE.md", "content": "Claude-specific notes."},
            {"path": "UNRELATED.md", "content": "totally unrelated shipping docs"},
        ]
        chosen = select_applicable_instructions(
            items, prompt="Explain sandbox policy", provider="codex"
        )
        paths = [row["path"] for row in chosen]
        self.assertIn("AGENTS.md", paths)
        self.assertIn("CODEX.md", paths)
        self.assertNotIn("CLAUDE.md", paths)

    def test_nearest_scoped_instructions_preferred(self):
        items = [
            {"path": "AGENTS.md", "content": "Root guidance for the whole repo."},
            {"path": "pkg/AGENTS.md", "content": "Nearest pkg-specific guidance."},
            {"path": "CODEX.md", "content": "Provider notes."},
        ]
        chosen = select_applicable_instructions(
            items,
            prompt="Explain anc_binary helper",
            provider="codex",
            current_file="pkg/logic.py",
        )
        paths = [row["path"] for row in chosen]
        self.assertTrue(paths)
        self.assertEqual(paths[0], "pkg/AGENTS.md")
        # Root AGENTS may remain as a short deduped pointer, not a full dump duplicate.
        root = next((row for row in chosen if row["path"] == "AGENTS.md"), None)
        if root is not None:
            self.assertTrue(root.get("deduped") or len(str(root.get("content") or "")) < 200)

    def test_relevant_skill_sections_only(self):
        skills = (
            "# Skills\n\n"
            "## ANC Binary\n"
            "description: Explain visit threshold derivation\n"
            "triggers: anc, binary, visit\n"
            "paths: docs/anc.md\n"
            "modes: ask, edit\n"
            "capabilities: explain\n\n"
            "## Deploy Shipping\n"
            "description: Push containers to prod\n"
            "triggers: deploy, shipping\n"
            "modes: edit\n"
        )
        chosen = select_relevant_skill_sections(
            skills,
            prompt="How is ANC Binary derived?",
            path="SKILLS.md",
            current_file="docs/anc.md",
            search_paths=["docs/anc.md"],
            task_mode="ask",
        )
        names = [row["name"] for row in chosen]
        self.assertEqual(names, ["ANC Binary"])
        self.assertTrue(all("Deploy" not in row["content"] for row in chosen))


class ClimateContextResolverGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / "work"
        self.work.mkdir()
        (self.work / "AGENTS.md").write_text("# Agents\nCite files.\n", encoding="utf-8")
        (self.work / "SKILLS.md").write_text(
            "# Skills\n\n"
            "## ANC Binary\n"
            "description: Visit thresholds\n"
            "triggers: anc, binary\n"
            "paths: docs/anc.md\n"
            "modes: ask, edit\n\n"
            "## Shipping\n"
            "description: Containers\n"
            "triggers: shipping, deploy\n",
            encoding="utf-8",
        )
        (self.work / "docs").mkdir()
        (self.work / "docs" / "anc.md").write_text(
            "ANC Binary is derived from visit thresholds.\n",
            encoding="utf-8",
        )
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        nested = self.work / "pkg"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("# Nested\nPkg rules.\n", encoding="utf-8")
        (nested / "logic.py").write_text("def anc_binary():\n    return 1\n", encoding="utf-8")
        (nested / "scoring.py").write_text(
            "def derive_anc_score(ctx):\n    return anc_rule(ctx)\n\n"
            "def anc_rule(ctx):\n    return bool(ctx)\n",
            encoding="utf-8",
        )
        (self.work / "docs" / "anc.md").write_text(
            "ANC Binary uses `derive_anc_score` in `pkg/scoring.py`.\n",
            encoding="utf-8",
        )
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work)),
        ])
        self.repo_service = RepositoryWorkspaceService(WorkspaceSettings())
        self.coding = FakeCodingAdapter()
        self.service = ClimateService(self.registry, self.repo_service, self.coding)
        self.repo = self.registry.get("work-repo")

    def tearDown(self):
        self.temp.cleanup()

    def test_no_evidence_zero_provider_call_for_packet_only_provider(self):
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
        self.assertEqual(result["status"], "completed")
        self.assertIn(GATE_MESSAGE, result["answer"])
        self.assertEqual((result.get("usage") or {}).get("total_tokens"), 0)
        self.assertIn("No model invoked · 0 tokens", result["logs"])
        self.assertEqual((result.get("preflight") or {}).get("confidence"), "low")
        polled = self.service.result("work", result["id"])
        self.assertEqual(polled["answer"], GATE_MESSAGE)
        self.assertEqual((polled.get("usage") or {}).get("total_tokens"), 0)

    def test_low_confidence_codex_is_invoked_to_investigate_repository(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Explain quantum foam topology xyzzy-no-match",
        )
        self.assertTrue(result.get("provider_invoked"))
        self.assertEqual(len(self.coding.calls), 1)
        call = self.coding.calls[0]
        self.assertTrue(call.get("repository_investigation"))
        self.assertEqual(result["preflight"]["confidence"], "low")
        self.assertIn("starting hints", call["prompt"])

    def test_grounded_ask_invokes_provider_with_bounded_packet(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Explain how ANC Binary is derived",
            current_file="docs/anc.md",
        )
        self.assertTrue(result.get("provider_invoked"))
        self.assertEqual(len(self.coding.calls), 1)
        call = self.coding.calls[-1]
        self.assertEqual(call["task_mode"], "ask")
        self.assertIn("CLIMATE context packet (ASK)", call["prompt"])
        self.assertIn("docs/anc.md", call["prompt"])
        self.assertIn("AGENTS.md", call["prompt"])
        self.assertNotIn("return anc_rule(ctx)", call["prompt"])
        self.assertNotIn("Push containers", call["prompt"])
        self.assertLessEqual(len(call["prompt"]), 24_000)
        self.assertTrue(call["evidence_packet"]["usable"])
        self.assertTrue(call["evidence_packet"]["hits"])
        self.assertTrue(all(hit.get("path", "").endswith(".py") for hit in call["evidence_packet"]["hits"]))
        self.assertIn("ANC Binary", ",".join(result["preflight"]["skills_used"]))
        self.assertEqual(result["preflight"]["confidence"], "high")

    def test_edit_capability_after_evidence(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Fix app.py to set value = 9",
            current_file="app.py",
        )
        self.assertTrue(result.get("provider_invoked"))
        call = self.coding.calls[-1]
        self.assertEqual(call["task_mode"], "edit")
        self.assertIn("CLIMATE context packet (EDIT)", call["prompt"])
        self.assertIn("app.py", call["prompt"])

    def test_nested_agents_resolution(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt="Explain anc_binary helper",
            provider="codex",
            model="m",
            current_file="pkg/logic.py",
            selected_files=["pkg/logic.py"],
        )
        self.assertTrue(resolved.ok)
        self.assertEqual(resolved.confidence, "high")
        self.assertTrue(
            any(path.endswith("pkg/AGENTS.md") or path == "pkg/AGENTS.md" for path in resolved.instruction_files)
        )
        self.assertIn("pkg/logic.py", resolved.source_files)
        self.assertIn("Matching skill", "\n".join(resolved.activity))

    def test_weak_docs_expand_once_to_authoritative_implementation(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt="Give me the logic of the ANC and cite exact implementation functions",
            provider="codex",
            model="m",
            current_file="docs/anc.md",
        )
        self.assertTrue(resolved.ok)
        self.assertLessEqual(resolved.activity.count("Expanding local search"), 1)
        self.assertIn("pkg/scoring.py", resolved.diagnostics["authoritative_sources"])
        scoring = next(
            row for row in resolved.diagnostics["qualification"]
            if row["path"] == "pkg/scoring.py"
        )
        self.assertTrue(scoring["accepted"])
        self.assertIn("derive_anc_score", scoring["functions"])
        self.assertEqual(resolved.confidence, "high")

    def test_diagnostics_and_packet_are_bounded(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt="Give me the logic of the ANC and cite exact implementation functions",
            provider="codex",
            model="m",
            current_file="docs/anc.md",
        )
        self.assertTrue(resolved.ok)
        self.assertLessEqual(resolved.context_chars, MAX_PACKET_CHARS)
        self.assertEqual(resolved.context_tokens_est, (resolved.context_chars + 3) // 4)
        self.assertGreater(resolved.diagnostics["candidates_found"], 0)
        self.assertTrue(resolved.diagnostics["authoritative_sources"])
        self.assertTrue(resolved.diagnostics["provider_invoked"])
        log = resolved.activity_log()
        self.assertIn("function/symbol=", log)
        self.assertIn("accepted=", log)
        self.assertIn("current_run_tokens=0", log)

    def test_completed_run_diagnostics_include_qualification_and_current_tokens(self):
        started = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Give me the logic of the ANC and cite exact implementation functions",
            current_file="docs/anc.md",
        )
        self.coding._answers[started["id"]] = {
            "status": "completed",
            "answer": "Grounded answer from pkg/scoring.py derive_anc_score.",
            "logs": "[turn.completed]",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }
        result = self.service.result("work", started["id"])
        self.assertIn("candidates_found=", result["logs"])
        self.assertIn("authoritative_sources=", result["logs"])
        self.assertIn("function/symbol=", result["logs"])
        self.assertIn("current_run_tokens=18", result["logs"])

    def test_provider_switch_uses_compact_handoff_flag(self):
        result = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="m",
            prompt="Explain how ANC Binary is derived",
            current_file="docs/anc.md",
            handoff=True,
            reuse_session=False,
        )
        self.assertTrue(result.get("provider_invoked"))
        call = self.coding.calls[-1]
        self.assertTrue(call.get("handoff"))
        self.assertFalse(call.get("reuse_session"))
        self.assertIn("cross-provider handoff", call["prompt"].lower())
        self.assertIn("compact prior summary", call["prompt"].lower())
        self.assertNotIn("[CLIMATE cross-provider handoff]", call["prompt"])


class ClimateContextResolverUiMarkers(unittest.TestCase):
    def test_activity_recognizes_resolver_steps(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        for marker in (
            "Resolving repo",
            "Loading instructions",
            "Matching skill",
            "Searching repo",
            "Building context",
            "No model invoked",
            "Not enough repository evidence",
            "provider_invoked",
            "preflightDiagnostics",
            "compactHandoffPrompt",
            "thread\\.started|turn\\.started|turn\\.completed",
            "normal chat body comes exclusively",
        ):
            self.assertIn(marker, script)


if __name__ == "__main__":
    unittest.main()
