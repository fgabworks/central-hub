"""Coding Agent Phase 1 controlled proposal, safety, and persistence contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.db import AgentCenterDb
from hub.audit.store import AuditStore
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.proposal_store import CodingProposalStore
from hub.climate.service import ClimateService, MAX_PROPOSAL_FILES
from hub.registry.models import Registry, Repository
from hub.repository_workspace.security import WorkspaceSecurityError
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings


class _CodingStub:
    @staticmethod
    def proposed_change(answer: str):
        return ClimateCodingAdapter.proposed_change(answer)

    @staticmethod
    def proposed_edits(answer: str):
        return ClimateCodingAdapter.proposed_edits(answer)

    @staticmethod
    def humanize_answer(answer: str, *, task_mode="ask", prompt=""):
        return ClimateCodingAdapter.humanize_answer(answer, task_mode=task_mode, prompt=prompt)


class CodingAgentPhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo_root / "other.py").write_text("other = 1\n", encoding="utf-8")
        (self.repo_root / "vendor").mkdir()
        (self.repo_root / "vendor" / "library.py").write_text("vendor = 1\n", encoding="utf-8")
        (self.repo_root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (self.repo_root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.repo = Repository(
            id="repo", name="Repo", type="command", enabled=True, local_path=str(self.repo_root)
        )
        self.registry = Registry([self.repo])
        self.workspace = RepositoryWorkspaceService(WorkspaceSettings())
        self.db = AgentCenterDb(self.root / "agent.db")
        self.proposals = CodingProposalStore(self.db)
        self.audit = AuditStore(self.root / "audit.jsonl")
        self.service = ClimateService(
            self.registry,
            self.workspace,
            _CodingStub(),
            audit_store=self.audit,
            proposal_store=self.proposals,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _stage(self, run_id="run-1", content="value = 2\n", **kwargs):
        execution_mode = str(kwargs.pop("execution_mode", "climate_assisted"))
        return self.service.stage_proposal(
            run_id,
            "work",
            "repo",
            [{"path": "app.py", "content": content}],
            plan=["Update the configured value."],
            requested_change="Change the configured value",
            conversation_id="conversation-1",
            inspected_files=["app.py"],
            provider="codex",
            model="exact-model",
            execution_mode=execution_mode,
            evidence_provenance={"repobrain": True, "live_repository": True},
            **kwargs,
        )

    def test_proposal_generation_is_bounded_and_does_not_write(self):
        parsed = ClimateCodingAdapter.proposed_change(
            '```json\n{"plan":["Change the value"],"edits":[{"path":"app.py","content":"value = 2\\n"}]}\n```'
        )
        proposal = self.service.stage_proposal(
            "run-generated", "work", "repo", parsed["edits"], plan=parsed["plan"]
        )
        self.assertEqual(proposal.plan, ["Change the value"])
        self.assertEqual(proposal.affected_files, ["app.py"])
        self.assertIn("--- a/app.py", proposal.edits[0]["diff"])
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_accept_applies_valid_patch_and_persists_result_and_rollback(self):
        proposal = self._stage()
        stored = self.proposals.get(proposal.run_id)
        self.assertEqual(stored["state"], "pending")
        self.assertEqual(stored["rollback_snapshot"][0]["content"].splitlines(), ["value = 1"])
        result = self.service.accept("work", proposal.run_id)
        self.assertEqual(result["state"], "accepted")
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 2\n")
        stored = self.proposals.get(proposal.run_id)
        self.assertEqual(stored["decision"], "accepted")
        self.assertEqual(stored["files_changed"][0]["path"], "app.py")
        self.assertTrue(stored["resulting_state"][0]["sha256"])

    def test_reject_persists_decision_without_writing(self):
        proposal = self._stage("run-reject")
        self.assertEqual(self.service.reject("work", proposal.run_id)["state"], "rejected")
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(self.proposals.get(proposal.run_id)["decision"], "rejected")

    def test_stale_file_requires_regeneration(self):
        proposal = self._stage("run-stale")
        (self.repo_root / "app.py").write_text("value = 9\n", encoding="utf-8")
        with self.assertRaisesRegex(ClimateCodingError, "changed since proposal"):
            self.service.accept("work", proposal.run_id)
        self.assertEqual(self.proposals.get(proposal.run_id)["state"], "conflict")
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 9\n")

    def test_path_traversal_and_excluded_paths_are_rejected(self):
        with self.assertRaises(ClimateCodingError) as traversal:
            self.service.stage_proposal(
                "run-traversal", "work", "repo", [{"path": "../escape.py", "content": "x = 1\n"}]
            )
        self.assertEqual(traversal.exception.code, "path_invalid")
        with self.assertRaises(ClimateCodingError) as excluded:
            self.service.stage_proposal(
                "run-vendor", "work", "repo", [{"path": "vendor/library.py", "content": "x = 1\n"}]
            )
        self.assertEqual(excluded.exception.code, "path_excluded")
        with self.assertRaises(WorkspaceSecurityError):
            self.service.stage_proposal(
                "run-secret", "work", "repo", [{"path": ".env", "content": "TOKEN=x\n"}]
            )
        with self.assertRaises(WorkspaceSecurityError):
            self.service.stage_proposal(
                "run-binary", "work", "repo", [{"path": "image.png", "content": "not binary\n"}]
            )

    def test_specific_repository_scope_is_required(self):
        with self.assertRaises(ClimateCodingError) as caught:
            self.service.stage_proposal(
                "run-general", "work", "repo", [{"path": "app.py", "content": "value = 2\n"}],
                context_scope="general",
            )
        self.assertEqual(caught.exception.code, "repository_scope_required")

    def test_direct_mode_remains_proposal_only_until_accept(self):
        proposal = self._stage("run-direct", execution_mode="direct")
        self.assertEqual(proposal.execution_mode, "direct")
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.service.reject("work", proposal.run_id)
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_file_count_limit_is_enforced(self):
        edits = [{"path": f"file-{index}.py", "content": "x = 1\n"} for index in range(MAX_PROPOSAL_FILES + 1)]
        with self.assertRaises(ClimateCodingError) as caught:
            self.service.stage_proposal("run-large", "work", "repo", edits)
        self.assertEqual(caught.exception.code, "proposal_too_large")

    def test_aggregate_patch_size_limit_is_enforced(self):
        with mock.patch("hub.climate.service.MAX_PROPOSAL_PATCH_CHARS", 20):
            with self.assertRaises(ClimateCodingError) as caught:
                self.service.stage_proposal(
                    "run-patch-large",
                    "work",
                    "repo",
                    [{"path": "app.py", "content": "value = 'a much larger replacement'\n"}],
                )
        self.assertEqual(caught.exception.code, "proposal_too_large")

    def test_persisted_proposal_reloads_with_audit_metadata(self):
        proposal = self._stage("run-persisted")
        restarted = ClimateService(
            self.registry,
            self.workspace,
            _CodingStub(),
            audit_store=self.audit,
            proposal_store=CodingProposalStore(AgentCenterDb(self.root / "agent.db")),
        )
        loaded = restarted._require_proposal("work", proposal.run_id)
        self.assertEqual(loaded.requested_change, "Change the configured value")
        self.assertEqual(loaded.inspected_files, ["app.py"])
        self.assertEqual(loaded.provider, "codex")
        self.assertEqual(loaded.model, "exact-model")
        self.assertEqual(loaded.evidence_provenance, {"repobrain": True, "live_repository": True})
        events = self.audit.list_recent()
        self.assertEqual(events[0]["action"], "coding_proposal_created")

    def test_crlf_is_preserved_on_accept(self):
        (self.repo_root / "app.py").write_bytes(b"value = 1\r\n")
        proposal = self._stage("run-crlf", content="value = 2\n")
        self.service.accept("work", proposal.run_id)
        self.assertEqual((self.repo_root / "app.py").read_bytes(), b"value = 2\r\n")


if __name__ == "__main__":
    unittest.main()
