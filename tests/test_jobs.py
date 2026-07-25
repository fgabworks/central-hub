"""Tests for Phases 2–6 job engine (SQLite, executors, confirm gates, files)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from hub.jobs.db import JobDatabase
from hub.jobs.executor import CapabilityExecutionError, run_command_capability, run_api_capability
from hub.jobs.files import FileSafetyError, list_artifacts, save_upload
from hub.jobs.store import JobStore, progress_payload
from hub.jobs.worker import JobWorker
from hub.registry.loader import load_registry
from hub.registry.models import Capability, HealthCheckConfig, Registry, RegistryDefaults, Repository
from hub.settings import ROOT_DIR


def _sample_cli_repo() -> Repository:
    return Repository(
        id="sample-cli",
        name="Sample CLI",
        type="command",
        enabled=True,
        local_path="samples/sample-cli",
        working_directory="samples/sample-cli",
        health_check=HealthCheckConfig(type="path", local_path="samples/sample-cli"),
        capabilities=[
            Capability(
                id="echo_dry_run",
                label="Echo",
                adapter_type="command",
                dry_run_default=True,
                raw={
                    "command_template": ["python", "echo_job.py"],
                    "dry_run_command_template": ["python", "echo_job.py"],
                    "timeout_seconds": 30,
                },
            ),
            Capability(
                id="echo_apply",
                label="Echo apply",
                adapter_type="command",
                dry_run_default=False,
                raw={
                    "command_template": ["python", "echo_job.py"],
                    "require_confirm": True,
                    "timeout_seconds": 30,
                },
            ),
        ],
    )


class JobStoreTests(unittest.TestCase):
    def test_create_list_cancel_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JobStore(JobDatabase(root / "hub.db"), data_root=root)
            job = store.create(repository_id="sample-cli", capability_id="echo_dry_run", dry_run=True)
            self.assertEqual(job["status"], "queued")
            self.assertTrue(Path(job["log_path"]).is_file())
            cancelled = store.request_cancel(job["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(len(store.list_recent()), 1)
            payload = progress_payload(cancelled)
            self.assertEqual(payload["id"], job["id"])


class ExecutorTests(unittest.TestCase):
    def test_command_capability_writes_result(self) -> None:
        repo = _sample_cli_repo()
        cap = repo.capabilities[0]
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "in"
            result_dir = Path(tmp) / "out"
            input_dir.mkdir()
            result_dir.mkdir()
            logs: list[str] = []
            result = run_command_capability(
                repo,
                cap,
                dry_run=True,
                job_id="job_test",
                input_dir=input_dir,
                result_dir=result_dir,
                log_append=logs.append,
                timeout_seconds=30,
            )
            self.assertTrue(result["ok"])
            self.assertTrue((result_dir / "echo_dry_run.json").is_file())

    def test_blocks_shell_metacharacters(self) -> None:
        repo = _sample_cli_repo()
        cap = Capability(
            id="bad",
            label="bad",
            adapter_type="command",
            raw={"command_template": ["python", "-c", "print(1); print(2)"]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CapabilityExecutionError):
                run_command_capability(
                    repo,
                    cap,
                    dry_run=True,
                    job_id="j",
                    input_dir=Path(tmp),
                    result_dir=Path(tmp),
                    log_append=lambda _l: None,
                )

    def test_api_get_capability(self) -> None:
        repo = Repository(
            id="sample-api",
            name="API",
            type="api",
            enabled=True,
            base_url="http://127.0.0.1:9099",
            capabilities=[
                Capability(
                    id="health",
                    label="Health",
                    adapter_type="api",
                    raw={"http_method": "GET", "http_path": "/health"},
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            with patch("hub.jobs.executor.requests.request") as req:
                resp = req.return_value
                resp.status_code = 200
                resp.text = '{"ok":true}'
                out = run_api_capability(
                    repo,
                    repo.capabilities[0],
                    dry_run=True,
                    result_dir=result_dir,
                    log_append=lambda _l: None,
                )
            self.assertTrue(out["ok"])
            self.assertTrue((result_dir / "api_response.json").is_file())

    def test_api_post_blocked(self) -> None:
        repo = Repository(
            id="sample-api",
            name="API",
            type="api",
            enabled=True,
            base_url="http://127.0.0.1:9099",
            capabilities=[
                Capability(
                    id="write",
                    label="Write",
                    adapter_type="api",
                    raw={"http_method": "POST", "http_path": "/x", "allow_write": True},
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CapabilityExecutionError):
                run_api_capability(
                    repo,
                    repo.capabilities[0],
                    dry_run=False,
                    result_dir=Path(tmp),
                    log_append=lambda _l: None,
                )


class WorkerTests(unittest.TestCase):
    def test_worker_runs_queued_job(self) -> None:
        repo = _sample_cli_repo()
        registry = Registry(repositories=[repo], defaults=RegistryDefaults(max_concurrent_jobs=1))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JobStore(JobDatabase(root / "hub.db"), data_root=root)
            worker = JobWorker(
                store,
                registry_provider=lambda: registry,
                max_concurrent=1,
                poll_seconds=0.1,
            )
            job = store.create(repository_id="sample-cli", capability_id="echo_dry_run", dry_run=True)
            worker.start()
            worker.kick()
            deadline = time.time() + 15
            final = None
            while time.time() < deadline:
                final = store.get(job["id"])
                if final and final["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.2)
            worker.stop()
            self.assertIsNotNone(final)
            self.assertEqual(final["status"], "completed")
            artifacts = list_artifacts(Path(final["result_path"]))
            self.assertTrue(any(a["name"].endswith(".json") for a in artifacts))


class FileTests(unittest.TestCase):
    def test_upload_and_reject_bad_type(self) -> None:
        from werkzeug.datastructures import FileStorage

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            ok = FileStorage(stream=BytesIO(b"a,b\n1,2\n"), filename="data.csv", content_type="text/csv")
            saved = save_upload(dest, ok)
            self.assertEqual(saved["filename"], "data.csv")
            bad = FileStorage(stream=BytesIO(b"x"), filename="evil.exe")
            with self.assertRaises(FileSafetyError):
                save_upload(dest, bad)


class RegistryAndRouteTests(unittest.TestCase):
    def test_registry_loads_command_templates(self) -> None:
        registry = load_registry(ROOT_DIR / "tests" / "fixtures" / "repositories.yaml")
        cli = registry.get("sample-cli")
        self.assertIsNotNone(cli)
        echo = next(c for c in cli.capabilities if c.id == "echo_dry_run")
        self.assertIn("command_template", echo.raw)

    def _app_with_fixture_registry(self):
        from hub.adapters import AdapterManager
        from app import create_app

        app = create_app()
        path = ROOT_DIR / "tests" / "fixtures" / "repositories.yaml"
        registry = load_registry(path)
        app.config["REGISTRY_CONFIG_PATH"] = path
        app.config["REGISTRY"] = registry
        app.config["ADAPTERS"] = AdapterManager(registry, default_timeout=2, cache_ttl_seconds=0)
        return app

    def test_submit_requires_confirm_for_apply(self) -> None:
        app = self._app_with_fixture_registry()
        client = app.test_client()
        resp = client.post(
            "/api/jobs",
            json={
                "repository_id": "sample-cli",
                "capability_id": "echo_apply",
                "dry_run": False,
                "confirm": False,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("confirm", resp.get_json()["error"])

    def test_submit_dry_run_job_via_api(self) -> None:
        app = self._app_with_fixture_registry()
        client = app.test_client()
        resp = client.post(
            "/api/jobs",
            json={
                "repository_id": "sample-cli",
                "capability_id": "echo_dry_run",
                "dry_run": True,
            },
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.get_json()["ok"])
        job_id = resp.get_json()["job"]["id"]
        deadline = time.time() + 15
        final = None
        while time.time() < deadline:
            detail = client.get(f"/api/jobs/{job_id}").get_json()["job"]
            if detail["status"] in {"completed", "failed", "cancelled"}:
                final = detail
                break
            time.sleep(0.2)
        self.assertIsNotNone(final)
        self.assertEqual(final["status"], "completed")


if __name__ == "__main__":
    unittest.main()
