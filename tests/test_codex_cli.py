"""Focused tests for the Codex CLI provider MVP (Okarun)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.base import AgentDescriptor
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.codex_jsonl import CodexJsonlAccumulator
from hub.agent_center.codex_safety import (
    INCOMPLETE_CODEX_HOST_DETAIL,
    assert_git_unchanged,
    assert_safe_codex_argv,
    discover_codex_executable,
    inspect_codex_installation,
    git_status_snapshot,
    resolve_approved_repo_cwd,
    windows_official_codex_executable,
)
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import classify_provider_error, redact_text
from hub.agent_center.runner import AgentRunner
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, RegistryDefaults, Repository


def _descriptor(executable: str = "codex") -> AgentDescriptor:
    return AgentDescriptor(
        id="codex",
        label="Codex",
        provider="codex",
        executable=executable,
        modes=["find", "ask", "plan", "review"],
    )


class CodexDetectionTests(unittest.TestCase):
    def test_detect_missing_cli(self):
        adapter = CodexAdapter(_descriptor("codex-missing-xyz"))
        missing = {
            "executable": None,
            "installed": False,
            "complete": False,
            "error_code": "missing_cli",
            "detail": "Codex CLI is not installed or not discoverable",
            "incomplete_path": "",
        }
        with mock.patch("hub.agent_center.adapters.codex.inspect_codex_installation", return_value=missing):
            with mock.patch("hub.agent_center.adapters.codex.discover_codex_executable", return_value=None):
                status = adapter.connection_status()
        self.assertEqual(status["state"], "unavailable")
        self.assertFalse(status["installed"])
        self.assertEqual(status["error_code"], "missing_cli")

    def test_login_status_authenticated(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: sys.executable
        adapter._detect_version = lambda _exe: "codex-cli 0.146.0"

        def fake_probe(argv, timeout=15.0):
            return subprocess.CompletedProcess(argv, 0, stdout="Logged in using ChatGPT\n", stderr="")

        adapter._run_probe = fake_probe
        status = adapter.connection_status()
        self.assertTrue(status["installed"])
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["version"], "codex-cli 0.146.0")
        self.assertEqual(status["account_label"], "ChatGPT")

    def test_login_status_authentication_required(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: sys.executable
        adapter._detect_version = lambda _exe: "codex-cli 0.1"

        def fake_probe(argv, timeout=15.0):
            return subprocess.CompletedProcess(argv, 1, stdout="Not logged in\n", stderr="")

        adapter._run_probe = fake_probe
        status = adapter.connection_status()
        self.assertEqual(status["state"], "authentication_required")
        self.assertFalse(status["authenticated"])
        self.assertIn("codex login", status["detail"])

    def test_connect_uses_visible_codex_login(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: sys.executable
        with mock.patch("subprocess.Popen") as popen:
            result = adapter.connect()
        self.assertEqual(result["login_command"], "codex login")
        self.assertEqual(result["state"], "authentication_required")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-1], "login")
        self.assertFalse(popen.call_args.kwargs.get("shell"))


class CodexArgvSafetyTests(unittest.TestCase):
    def test_build_argv_read_only_json_and_cd(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: "/safe/codex"
        argv = adapter.build_argv(
            mode="ask",
            prompt="hello",
            model="__provider_default__",
            cwd="/repos/demo",
            prompt_file="/tmp/prompt.txt",
        )
        self.assertEqual(
            argv,
            ["/safe/codex", "-C", "/repos/demo", "--sandbox", "read-only", "exec", "--json", "--ephemeral", "-"],
        )
        assert_safe_codex_argv(argv)

    def test_persisted_session_uses_safe_explicit_resume(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: "/safe/codex"
        session_id = "019fbda6-4acd-7920-a6d9-385d0e229af9"
        first = adapter.build_argv(
            mode="ask", prompt="hello", model="gpt-5.6-sol", cwd="/repos/demo",
            prompt_file="/tmp/prompt.txt", persist_session=True,
        )
        self.assertNotIn("--ephemeral", first)
        self.assertIn("read-only", first)
        resumed = adapter.build_argv(
            mode="ask", prompt="follow up", model="gpt-5.6-sol", cwd="/repos/demo",
            prompt_file="/tmp/prompt.txt", persist_session=True,
            provider_session_id=session_id,
        )
        self.assertEqual(resumed[resumed.index("exec") + 1], "resume")
        self.assertIn(session_id, resumed)
        self.assertNotIn("--ephemeral", resumed)
        assert_safe_codex_argv(resumed, require_ephemeral=False)

    def test_rejects_dangerous_sandbox_and_yolo(self):
        with self.assertRaises(ValueError):
            assert_safe_codex_argv(["codex", "exec", "--sandbox", "workspace-write", "--ephemeral", "--json", "p"])
        with self.assertRaises(ValueError):
            assert_safe_codex_argv(["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--json", "--yolo", "p"])
        with self.assertRaises(ValueError):
            assert_safe_codex_argv(
                ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--json", "--ask-for-approval", "never", "p"]
            )

    def test_rejects_arbitrary_executable_paths_in_config(self):
        self.assertIsNone(discover_codex_executable(r"C:\evil\codex.exe"))
        self.assertIsNone(discover_codex_executable("../codex"))

    def test_sandbox_read_only_safety_not_weakened(self):
        adapter = CodexAdapter(_descriptor())
        adapter.resolve_executable = lambda: r"C:\safe\codex.exe"
        argv = adapter.build_argv(
            mode="ask",
            prompt="hello",
            model="__provider_default__",
            cwd=r"C:\repos\demo",
            prompt_file=r"C:\tmp\prompt.txt",
        )
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertNotIn("workspace-write", argv)
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("--full-auto", argv)
        assert_safe_codex_argv(argv)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


class CodexWindowsRuntimeDiscoveryTests(unittest.TestCase):
    def test_path_codex_with_host_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            on_path = _touch(root / "path" / "codex.exe")
            _touch(on_path.parent / "codex-code-mode-host.exe")
            local = root / "Local"
            official = _touch(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
            _touch(official.parent / "codex-code-mode-host.exe")
            stale = _touch(root / "home" / ".sandbox-bin" / "codex.exe")
            with mock.patch("hub.agent_center.codex_safety.os.name", "nt"):
                with mock.patch("hub.agent_center.codex_safety.codex_home", return_value=root / "home"):
                    with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=False):
                        with mock.patch(
                            "hub.agent_center.adapters.base.which_executable",
                            return_value=str(on_path),
                        ):
                            selected = discover_codex_executable("codex")
                            inspection = inspect_codex_installation("codex")
            self.assertEqual(Path(selected).resolve(), on_path.resolve())
            self.assertNotEqual(Path(selected).resolve(), official.resolve())
            self.assertNotEqual(Path(selected).resolve(), stale.resolve())
            self.assertEqual(inspection["source"], "path")
            self.assertEqual(inspection["runtime_health"], "ok")

    def test_official_standalone_selected_when_path_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "Local"
            official = _touch(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe")
            _touch(official.parent / "codex-code-mode-host.exe")
            stale = _touch(root / "home" / ".sandbox-bin" / "codex.exe")
            with mock.patch("hub.agent_center.codex_safety.os.name", "nt"):
                with mock.patch("hub.agent_center.codex_safety.codex_home", return_value=root / "home"):
                    with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=False):
                        with mock.patch(
                            "hub.agent_center.adapters.base.which_executable",
                            return_value=str(stale),
                        ):
                            selected = discover_codex_executable("codex")
                            inspection = inspect_codex_installation("codex")
            self.assertEqual(Path(selected).resolve(), official.resolve())
            self.assertEqual(inspection["source"], "official_standalone")
            self.assertEqual(inspection["runtime_health"], "ok")
            self.assertTrue(inspection["complete"])
            self.assertEqual(
                Path(selected).resolve(),
                (local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe").resolve(),
            )

    def test_stale_sandbox_bin_without_host_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            stale = _touch(home / ".sandbox-bin" / "codex.exe")
            local = Path(tmp) / "LocalEmpty"
            local.mkdir()
            with mock.patch("hub.agent_center.codex_safety.os.name", "nt"):
                with mock.patch("hub.agent_center.codex_safety.codex_home", return_value=home):
                    with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=False):
                        with mock.patch(
                            "hub.agent_center.adapters.base.which_executable",
                            return_value=str(stale),
                        ):
                            selected = discover_codex_executable("codex")
                            inspection = inspect_codex_installation("codex")
            self.assertIsNone(selected)
            self.assertEqual(inspection["error_code"], "incomplete_cli")
            self.assertEqual(inspection["detail"], INCOMPLETE_CODEX_HOST_DETAIL)
            self.assertEqual(inspection["runtime_health"], "incomplete_host")
            self.assertTrue(inspection["installed"])
            self.assertEqual(Path(inspection["incomplete_path"]).resolve(), stale.resolve())
            adapter = CodexAdapter(_descriptor())
            adapter.resolve_executable = lambda: None
            with mock.patch(
                "hub.agent_center.adapters.codex.inspect_codex_installation",
                return_value=inspection,
            ):
                status = adapter.connection_status()
            self.assertEqual(status["state"], "unavailable")
            self.assertFalse(status["available"])
            self.assertEqual(status["error_code"], "incomplete_cli")
            self.assertEqual(status["detail"], INCOMPLETE_CODEX_HOST_DETAIL)
            self.assertEqual(status["runtime_health"], "incomplete_host")
            self.assertIn("sandbox-bin", status["executable_path"].replace("\\", "/"))

    def test_fallback_with_host_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fallback = _touch(home / "bin" / "codex.exe")
            _touch(fallback.parent / "codex-code-mode-host.exe")
            _touch(home / ".sandbox-bin" / "codex.exe")
            local = Path(tmp) / "LocalEmpty"
            local.mkdir()
            with mock.patch("hub.agent_center.codex_safety.os.name", "nt"):
                with mock.patch("hub.agent_center.codex_safety.codex_home", return_value=home):
                    with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=False):
                        with mock.patch(
                            "hub.agent_center.adapters.base.which_executable",
                            return_value=None,
                        ):
                            selected = discover_codex_executable("codex")
                            inspection = inspect_codex_installation("codex")
            self.assertEqual(Path(selected).resolve(), fallback.resolve())
            self.assertEqual(inspection["source"], "codex_home_bin")
            self.assertEqual(inspection["runtime_health"], "ok")

    def test_official_path_uses_localappdata_not_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "LocalApp"
            with mock.patch("hub.agent_center.codex_safety.os.name", "nt"):
                with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=False):
                    path = windows_official_codex_executable()
            self.assertEqual(
                path,
                local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
            )

    def test_posix_sandbox_bin_without_host_still_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fallback = _touch(home / ".sandbox-bin" / "codex")
            with mock.patch("hub.agent_center.codex_safety.os.name", "posix"):
                with mock.patch("hub.agent_center.codex_safety.codex_home", return_value=home):
                    with mock.patch(
                        "hub.agent_center.adapters.base.which_executable",
                        return_value=None,
                    ):
                        selected = discover_codex_executable("codex")
            self.assertEqual(Path(selected).resolve(), fallback.resolve())


class CodexPathJailTests(unittest.TestCase):
    def test_invalid_repository_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            resolved = resolve_approved_repo_cwd(root, [root])
            self.assertEqual(resolved, root.resolve())
            with self.assertRaises(ValueError):
                resolve_approved_repo_cwd(outside, [root])
            with self.assertRaises(ValueError):
                resolve_approved_repo_cwd(root / ".." / "outside", [root])


class CodexJsonlTests(unittest.TestCase):
    def test_jsonl_streaming_messages_tools_usage_errors(self):
        acc = CodexJsonlAccumulator()
        events = [
            {"type": "thread.started", "thread_id": "019fbda6-4acd-7920-a6d9-385d0e229af9"},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "git status", "status": "completed"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Final answer here"}},
            {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}},
            {"type": "error", "message": "quota exceeded"},
        ]
        for event in events:
            acc.feed(json.dumps(event) + "\n")
        self.assertEqual(acc.final_answer(), "Final answer here")
        self.assertEqual(acc.usage["input_tokens"], 11)
        self.assertEqual(acc.usage["output_tokens"], 7)
        self.assertEqual(acc.usage["provider_session_id"], "019fbda6-4acd-7920-a6d9-385d0e229af9")
        self.assertTrue(any(row["type"] == "command_execution" for row in acc.tool_activity))
        self.assertEqual(classify_provider_error(acc.errors[-1])["code"], "quota")


class CodexRedactionTests(unittest.TestCase):
    def test_redacts_tokens_env_and_secret_commands(self):
        text = redact_text(
            "token=abc123\nOPENAI_API_KEY=sk-secretvaluehere\n"
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaa.bbbb\n"
            "codex --api-key supersecret run\n"
        )
        self.assertNotIn("sk-secretvaluehere", text)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("abc123", text)
        self.assertIn("[redacted]", text)

    def test_classifies_missing_code_mode_host(self):
        classified = classify_provider_error(
            "failed to spawn code-mode host .codex/.sandbox-bin/codex-code-mode-host.exe: "
            "The system cannot find the file specified. (os error 2)"
        )
        self.assertEqual(classified["code"], "incomplete_cli")
        self.assertEqual(
            classified["detail"],
            "Codex installation incomplete: codex-code-mode-host.exe is missing",
        )


class CodexRunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "agent.db"))
        self.runner = AgentRunner(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _create_run(self) -> str:
        run = self.store.create_run(
            {
                "status": "queued",
                "mode": "ask",
                "agent_id": "codex",
                "agent_label": "Codex",
                "model": "__provider_default__",
                "repository_ids": ["demo"],
                "prompt": "hi",
                "packed_prompt": "hi",
                "context": {},
                "referenced_files": [],
                "profile_id": "okarun",
                "conversation_id": "",
            }
        )
        return run["id"]

    def test_jsonl_run_completes_and_parses(self):
        script = Path(self.temp.name) / "fake_codex.py"
        script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'ok from fake'}}))",
                    "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':2}}))",
                ]
            ),
            encoding="utf-8",
        )
        run_id = self._create_run()
        self.runner.start(
            run_id=run_id,
            argv=[sys.executable, str(script)],
            cwd=Path(self.temp.name),
            timeout_seconds=10,
            jsonl=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            row = self.store.get_run(run_id)
            if row and row["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        row = self.store.get_run(run_id)
        self.assertEqual(row["status"], "completed")
        self.assertIn("ok from fake", row["answer"])
        self.assertEqual(row["usage"].get("output_tokens"), 2)

    def test_provider_session_is_scoped_to_latest_exact_conversation_run(self):
        conversation = self.store.create_conversation(profile_id="okarun", title="session")
        run = self.store.create_run(
            {
                "status": "completed", "mode": "ask", "agent_id": "codex",
                "agent_label": "Codex", "model": "gpt-5.6-sol",
                "repository_ids": ["demo"], "prompt": "one", "packed_prompt": "one",
                "context": {}, "referenced_files": [], "profile_id": "okarun",
                "conversation_id": conversation["id"],
            }
        )
        session_id = "019fbda6-4acd-7920-a6d9-385d0e229af9"
        self.store.update_run(run["id"], usage={"provider_session_id": session_id})
        self.assertEqual(
            self.store.latest_provider_session(
                conversation_id=conversation["id"], profile_id="okarun", agent_id="codex",
                model="gpt-5.6-sol", repository_ids=["demo"],
            ),
            session_id,
        )
        other = self.store.create_run(
            {
                "status": "completed", "mode": "ask", "agent_id": "claude-code",
                "agent_label": "Claude", "model": "claude",
                "repository_ids": ["demo"], "prompt": "handoff", "packed_prompt": "handoff",
                "context": {}, "referenced_files": [], "profile_id": "okarun",
                "conversation_id": conversation["id"],
            }
        )
        self.assertTrue(other["id"])
        self.assertEqual(
            self.store.latest_provider_session(
                conversation_id=conversation["id"], profile_id="okarun", agent_id="codex",
                model="gpt-5.6-sol", repository_ids=["demo"],
            ),
            "",
        )

    def test_cancellation(self):
        run_id = self._create_run()
        self.runner.start(
            run_id=run_id,
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=Path(self.temp.name),
            timeout_seconds=60,
        )
        time.sleep(0.2)
        self.runner.cancel(run_id)
        deadline = time.time() + 5
        while time.time() < deadline:
            row = self.store.get_run(run_id)
            if row and row["status"] == "cancelled":
                break
            time.sleep(0.05)
        self.assertEqual(self.store.get_run(run_id)["status"], "cancelled")

    def test_timeout(self):
        run_id = self._create_run()
        self.runner._run(
            run_id=run_id,
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=self.temp.name,
            timeout_seconds=5.0,
            env=None,
            stdin_path=None,
            jsonl=False,
            safety_repo=None,
        )
        row = self.store.get_run(run_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("timed out", row["error"].lower())


class CodexServiceOkarunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        repo = Path(self.temp.name) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        self.repo = repo
        self.store = AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "agent.db"))
        self.adapter = CodexAdapter(_descriptor())
        self.adapter.resolve_executable = lambda: sys.executable
        self.adapter.connection_status = lambda force_refresh=False: {
            "state": "connected",
            "detail": "ok",
            "installed": True,
            "authenticated": True,
            "version": "test",
            "available": True,
        }
        self.adapter.availability = lambda: type("A", (), {
            "id": "codex", "label": "Codex", "status": "available", "detail": "ok",
            "executable_found": True, "modes": ["ask", "find", "plan", "review"],
            "models": ["__provider_default__"], "models_source": "provider_default",
        })()
        script = Path(self.temp.name) / "echo_jsonl.py"
        script.write_text(
            "import json,sys;\n"
            "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'safe'}}))\n",
            encoding="utf-8",
        )
        self.echo = script

        def build_argv(*, mode, prompt, model, cwd, prompt_file=""):
            # Include required safety tokens so service-level argv checks pass; Python ignores extras.
            return [
                sys.executable,
                str(script),
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--json",
                "-C",
                cwd,
            ]

        self.adapter.build_argv = build_argv
        self.registry = Registry(
            repositories=[
                Repository(
                    id="demo",
                    name="Demo",
                    type="command",
                    enabled=True,
                    local_path=str(repo),
                )
            ],
            defaults=RegistryDefaults(),
        )
        self.service = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=[self.adapter],
            timeout_seconds=15,
        )
        # Force connection registry to see connected Codex
        self.service.connections.get = lambda agent_id, refresh=False, probe=True: {
            "state": "connected",
            "detail": "ok",
            "installed": True,
            "authenticated": True,
            "version": "test",
            "status": "Connected",
            "capabilities": self.adapter.capabilities(),
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_okarun_only_and_aira_rejected(self):
        agents_okarun = self.service.list_agents(profile_id="okarun", probe=False)
        self.assertTrue(any(a["id"] == "codex" for a in agents_okarun))
        agents_aira = self.service.list_agents(profile_id="aira", probe=False)
        self.assertFalse(any(a["id"] == "codex" for a in agents_aira))
        with self.assertRaises(AgentCenterError) as ctx:
            self.service.start_run(
                {
                    "profile_id": "aira",
                    "agent_id": "codex",
                    "mode": "ask",
                    "prompt": "hi",
                    "repository_ids": ["demo"],
                    "model": "__provider_default__",
                }
            )
        self.assertEqual(ctx.exception.code, "profile_unsupported")

    def test_authentication_failure_marks_unavailable(self):
        self.service.connections.get = lambda agent_id, refresh=False, probe=True: {
            "state": "authentication_required",
            "detail": "Authentication required. Use Connect to run `codex login`.",
            "installed": True,
            "authenticated": False,
            "version": "test",
            "status": "Authentication Required",
            "capabilities": self.adapter.capabilities(),
        }
        run = self.service.start_run(
            {
                "profile_id": "okarun",
                "agent_id": "codex",
                "mode": "ask",
                "prompt": "hi",
                "repository_ids": ["demo"],
                "model": "__provider_default__",
            }
        )
        self.assertEqual(run["status"], "unavailable")
        self.assertIn("Authentication required", run["error"])

    def test_read_only_git_safety_fails_when_tree_changes(self):
        before = git_status_snapshot(self.repo)
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        after = git_status_snapshot(self.repo)
        with self.assertRaises(RuntimeError):
            assert_git_unchanged(before, after)

    def test_repository_investigation_bypasses_empty_packet_block(self):
        before = git_status_snapshot(self.repo)
        run = self.service.start_run(
            {
                "profile_id": "okarun",
                "agent_id": "codex",
                "mode": "ask",
                "prompt": "Explain the exact internal implementation of xyzzy",
                "repository_ids": ["demo"],
                "model": "__provider_default__",
                "tool_ids": [],
                "bounded_evidence_only": True,
                "repository_investigation": True,
                "evidence_packet": {
                    "repository_ids": ["demo"], "hits": [], "sources": [],
                    "usable": False, "errors": [], "summary": "No initial matches",
                },
            }
        )
        self.assertNotIn("cannot verify", str(run.get("answer") or "").lower())
        self.assertIn(run["status"], {"queued", "running", "completed"})
        self.assertIn("Native Codex read-only repository search", run["packed_prompt"])
        self.assertIn("starting hints", run["packed_prompt"])
        self.assertIn("Hub tools: none.", run["packed_prompt"])
        self.assertIn("Remain read-only", run["packed_prompt"])
        self.assertNotIn("Enabled read-only tools: none.", run["packed_prompt"])
        argv = self.adapter.build_argv(
            mode="ask",
            prompt="Explain the exact internal implementation of xyzzy",
            model="__provider_default__",
            cwd=str(self.repo),
        )
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertNotIn("workspace-write", argv)
        self.assertNotIn("--yolo", argv)
        deadline = time.time() + 5
        while time.time() < deadline:
            current = self.service.get_run(run["id"], profile_id="okarun")
            if current["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert_git_unchanged(before, git_status_snapshot(self.repo))

    def test_history_preserves_provider_prompt_and_status(self):
        # Avoid real runner path complexity: mark connected and use fake argv that exits quickly.
        run = self.service.start_run(
            {
                "profile_id": "okarun",
                "agent_id": "codex",
                "mode": "ask",
                "prompt": "summarize repo",
                "repository_ids": ["demo"],
                "model": "__provider_default__",
            }
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            row = self.service.get_run(run["id"], profile_id="okarun")
            if row["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        full = self.service.get_run(run["id"], profile_id="okarun")
        self.assertEqual(full["agent_id"], "codex")
        self.assertEqual(full["prompt"], "summarize repo")
        self.assertEqual(full["model"], "__provider_default__")
        self.assertEqual(full["repository_ids"], ["demo"])
        self.assertIn(full["status"], {"completed", "failed", "cancelled"})
        history = self.service.history(profile_id="okarun")
        self.assertTrue(history)
        self.assertEqual(history[0]["agent_id"], "codex")
        self.assertIn("summarize repo", history[0]["prompt_preview"])
        self.assertEqual(history[0]["model"], "__provider_default__")
        self.assertEqual(history[0]["repository_ids"], ["demo"])
        self.assertIn(history[0]["status"], {"completed", "failed", "cancelled"})


class CodexUiFactsTests(unittest.TestCase):
    def test_ai_connections_page_shows_required_facts(self):
        from app import create_app

        app = create_app()
        app.config.update(TESTING=True)
        client = app.test_client()
        page = client.get("/system/ai-connections")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Installed", page.data)
        self.assertIn(b"Connected", page.data)
        self.assertIn(b"Version", page.data)
        self.assertIn(b"Executable", page.data)
        self.assertIn(b"Runtime health", page.data)
        self.assertIn(b"Last Checked", page.data)
        self.assertIn(b"Refresh Status", page.data)
        self.assertIn(b"Default Coding Provider", page.data)


if __name__ == "__main__":
    unittest.main()
