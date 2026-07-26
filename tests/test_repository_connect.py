"""Connect Local Workspace — scan / preview / confirm save."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.registry.models import RegistryDefaults, Repository
from hub.registry.store import RegistryStore
from hub.repository_workspace.connect import preview_connect, save_connect
from hub.repository_workspace.connect_scan import scan_workspace_path
from hub.repository_workspace.security import WorkspaceSecurityError
from hub.repository_workspace.run_profiles import load_run_profiles, parse_profile


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )


class ConnectScanTests(unittest.TestCase):
    def test_missing_and_inaccessible(self) -> None:
        missing = scan_workspace_path(r"C:\this\path\does\not\exist-central-hub")
        self.assertFalse(missing.ok)
        self.assertEqual(missing.error_code, "missing")

        empty = scan_workspace_path("")
        self.assertEqual(empty.error_code, "missing_path")

        with tempfile.NamedTemporaryFile(delete=False) as fh:
            file_path = fh.name
        try:
            as_file = scan_workspace_path(file_path)
            self.assertEqual(as_file.error_code, "not_directory")
        finally:
            Path(file_path).unlink(missing_ok=True)

    def test_ordinary_non_git_folder_and_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "My Project Folder"
            root.mkdir()
            (root / "index.html").write_text("<html></html>", encoding="utf-8")
            (root / "README.md").write_text("# Hi", encoding="utf-8")
            # Quoted path with spaces
            scan = scan_workspace_path(f'"{root}"')
            self.assertTrue(scan.ok)
            self.assertFalse(scan.is_git)
            self.assertIn("README.md", scan.readme_files)
            self.assertTrue(any(p.suggestion_id.endswith("python-http") for p in scan.suggested_profiles))

    def test_valid_git_repo_remote_match_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Data-Script"
            root.mkdir()
            _git(root, "init")
            _git(root, "remote", "add", "origin", "https://github.com/PMNP-IS/Data-Script.git")
            (root / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
            (root / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
            (root / "app.py").write_text("from flask import Flask\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")

            match = scan_workspace_path(
                str(root),
                registered_git_url="https://github.com/PMNP-IS/Data-Script",
                repo_id="data-script",
            )
            self.assertTrue(match.ok)
            self.assertTrue(match.is_git)
            self.assertTrue(match.remote_matches_registered)
            self.assertFalse(match.remote_mismatch)
            self.assertIn("Python", match.languages)
            self.assertIn("Flask", match.frameworks)
            self.assertIn("AGENTS.md", match.ai_instruction_files)
            # Secrets never appear as entry/readme/ai lists
            self.assertNotIn(".env", match.entry_points)
            self.assertNotIn(".env", match.readme_files)

            mismatch = scan_workspace_path(
                str(root),
                registered_git_url="https://github.com/other/other.git",
                repo_id="data-script",
            )
            self.assertTrue(mismatch.remote_mismatch)
            self.assertTrue(any("does not match" in w for w in mismatch.warnings))

    def test_node_framework_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "web app"
            root.mkdir()
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "web",
                        "scripts": {"dev": "vite", "build": "vite build"},
                        "devDependencies": {"vite": "5.0.0", "react": "18.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            scan = scan_workspace_path(str(root), repo_id="web-app")
            self.assertIn("Vite", scan.frameworks)
            self.assertIn("React", scan.frameworks)
            self.assertTrue(any(s["name"] == "dev" for s in scan.package_scripts))
            self.assertTrue(scan.suggested_profiles)

    def test_secret_file_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".env").write_text("TOKEN=super-secret-value\n", encoding="utf-8")
            (root / "README.md").write_text("ok", encoding="utf-8")
            scan = scan_workspace_path(str(root))
            blob = json.dumps(scan.to_public())
            self.assertNotIn("super-secret-value", blob)
            self.assertNotIn(".env", scan.entry_points)

    def test_no_command_execution_during_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "package.json").write_text(
                '{"scripts":{"dev":"node server.js"}}', encoding="utf-8"
            )
            with mock.patch("subprocess.run") as mocked, mock.patch(
                "subprocess.Popen"
            ) as mocked_popen:
                scan = scan_workspace_path(str(root), repo_id="r1")
                self.assertTrue(scan.ok)
                mocked.assert_not_called()
                mocked_popen.assert_not_called()
        # Module must not import subprocess for scanning
        import hub.repository_workspace.connect_scan as mod

        self.assertFalse(hasattr(mod, "subprocess"))


class ConnectSaveTests(unittest.TestCase):
    def test_confirm_required_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_dir = base / "checkout"
            repo_dir.mkdir()
            (repo_dir / "README.md").write_text("x", encoding="utf-8")
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
            profiles_path = base / "run_profiles.yaml"
            profiles_path.write_text("profiles: []\n", encoding="utf-8")
            store = RegistryStore(registry_path)
            repo = Repository(
                id="demo-repo",
                name="Demo",
                type="command",
                enabled=True,
                git_url="https://github.com/example/demo",
            )
            with self.assertRaises(WorkspaceSecurityError) as ctx:
                save_connect(
                    repo,
                    store=store,
                    path=str(repo_dir),
                    confirm_save=False,
                    profiles_path=profiles_path,
                )
            self.assertEqual(ctx.exception.code, "confirm_required")

            audits: list[tuple] = []

            def audit(action, target, detail, ok=True):
                audits.append((action, target, detail, ok))

            preview = preview_connect(repo, path=str(repo_dir))
            profile = preview["editable"]["profiles"][0]
            result = save_connect(
                repo,
                store=store,
                path=str(repo_dir),
                name="Demo Connected",
                confirm_save=True,
                selected_profiles=[
                    {
                        **profile,
                        "id": profile["suggestion_id"],
                        "args": profile["args"],
                    }
                ],
                audit=audit,
                profiles_path=profiles_path,
            )
            self.assertEqual(result["local_path"], str(repo_dir.resolve()))
            raw = store.get_raw("demo-repo")
            assert raw is not None
            self.assertEqual(raw["local_path"], str(repo_dir.resolve()))
            self.assertEqual(raw["name"], "Demo Connected")
            loaded = load_run_profiles(profiles_path)
            self.assertTrue(any(p.id == profile["suggestion_id"] or p.id.endswith("-connected") for p in loaded))
            self.assertTrue(any(a[0] == "REPO_WS_CONNECT_SAVE" for a in audits))
            for _a, _t, detail, _ok in audits:
                self.assertNotIn("SECRET=", detail)

    def test_replace_path_and_mismatch_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = base / "old"
            new = base / "new"
            old.mkdir()
            new.mkdir()
            _git(new, "init")
            _git(new, "remote", "add", "origin", "https://github.com/other/other.git")
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
                f"    local_path: \"{old.as_posix()}\"\n"
                "    capabilities: []\n",
                encoding="utf-8",
            )
            store = RegistryStore(registry_path)
            repo = Repository(
                id="demo-repo",
                name="Demo",
                type="command",
                enabled=True,
                git_url="https://github.com/example/demo",
                local_path=str(old),
            )
            with self.assertRaises(WorkspaceSecurityError) as ctx:
                save_connect(
                    repo,
                    store=store,
                    path=str(new),
                    confirm_save=True,
                    confirm_remote_mismatch=False,
                    confirm_replace_path=True,
                )
            self.assertEqual(ctx.exception.code, "confirm_remote_mismatch")

            with self.assertRaises(WorkspaceSecurityError) as ctx2:
                save_connect(
                    repo,
                    store=store,
                    path=str(new),
                    confirm_save=True,
                    confirm_remote_mismatch=True,
                    confirm_replace_path=False,
                )
            self.assertEqual(ctx2.exception.code, "confirm_replace_path")

            save_connect(
                repo,
                store=store,
                path=str(new),
                confirm_save=True,
                confirm_remote_mismatch=True,
                confirm_replace_path=True,
            )
            raw = store.get_raw("demo-repo")
            assert raw is not None
            self.assertEqual(Path(raw["local_path"]).resolve(), new.resolve())

    def test_repository_scope_isolation_for_profiles(self) -> None:
        entry = parse_profile(
            {
                "id": "demo-flask",
                "name": "Demo Flask",
                "executable": "python",
                "args": ["-m", "flask", "run", "--port", "{port}"],
                "working_directory": "{repository_path}",
                "environments": ["development"],
                "repository_ids": ["demo-repo"],
            }
        )
        self.assertTrue(entry.applies_to("demo-repo"))
        self.assertFalse(entry.applies_to("other-repo"))


if __name__ == "__main__":
    unittest.main()
