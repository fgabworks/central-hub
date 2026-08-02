"""Focused tests for Central Hub single-instance and Health process controls."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from hub.repository_workspace.hub_owned_registry import (
    OwnedProcessRegistry,
    ownership_token,
)
from hub.repository_workspace.hub_process_manager import (
    STOP_ALL_CONFIRMATION,
    STOP_CENTRAL_HUB_CONFIRMATION,
    CentralHubInstanceGuard,
    CentralHubProcessManager,
    SingleInstanceError,
)
from hub.repository_workspace.hub_process_routes import register_central_hub_process_routes
from hub.repository_workspace.process_detect import RawProcess, _identity_token


def _raw(pid: int, root: Path, *, absolute: bool = True, ppid: int | None = None) -> RawProcess:
    app = str(root / "app.py") if absolute else "app.py"
    return RawProcess(
        pid=pid,
        name="python.exe",
        executable=r"C:\Python\python.exe",
        command_line=f'"C:\\Python\\python.exe" "{app}"',
        cwd=str(root),
        started_at="2026-08-02T00:00:00+00:00",
        ppid=ppid,
        listening_ports=(8080,) if pid in {10, 20} else (),
        status="running",
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
            self.assertTrue((state / "owned_processes.json").is_file())
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
        worker = RawProcess(
            pid=11, name="python.exe", executable=r"C:\Python\python.exe",
            command_line=r'"C:\Python\python.exe" -c worker', cwd=str(root),
            started_at="2026-08-02T00:00:01+00:00", ppid=10, status="running",
        )
        unrelated = RawProcess(
            pid=30, name="python.exe", executable=r"C:\Python\python.exe",
            command_line="python -m http.server 9000", cwd=r"C:\elsewhere",
            started_at="2026-08-02T00:00:02+00:00", status="running",
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
        registry = OwnedProcessRegistry(state / "owned_processes.json")
        registry.register(
            raw=current, role="server", label="Central Hub Server",
            script_path=str(root / "app.py"), port=8080,
        )

        def stopper(**kwargs):
            stopped.append(kwargs)
            return {"pid": kwargs["pid"], "ended": True, "port_released": True}

        return CentralHubProcessManager(
            root=root, state_dir=state,
            process_loader=lambda: [current, worker, stale, unrelated],
            listener_loader=lambda _ports: {8080: [10, 20]},
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

    def test_inventory_groups_hub_and_other_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            manager = self._manager(root, state, [])
            inventory = manager.inventory()
            hub_pids = [item["pid"] for item in inventory["hub_processes"]]
            other_pids = [item["pid"] for item in inventory["other_python"]]
            self.assertIn(10, hub_pids)
            self.assertIn(11, hub_pids)  # child worker
            self.assertIn(20, hub_pids)  # stale/orphan server candidate
            self.assertIn(30, other_pids)
            server = next(item for item in inventory["hub_processes"] if item["pid"] == 10)
            self.assertEqual(server["label"], "Central Hub Server")
            self.assertTrue(server["hub_owned"])
            self.assertTrue(server["stoppable"])
            other = next(item for item in inventory["other_python"] if item["pid"] == 30)
            self.assertFalse(other["hub_owned"])
            self.assertFalse(other["stoppable"])

    def test_stop_owned_refuses_unrelated_and_stale_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, calls = Path(tmp) / "hub", Path(tmp) / "state", []
            root.mkdir()
            manager = self._manager(root, state, calls)
            inventory = manager.inventory()
            other = next(item for item in inventory["other_python"] if item["pid"] == 30)
            with self.assertRaises(ValueError):
                manager.stop_owned(
                    pid=30,
                    identity_token=other["identity_token"],
                    ownership_token_value="",
                    actor="owner",
                    confirm=True,
                )
            worker = next(item for item in inventory["hub_processes"] if item["pid"] == 11)
            with self.assertRaises(ValueError):
                manager.stop_owned(
                    pid=11,
                    identity_token="deadbeef",
                    ownership_token_value=worker["ownership_token"],
                    actor="owner",
                    confirm=True,
                )
            result = manager.stop_owned(
                pid=11,
                identity_token=worker["identity_token"],
                ownership_token_value=worker["ownership_token"],
                actor="owner",
                confirm=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(calls[-1]["pid"], 11)

    def test_stale_start_time_blocks_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state, calls = Path(tmp) / "hub", Path(tmp) / "state", []
            root.mkdir()
            manager = self._manager(root, state, calls)
            inventory = manager.inventory()
            worker = next(item for item in inventory["hub_processes"] if item["pid"] == 11)
            # Forge a mismatched ownership token as if PID was reused with new start time.
            with self.assertRaises(ValueError):
                manager.stop_owned(
                    pid=11,
                    identity_token=worker["identity_token"],
                    ownership_token_value=ownership_token(
                        pid=11,
                        executable=r"C:\Python\python.exe",
                        command_line=r'"C:\Python\python.exe" -c worker',
                        script_path="python-worker",
                        cwd=str(root),
                        started_at="1999-01-01T00:00:00+00:00",
                    ),
                    actor="owner",
                    confirm=True,
                )
            self.assertEqual(calls, [])

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
            hub_status = manager.queue_control(
                action="stop_central_hub",
                actor="owner",
                typed_confirmation=STOP_CENTRAL_HUB_CONFIRMATION,
            )
            restart_status = manager.queue_control(action="restart", actor="owner", confirm=True)
            self.assertEqual(len(launched), 3)
            self.assertFalse(launched[0][1]["shell"])
            self.assertIn("hub.repository_workspace.hub_process_manager", launched[0][0])
            self.assertEqual(manager.action_status(stop_status["action_id"])["status"], "queued")
            self.assertEqual(hub_status["action"], "stop_central_hub")
            self.assertEqual(manager.action_status(restart_status["action_id"])["action"], "restart")

    def test_registry_drops_reused_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, state = Path(tmp) / "hub", Path(tmp) / "state"
            root.mkdir()
            state.mkdir()
            first = _raw(40, root)
            registry = OwnedProcessRegistry(state / "owned_processes.json")
            registry.register(
                raw=first, role="server", label="Central Hub Server",
                script_path=str(root / "app.py"), port=8080,
            )
            reused = RawProcess(
                pid=40, name="python.exe", executable=r"C:\Python\python.exe",
                command_line="python -m http.server", cwd=r"C:\other",
                started_at="2026-08-03T00:00:00+00:00",
            )
            result = registry.reconcile([reused], root=root, app_path=root / "app.py")
            self.assertEqual(result["removed_count"], 1)
            self.assertEqual(result["entries"], [])


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        class Audit:
            def append(self, **_kwargs):
                return None

        class Manager:
            def inventory(self):
                return {
                    "hub_processes": [{
                        "pid": 10, "label": "Central Hub Server", "hub_owned": True,
                        "stoppable": True, "role": "server", "current": True,
                        "identity_token": "abc", "ownership_token": "own",
                        "status": "Current", "health": "listening", "ppid": 1,
                        "script_module": "app.py", "listening_port": 8080,
                        "cwd": "C:/hub", "started_at": "2026-08-02T00:00:00+00:00",
                        "runtime_label": "1m", "command_redacted": "python app.py",
                    }],
                    "other_python": [{
                        "pid": 30, "label": "http.server", "hub_owned": False,
                        "stoppable": False, "role": "unrelated",
                        "identity_token": "x", "ownership_token": "",
                        "status": "running", "health": "running", "ppid": 1,
                        "script_module": "http.server", "listening_port": 9000,
                        "cwd": "C:/tmp", "started_at": None, "runtime_label": "—",
                        "command_redacted": "python -m http.server",
                    }],
                    "instances": [{"pid": 10, "current": True}],
                    "current_pid": 10,
                    "registry": {"count": 1, "removed_stale": 0, "orphans": 0},
                }

            def scan(self):
                return []

            def stop_stale(self, *, actor, confirm):
                if not confirm:
                    raise ValueError("Explicit confirmation is required.")
                return {"ok": True, "count": 0, "results": []}

            def stop_owned(self, **kwargs):
                if not kwargs.get("confirm"):
                    raise ValueError("Explicit confirmation is required.")
                if int(kwargs.get("pid") or 0) == 30:
                    raise ValueError("Process is not a verified Central Hub-owned target.")
                return {"ok": True, "queued": False, "pid": kwargs["pid"]}

            def restart_owned(self, **kwargs):
                if not kwargs.get("confirm"):
                    raise ValueError("Explicit confirmation is required.")
                return {"action_id": "abc123", "status": "queued", "target_pids": [10]}

            def queue_control(self, *, action, actor, confirm=False, typed_confirmation="", target_snapshot=None):
                if action == "stop_all" and typed_confirmation != STOP_ALL_CONFIRMATION:
                    raise ValueError("confirmation required")
                if action == "stop_central_hub" and typed_confirmation != STOP_CENTRAL_HUB_CONFIRMATION:
                    raise ValueError("confirmation required")
                if action == "restart" and not confirm:
                    raise ValueError("confirmation required")
                return {"action_id": "abc123", "status": "queued", "target_pids": [10], "action": action}

            def action_status(self, action_id):
                return {"action_id": action_id, "status": "completed", "new_pid": 11}

        app = Flask(__name__)
        app.secret_key = "test"
        app.config.update(TESTING=True, AUDIT=Audit(), CENTRAL_HUB_PROCESSES=Manager())
        register_central_hub_process_routes(app)
        self.client = app.test_client()

    def test_routes_require_confirmations_and_return_action_status(self) -> None:
        scan = self.client.get("/api/health/central-hub-processes")
        self.assertEqual(scan.status_code, 200)
        body = scan.get_json()
        self.assertEqual(body["hub_processes"][0]["label"], "Central Hub Server")
        self.assertFalse(body["other_python"][0]["stoppable"])
        self.assertEqual(
            self.client.post("/api/health/central-hub-processes/stop-stale", json={}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/health/central-hub-processes/stop",
                json={"confirm": True, "pid": 30, "identity_token": "x"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/health/central-hub-processes/stop",
                json={"confirm": True, "pid": 10, "identity_token": "abc", "ownership_token": "own"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/health/central-hub-processes/stop-central-hub",
                json={"typed_confirmation": STOP_CENTRAL_HUB_CONFIRMATION},
            ).status_code,
            202,
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
