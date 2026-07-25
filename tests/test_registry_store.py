"""Registry store, git URL helpers, and repository management routes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.registry.git_util import (
    find_local_checkout,
    git_urls_match,
    normalize_git_url,
    read_origin_url,
    slugify_repo_id,
)
from hub.registry.loader import RegistryError, load_registry
from hub.registry.status import ui_repo_status
from hub.registry.store import RegistryStore, build_entry_from_form
from hub.registry.models import Repository
from hub.settings import ROOT_DIR


class GitUtilTests(unittest.TestCase):
    def test_normalize_and_match(self) -> None:
        a = "https://github.com/PMNP-IS/pmnp-live-processing.git"
        b = "git@github.com:PMNP-IS/pmnp-live-processing"
        self.assertTrue(git_urls_match(a, b))
        self.assertEqual(
            normalize_git_url(a),
            "https://github.com/pmnp-is/pmnp-live-processing",
        )

    def test_find_local_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "checkout"
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/PMNP-IS/Data-Script.git\n',
                encoding="utf-8",
            )
            found = find_local_checkout(
                "https://github.com/PMNP-IS/Data-Script",
                [root],
            )
            self.assertEqual(found, repo.resolve())
            self.assertTrue(
                git_urls_match(read_origin_url(repo), "https://github.com/PMNP-IS/Data-Script")
            )

    def test_slugify(self) -> None:
        self.assertEqual(slugify_repo_id("Report Template"), "report-template")


class RegistryStoreTests(unittest.TestCase):
    def test_add_update_disable_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repositories.yaml"
            path.write_text(
                "repositories: []\ndefaults:\n  max_concurrent_jobs: 1\n",
                encoding="utf-8",
            )
            store = RegistryStore(path)
            entry = build_entry_from_form(
                name="Data-Script",
                repo_type="command",
                enabled=True,
                git_url="https://github.com/PMNP-IS/Data-Script",
                local_path=None,
            )
            saved = store.add(entry)
            self.assertEqual(saved["id"], "data-script")
            registry = load_registry(path)
            self.assertIsNone(registry.get("data-script").local_path)
            self.assertEqual(registry.get("data-script").git_url, "https://github.com/PMNP-IS/Data-Script")

            with self.assertRaises(RegistryError):
                store.add(entry)

            store.update(
                "data-script",
                {"local_path": str(Path(tmp) / "missing"), "working_directory": str(Path(tmp) / "missing")},
            )
            store.set_enabled("data-script", False)
            registry = load_registry(path)
            self.assertFalse(registry.get("data-script").enabled)

    def test_api_and_command_may_share_git_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repositories.yaml"
            path.write_text("repositories: []\ndefaults: {}\n", encoding="utf-8")
            store = RegistryStore(path)
            store.add(
                build_entry_from_form(
                    name="LP API",
                    repo_type="api",
                    enabled=True,
                    git_url="https://github.com/PMNP-IS/pmnp-live-processing.git",
                    base_url="http://127.0.0.1:5050",
                    repo_id="lp-api",
                )
            )
            store.add(
                build_entry_from_form(
                    name="LP Local",
                    repo_type="command",
                    enabled=True,
                    git_url="https://github.com/PMNP-IS/pmnp-live-processing.git",
                    local_path=str(Path(tmp) / "lp"),
                    repo_id="lp-local",
                )
            )
            self.assertEqual(len(store.list_raw()), 2)


class ActiveRegistryTests(unittest.TestCase):
    def test_active_registry_has_connected_repos_not_samples(self) -> None:
        registry = load_registry(ROOT_DIR / "config" / "repositories.yaml")
        ids = {r.id for r in registry.repositories}
        self.assertIn("live-processing", ids)
        self.assertIn("live-processing-local", ids)
        self.assertIn("data-script", ids)
        self.assertIn("report-template", ids)
        self.assertNotIn("sample-cli", ids)
        self.assertNotIn("sample-api", ids)
        local = registry.get("live-processing-local")
        assert local is not None
        self.assertTrue(
            git_urls_match(local.git_url, "https://github.com/PMNP-IS/pmnp-live-processing.git")
        )
        self.assertEqual(local.name, "PMNP Live Processing")

    def test_ui_status_not_cloned(self) -> None:
        repo = Repository(
            id="x",
            name="X",
            type="command",
            enabled=True,
            git_url="https://github.com/PMNP-IS/Data-Script",
        )
        self.assertEqual(
            ui_repo_status(repo, {"ok": False, "status": "not_cloned", "enabled": True}),
            "not_cloned",
        )
        self.assertEqual(
            ui_repo_status(repo, {"ok": False, "status": "unreachable", "enabled": True}),
            "unreachable",
        )


class RegistryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import importlib

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.registry_path = root / "repositories.yaml"
        cls.registry_path.write_text(
            "repositories: []\ndefaults:\n  max_concurrent_jobs: 1\n  require_explicit_apply: true\n",
            encoding="utf-8",
        )
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["CENTRAL_HUB_REPOSITORIES_CONFIG"] = str(cls.registry_path)
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

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

    def test_add_edit_disable_flow(self) -> None:
        r = self.client.get("/repositories/new")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Add Repository", r.data)

        with tempfile.TemporaryDirectory() as checkout_tmp:
            checkout = Path(checkout_tmp) / "Data-Script"
            (checkout / ".git").mkdir(parents=True)
            (checkout / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/PMNP-IS/Data-Script\n',
                encoding="utf-8",
            )
            with patch(
                "app.default_search_roots",
                return_value=[Path(checkout_tmp)],
            ):
                resp = self.client.post(
                    "/repositories/new",
                    data={
                        "name": "Data-Script",
                        "type": "command",
                        "enabled": "1",
                        "git_url": "https://github.com/PMNP-IS/Data-Script",
                        "local_path": "",
                        "description": "test add",
                    },
                    follow_redirects=True,
                )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Data-Script", html)
        self.assertIn("data-script", html)
        self.assertIn("Reused existing checkout", html)

        edit = self.client.post(
            "/repositories/data-script/edit",
            data={
                "name": "Data-Script",
                "type": "command",
                "enabled": "1",
                "git_url": "https://github.com/PMNP-IS/Data-Script",
                "local_path": "",
                "description": "updated",
            },
            follow_redirects=True,
        )
        self.assertEqual(edit.status_code, 200)
        self.assertIn("Updated Data-Script", edit.get_data(as_text=True))

        disabled = self.client.post(
            "/repositories/data-script/disable",
            follow_redirects=True,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertIn("Disabled data-script", disabled.get_data(as_text=True))

        listing = self.client.get("/repositories")
        self.assertIn(b"Disabled", listing.data)


if __name__ == "__main__":
    unittest.main()
