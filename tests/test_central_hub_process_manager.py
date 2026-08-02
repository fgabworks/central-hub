"""Focused tests for Central Hub single-instance and Health process controls."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from hub.repository_workspace.hub_process_manager import (
    STOP_ALL_CONFIRMATION,
    CentralHubInstanceGuard,
    CentralHubProcessManager,
    SingleInstanceError,
)
from hub.repository_workspace.hub_process_routes import register_central_hub_process_routes
from hub.repository_workspace.process_detect import RawProcess, _identity_token


def _raw(pid: int, root: Path, *, absolute: bool = True) -> RawProcess:
    app = str(root / "app.py") if absolute else "app.py"
    return RawProcess(
        pid=pid,
        name="python.exe",
        executable=r"C:\Python\python.exe",
        command_line=f'"C:\\Python\\python.exe" "{app}"',
        cwd=str(root),
        started_at="2026-08-02T00:00:00+00:00",
    )


class InstanceGuardTests(unittest.TestCase):
    def test_invalid_lock_is_cleaned_before_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            state.mkdir()
            (state / "instance.lock.json").write_text("not-json", encoding="utf-8")
            current = _raw(10, root)
            guard = CentralHubInstanceGuard(
                root=root, state_dir=state, pid=10,
                process_loader=lambda: [current], listener_loader=lambda _ports: {},
            )
            guard.acquire()
            self.assertIn("invalid_lock_cleaned", (state / "guard.jsonl").read_text(encoding="utf-8"))
            guard.release()

    def test_acquire_release_and_stale_lock_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            current = _raw(10, root)
            state.mkdir()
            (state / "instance.lock.json").write_text(
                json.dumps({"pid": 99, "root": str(root), "app_path": str(root / "app.py")}),
                encoding="utf-8",
            )
            guard = CentralHubInstanceGuard(
                root=root, state_dir=state, pid=10,
                process_loader=lambda: [current], listener_loader=lambda _ports: {},
            )
            record = guard.acquire()
            self.assertEqual(record["pid"], 10)
            self.assertTrue(guard.lock_path.is_file())
            self.assertIn("stale_lock_cleaned", (state / "guard.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(guard.release())
            self.assertFalse(guard.lock_path.exists())

    def test_refuses_active_registry_and_verified_port_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            active = _raw(20, root)
            state.mkdir()
            record = {
                "pid": 20,
                "identity_token": _identity_token(
                    pid=20, executable=active.executable, command_line=active.command_line
                ),
                "root": str(root),
                "app_path": str(root / "app.py"),
            }
            (state / "instance.lock.json").write_text(json.dumps(record), encoding="utf-8")
            guard = CentralHubInstanceGuard(
                root=root, state_dir=state, pid=10,
                process_loader=lambda: [active], listener_loader=lambda _ports: {8080: [20]},
            )
            with self.assertRaises(SingleInstanceError):
                guard.acquire()

            (state / "instance.lock.json").unlink()
            with self.assertRaises(SingleInstanceError):
                guard.acquire()


class ProcessInventoryTests(unittest.TestCase):
    def _manager(self, root: Path, state: Path, stopped: list[dict]) -> CentralHubProcessManager:
        current, stale = _raw(10, root), _raw(20, root)
        unrelated = RawProcess(
            pid=30, name="python.exe", executable=r"C:\Python\python.exe",
            command_line="python -m http.server 8080", cwd=str(root),
        )
        record = {
            "pid": 10,
            "identity_token": _identity_token(
                pid=10, executable=current.executable, command_line=current.command_line
            ),
            "root": str(root), "app_path": str(root / "app.py"),
        }
        state.mkdir(parents=True, exist_ok=True)
        (state / "instance.lock.json").write_text(json.dumps(record), encoding="utf-8")

        def stopper(**kwargs):
            stopped.append(kwargs)
            return {"pid": kwargs["pid"], "ended": True, "port_released": True}

        return CentralHubProcessManager(
            root=root, state_dir=state,
            process_loader=lambda: [current, stale, unrelated],
            listener_loader=lambda _ports: {8080: [10, 20, 30]},
            stopper=stopper,
        )

    def test_scan_requires_registry_or_exact_app_path_plus_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            manager = self._manager(root, state, [])
            instances = manager.scan()
            self.assertEqual([item.pid for item in instances], [10, 20])
            self.assertTrue(instances[0].current)
            self.assertTrue(instances[0].registered)
            self.assertTrue(instances[1].stale)
            self.assertNotIn(30, [item.pid for item in instances])

    def test_stop_stale_revalidates_one_pid_and_preserves_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, calls = Path(tmp) / "hub", Path(tmp) / "state", []
            root.mkdir()
            manager = self._manager(root, state, calls)
            with self.assertRaises(ValueError):
                manager.stop_stale(actor="owner", confirm=False)
            result = manager.stop_stale(actor="owner", confirm=True)
            self.assertTrue(result["ok"])
            self.assertEqual([call["pid"] for call in calls], [20])
            self.assertEqual(calls[0]["grace_timeout_seconds"], 5.0)
            self.assertFalse(calls[0]["include_tree"])

    def test_stop_all_confirmation_and_restart_queue_use_fixed_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            launched = []

            class Proc:
                pid = 123

            def popen(args, **kwargs):
                launched.append((args, kwargs))
                return Proc()

            manager = self._manager(root, state, [])
            manager.popen_factory = popen
            with self.assertRaises(ValueError):
                manager.queue_control(action="stop_all", actor="owner", typed_confirmation="wrong")
            stop_status = manager.queue_control(
                action="stop_all", actor="owner", typed_confirmation=STOP_ALL_CONFIRMATION
            )
            restart_status = manager.queue_control(action="restart", actor="owner", confirm=True)
            self.assertEqual(len(launched), 2)
            self.assertFalse(launched[0][1]["shell"])
            self.assertIn("hub.repository_workspace.hub_process_manager", launched[0][0])
            self.assertEqual(manager.action_status(stop_status["action_id"])["status"], "queued")
            self.assertEqual(manager.action_status(restart_status["action_id"])["action"], "restart")


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        class Audit:
            def append(self, **_kwargs):
                return None

        class Manager:
            def scan(self):
                return []

            def stop_stale(self, *, actor, confirm):
                if not confirm:
                    raise ValueError("Explicit confirmation is required.")
                return {"ok": True, "count": 0, "results": []}

            def queue_control(self, *, action, actor, confirm=False, typed_confirmation=""):
                if action == "stop_all" and typed_confirmation != STOP_ALL_CONFIRMATION:
                    raise ValueError("confirmation required")
                if action == "restart" and not confirm:
                    raise ValueError("confirmation required")
                return {"action_id": "abc123", "status": "queued", "target_pids": [10]}

            def action_status(self, action_id):
                return {"action_id": action_id, "status": "completed", "new_pid": 11}

        app = Flask(__name__)
        app.secret_key = "test"
        app.config.update(TESTING=True, AUDIT=Audit(), CENTRAL_HUB_PROCESSES=Manager())
        register_central_hub_process_routes(app)
        self.client = app.test_client()

    def test_routes_require_confirmations_and_return_action_status(self) -> None:
        self.assertEqual(self.client.get("/api/health/central-hub-processes").status_code, 200)
        self.assertEqual(
            self.client.post("/api/health/central-hub-processes/stop-stale", json={}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/health/central-hub-processes/stop-all",
                json={"typed_confirmation": STOP_ALL_CONFIRMATION},
            ).status_code,
            202,
        )
        self.assertEqual(
            self.client.post(
                "/api/health/central-hub-processes/restart", json={"confirm": True}
            ).status_code,
            202,
        )
        status = self.client.get("/api/health/central-hub-processes/actions/abc123")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["new_pid"], 11)


if __name__ == "__main__":
    unittest.main()
