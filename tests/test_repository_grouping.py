"""Dynamic repository grouping by repository_group_id."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.registry.grouping import (
    ACTIVE_RUN_STATUSES,
    build_grouped_rows,
    group_siblings,
    linked_api_repositories,
)
from hub.registry.models import HealthCheckConfig, Registry, Repository
from hub.registry.store import RegistryStore, build_entry_from_form
from hub.registry.loader import load_registry


def _repo(
    rid: str,
    *,
    name: str | None = None,
    repo_type: str = "command",
    group: str | None = None,
    local_path: str | None = None,
    base_url: str | None = None,
    enabled: bool = True,
) -> Repository:
    return Repository(
        id=rid,
        name=name or rid,
        type=repo_type,
        enabled=enabled,
        description=f"{rid} desc",
        local_path=local_path,
        working_directory=local_path,
        base_url=base_url,
        repository_group_id=group,
        health_check=HealthCheckConfig(
            type="http" if repo_type == "api" else "path",
            path="/health" if repo_type == "api" else None,
            local_path=local_path if repo_type == "command" else None,
        ),
        capabilities=[],
    )


class RepositoryGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace-a"
        self.ws.mkdir()
        self.ws_b = self.root / "workspace-b"
        self.ws_b.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_local_plus_api_grouping(self) -> None:
        registry = Registry(
            repositories=[
                _repo(
                    "proj-api",
                    name="Proj API",
                    repo_type="api",
                    group="proj-alpha",
                    base_url="http://127.0.0.1:9001",
                ),
                _repo(
                    "proj-local",
                    name="Proj Local",
                    repo_type="command",
                    group="proj-alpha",
                    local_path=str(self.ws),
                ),
            ]
        )
        health = {
            "proj-api": {"ok": True, "status": "healthy", "checked_at": "2026-01-01T00:00:00"},
            "proj-local": {"ok": True, "status": "healthy"},
        }
        rows = build_grouped_rows(registry, health, active_run_repo_ids=set())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["repository_group_id"], "proj-alpha")
        self.assertTrue(row["is_group"])
        self.assertCountEqual(row["member_ids"], ["proj-api", "proj-local"])
        self.assertEqual(row["workspace_status"], "Ready")
        self.assertEqual(row["application_status"], "Stopped")
        self.assertEqual(row["api_status"], "Online")
        labels = [a["label"] for a in row["actions"]]
        self.assertIn("Open Workspace", labels)
        self.assertIn("Start / Stop", labels)
        self.assertIn("Logs", labels)
        self.assertIn("Open API", labels)
        self.assertIn("Health Check", labels)

    def test_local_only_and_api_only(self) -> None:
        registry = Registry(
            repositories=[
                _repo(
                    "local-only",
                    repo_type="command",
                    local_path=str(self.ws),
                ),
                _repo(
                    "api-only",
                    repo_type="api",
                    base_url="http://127.0.0.1:9002",
                ),
            ]
        )
        rows = build_grouped_rows(
            registry,
            {
                "local-only": {"ok": True, "status": "healthy"},
                "api-only": {"ok": False, "status": "unreachable"},
            },
        )
        self.assertEqual(len(rows), 2)
        by_id = {r["primary_repo_id"]: r for r in rows}
        local = by_id["local-only"]
        self.assertIsNone(local["repository_group_id"])
        self.assertEqual(local["workspace_status"], "Ready")
        self.assertEqual(local["application_status"], "Stopped")
        self.assertIsNone(local["api_status"])
        api = by_id["api-only"]
        self.assertIsNone(api["workspace_status"])
        self.assertIsNone(api["application_status"])
        self.assertEqual(api["api_status"], "Offline")

    def test_multiple_grouped_repositories(self) -> None:
        registry = Registry(
            repositories=[
                _repo("a-local", group="group-a", local_path=str(self.ws)),
                _repo(
                    "a-api",
                    repo_type="api",
                    group="group-a",
                    base_url="http://127.0.0.1:1",
                ),
                _repo("b-local", group="group-b", local_path=str(self.ws_b)),
                _repo(
                    "b-api",
                    repo_type="api",
                    group="group-b",
                    base_url="http://127.0.0.1:2",
                ),
                _repo("solo", local_path=str(self.ws)),
            ]
        )
        rows = build_grouped_rows(registry, {})
        self.assertEqual(len(rows), 3)
        groups = {r["repository_group_id"] for r in rows}
        self.assertEqual(groups, {"group-a", "group-b", None})

    def test_status_and_action_independence(self) -> None:
        """Workspace Ready must not imply Application Running."""
        registry = Registry(
            repositories=[
                _repo(
                    "ready-local",
                    group="ready-group",
                    local_path=str(self.ws),
                ),
                _repo(
                    "ready-api",
                    repo_type="api",
                    group="ready-group",
                    base_url="http://127.0.0.1:3",
                ),
            ]
        )
        # Workspace path exists, no active run, API offline
        rows = build_grouped_rows(
            registry,
            {"ready-api": {"ok": False, "status": "unreachable"}},
            active_run_repo_ids=set(),
        )
        row = rows[0]
        self.assertEqual(row["workspace_status"], "Ready")
        self.assertEqual(row["application_status"], "Stopped")
        self.assertEqual(row["api_status"], "Offline")

        # Active run flips application only
        rows2 = build_grouped_rows(
            registry,
            {"ready-api": {"ok": True, "status": "healthy"}},
            active_run_repo_ids={"ready-local"},
        )
        row2 = rows2[0]
        self.assertEqual(row2["workspace_status"], "Ready")
        self.assertEqual(row2["application_status"], "Running")
        self.assertEqual(row2["api_status"], "Online")

        # Missing workspace: app still Stopped (not Running), Open Workspace unavailable
        missing = Registry(
            repositories=[
                _repo(
                    "missing-local",
                    group="miss",
                    local_path=str(self.root / "does-not-exist"),
                ),
                _repo(
                    "miss-api",
                    repo_type="api",
                    group="miss",
                    base_url="http://127.0.0.1:4",
                ),
            ]
        )
        row3 = build_grouped_rows(missing, {}, active_run_repo_ids={"missing-local"})[0]
        self.assertEqual(row3["workspace_status"], "Not Connected")
        self.assertEqual(row3["application_status"], "Stopped")
        open_ws = next(a for a in row3["actions"] if a["label"] == "Open Workspace")
        self.assertFalse(open_ws["available"])
        start = next(a for a in row3["actions"] if a["label"] == "Start / Stop")
        self.assertFalse(start["available"])

    def test_ungrouped_backward_compatibility(self) -> None:
        registry = Registry(
            repositories=[
                _repo("alpha", name="Alpha", local_path=str(self.ws)),
                _repo(
                    "beta",
                    name="Beta",
                    repo_type="api",
                    base_url="http://127.0.0.1:5",
                ),
            ]
        )
        rows = build_grouped_rows(registry, {"beta": {"ok": True, "status": "healthy"}})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertFalse(row["is_group"])
            self.assertIsNone(row["repository_group_id"])
            self.assertEqual(len(row["member_ids"]), 1)

    def test_linked_api_and_siblings(self) -> None:
        registry = Registry(
            repositories=[
                _repo("x-local", group="x", local_path=str(self.ws)),
                _repo(
                    "x-api",
                    repo_type="api",
                    group="x",
                    base_url="http://127.0.0.1:6",
                ),
                _repo("y", local_path=str(self.ws)),
            ]
        )
        siblings = group_siblings(registry, "x-local")
        self.assertEqual({r.id for r in siblings}, {"x-local", "x-api"})
        linked = linked_api_repositories(registry, "x-local")
        self.assertEqual([r.id for r in linked], ["x-api"])
        self.assertEqual([r.id for r in linked_api_repositories(registry, "y")], [])

    def test_store_persists_group_id(self) -> None:
        path = self.root / "repos.yaml"
        path.write_text("repositories: []\ndefaults: {}\n", encoding="utf-8")
        store = RegistryStore(path)
        entry = build_entry_from_form(
            name="Grouped Local",
            repo_type="command",
            enabled=True,
            git_url="https://example.com/g.git",
            local_path=str(self.ws),
            repository_group_id="shared-proj",
            repo_id="grouped-local",
        )
        store.add(entry)
        entry_api = build_entry_from_form(
            name="Grouped API",
            repo_type="api",
            enabled=True,
            base_url="http://127.0.0.1:7",
            repository_group_id="shared-proj",
            repo_id="grouped-api",
        )
        store.add(entry_api)
        registry = load_registry(path)
        self.assertEqual(registry.get("grouped-local").repository_group_id, "shared-proj")
        self.assertEqual(registry.get("grouped-api").repository_group_id, "shared-proj")
        rows = build_grouped_rows(registry, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["repository_group_id"], "shared-proj")

    def test_active_run_statuses_constant(self) -> None:
        self.assertIn("running", ACTIVE_RUN_STATUSES)
        self.assertIn("starting", ACTIVE_RUN_STATUSES)


if __name__ == "__main__":
    unittest.main()
