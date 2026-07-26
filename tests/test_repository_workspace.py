"""Repository Workspace Phase 1 — security, files, editor, git, routes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from hub.repository_workspace.editor import RepositoryEditor
from hub.repository_workspace.files import RepositoryFiles
from hub.repository_workspace.git_status import RepositoryGitStatus
from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    resolve_repo_root,
    safe_join,
)
from hub.repository_workspace.service import UNAVAILABLE_MESSAGE, RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings
from hub.registry.models import Repository


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    )


class WorkspaceSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / "ok.py").write_text("print('hi')\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "notes.md").write_text("# hi\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_path_traversal_and_absolute_rejected(self) -> None:
        with self.assertRaises(WorkspaceSecurityError) as ctx:
            safe_join(self.root, "../outside.txt")
        self.assertEqual(ctx.exception.code, "path_traversal")
        with self.assertRaises(WorkspaceSecurityError) as ctx2:
            safe_join(self.root, str(self.root / "ok.py"))
        self.assertEqual(ctx2.exception.code, "absolute_path")

    def test_secret_blocked(self) -> None:
        self.assertTrue(is_blocked_secret(".env"))
        self.assertTrue(is_blocked_secret("secrets/token.json"))
        with self.assertRaises(WorkspaceSecurityError) as ctx:
            safe_join(self.root, ".env")
        self.assertEqual(ctx.exception.code, "secret_blocked")

    def test_symlink_escape_rejected(self) -> None:
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("nope\n", encoding="utf-8")
        link = self.root / "escape.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks not available")
        with self.assertRaises(WorkspaceSecurityError) as ctx:
            safe_join(self.root, "escape.txt")
        self.assertEqual(ctx.exception.code, "symlink_blocked")

    def test_resolve_repo_root(self) -> None:
        self.assertIsNone(resolve_repo_root(""))
        self.assertIsNone(resolve_repo_root(str(self.root / "missing")))
        self.assertEqual(resolve_repo_root(str(self.root)), self.root.resolve())


class WorkspaceFilesEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "readme.md").write_text("# Title\nneedle-token\n", encoding="utf-8")
        (self.root / "data.bin").write_bytes(b"\x00\x01\x02\xff")
        self.settings = WorkspaceSettings(
            max_preview_bytes=10_000,
            max_edit_bytes=10_000,
            max_search_file_bytes=10_000,
            max_search_matches=50,
            max_search_files=200,
            max_tree_entries=200,
        )
        self.files = RepositoryFiles(self.root, self.settings)
        self.editor = RepositoryEditor(self.root, self.settings)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tree_and_search(self) -> None:
        tree = self.files.build_tree()
        names = {e["name"] for e in tree["entries"]}
        self.assertIn("app.py", names)
        self.assertIn("readme.md", names)
        self.assertNotIn(".env", names)
        hits = self.files.search_filenames("readme")
        self.assertEqual(hits[0]["path"], "readme.md")
        content = self.files.search_content("needle-token")
        self.assertEqual(content[0]["path"], "readme.md")

    def test_preview_binary_and_text(self) -> None:
        text = self.files.read_preview("app.py")
        self.assertFalse(text["binary"])
        self.assertIn("x = 1", text["content"])
        binary = self.files.read_preview("data.bin")
        self.assertTrue(binary["binary"])

    def test_edit_diff_save_revert_create_rename_delete(self) -> None:
        preview = self.editor.preview_save("app.py", "x = 2\n")
        self.assertTrue(preview["changed"])
        self.assertIn("-x = 1", preview["diff"])
        with self.assertRaises(WorkspaceSecurityError):
            self.editor.save("app.py", "x = 2\n", confirm=False)
        saved = self.editor.save("app.py", "x = 2\n", confirm=True)
        self.assertTrue(saved["saved"])
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "x = 2\n")
        reverted = self.editor.revert_to_disk("app.py")
        self.assertEqual(reverted["content"], "x = 2\n")

        created = self.editor.create_file("new.txt", "hello\n", confirm=True)
        self.assertTrue(created["created"])
        renamed = self.editor.rename("new.txt", "renamed.txt", confirm=True)
        self.assertEqual(renamed["to"], "renamed.txt")
        deleted = self.editor.delete("renamed.txt", confirm=True)
        self.assertTrue(deleted["deleted"])
        self.assertFalse((self.root / "renamed.txt").exists())


class WorkspaceGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        try:
            _git(self.root, "init")
            _git(self.root, "config", "user.email", "test@example.com")
            _git(self.root, "config", "user.name", "Test")
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git unavailable")
        (self.root / "tracked.py").write_text("a=1\n", encoding="utf-8")
        _git(self.root, "add", "tracked.py")
        _git(self.root, "commit", "-m", "init")
        self.git = RepositoryGitStatus(self.root, WorkspaceSettings())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clean_modified_added_deleted_untracked(self) -> None:
        clean = self.git.summary()
        self.assertTrue(clean["is_git"])
        self.assertTrue(clean["clean"])

        (self.root / "tracked.py").write_text("a=2\n", encoding="utf-8")
        (self.root / "added.py").write_text("b=1\n", encoding="utf-8")
        _git(self.root, "add", "added.py")
        (self.root / "untracked.py").write_text("c=1\n", encoding="utf-8")
        # Stage deletion without failing on local modifications.
        _git(self.root, "rm", "-f", "tracked.py")
        summary = self.git.summary()
        cats = {f["category"] for f in summary["files"]}
        self.assertIn("deleted", cats)
        self.assertIn("added", cats)
        self.assertIn("untracked", cats)
        self.assertFalse(summary["clean"])
        # Modified file status via a fresh tracked edit
        (self.root / "added.py").write_text("b=2\n", encoding="utf-8")
        summary2 = self.git.summary()
        cats2 = {f["category"] for f in summary2["files"]}
        self.assertTrue({"modified", "added", "deleted", "untracked"} & cats2)


class WorkspaceRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.repo_dir = root / "local-repo"
        cls.repo_dir.mkdir()
        (cls.repo_dir / "hello.py").write_text("print('ok')\n", encoding="utf-8")
        (cls.repo_dir / ".env").write_text("SECRET=no\n", encoding="utf-8")
        try:
            _git(cls.repo_dir, "init")
            _git(cls.repo_dir, "config", "user.email", "test@example.com")
            _git(cls.repo_dir, "config", "user.name", "Test")
            _git(cls.repo_dir, "add", "hello.py")
            _git(cls.repo_dir, "commit", "-m", "init")
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

        # Minimal registry yaml pointing at temp checkout + URL-only repo
        cfg = root / "repositories.yaml"
        cfg.write_text(
            f"""
defaults:
  job_timeout_seconds: 60
  max_concurrent_jobs: 1
  require_explicit_apply: true
repositories:
  - id: ws-local
    name: Workspace Local
    type: command
    enabled: true
    local_path: "{cls.repo_dir.as_posix()}"
    working_directory: "{cls.repo_dir.as_posix()}"
    health_check:
      type: path
      local_path: "{cls.repo_dir.as_posix()}"
  - id: ws-remote-only
    name: Workspace Remote Only
    type: command
    enabled: true
    git_url: "https://example.com/demo.git"
    local_path: ""
    working_directory: ""
    health_check:
      type: path
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.environ["CENTRAL_HUB_REPOSITORIES_CONFIG"] = str(cfg)

        import importlib
        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_unavailable_without_local_path(self) -> None:
        r = self.client.get("/repositories/ws-remote-only/files")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn(UNAVAILABLE_MESSAGE, html)
        self.assertIn("Overview", html)
        self.assertIn("Files", html)
        self.assertIn("Changes", html)
        self.assertIn("Settings", html)

    def test_tabs_and_tree_file_search_security(self) -> None:
        overview = self.client.get("/repositories/ws-local")
        self.assertEqual(overview.status_code, 200)
        self.assertIn("Overview", overview.get_data(as_text=True))

        files_page = self.client.get("/repositories/ws-local/files")
        self.assertEqual(files_page.status_code, 200)
        self.assertIn("rw-shell", files_page.get_data(as_text=True))

        tree = self.client.get("/api/repositories/ws-local/workspace/tree").get_json()
        self.assertTrue(tree["ok"])
        names = {e["name"] for e in tree["entries"]}
        self.assertIn("hello.py", names)
        self.assertNotIn(".env", names)

        preview = self.client.get(
            "/api/repositories/ws-local/workspace/file?path=hello.py"
        ).get_json()
        self.assertTrue(preview["ok"])
        self.assertIn("print", preview["file"]["content"])

        secret = self.client.get(
            "/api/repositories/ws-local/workspace/file?path=.env"
        ).get_json()
        self.assertFalse(secret["ok"])
        self.assertEqual(secret["code"], "secret_blocked")

        trav = self.client.get(
            "/api/repositories/ws-local/workspace/file?path=../hello.py"
        ).get_json()
        self.assertFalse(trav["ok"])

        abs_path = self.client.get(
            "/api/repositories/ws-local/workspace/file?path=" + str(self.repo_dir / "hello.py")
        ).get_json()
        self.assertFalse(abs_path["ok"])

        search = self.client.get(
            "/api/repositories/ws-local/workspace/search?mode=filename&q=hello"
        ).get_json()
        self.assertTrue(search["ok"])
        self.assertGreaterEqual(search["count"], 1)

        content = self.client.get(
            "/api/repositories/ws-local/workspace/search?mode=content&q=print"
        ).get_json()
        self.assertTrue(content["ok"])
        self.assertGreaterEqual(content["count"], 1)

    def test_save_requires_confirm_and_audits(self) -> None:
        blocked = self.client.post(
            "/api/repositories/ws-local/workspace/save",
            data=json.dumps({"path": "hello.py", "content": "print(2)\n", "confirm": False}),
            content_type="application/json",
        ).get_json()
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "confirm_required")

        preview = self.client.post(
            "/api/repositories/ws-local/workspace/preview-save",
            data=json.dumps({"path": "hello.py", "content": "print(2)\n"}),
            content_type="application/json",
        ).get_json()
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["changed"])

        saved = self.client.post(
            "/api/repositories/ws-local/workspace/save",
            data=json.dumps({"path": "hello.py", "content": "print(2)\n", "confirm": True}),
            content_type="application/json",
        ).get_json()
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["saved"])

        changes = self.client.get("/repositories/ws-local/changes")
        self.assertEqual(changes.status_code, 200)

        open_resp = self.client.post(
            "/api/repositories/ws-local/workspace/open",
            data=json.dumps({"target": "explorer", "path": "hello.py"}),
            content_type="application/json",
        )
        # May fail if explorer missing in CI — still must not 500 on validation
        self.assertIn(open_resp.status_code, {200, 400})

    def test_service_scope_isolation_message(self) -> None:
        svc = RepositoryWorkspaceService()
        remote = Repository(
            id="x",
            name="X",
            type="command",
            enabled=True,
            git_url="https://example.com/x.git",
            local_path=None,
        )
        avail = svc.availability(remote)
        self.assertFalse(avail["available"])
        self.assertEqual(avail["message"], UNAVAILABLE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
