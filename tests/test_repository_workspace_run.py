"""Repository Workspace Phase 2 — run profiles, ports, process manager, logs."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.openai_tools import AgentToolsContext, execute_tool
from hub.registry.models import Registry, RegistryDefaults, Repository
from hub.repository_workspace.logs import RunLogStore, redact_log_line
from hub.repository_workspace.ports import find_available_port, port_available
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.run_profiles import (
    RunProfileError,
    live_runs_allowed,
    parse_profile,
    prepare_launch,
)
from hub.repository_workspace.security import redact_audit_detail
from hub.repository_workspace.service import RepositoryWorkspaceService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _occupy(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


class RunProfileSchemaTests(unittest.TestCase):
    def test_valid_profile_and_argv_array(self) -> None:
        profile = parse_profile(
            {
                "id": "py-http",
                "name": "HTTP",
                "executable": "python",
                "args": ["-m", "http.server", "{port}"],
                "working_directory": "{repository_path}",
                "environments": ["development"],
                "default_port": 8765,
                "local_url": "http://127.0.0.1:{port}/",
                "allowed_env_names": ["FLASK_APP"],
            }
        )
        self.assertEqual(profile.id, "py-http")
        self.assertEqual(profile.args[-1], "{port}")
        self.assertNotIn("shell", profile.executable)

    def test_disallowed_placeholder(self) -> None:
        with self.assertRaises(RunProfileError) as ctx:
            parse_profile(
                {
                    "id": "bad",
                    "executable": "python",
                    "args": ["{shell_cmd}"],
                    "environments": ["development"],
                }
            )
        self.assertEqual(ctx.exception.code, "bad_placeholder")

    def test_unsafe_executable_rejected(self) -> None:
        with self.assertRaises(RunProfileError) as ctx:
            parse_profile(
                {
                    "id": "bad",
                    "executable": "python && rm -rf /",
                    "args": [],
                    "environments": ["development"],
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_executable")

    def test_prepare_substitutes_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("ok", encoding="utf-8")
            profile = parse_profile(
                {
                    "id": "py-http",
                    "executable": "python",
                    "args": ["-m", "http.server", "{port}"],
                    "working_directory": "{repository_path}",
                    "environments": ["development", "stage"],
                    "default_port": 8000,
                    "local_url": "http://127.0.0.1:{port}/",
                    "port_env": "PORT",
                    "allowed_env_names": [],
                }
            )
            launch = prepare_launch(
                profile,
                repo_id="demo",
                repository_path=root,
                environment="development",
                port=9123,
            )
            self.assertEqual(launch.argv[-1], "9123")
            self.assertEqual(launch.port, 9123)
            self.assertEqual(launch.env.get("PORT"), "9123")
            self.assertEqual(launch.cwd, root.resolve())
            self.assertIn("9123", launch.local_url)

    def test_cwd_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            profile = parse_profile(
                {
                    "id": "escape",
                    "executable": "python",
                    "args": ["-c", "pass"],
                    "working_directory": "{repository_path}/../outside",
                    "environments": ["development"],
                }
            )
            with self.assertRaises(RunProfileError) as ctx:
                prepare_launch(
                    profile,
                    repo_id="demo",
                    repository_path=root,
                    environment="development",
                    port=8000,
                )
            self.assertEqual(ctx.exception.code, "cwd_escape")

    def test_live_requires_flag_and_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = parse_profile(
                {
                    "id": "live-app",
                    "executable": "python",
                    "args": ["-m", "http.server", "{port}"],
                    "working_directory": "{repository_path}",
                    "environments": ["live"],
                    "live_profile": True,
                }
            )
            with mock.patch.dict(os.environ, {"REPO_WS_ALLOW_LIVE_RUNS": ""}, clear=False):
                self.assertFalse(live_runs_allowed())
                with self.assertRaises(RunProfileError) as ctx:
                    prepare_launch(
                        profile,
                        repo_id="demo",
                        repository_path=root,
                        environment="live",
                        port=8000,
                        confirm_live=True,
                    )
                self.assertEqual(ctx.exception.code, "live_blocked")
            with mock.patch.dict(os.environ, {"REPO_WS_ALLOW_LIVE_RUNS": "true"}, clear=False):
                with self.assertRaises(RunProfileError) as ctx2:
                    prepare_launch(
                        profile,
                        repo_id="demo",
                        repository_path=root,
                        environment="live",
                        port=8000,
                        confirm_live=False,
                    )
                self.assertEqual(ctx2.exception.code, "confirm_required")
                launch = prepare_launch(
                    profile,
                    repo_id="demo",
                    repository_path=root,
                    environment="live",
                    port=8000,
                    confirm_live=True,
                )
                self.assertTrue(launch.live_profile)


class PortTests(unittest.TestCase):
    def test_occupied_and_alternate(self) -> None:
        port = _free_port()
        sock = _occupy(port)
        try:
            self.assertFalse(port_available(port))
            alt = find_available_port(port)
            self.assertIsNotNone(alt)
            self.assertNotEqual(alt, port)
            self.assertTrue(port_available(alt))
        finally:
            sock.close()


class LogRedactionTests(unittest.TestCase):
    def test_redact_secrets_and_retention(self) -> None:
        self.assertIn("[REDACTED]", redact_log_line("API_KEY=super-secret-value"))
        self.assertIn("[REDACTED]", redact_log_line("Authorization: Bearer abc.def"))
        with tempfile.TemporaryDirectory() as tmp:
            store = RunLogStore(Path(tmp))
            store.append("run1", "password=hunter2", stream="stdout")
            store.append("run1", "hello world", stream="stderr")
            payload = store.read("run1")
            blob = "\n".join(payload["lines"])
            self.assertNotIn("hunter2", blob)
            self.assertIn("[REDACTED]", blob)
            self.assertIn("hello world", blob)


class ProcessManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "index.html").write_text("<html>ok</html>", encoding="utf-8")
        self.state_dir = self.root / "state"
        self.log_dir = self.root / "logs"
        self.audits: list[tuple] = []

        def audit(action, target, detail, ok=True):
            self.audits.append((action, target, detail, ok))

        self.logs = RunLogStore(self.log_dir)
        self.pm = ProcessManager(state_dir=self.state_dir, logs=self.logs, audit=audit)
        self.profile = parse_profile(
            {
                "id": "py-http",
                "executable": sys.executable,
                "args": ["-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                "working_directory": "{repository_path}",
                "environments": ["development"],
                "default_port": 8765,
                "local_url": "http://127.0.0.1:{port}/",
                "health_url": "http://127.0.0.1:{port}/",
                "startup_timeout_seconds": 20,
            }
        )

    def tearDown(self) -> None:
        for run in list(self.pm.list_runs()):
            if run.status not in {"stopped", "failed"}:
                try:
                    self.pm.stop(run.run_id)
                except Exception:  # noqa: BLE001
                    pass
        self.tmp.cleanup()

    def _launch(self, *, repo_id: str = "repo-a", port: int | None = None):
        port = port or _free_port()
        return prepare_launch(
            self.profile,
            repo_id=repo_id,
            repository_path=self.repo,
            environment="development",
            port=port,
        )

    def test_start_stop_restart_and_logs(self) -> None:
        launch = self._launch()
        run = self.pm.start(repo_id="repo-a", launch=launch)
        self.assertIn(run.status, {"starting", "running", "healthy"})
        self.assertTrue(run.pid)
        # wait briefly for health / listeners
        deadline = time.time() + 8
        while time.time() < deadline:
            current = self.pm.get(run.run_id)
            assert current is not None
            if current.status in {"healthy", "running", "unhealthy"}:
                break
            time.sleep(0.2)
        logs = self.logs.read(run.run_id)
        self.assertGreaterEqual(logs["total_lines"], 1)
        stopped = self.pm.stop(run.run_id)
        self.assertEqual(stopped.status, "stopped")
        # restart
        with mock.patch.dict(os.environ, {"REPO_WS_ALLOW_LIVE_RUNS": "false"}, clear=False):
            again = self.pm.restart(
                run.run_id,
                lambda old: self._launch(port=_free_port()),
            )
        self.assertNotEqual(again.run_id, run.run_id)
        self.assertTrue(again.pid)
        self.pm.stop(again.run_id)
        actions = {a[0] for a in self.audits}
        self.assertIn("REPO_WS_RUN_START", actions)
        self.assertIn("REPO_WS_RUN_STOP", actions)
        self.assertIn("REPO_WS_RUN_RESTART", actions)
        for _action, _target, detail, _ok in self.audits:
            self.assertNotIn("password=", detail.lower())

    def test_duplicate_run_protection(self) -> None:
        port = _free_port()
        run = self.pm.start(repo_id="repo-a", launch=self._launch(port=port))
        with self.assertRaises(RunProfileError) as ctx:
            self.pm.start(repo_id="repo-a", launch=self._launch(port=port))
        self.assertEqual(ctx.exception.code, "duplicate_run")
        self.pm.stop(run.run_id)

    def test_multiple_repos_different_ports(self) -> None:
        a = self.pm.start(repo_id="repo-a", launch=self._launch(repo_id="repo-a"))
        b = self.pm.start(repo_id="repo-b", launch=self._launch(repo_id="repo-b"))
        self.assertNotEqual(a.port, b.port)
        self.assertNotEqual(a.pid, b.pid)
        self.pm.stop(a.run_id)
        self.pm.stop(b.run_id)

    def test_occupied_port_rejected(self) -> None:
        port = _free_port()
        sock = _occupy(port)
        try:
            with self.assertRaises(RunProfileError) as ctx:
                self.pm.start(repo_id="repo-a", launch=self._launch(port=port))
            self.assertEqual(ctx.exception.code, "port_occupied")
            self.assertIn("Suggested alternate", str(ctx.exception))
        finally:
            sock.close()

    def test_stale_pid_and_reuse_protection(self) -> None:
        # Fabricate a stopped-looking active record with a dead / unmatched PID
        from hub.repository_workspace.process_manager import ManagedRun, _utcnow

        fake = ManagedRun(
            run_id="stale1",
            repo_id="repo-a",
            profile_id="py-http",
            environment="development",
            port=1,
            status="running",
            pid=1,
            pgid=1,
            executable_path=str(Path(sys.executable).resolve()),
            started_at=_utcnow(),
            create_token="tok",
        )
        self.pm._save(fake)
        # Reconcile on a fresh manager (simulates hub restart)
        pm2 = ProcessManager(state_dir=self.state_dir, logs=self.logs)
        loaded = pm2.get("stale1")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "stopped")
        # Even if PID 1 somehow matched image, stop refuses unverified when no match
        fake2 = ManagedRun(
            run_id="reuse1",
            repo_id="repo-a",
            profile_id="py-http",
            environment="development",
            port=2,
            status="running",
            pid=os.getpid(),  # live but wrong executable fingerprint likely
            pgid=os.getpid(),
            executable_path=str(Path(sys.executable).resolve()) + ".not-real",
            started_at=_utcnow(),
            create_token="tok2",
        )
        self.pm._save(fake2)
        result = self.pm.stop("reuse1")
        self.assertEqual(result.status, "stopped")
        self.assertIn("unverified", result.error.lower())

    def test_stop_only_tracked_group(self) -> None:
        # Start a hub-managed process and an unrelated sleeper; stop must not kill sleeper
        import subprocess

        sleeper = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", "import time; time.sleep(60)"],
            shell=False,
        )
        try:
            run = self.pm.start(repo_id="repo-a", launch=self._launch())
            self.pm.stop(run.run_id)
            self.assertIsNone(sleeper.poll(), "unrelated process must remain alive")
        finally:
            sleeper.terminate()
            try:
                sleeper.wait(timeout=5)
            except Exception:  # noqa: BLE001
                sleeper.kill()


class ServiceScopeTests(unittest.TestCase):
    def test_repository_scope_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_a = root / "a"
            repo_b = root / "b"
            repo_a.mkdir()
            repo_b.mkdir()
            (repo_a / "index.html").write_text("a", encoding="utf-8")
            (repo_b / "index.html").write_text("b", encoding="utf-8")
            state = root / "state"
            logs = RunLogStore(root / "logs")
            pm = ProcessManager(state_dir=state, logs=logs)
            svc = RepositoryWorkspaceService(process_manager=pm)
            ra = Repository(
                id="repo-a",
                name="A",
                type="command",
                enabled=True,
                local_path=str(repo_a),
            )
            rb = Repository(
                id="repo-b",
                name="B",
                type="command",
                enabled=True,
                local_path=str(repo_b),
            )
            profile = parse_profile(
                {
                    "id": "py-http",
                    "executable": sys.executable,
                    "args": ["-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                    "working_directory": "{repository_path}",
                    "environments": ["development"],
                    "repository_ids": ["repo-a"],
                }
            )
            with mock.patch(
                "hub.repository_workspace.service.load_run_profiles",
                return_value=[profile],
            ), mock.patch(
                "hub.repository_workspace.service.profiles_for_repository",
                side_effect=lambda rid, profiles=None: [
                    p for p in [profile] if p.applies_to(rid)
                ],
            ):
                ids_a = {p["id"] for p in svc.list_profiles(ra)}
                ids_b = {p["id"] for p in svc.list_profiles(rb)}
                self.assertIn("py-http", ids_a)
                self.assertNotIn("py-http", ids_b)
                run = svc.start_run(
                    ra, profile_id="py-http", environment="development", port=_free_port()
                )
                with self.assertRaises(Exception):
                    svc.get_run(rb, run["run_id"])
                svc.stop_run(ra, run["run_id"])


class AgentReadonlyReuseTests(unittest.TestCase):
    def test_agent_uses_workspace_search_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            (repo_path / "src").mkdir()
            (repo_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
            (repo_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
            repo = Repository(
                id="demo-repo",
                name="Demo",
                type="command",
                enabled=True,
                local_path=str(repo_path),
            )
            registry = Registry(repositories=[repo], defaults=RegistryDefaults())
            ctx = AgentToolsContext(registry=registry, repository_ids=["demo-repo"])
            search = json.loads(
                execute_tool("repo_search", {"query": "app.py", "limit": 10}, ctx)
            )
            self.assertTrue(search["matches"])
            read = json.loads(
                execute_tool(
                    "read_file",
                    {"repo_id": "demo-repo", "path": "src/app.py"},
                    ctx,
                )
            )
            self.assertIn("print", read["content"])
            blocked = json.loads(
                execute_tool(
                    "read_file",
                    {"repo_id": "demo-repo", "path": ".env"},
                    ctx,
                )
            )
            self.assertIn("error", blocked)
            # No command-execution tools exist
            from hub.agent_center.openai_tools import ALLOWED_TOOLS

            self.assertNotIn("run_command", ALLOWED_TOOLS)
            self.assertNotIn("shell", ALLOWED_TOOLS)


class AuditRedactionTests(unittest.TestCase):
    def test_audit_detail_redacts_tokens(self) -> None:
        text = redact_audit_detail("token=abc123SECRET and password=xyz")
        self.assertNotIn("abc123SECRET", text)
        self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()
