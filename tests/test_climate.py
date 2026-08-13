"""CLIMATE Code Workspace v1 contracts."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from hub.climate.coding import ClimateCodingError
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings


class FakeCodingAdapter:
    def __init__(self) -> None:
        self.calls = []
        self.cancelled = []

    def availability(self):
        return [
            {"id": "codex", "state": "connected", "status": "Connected"},
            {"id": "claude-code", "state": "authentication_required", "status": "Authentication Required"},
            {"id": "cursor-agent", "state": "unavailable", "status": "Unavailable"},
        ]

    def coding_defaults(self):
        return {
            "default_provider": "codex",
            "default_models": {
                "codex": "codex-mini",
                "claude-code": "",
                "cursor-agent": "",
            },
            "providers": ["codex", "claude-code", "cursor-agent"],
        }

    def execute(self, **payload):
        self.calls.append(payload)
        return {
            "id": f"run-{len(self.calls)}", "status": "running",
            "provider": payload["provider"], "model": payload["model"],
            "workspace": payload["workspace"], "repository_id": payload["repository_id"],
        }

    def result(self, run_id, *, workspace):
        return {"id": run_id, "workspace": workspace, "status": "running", "answer": ""}

    def cancel(self, run_id, *, workspace):
        self.cancelled.append((workspace, run_id))
        return {"id": run_id, "workspace": workspace, "status": "cancelled"}

    @staticmethod
    def proposed_edits(_answer):
        return []


class ClimateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / "work"
        self.personal = root / "personal"
        self.work.mkdir()
        self.personal.mkdir()
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.personal / "note.md").write_text("private\n", encoding="utf-8")
        self._git_init(self.work)
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work)),
            Repository(id="personal-repo", name="Personal", type="command", enabled=True, local_path=str(self.personal), tags=["arctic"]),
        ])
        self.repo_service = RepositoryWorkspaceService(WorkspaceSettings())
        self.coding = FakeCodingAdapter()
        self.service = ClimateService(self.registry, self.repo_service, self.coding)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _git_init(root: Path):
        try:
            for args in (("init",), ("config", "user.email", "test@example.com"), ("config", "user.name", "Test"), ("add", "app.py"), ("commit", "-m", "init")):
                subprocess.run(["git", *args], cwd=root, check=True, shell=False, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            pass

    def test_file_open_safe_save_and_git_status(self):
        repo = self.service.require_repo("vanta", "work-repo")
        opened = self.repo_service.preview(repo, "app.py")
        self.assertEqual(opened["content"].splitlines(), ["value = 1"])
        preview = self.repo_service.preview_save(repo, "app.py", "value = 2\n")
        self.assertTrue(preview["changed"])
        self.repo_service.save(repo, "app.py", "value = 2\n", confirm=True)
        self.assertEqual((self.work / "app.py").read_text(encoding="utf-8").splitlines(), ["value = 2"])
        status = self.repo_service.changes(repo)
        if status["is_git"]:
            self.assertEqual(status["files"][0]["path"], "app.py")

    def test_workspace_repository_isolation(self):
        self.assertEqual([row["id"] for row in self.service.repositories("work")], ["work-repo"])
        self.assertEqual([row["id"] for row in self.service.repositories("personal")], ["personal-repo"])
        boot = self.service.bootstrap("work")
        self.assertEqual(boot["coding_defaults"]["default_provider"], "codex")
        with self.assertRaises(ClimateCodingError) as ctx:
            self.service.require_repo("personal", "work-repo")
        self.assertEqual(ctx.exception.code, "workspace_isolation")
        self.service.execute(
            "personal", "personal-repo", provider="claude-code", model="claude-exact",
            prompt="summarize", current_file="note.md", selected_files=[],
        )
        self.assertIn("ARCTIC selected file note.md", self.coding.calls[-1]["selection"])
        self.assertIn("private", self.coding.calls[-1]["selection"])
        self.assertNotIn("value = 1", self.coding.calls[-1]["selection"])

    def test_exact_provider_model_switching_and_context_scope(self):
        first = self.service.execute(
            "work", "work-repo", provider="codex", model="gpt-exact",
            prompt="inspect", current_file="app.py", selected_files=[],
        )
        second = self.service.execute(
            "work", "work-repo", provider="cursor-agent", model="cursor-exact",
            prompt="review", current_file="app.py", selected_files=[],
        )
        self.assertEqual((first["provider"], first["model"]), ("codex", "gpt-exact"))
        self.assertEqual((second["provider"], second["model"]), ("cursor-agent", "cursor-exact"))
        self.assertEqual(self.coding.calls[0]["workspace"], "work")
        self.assertEqual(self.coding.calls[0]["repository_id"], "work-repo")

    def test_unavailable_and_auth_failed_states_are_exposed(self):
        rows = {row["id"]: row for row in self.service.bootstrap("work")["providers"]}
        self.assertEqual(rows["claude-code"]["state"], "authentication_required")
        self.assertEqual(rows["cursor-agent"]["state"], "unavailable")
        personal = {row["id"]: row for row in self.service.bootstrap("personal")["providers"]}
        self.assertEqual(personal["codex"]["state"], "workspace_unsupported")

    def test_ui_context_names_do_not_change_backend_workspace_values(self):
        work = self.service.bootstrap("work")
        personal = self.service.bootstrap("personal")
        self.assertEqual(work["workspace"], "work")
        self.assertEqual(work["context_label"], "VANTA / DOH / Work")
        self.assertEqual(personal["workspace"], "personal")
        self.assertEqual(personal["context_label"], "ARCTIC / Personal")

    def test_cancellation_is_scoped(self):
        run = self.service.execute("work", "work-repo", provider="codex", model="m", prompt="wait")
        cancelled = self.service.cancel("work", run["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ClimateCodingError):
            self.service.cancel("personal", run["id"])

    def test_diff_accept_and_reject(self):
        proposal = self.service.stage_proposal(
            "manual-1", "work", "work-repo", [{"path": "app.py", "content": "value = 3\n"}]
        )
        self.assertIn("+value = 3", proposal.edits[0]["diff"])
        accepted = self.service.accept("work", "manual-1")
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual((self.work / "app.py").read_text(encoding="utf-8").splitlines(), ["value = 3"])

        self.service.stage_proposal(
            "manual-2", "work", "work-repo", [{"path": "app.py", "content": "value = 4\n"}]
        )
        rejected = self.service.reject("work", "manual-2")
        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual((self.work / "app.py").read_text(encoding="utf-8").splitlines(), ["value = 3"])


class ClimateUiContractTests(unittest.TestCase):
    def test_mockup_shell_and_persistence_contract(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "climate.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        for marker in (
            'id="climate-show-excluded"', 'id="climate-chat-new"',
            'id="climate-chat-history"', 'id="climate-chat-title"',
            'id="climate-breadcrumb"', 'data-panel="problems"',
            'data-panel="output"', 'data-panel="tests"', 'data-panel="git"',
            'Ask a follow-up',
        ):
            self.assertIn(marker, template)
        self.assertIn("monaco.editor.create", script)
        self.assertIn("localStorage.setItem", script)
        self.assertIn("climate:chat:v1:", script)
        self.assertIn("splitRunOutput", script)
        self.assertIn("Details / Diagnostics", script)
        self.assertIn("newChatSession", script)
        self.assertIn("restoreChatSession", script)
        self.assertIn("compactHandoffPrompt", script)
        self.assertIn("climate-token-pill", template)
        self.assertIn("SESSION USAGE", template)
        self.assertIn("climate-run-summary", script)
        self.assertIn("selectProvider", script)
        self.assertIn("enhanceClimateSelect", script)
        self.assertIn("--cl-font-ui", (root / "static" / "css" / "climate.css").read_text(encoding="utf-8"))
        self.assertIn("climate-dd-menu", (root / "static" / "css" / "climate.css").read_text(encoding="utf-8"))
        self.assertIn('key==="s"', script)
        self.assertIn('key==="p"', script)
        self.assertIn("renderProposalReview", script)
        self.assertIn("show_excluded", script)

    def test_climate_is_the_visible_shell(self):
        root = Path(__file__).resolve().parents[1]
        base = (root / "templates" / "base.html").read_text(encoding="utf-8")
        macros = (root / "templates" / "macros.html").read_text(encoding="utf-8")
        self.assertIn("brand_logo(", base)
        self.assertIn("climate-mark.png", base)
        self.assertIn("climate-theme.css", base)
        self.assertIn("brand-wordmark", macros)
        self.assertIn("CLIMATE", macros)
        self.assertIn("CLIMATE v{{ hub_version }}", base)
        self.assertNotIn("Personal Repository Control Center", base)
        self.assertNotIn("Central Hub v{{ hub_version }}", base)
        self.assertNotIn("{{ workspace_labels[workspace] }} workspace", base)


if __name__ == "__main__":
    unittest.main()
