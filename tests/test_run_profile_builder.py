"""Run Profile Builder — CRUD, merge, port modes, safety, connect suggestions."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.registry.models import Repository
from hub.registry.store import RegistryStore
from hub.repository_workspace.connect import preview_connect, save_connect, save_connect_suggestions
from hub.repository_workspace.ports import port_available
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.profile_store import RunProfileStore
from hub.repository_workspace.run_profiles import (
    RunProfileError,
    load_run_profiles,
    merged_profiles_for_repository,
    parse_profile,
    prepare_launch,
    profiles_for_repository,
)
from hub.repository_workspace.security import WorkspaceSecurityError
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


def _repo(tmp: Path, repo_id: str = "demo-repo") -> Repository:
    root = tmp / repo_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    return Repository(
        id=repo_id,
        name="Demo",
        type="command",
        enabled=True,
        local_path=str(root),
        working_directory=str(root),
    )


class PortModeParseTests(unittest.TestCase):
    def test_none_fixed_argument_env_modes(self) -> None:
        none = parse_profile(
            {
                "id": "cli",
                "executable": "python",
                "args": ["script.py"],
                "port_mode": "none",
                "local_url": "http://127.0.0.1/",
                "environments": ["development"],
            }
        )
        self.assertEqual(none.port_mode, "none")
        self.assertFalse(none.uses_port)

        fixed = parse_profile(
            {
                "id": "fixed",
                "executable": "python",
                "args": ["app.py"],
                "port_mode": "fixed",
                "fixed_port": 5050,
                "local_url": "http://127.0.0.1:5050/",
                "environments": ["live"],
                "live_profile": True,
            }
        )
        self.assertEqual(fixed.port_mode, "fixed")
        self.assertEqual(fixed.fixed_port, 5050)
        self.assertFalse(fixed.allows_dynamic_port)

        arg = parse_profile(
            {
                "id": "arg",
                "executable": "python",
                "args": ["-m", "http.server"],
                "port_mode": "argument",
                "port_arg": "--port {port}",
                "default_port": 8000,
                "environments": ["development"],
            }
        )
        self.assertEqual(arg.port_mode, "argument")
        self.assertTrue(arg.allows_dynamic_port)

        env = parse_profile(
            {
                "id": "envp",
                "executable": "python",
                "args": ["app.py"],
                "port_mode": "environment_variable",
                "port_env": "PORT",
                "default_port": 9000,
                "environments": ["development"],
            }
        )
        self.assertEqual(env.port_mode, "environment_variable")
        self.assertEqual(env.port_env, "PORT")

    def test_args_must_be_array_not_shell_string(self) -> None:
        with self.assertRaises(RunProfileError) as ctx:
            parse_profile(
                {
                    "id": "bad",
                    "executable": "python",
                    "args": "app.py --port 8000 && echo hi",
                    "environments": ["development"],
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_args")

    def test_placeholder_allowlist_and_cwd_jail(self) -> None:
        with self.assertRaises(RunProfileError) as ctx:
            parse_profile(
                {
                    "id": "bad",
                    "executable": "python",
                    "args": ["{shell}"],
                    "environments": ["development"],
                }
            )
        self.assertEqual(ctx.exception.code, "bad_placeholder")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok").mkdir()
            profile = parse_profile(
                {
                    "id": "cwd",
                    "executable": "python",
                    "args": ["-c", "print(1)"],
                    "working_directory": "{repository_path}/ok",
                    "port_mode": "none",
                    "local_url": "http://127.0.0.1/",
                    "environments": ["development"],
                }
            )
            launch = prepare_launch(
                profile,
                repo_id="x",
                repository_path=root,
                environment="development",
            )
            self.assertEqual(launch.cwd, (root / "ok").resolve())

            bad = parse_profile(
                {
                    "id": "escape",
                    "executable": "python",
                    "args": ["-m", "http.server", "8765"],
                    "working_directory": "{repository_path}/../outside",
                    "port_mode": "none",
                    "local_url": "http://127.0.0.1/",
                    "environments": ["development"],
                }
            )
            with self.assertRaises(RunProfileError) as ctx2:
                prepare_launch(
                    bad,
                    repo_id="x",
                    repository_path=root,
                    environment="development",
                )
            self.assertEqual(ctx2.exception.code, "cwd_escape")

    def test_localhost_url_required(self) -> None:
        with self.assertRaises(RunProfileError) as ctx:
            parse_profile(
                {
                    "id": "remote",
                    "executable": "python",
                    "args": ["app.py"],
                    "port_mode": "fixed",
                    "fixed_port": 8080,
                    "local_url": "http://example.com:8080/",
                    "environments": ["development"],
                }
            )
        self.assertEqual(ctx.exception.code, "non_local_url")


class MergeAndCrudTests(unittest.TestCase):
    def test_yaml_and_db_merge_with_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            yaml_path = base / "profiles.yaml"
            yaml_path.write_text(
                "profiles:\n"
                "  - id: shared\n"
                "    name: YAML Shared\n"
                "    executable: python\n"
                "    args: ['-m', 'http.server', '{port}']\n"
                "    environments: [development]\n"
                "    default_port: 8765\n"
                "    local_url: 'http://127.0.0.1:{port}/'\n",
                encoding="utf-8",
            )
            store = RunProfileStore(base / "profiles.db")
            yaml_profiles = load_run_profiles(yaml_path)
            store.upsert(
                "repo-a",
                {
                    "id": "shared",
                    "name": "DB Override",
                    "executable": "python",
                    "args": ["-m", "http.server", "{port}"],
                    "environments": ["development"],
                    "default_port": 9001,
                    "port_mode": "argument",
                    "local_url": "http://127.0.0.1:{port}/",
                    "enabled": True,
                    "approved": True,
                    "source": "user",
                },
            )
            store.upsert(
                "repo-a",
                {
                    "id": "custom-only",
                    "name": "Custom",
                    "executable": "python",
                    "args": ["app.py"],
                    "environments": ["development"],
                    "port_mode": "none",
                    "local_url": "http://127.0.0.1/",
                    "enabled": True,
                    "approved": True,
                    "source": "user",
                },
            )
            merged = merged_profiles_for_repository(
                "repo-a",
                store=store,
                yaml_profiles=yaml_profiles,
                include_disabled=True,
                include_unapproved=True,
            )
            by_id = {p.id: p for p in merged}
            self.assertEqual(by_id["shared"].name, "DB Override")
            self.assertEqual(by_id["shared"].default_port, 9001)
            self.assertIn("custom-only", by_id)

            # Other repo still sees YAML template, not repo-a DB rows
            other = {
                p.id: p
                for p in merged_profiles_for_repository(
                    "repo-b", store=store, yaml_profiles=yaml_profiles
                )
            }
            self.assertEqual(other["shared"].name, "YAML Shared")
            self.assertNotIn("custom-only", other)

    def test_crud_enable_disable_duplicate_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base)
            store = RunProfileStore(base / "profiles.db")
            svc = RepositoryWorkspaceService()
            svc.profile_store = store

            created = svc.save_managed_profile(
                repo,
                {
                    "id": "demo-http",
                    "name": "Demo HTTP",
                    "executable": "python",
                    "args": ["-m", "http.server", "{port}"],
                    "environments": ["development"],
                    "port_mode": "argument",
                    "default_port": 8765,
                    "local_url": "http://127.0.0.1:{port}/",
                },
                approve=True,
            )
            self.assertTrue(created["approved"])
            self.assertTrue(created["enabled"])

            edited = svc.save_managed_profile(
                repo,
                {
                    **created,
                    "name": "Demo HTTP Edited",
                    "args": created["args_template"],
                    "local_url": created["local_url_template"],
                    "health_url": created.get("health_url_template"),
                },
                approve=True,
            )
            self.assertEqual(edited["name"], "Demo HTTP Edited")

            dup = svc.duplicate_managed_profile(repo, "demo-http")
            self.assertEqual(dup["id"], "demo-http-copy")
            self.assertFalse(dup["enabled"])

            disabled = svc.set_profile_enabled(repo, "demo-http", False)
            self.assertFalse(disabled["enabled"])
            run_ids = {p["id"] for p in svc.list_profiles(repo)}
            self.assertNotIn("demo-http", run_ids)

            enabled = svc.set_profile_enabled(repo, "demo-http", True)
            self.assertTrue(enabled["enabled"])

            svc.delete_managed_profile(repo, "demo-http")
            self.assertIsNone(store.get(repo.id, "demo-http"))

    def test_secret_names_only_no_values_in_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base)
            store = RunProfileStore(base / "profiles.db")
            svc = RepositoryWorkspaceService()
            svc.profile_store = store
            os.environ["FLASK_APP"] = "secret_app_value"
            try:
                profile = svc.save_managed_profile(
                    repo,
                    {
                        "id": "flasky",
                        "name": "Flasky",
                        "executable": "python",
                        "args": ["-m", "flask", "run", "--port", "{port}"],
                        "environments": ["development"],
                        "port_mode": "argument",
                        "default_port": 5000,
                        "local_url": "http://127.0.0.1:{port}/",
                        "allowed_env_names": ["FLASK_APP"],
                    },
                )
                self.assertEqual(profile["allowed_env_names"], ["FLASK_APP"])
                blob = str(profile)
                self.assertNotIn("secret_app_value", blob)
                preview = svc.test_managed_profile(
                    repo,
                    {
                        "id": "flasky",
                        "name": "Flasky",
                        "executable": "python",
                        "args": ["-m", "flask", "run", "--port", "{port}"],
                        "environments": ["development"],
                        "port_mode": "argument",
                        "default_port": 5000,
                        "local_url": "http://127.0.0.1:{port}/",
                        "allowed_env_names": ["FLASK_APP"],
                        "enabled": True,
                        "approved": True,
                    },
                    port=5000,
                )
                self.assertIn("FLASK_APP", preview["env_names"])
                self.assertNotIn("secret_app_value", str(preview))
            finally:
                os.environ.pop("FLASK_APP", None)


class FixedPortAndLiveGateTests(unittest.TestCase):
    def test_occupied_fixed_port_blocks_without_kill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base)
            port = _free_port()
            holder = _occupy(port)
            try:
                store = RunProfileStore(base / "profiles.db")
                pm = ProcessManager(state_dir=base / "state", logs=None)
                svc = RepositoryWorkspaceService(process_manager=pm)
                svc.profile_store = store
                svc.save_managed_profile(
                    repo,
                    {
                        "id": "fixed-app",
                        "name": "Fixed",
                        "executable": sys.executable,
                        "args": ["-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                        "environments": ["development"],
                        "port_mode": "fixed",
                        "fixed_port": port,
                        "local_url": f"http://127.0.0.1:{port}/",
                    },
                )
                with self.assertRaises(Exception) as ctx:
                    svc.start_run(
                        repo,
                        profile_id="fixed-app",
                        environment="development",
                    )
                code = getattr(ctx.exception, "code", "")
                self.assertIn(code, {"port_occupied", "process_conflict"})
                self.assertNotIn("Suggested alternate", str(ctx.exception))
                self.assertFalse(port_available(port))
            finally:
                holder.close()

    def test_dynamic_port_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base)
            store = RunProfileStore(base / "profiles.db")
            pm = ProcessManager(state_dir=base / "state")
            svc = RepositoryWorkspaceService(process_manager=pm)
            svc.profile_store = store
            preferred = _free_port()
            svc.save_managed_profile(
                repo,
                {
                    "id": "dyn",
                    "name": "Dyn",
                    "executable": sys.executable,
                    "args": ["-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                    "environments": ["development"],
                    "port_mode": "argument",
                    "default_port": preferred,
                    "local_url": "http://127.0.0.1:{port}/",
                },
            )
            run = svc.start_run(
                repo,
                profile_id="dyn",
                environment="development",
                port=preferred,
            )
            self.assertEqual(run["port"], preferred)
            svc.stop_run(repo, run["run_id"])

    def test_live_and_write_capable_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            profile = parse_profile(
                {
                    "id": "live-write",
                    "executable": "python",
                    "args": ["app.py"],
                    "port_mode": "fixed",
                    "fixed_port": 5050,
                    "local_url": "http://127.0.0.1:5050/",
                    "environments": ["live"],
                    "live_profile": True,
                    "write_capable": True,
                    "enabled": True,
                    "approved": True,
                }
            )
            with mock.patch.dict(os.environ, {"REPO_WS_ALLOW_LIVE_RUNS": ""}, clear=False):
                with self.assertRaises(RunProfileError) as ctx:
                    prepare_launch(
                        profile,
                        repo_id="x",
                        repository_path=root,
                        environment="live",
                        confirm_live=True,
                    )
                self.assertEqual(ctx.exception.code, "live_blocked")
            with mock.patch.dict(os.environ, {"REPO_WS_ALLOW_LIVE_RUNS": "true"}, clear=False):
                with self.assertRaises(RunProfileError) as ctx2:
                    prepare_launch(
                        profile,
                        repo_id="x",
                        repository_path=root,
                        environment="live",
                        confirm_live=False,
                    )
                self.assertEqual(ctx2.exception.code, "confirm_required")
                launch = prepare_launch(
                    profile,
                    repo_id="x",
                    repository_path=root,
                    environment="live",
                    confirm_live=True,
                )
                self.assertEqual(launch.port, 5050)
                self.assertTrue(launch.live_profile)


class ConnectSuggestionTests(unittest.TestCase):
    def test_suggestion_to_approved_and_rescan_preserves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_dir = base / "checkout"
            repo_dir.mkdir()
            (repo_dir / "README.md").write_text("x", encoding="utf-8")
            (repo_dir / "app.py").write_text("print(1)\n", encoding="utf-8")
            registry_path = base / "repositories.yaml"
            registry_path.write_text(
                "defaults:\n  job_timeout_seconds: 10\n  max_concurrent_jobs: 1\n"
                "  require_explicit_apply: true\n"
                "repositories:\n"
                "  - id: demo-repo\n"
                "    name: Demo\n"
                "    type: command\n"
                "    enabled: true\n"
                "    git_url: https://github.com/example/demo\n"
                "    capabilities: []\n",
                encoding="utf-8",
            )
            store = RegistryStore(registry_path)
            profile_store = RunProfileStore(base / "profiles.db")
            repo = Repository(
                id="demo-repo",
                name="Demo",
                type="command",
                enabled=True,
                git_url="https://github.com/example/demo",
            )
            preview = preview_connect(repo, path=str(repo_dir), profile_store=profile_store)
            self.assertIn("approved_profiles", preview)
            self.assertIn("suggested_profiles", preview)
            suggestion = preview["suggested_profiles"][0]
            result = save_connect(
                repo,
                store=store,
                path=str(repo_dir),
                confirm_save=True,
                selected_profiles=[
                    {
                        **suggestion,
                        "id": suggestion["suggestion_id"],
                    }
                ],
                profile_store=profile_store,
            )
            self.assertTrue(result["profiles_added"])
            row = profile_store.get("demo-repo", result["profiles_added"][0])
            assert row is not None
            self.assertFalse(row["approved"])
            self.assertFalse(row["enabled"])
            self.assertEqual(row["source"], "suggestion")

            # Approve via builder
            svc = RepositoryWorkspaceService()
            svc.profile_store = profile_store
            connected = Repository(
                id="demo-repo",
                name="Demo Connected",
                type="command",
                enabled=True,
                local_path=str(repo_dir.resolve()),
                working_directory=str(repo_dir.resolve()),
            )
            approved = svc.save_managed_profile(
                connected,
                {
                    **row,
                    "enabled": True,
                },
                approve=True,
                source="user",
            )
            self.assertTrue(approved["approved"])

            # Rescan suggestion with same id must not overwrite approved
            again = save_connect_suggestions(
                "demo-repo",
                [
                    {
                        **suggestion,
                        "id": suggestion["suggestion_id"],
                        "name": "SHOULD NOT OVERWRITE",
                    }
                ],
                store=profile_store,
            )
            self.assertEqual(again, [])
            kept = profile_store.get("demo-repo", suggestion["suggestion_id"])
            assert kept is not None
            self.assertTrue(kept["approved"])
            self.assertNotEqual(kept["name"], "SHOULD NOT OVERWRITE")

    def test_yaml_templates_remain_compatible(self) -> None:
        profiles = load_run_profiles()
        ids = {p.id for p in profiles}
        self.assertIn("python-http", ids)
        self.assertIn("flask-dev", ids)
        self.assertIn("fastapi-uvicorn", ids)
        self.assertIn("node-dev", ids)
        self.assertIn("pmnp-live-processing", ids)
        fixed = next(p for p in profiles if p.id == "pmnp-live-processing")
        self.assertEqual(fixed.port_mode, "fixed")
        self.assertEqual(fixed.fixed_port, 5050)
        self.assertTrue(fixed.live_profile)


if __name__ == "__main__":
    unittest.main()
