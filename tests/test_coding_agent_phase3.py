"""Coding Agent Phase 3 explicit iterative test/fix contracts."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.db import AgentCenterDb
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.proposal_store import CodingProposalStore
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings


class _CodingStub:
    proposed_change = staticmethod(ClimateCodingAdapter.proposed_change)
    proposed_edits = staticmethod(ClimateCodingAdapter.proposed_edits)
    humanize_answer = staticmethod(ClimateCodingAdapter.humanize_answer)


class CodingAgentPhase3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo_root = self.root / "repo"
        (self.repo_root / "tests").mkdir(parents=True)
        (self.repo_root / "app.py").write_text("value = 1\n", encoding="utf-8")
        self._write_test("3")
        repo = Repository(
            id="repo", name="Repo", type="command", enabled=True,
            local_path=str(self.repo_root),
        )
        self.db = AgentCenterDb(self.root / "agent.db")
        self.service = ClimateService(
            Registry([repo]),
            RepositoryWorkspaceService(WorkspaceSettings()),
            _CodingStub(),
            proposal_store=CodingProposalStore(self.db),
        )
        self.root_proposal = self._stage("root-run", "value = 2\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_test(self, expected: str, *, constant_failure: bool = False) -> None:
        failure = "self.fail('same failure')" if constant_failure else "self.fail('found=' + actual)"
        (self.repo_root / "tests" / "test_app.py").write_text(
            "import unittest\nfrom pathlib import Path\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        actual = Path('app.py').read_text(encoding='utf-8').strip()\n"
            f"        if actual != 'value = {expected}': {failure}\n",
            encoding="utf-8",
        )

    def _stage(
        self,
        run_id: str,
        content: str,
        *,
        parent=None,
        source_test_run_id: str = "",
    ):
        return self.service.stage_proposal(
            run_id, "work", "repo", [{"path": "app.py", "content": content}],
            plan=["Update the value."], requested_change="Update value",
            provider="codex", model="exact-model", execution_mode="climate_assisted",
            parent_proposal_id=parent.id if parent else "",
            source_test_run_id=source_test_run_id,
        )

    def _wait(self, run_id: str, timeout: float = 8) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            row = self.service.test_result("work", run_id)
            if row["status"] != "running":
                return row
            time.sleep(0.05)
        self.fail("test run did not finish")

    def _test(self, proposal) -> dict:
        started = self.service.run_tests("work", proposal.run_id, "python-unittest-targeted")
        return self._wait(started["id"])

    def test_successful_one_pass_is_explicit_and_finishes_chain(self):
        self._write_test("2")
        accepted = self.service.accept("work", self.root_proposal.run_id)
        self.assertEqual(accepted["iteration"]["status"], "awaiting_test_action")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coding_test_runs").fetchone()[0], 0)
        passed = self._test(self.root_proposal)
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["iteration"]["timeline"], ["Change 1", "Tests passed"])

    def test_fail_fix_pass_links_proposals_and_test_runs(self):
        self.service.accept("work", self.root_proposal.run_id)
        failed = self._test(self.root_proposal)
        self.assertEqual(failed["status"], "failed")
        fix = self._stage("fix-run", "value = 3\n", parent=self.root_proposal, source_test_run_id=failed["id"])
        self.assertEqual(fix.root_proposal_id, self.root_proposal.id)
        self.assertEqual(fix.iteration_depth, 1)
        self.service.accept("work", fix.run_id)
        passed = self._test(fix)
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(
            passed["iteration"]["timeline"],
            ["Change 1", "Tests failed", "Fix 1", "Tests passed"],
        )
        with self.db.connect() as conn:
            child = conn.execute(
                "SELECT parent_proposal_id,source_test_run_id,root_proposal_id,iteration_depth "
                "FROM coding_edit_proposals WHERE run_id='fix-run'"
            ).fetchone()
        self.assertEqual(tuple(child), (self.root_proposal.id, failed["id"], self.root_proposal.id, 1))

    def test_failed_fix_can_be_rejected_without_write_or_test(self):
        self.service.accept("work", self.root_proposal.run_id)
        failed = self._test(self.root_proposal)
        fix = self._stage("fix-run", "value = 3\n", parent=self.root_proposal, source_test_run_id=failed["id"])
        rejected = self.service.reject("work", fix.run_id)
        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 2\n")
        self.assertEqual(rejected["iteration"]["status"], "fix_rejected")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coding_test_runs").fetchone()[0], 1)

    def test_multiple_iterations_each_require_accept_and_run_tests(self):
        self._write_test("4")
        self.service.accept("work", self.root_proposal.run_id)
        first = self._test(self.root_proposal)
        fix1 = self._stage("fix-1", "value = 3\n", parent=self.root_proposal, source_test_run_id=first["id"])
        self.service.accept("work", fix1.run_id)
        second = self._test(fix1)
        self.assertFalse(second["repeated_failure_detected"])
        fix2 = self._stage("fix-2", "value = 4\n", parent=fix1, source_test_run_id=second["id"])
        self.assertEqual(fix2.iteration_depth, 2)
        self.service.accept("work", fix2.run_id)
        final = self._test(fix2)
        self.assertEqual(final["status"], "passed")
        self.assertEqual(
            final["iteration"]["timeline"],
            ["Change 1", "Tests failed", "Fix 1", "Tests failed", "Fix 2", "Tests passed"],
        )

    def test_maximum_depth_guard_blocks_another_fix(self):
        self.service.accept("work", self.root_proposal.run_id)
        first = self._test(self.root_proposal)
        with mock.patch("hub.climate.service.MAX_CODING_ITERATION_DEPTH", 1):
            fix = self._stage("fix-1", "value = 3\n", parent=self.root_proposal, source_test_run_id=first["id"])
            self._write_test("4")
            self.service.accept("work", fix.run_id)
            failed = self._test(fix)
            with self.assertRaises(ClimateCodingError) as raised:
                self.service.follow_up_test_failure("work", failed["id"])
        self.assertEqual(raised.exception.code, "iteration_limit")
        self.assertEqual(self.service.iteration_status(self.root_proposal.id)["status"], "blocked")

    def test_repeated_failure_stops_chain(self):
        self._write_test("9", constant_failure=True)
        self.service.accept("work", self.root_proposal.run_id)
        first = self._test(self.root_proposal)
        fix = self._stage("fix-1", "value = 3\n", parent=self.root_proposal, source_test_run_id=first["id"])
        self.service.accept("work", fix.run_id)
        repeated = self._test(fix)
        self.assertTrue(repeated["repeated_failure_detected"])
        self.assertEqual(repeated["iteration"]["status"], "blocked")
        with self.assertRaises(ClimateCodingError) as raised:
            self.service.follow_up_test_failure("work", repeated["id"])
        self.assertEqual(raised.exception.code, "repeated_iteration")
        with self.assertRaises(ClimateCodingError) as blocked:
            self._stage("blocked-fix", "value = 4\n", parent=fix, source_test_run_id=repeated["id"])
        self.assertEqual(blocked.exception.code, "iteration_blocked")

    def test_repeated_proposal_stops_cyclic_patch(self):
        self.service.accept("work", self.root_proposal.run_id)
        fix = self._stage("fix-1", "value = 3\n", parent=self.root_proposal)
        self.service.accept("work", fix.run_id)
        with self.assertRaises(ClimateCodingError) as repeated_patch:
            self._stage("fix-2", "value = 2\n", parent=fix)
        self.assertEqual(repeated_patch.exception.code, "repeated_iteration")

    def test_stale_follow_up_is_not_force_applied(self):
        self.service.accept("work", self.root_proposal.run_id)
        failed = self._test(self.root_proposal)
        fix = self._stage("fix-run", "value = 3\n", parent=self.root_proposal, source_test_run_id=failed["id"])
        (self.repo_root / "app.py").write_text("value = external\n", encoding="utf-8")
        with self.assertRaises(ClimateCodingError) as raised:
            self.service.accept("work", fix.run_id)
        self.assertEqual(raised.exception.code, "proposal_conflict")
        self.assertEqual(self.service.iteration_status(self.root_proposal.id)["status"], "stale_proposal")

    def test_propose_fix_builds_bounded_context_but_does_not_apply_or_test(self):
        self.service.accept("work", self.root_proposal.run_id)
        failed = self._test(self.root_proposal)
        with mock.patch.object(
            self.service,
            "execute",
            return_value={"id": "follow-run", "status": "running", "provider": "codex"},
        ) as execute:
            run = self.service.follow_up_test_failure("work", failed["id"])
        self.assertEqual(run["id"], "follow-run")
        prompt = execute.call_args.kwargs["prompt"]
        self.assertIn("Previous approved diff (bounded)", prompt)
        self.assertIn("Bounded test evidence", prompt)
        self.assertLessEqual(len(prompt), 25_000)
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8"), "value = 2\n")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM coding_test_runs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
