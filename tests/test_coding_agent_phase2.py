"""Coding Agent Phase 2 approved test execution contracts."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.db import AgentCenterDb
from hub.audit.store import AuditStore
from hub.climate.coding import ClimateCodingAdapter
from hub.climate.proposal_store import CodingProposalStore
from hub.climate.service import ClimateService
from hub.climate.test_execution import CodingTestExecutionService, CodingTestRunStore
from hub.registry.models import Registry, Repository
from hub.repository_workspace.security import WorkspaceSecurityError
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings


class _CodingStub:
    proposed_change = staticmethod(ClimateCodingAdapter.proposed_change)
    proposed_edits = staticmethod(ClimateCodingAdapter.proposed_edits)
    humanize_answer = staticmethod(ClimateCodingAdapter.humanize_answer)


class CodingAgentPhase2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo_root = self.root / "repo"
        (self.repo_root / "tests").mkdir(parents=True)
        (self.repo_root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo_root / "tests" / "test_app.py").write_text(
            "import unittest\nfrom pathlib import Path\n\n"
            "class AppTests(unittest.TestCase):\n"
            "    def test_repository_cwd(self):\n"
            "        self.assertTrue((Path.cwd() / 'app.py').is_file())\n",
            encoding="utf-8",
        )
        self.repo = Repository(id="repo", name="Repo", type="command", enabled=True, local_path=str(self.repo_root))
        self.registry = Registry([self.repo])
        self.workspace = RepositoryWorkspaceService(WorkspaceSettings())
        self.db = AgentCenterDb(self.root / "agent.db")
        self.proposal_store = CodingProposalStore(self.db)
        self.audit = AuditStore(self.root / "audit.jsonl")
        self.service = ClimateService(
            self.registry, self.workspace, _CodingStub(), audit_store=self.audit,
            proposal_store=self.proposal_store,
        )
        self.proposal = self.service.stage_proposal(
            "proposal-run", "work", "repo", [{"path": "app.py", "content": "value = 2\n"}],
            plan=["Change value."], requested_change="Change value", provider="codex",
            model="exact-model", execution_mode="climate_assisted",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _accept(self):
        return self.service.accept("work", self.proposal.run_id)

    def _wait(self, run_id: str, timeout: float = 8) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            row = self.service.test_result("work", run_id)
            if row["status"] != "running":
                return row
            time.sleep(0.05)
        self.fail("test run did not finish")

    def test_no_execution_without_explicit_run_and_targeted_selection(self):
        accepted = self._accept()
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8").splitlines(), ["value = 2"])
        self.assertTrue(accepted["tests_available"])
        self.assertTrue(accepted["test_profiles"][0]["targeted"])
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM coding_test_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_allowlisted_targeted_command_passes_in_repository_cwd(self):
        self._accept()
        started = self.service.run_tests("work", self.proposal.run_id, "python-unittest-targeted")
        result = self._wait(started["id"])
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(Path(result["cwd"]), self.repo_root.resolve())
        self.assertEqual(result["command"][1:3], ["-m", "unittest"])

    def test_unknown_and_unsafe_commands_are_blocked(self):
        self._accept()
        with self.assertRaises(WorkspaceSecurityError):
            self.service.run_tests("work", self.proposal.run_id, "arbitrary")
        for command in (("cmd", "/c", "echo hi"), ("python", "-m", "unittest", "tests;git push"), ("npm", "install")):
            with self.assertRaises(WorkspaceSecurityError):
                CodingTestExecutionService._validate_command(command)

    def test_timeout_and_cancellation(self):
        (self.repo_root / "tests" / "test_app.py").write_text(
            "import time, unittest\nclass Slow(unittest.TestCase):\n"
            "    def test_slow(self): time.sleep(5)\n", encoding="utf-8"
        )
        execution = CodingTestExecutionService(CodingTestRunStore(self.db), timeout_seconds=1)
        self.service.test_execution = execution
        self._accept()
        timed = self.service.run_tests("work", self.proposal.run_id, "python-unittest-targeted")
        self.assertEqual(self._wait(timed["id"])["status"], "timed_out")
        execution.timeout_seconds = 10
        started = self.service.run_tests("work", self.proposal.run_id, "python-unittest-targeted")
        self.service.cancel_tests("work", started["id"])
        self.assertEqual(self._wait(started["id"])["status"], "cancelled")

    def test_output_is_capped_redacted_and_failure_names_are_parsed(self):
        (self.repo_root / "tests" / "test_app.py").write_text(
            "import unittest\nclass Leak(unittest.TestCase):\n"
            "    def test_failure(self):\n"
            "        print('OPENAI_API_KEY=sk-12345678901234567890' + 'x'*5000)\n"
            "        self.fail('broken')\n", encoding="utf-8"
        )
        self.service.test_execution = CodingTestExecutionService(CodingTestRunStore(self.db), output_cap=1000)
        self._accept()
        started = self.service.run_tests("work", self.proposal.run_id, "python-unittest-targeted")
        result = self._wait(started["id"])
        self.assertEqual(result["status"], "failed")
        self.assertLessEqual(len(result["stdout"]), 1000)
        self.assertNotIn("sk-12345678901234567890", result["stdout"])
        self.assertIn("test_failure", result["failed_tests"])

    def test_skip_records_without_execution(self):
        self._accept()
        result = self.service.skip_tests("work", self.proposal.run_id)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["command"], [])

    def test_failed_run_can_start_follow_up_but_never_auto_applies(self):
        (self.repo_root / "tests" / "test_app.py").write_text(
            "import unittest\nclass Fail(unittest.TestCase):\n"
            "    def test_fail(self): self.fail('broken')\n", encoding="utf-8"
        )
        self._accept()
        started = self.service.run_tests("work", self.proposal.run_id, "python-unittest-targeted")
        failed = self._wait(started["id"])
        self.assertEqual(failed["status"], "failed")
        with mock.patch.object(self.service, "execute", return_value={"id": "follow-run", "status": "running", "provider": "codex", "model": "exact-model"}) as execute:
            follow = self.service.follow_up_test_failure("work", failed["id"])
        self.assertEqual(follow["id"], "follow-run")
        self.assertEqual(self.service.test_result("work", failed["id"])["follow_up_run_id"], "follow-run")
        self.assertEqual(self.service._run_meta["follow-run"]["parent_proposal_id"], self.proposal.id)
        self.assertIn("Bounded test evidence", execute.call_args.kwargs["prompt"])
        self.assertEqual((self.repo_root / "app.py").read_text(encoding="utf-8").splitlines(), ["value = 2"])


if __name__ == "__main__":
    unittest.main()
