"""CLIMATE Code Workspace v1 contracts."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError, classify_task_mode
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings


class FakeCodingAdapter:
    def __init__(self) -> None:
        self.calls = []
        self.cancelled = []
        self._answers: dict[str, dict] = {}

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
        run_id = f"run-{len(self.calls)}"
        return {
            "id": run_id, "status": "running",
            "provider": payload["provider"], "model": payload["model"],
            "workspace": payload["workspace"], "repository_id": payload["repository_id"],
            "task_mode": payload.get("task_mode") or "ask",
            "provider_invoked": True,
            "logs": str(payload.get("preflight_log") or ""),
        }

    def result(self, run_id, *, workspace):
        preset = self._answers.get(run_id)
        if preset:
            return dict(preset, id=run_id, workspace=workspace)
        return {"id": run_id, "workspace": workspace, "status": "running", "answer": ""}

    def cancel(self, run_id, *, workspace):
        self.cancelled.append((workspace, run_id))
        return {"id": run_id, "workspace": workspace, "status": "cancelled"}

    @staticmethod
    def proposed_edits(answer):
        from hub.climate.coding import ClimateCodingAdapter
        return ClimateCodingAdapter.proposed_edits(answer)

    @staticmethod
    def humanize_answer(answer, *, task_mode="ask"):
        from hub.climate.coding import ClimateCodingAdapter
        return ClimateCodingAdapter.humanize_answer(answer, task_mode=task_mode)

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
        (self.personal / "AGENTS.md").write_text(
            "# Personal agents\nKeep ARCTIC files isolated.\n",
            encoding="utf-8",
        )
        (self.work / "AGENTS.md").write_text(
            "# Agents\nUse repository files as authority for ask/edit tasks.\n",
            encoding="utf-8",
        )
        (self.work / "SKILLS.md").write_text(
            "# Skills\n\n## ANC Binary\nExplain ANC Binary derivation from visit thresholds.\n\n"
            "## Unrelated Shipping\nDeploy containers to staging.\n",
            encoding="utf-8",
        )
        (self.work / "docs").mkdir()
        (self.work / "docs" / "anc.md").write_text(
            "ANC Binary is derived from visit thresholds.\n",
            encoding="utf-8",
        )
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

    def test_ask_mode_does_not_stage_provider_edits(self):
        run = self.service.execute(
            "work", "work-repo", provider="codex", model="m",
            prompt="Explain how ANC Binary is derived", current_file="app.py",
        )
        self.assertEqual(self.coding.calls[-1]["task_mode"], "ask")
        raw = (
            '```json\n{"edits":[{"path":"app.py","content":"ANC Binary is 1 when compliant.\\n"}]}\n```'
        )
        self.coding._answers[run["id"]] = {
            "status": "completed", "answer": raw, "logs": "", "usage": {},
            "provider": "codex", "model": "m",
        }
        result = self.service.result("work", run["id"])
        self.assertEqual(result["task_mode"], "ask")
        self.assertIsNone(result["proposal"])
        self.assertIn("ANC Binary is 1 when compliant", result["answer"])
        self.assertNotIn('"edits"', result["answer"])

    def test_edit_mode_stages_reviewed_proposal(self):
        run = self.service.execute(
            "work", "work-repo", provider="codex", model="m",
            prompt="Fix app.py to set value = 9", current_file="app.py",
        )
        self.assertEqual(self.coding.calls[-1]["task_mode"], "edit")
        raw = '{"edits":[{"path":"app.py","content":"value = 9\\n"}]}'
        self.coding._answers[run["id"]] = {
            "status": "completed", "answer": raw, "logs": "", "usage": {},
            "provider": "codex", "model": "m",
        }
        result = self.service.result("work", run["id"])
        self.assertEqual(result["task_mode"], "edit")
        self.assertIsNotNone(result["proposal"])
        self.assertEqual(result["proposal"]["state"], "pending")
        self.assertEqual(result["proposal"]["edits"][0]["path"], "app.py")
        self.assertTrue(result["proposal"]["requires_review"])

    def test_cancelled_run_does_not_stage_edits(self):
        run = self.service.execute(
            "work", "work-repo", provider="codex", model="m",
            prompt="Fix app.py", current_file="app.py",
        )
        self.assertEqual(self.coding.calls[-1]["task_mode"], "edit")
        raw = '{"edits":[{"path":"app.py","content":"value = 9\\n"}]}'
        self.coding._answers[run["id"]] = {
            "status": "cancelled", "answer": raw, "logs": "partial\n[cancelled]\n", "usage": {},
            "provider": "codex", "model": "m",
        }
        result = self.service.result("work", run["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["proposal"])

    def test_large_diff_is_flagged_for_review(self):
        (self.work / "app.py").write_text("line\n" * 200, encoding="utf-8")
        proposal = self.service.stage_proposal(
            "manual-large", "work", "work-repo",
            [{"path": "app.py", "content": "x = 1\n"}],
        )
        public = self.service._public_proposal(proposal)
        self.assertTrue(public["large_diff"])
        self.assertIn("Large or destructive", public["warning"])

    def test_ports_are_read_only_and_workspace_isolated(self):
        rows = [{
            "port": 8080,
            "pid": 4321,
            "command_redacted": "python app.py",
            "executable": "python",
            "repo_id": "work-repo",
            "repository_name": "Work",
            "managed_by_hub": True,
            "detection_reasons": ["hub_tracked"],
            "confidence": "high",
            "run_id": "run-1",
            "profile_id": "web",
            "stoppable": True,
            "view_only": False,
        }]
        with mock.patch.object(self.repo_service, "summarize_local_processes", return_value=rows):
            with mock.patch.object(self.repo_service.processes, "list_runs", return_value=[]):
                with mock.patch("hub.climate.service.port_listeners", return_value={8080: [4321], 9999: [99]}):
                    payload = self.service.ports("work", "work-repo")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["forwarding"])
        ports = {row["port"]: row for row in payload["ports"]}
        self.assertEqual(ports[8080]["source"], "run-profile")
        self.assertEqual(ports[8080]["pid"], 4321)
        self.assertEqual(ports[8080]["open_url"], "http://127.0.0.1:8080")
        self.assertNotIn("can_stop", ports[8080])
        self.assertEqual(ports[9999]["source"], "local")
        with self.assertRaises(ClimateCodingError) as ctx:
            self.service.ports("personal", "work-repo")
        self.assertEqual(ctx.exception.code, "workspace_isolation")

    def test_debug_reports_inactive_or_real_run_session(self):
        with mock.patch.object(self.repo_service.processes, "list_runs", return_value=[]):
            idle = self.service.debug("work", "work-repo")
        self.assertFalse(idle["active"])
        self.assertEqual(idle["message"], "No active debug session")
        self.assertFalse(idle["evaluate"])
        run = mock.Mock()
        run.status = "running"
        run.run_id = "run-9"
        run.profile_id = "web"
        run.pid = 111
        run.port = 5000
        run.started_at = "2026-08-14T00:00:00+00:00"
        run.error = ""
        run.cwd = str(self.work)
        run.to_public.return_value = {
            "run_id": "run-9",
            "profile_id": "web",
            "status": "running",
            "pid": 111,
            "port": 5000,
            "local_url": "http://127.0.0.1:5000",
            "started_at": run.started_at,
            "error": "",
            "cwd": str(self.work),
        }
        with mock.patch.object(self.repo_service.processes, "list_runs", return_value=[run]):
            with mock.patch.object(
                self.repo_service, "read_logs",
                return_value={"lines": ["[OUT] hello", "[ERR] boom"]},
            ):
                payload = self.service.debug("work", "work-repo")
        self.assertTrue(payload["active"])
        self.assertEqual(payload["session"]["pid"], 111)
        self.assertEqual(payload["logs"][0]["stream"], "stdout")
        self.assertEqual(payload["logs"][1]["stream"], "stderr")
        self.assertFalse(payload["evaluate"])
        with self.assertRaises(ClimateCodingError) as ctx:
            self.service.debug("personal", "work-repo")
        self.assertEqual(ctx.exception.code, "workspace_isolation")


class ClimateTaskModeUnitTests(unittest.TestCase):
    def test_classify_ask_vs_edit(self):
        self.assertEqual(classify_task_mode("Explain how ANC Binary is derived"), "ask")
        self.assertEqual(classify_task_mode("what is the name of the repo selected"), "ask")
        self.assertEqual(classify_task_mode("Fix the null check in app.py"), "edit")
        self.assertEqual(
            classify_task_mode("Give me the logic of the ANC. Cite exact files/functions. Do not edit anything."),
            "ask",
        )
        self.assertEqual(classify_task_mode("anything", "edit"), "edit")

    def test_humanize_strips_edits_json(self):
        raw = '{"edits":[{"path":"docs/a.md","content":"Hello ANC Binary.\\nRule: 1 means yes."}]}'
        text, diag = ClimateCodingAdapter.humanize_answer(raw, task_mode="ask")
        self.assertIn("Hello ANC Binary", text)
        self.assertNotIn('"edits"', text)
        self.assertTrue(diag)

    def test_coding_adapter_prompt_respects_task_mode(self):
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

        def fake_availability(provider=None, *, refresh=False):
            row = {
                "id": "codex", "state": "connected", "status": "Connected",
                "detail": "", "account_label": "", "capabilities": {"native_repository_investigation": True},
            }
            return row if provider else [row]

        adapter.availability = fake_availability  # type: ignore[method-assign]
        ask = adapter.execute(
            workspace="work", repository_id="work-repo", provider="codex", model="m",
            prompt="CLIMATE context packet (ASK).\nLikely source: pkg/scoring.py",
            task_mode="ask", selected_files=["pkg/scoring.py"],
            repository_investigation=True, conversation_id="conversation-1",
        )
        self.assertEqual(ask["task_mode"], "ask")
        self.assertIn("ASK / EXPLAIN", center.payload["prompt"])
        self.assertNotIn('{"edits":[{"path"', center.payload["prompt"])
        self.assertEqual(center.payload["files"], {})
        self.assertEqual(center.payload["tool_ids"], [])
        self.assertTrue(center.payload["repository_investigation"])
        self.assertEqual(center.payload["conversation_id"], "conversation-1")
        self.assertIn("independently search", center.payload["prompt"])
        adapter.availability = lambda provider=None, refresh=False: (  # type: ignore[method-assign]
            {"id": "codex", "state": "connected", "capabilities": {}} if provider else []
        )
        self.assertTrue(adapter.can_investigate_repository("codex"))
        self.assertFalse(adapter.can_investigate_repository("claude-code"))
        self.assertFalse(adapter.can_investigate_repository("cursor-agent"))
        cached_ask = adapter.execute(
            workspace="work", repository_id="work-repo", provider="codex", model="m",
            prompt="CLIMATE context packet (ASK).\nLikely source: pkg/scoring.py",
            task_mode="ask", repository_investigation=adapter.can_investigate_repository("codex"),
        )
        self.assertEqual(cached_ask["task_mode"], "ask")
        self.assertTrue(center.payload["repository_investigation"])
        self.assertEqual(center.payload["tool_ids"], [])
        edit = adapter.execute(
            workspace="work", repository_id="work-repo", provider="codex", model="m",
            prompt="Fix ANC Binary", task_mode="edit",
        )
        self.assertEqual(edit["task_mode"], "edit")
        self.assertIn("EDIT mode", center.payload["prompt"])
        self.assertIn('"edits"', center.payload["prompt"])


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
            'data-panel="terminal"',
            'data-panel="debug"',
            'data-panel="ports"',
            'climate-output-channel',
            'climate-pane-problems',
            'climate-terminal-panel',
            'wc_terminal.js',
            'wc-xterm-a',
            'Ask a follow-up',
            'Session total',
            'Provider breakdown',
            'climate-token-quota',
            'climate-usage-limits',
            'climate-usage-refresh',
            'Codex capacity',
            'Codex limit unavailable',
            'id="climate-stop"',
            'id="climate-stop-top"',
        ):
            self.assertIn(marker, template)
        self.assertNotIn('id="climate-cancel"', template)
        self.assertNotIn('id="climate-cancel-top"', template)
        self.assertNotIn("OUTLINE", template)
        self.assertNotIn("TIMELINE", template)
        self.assertNotIn("› REPOSITORIES", template)
        self.assertNotIn("climate_terminal.js", template)
        self.assertIn("AI_MIN = 340", script)
        self.assertIn("normalizeAiPanelState", script)
        self.assertIn("collapseAiPanel", script)
        self.assertIn("ensureClimateTerminal", script)
        self.assertIn("WCTerminal", script)
        self.assertIn("filterOutputLines", script)
        self.assertIn("parseDiagnosticLines", script)
        self.assertIn("showBottomPane", script)
        self.assertIn("No problems detected", script)
        self.assertIn("No active debug session", script)
        self.assertIn("panel: state.panel", script)
        self.assertIn("/ports", script)
        self.assertIn("/debug", script)
        self.assertNotIn("Evaluate expression", script)
        self.assertIn("clamp(480px, 45vw, 720px)", script)
        css = (root / "static" / "css" / "climate.css").read_text(encoding="utf-8")
        self.assertIn("font-weight: 400", css)
        self.assertIn(".climate-usage-totals", css)
        self.assertIn(".climate-usage-limits", css)
        self.assertIn("font-size: 14px", css)
        self.assertNotIn(".climate-usage-total {\n  margin: 8px 0 10px;\n  font-size: 28px;", css)
        self.assertNotRegex(css, r"\.climate-usage-total\s*\{[^}]*font-size:\s*28px")
        self.assertIn("parseActivityEvidence", script)
        self.assertIn("climate-activity-progress", script)
        self.assertIn("renderActivityProgress", script)
        self.assertIn("renderActivityComplete", script)
        complete_fn = script.split("function renderActivityComplete", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("filesInspected", complete_fn)
        self.assertFalse(
            any("sources" in line for line in complete_fn.splitlines() if "exploreCount" in line)
        )
        self.assertIn("classifyTaskMode", script)
        self.assertIn("humanizeAnswer", script)
        self.assertIn("looksLikeEditsJson", script)
        self.assertIn("Sources ·", script)
        self.assertIn("task_mode", script)
        self.assertIn("formatQuotaMeter", script)
        self.assertIn("resolveCodexQuotaRemaining", script)
        self.assertIn("fetchCodexRateLimits", script)
        self.assertIn("Resolving repo", script)
        self.assertIn("Matching skill", script)
        self.assertIn("No model invoked", script)
        self.assertIn("stopRun", script)
        self.assertIn("setRunControls", script)
        self.assertIn("Stopped by user", script)
        self.assertIn("finalizeStoppedRun", script)
        self.assertNotIn("Planning next moves", script)
        self.assertNotIn("climate-activity-planning", script)
        self.assertIn("climate-activity-progress", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(".climate-wc-terminal .wc-term-toolbar", css)
        self.assertIn("height: 28px", css)
        self.assertIn("tabShortLabel", (root / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8"))
        self.assertIn("hostIsFitReady", (root / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8"))
        self.assertIn("convertEol", (root / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8"))
        self.assertIn("positionClimateDropdownMenu", script)
        self.assertIn("is-portal", script)
        self.assertIn("openClimateDropdown", script)
        self.assertIn("climate-dd-menu.is-portal", css)
        self.assertIn("monaco.editor.create", script)
        self.assertIn("localStorage.setItem", script)
        self.assertIn("climate:chat:v1:", script)
        self.assertIn("splitRunOutput", script)
        self.assertIn("Details / Diagnostics", script)
        self.assertIn("newChatSession", script)
        self.assertIn("restoreChatSession", script)
        self.assertIn("compactHandoffPrompt", script)
        self.assertIn("climate-token-pill", template)
        self.assertIn("Session usage", template)
        self.assertIn("climate-run-summary", script)
        self.assertIn("selectProvider", script)
        self.assertIn("enhanceClimateSelect", script)
        self.assertIn("--cl-font-ui", css)
        self.assertIn("climate-dd-menu", css)
        self.assertIn('key==="s"', script)
        self.assertIn('key==="p"', script)
        self.assertIn("renderProposalReview", script)
        self.assertIn("show_excluded", script)
        self.assertNotIn("usage_source=", script)
        self.assertNotIn("SESSION USAGE", template)

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
