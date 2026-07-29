"""Repository Processes — detection, stop gates, PID reuse, audit redaction."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.registry.models import Repository
from hub.repository_workspace.ports import port_available
from hub.repository_workspace.process_detect import (
    RawProcess,
    detect_repository_processes,
    find_start_conflicts,
    stop_external_process,
    verify_process_identity,
    _identity_token,
)
from hub.repository_workspace.process_manager import ManagedRun, ProcessManager
from hub.repository_workspace.run_profiles import parse_profile
from hub.repository_workspace.security import WorkspaceSecurityError, redact_audit_detail
from hub.repository_workspace.service import RepositoryWorkspaceService


def _occupy(port: int):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def _repo(root: Path, repo_id: str = "demo-repo") -> Repository:
    return Repository(
        id=repo_id,
        name="Demo",
        type="command",
        enabled=True,
        local_path=str(root),
        working_directory=str(root),
    )


class DetectionTests(unittest.TestCase):
    def test_detects_hub_path_entry_and_port_excludes_generic_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"
            root.mkdir()
            repo = _repo(root)
            profiles = [
                parse_profile(
                    {
                        "id": "web",
                        "executable": "python",
                        "args": ["lookup/app_lookup.py", "--environment", "live"],
                        "port_mode": "fixed",
                        "fixed_port": 5050,
                        "local_url": "http://127.0.0.1:5050/",
                        "environments": ["live"],
                        "live_profile": True,
                        "repository_ids": ["demo-repo"],
                    }
                )
            ]
            pm = ProcessManager(state_dir=Path(tmp) / "state")
            hub_run = ManagedRun(
                run_id="run1",
                repo_id="demo-repo",
                profile_id="web",
                environment="live",
                port=5050,
                status="running",
                pid=1111,
                executable_path=r"C:\Python\python.exe",
                argv_redacted=["lookup/app_lookup.py", "--environment", "live"],
                cwd=str(root),
                started_at="2026-01-01T00:00:00+00:00",
            )
            with mock.patch.object(pm, "list_runs", return_value=[hub_run]):
                inventory = [
                    RawProcess(
                        pid=1111,
                        name="python.exe",
                        executable=r"C:\Python\python.exe",
                        command_line=rf'C:\Python\python.exe "{root}\lookup\app_lookup.py" --environment live',
                    ),
                    RawProcess(
                        pid=2222,
                        name="python.exe",
                        executable=r"C:\Python\python.exe",
                        command_line=rf'C:\Python\python.exe "{root}\lookup\app_lookup.py"',
                        cwd=str(root),
                    ),
                    RawProcess(
                        pid=3333,
                        name="python.exe",
                        executable=r"C:\Python\python.exe",
                        command_line="C:\\Python\\python.exe -m http.server 9999",
                    ),
                    RawProcess(
                        pid=4444,
                        name="node.exe",
                        executable=r"C:\Program Files\nodejs\node.exe",
                        command_line="node.exe unrelated.js",
                    ),
                ]
                listeners = {5050: [2222]}
                rows = detect_repository_processes(
                    repo,
                    process_manager=pm,
                    profiles=profiles,
                    os_processes=inventory,
                    listeners=listeners,
                )
                by_pid = {r.pid: r for r in rows}
                self.assertIn(1111, by_pid)
                self.assertTrue(by_pid[1111].managed_by_hub)
                self.assertEqual(by_pid[1111].confidence, "High")
                self.assertIn(2222, by_pid)
                self.assertFalse(by_pid[2222].managed_by_hub)
                self.assertIn(by_pid[2222].confidence, {"High", "Medium"})
                self.assertNotIn(3333, by_pid)  # generic python, unrelated
                self.assertNotIn(4444, by_pid)  # generic node only

    def test_low_confidence_view_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            repo = _repo(root)
            profiles = [
                parse_profile(
                    {
                        "id": "web",
                        "executable": "python",
                        "args": ["app.py"],
                        "port_mode": "argument",
                        "default_port": 8000,
                        "environments": ["development"],
                    }
                )
            ]
            pm = ProcessManager(state_dir=Path(tmp) / "state")
            inventory = [
                RawProcess(
                    pid=55,
                    name="python.exe",
                    executable=r"C:\Python\python.exe",
                    command_line="python.exe",
                    cwd=str(root),
                )
            ]
            rows = detect_repository_processes(
                repo,
                process_manager=pm,
                profiles=profiles,
                os_processes=inventory,
                listeners={},
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].confidence, "Low")
            self.assertTrue(rows[0].view_only)
            self.assertFalse(rows[0].stoppable)


class ConflictAndStopTests(unittest.TestCase):
    def test_fixed_port_conflict_blocks_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            repo = _repo(root)
            profile = parse_profile(
                {
                    "id": "fixed",
                    "executable": "python",
                    "args": ["app.py"],
                    "port_mode": "fixed",
                    "fixed_port": 18080,
                    "local_url": "http://127.0.0.1:18080/",
                    "environments": ["development"],
                }
            )
            holder = _occupy(18080)
            try:
                pm = ProcessManager(state_dir=Path(tmp) / "state")
                conflict = find_start_conflicts(
                    repo,
                    process_manager=pm,
                    profile=profile,
                    resolved_port=18080,
                    detected=[],
                )
                self.assertTrue(conflict["blocked"])
                self.assertTrue(conflict["fixed_port_occupied"])
                self.assertIn("Fixed port", conflict["message"])
            finally:
                holder.close()

    def test_dynamic_related_process_does_not_block_other_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            repo = _repo(root)
            profile = parse_profile(
                {
                    "id": "dyn",
                    "executable": "python",
                    "args": ["-m", "http.server", "{port}"],
                    "port_mode": "argument",
                    "default_port": 8765,
                    "environments": ["development"],
                }
            )
            from hub.repository_workspace.process_detect import DetectedProcess

            related = DetectedProcess(
                pid=99,
                executable="python.exe",
                command_redacted=f"python {root}/app.py",
                port=5050,
                started_at=None,
                managed_by_hub=False,
                detection_reasons=["command_references_repository_path"],
                confidence="High",
                repo_id=repo.id,
                identity_token="abc",
                stoppable=True,
                view_only=False,
            )
            pm = ProcessManager(state_dir=Path(tmp) / "state")
            conflict = find_start_conflicts(
                repo,
                process_manager=pm,
                profile=profile,
                resolved_port=8765,
                detected=[related],
            )
            self.assertFalse(conflict["blocked"])

    def test_pid_reuse_and_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            repo = _repo(root)
            profiles = [
                parse_profile(
                    {
                        "id": "web",
                        "executable": "python",
                        "args": ["server.py"],
                        "port_mode": "argument",
                        "default_port": 8000,
                        "environments": ["development"],
                        "repository_ids": ["demo-repo"],
                    }
                )
            ]
            inventory = [
                RawProcess(
                    pid=77,
                    name="python.exe",
                    executable=r"C:\Python\python.exe",
                    command_line=rf'C:\Python\python.exe "{root}\server.py"',
                    cwd=str(root),
                )
            ]
            pm = ProcessManager(state_dir=Path(tmp) / "state")
            rows = detect_repository_processes(
                repo,
                process_manager=pm,
                profiles=profiles,
                os_processes=inventory,
                listeners={},
            )
            target = rows[0]
            self.assertEqual(target.confidence, "High")
            svc = RepositoryWorkspaceService(process_manager=pm)
            with mock.patch(
                "hub.repository_workspace.service.detect_repository_processes",
                return_value=rows,
            ):
                with self.assertRaises(WorkspaceSecurityError) as ctx:
                    svc.stop_detected_process(
                        repo,
                        pid=target.pid,
                        identity_token=target.identity_token,
                        confirm=False,
                    )
                self.assertEqual(ctx.exception.code, "confirm_required")

            # Medium requires typed phrase
            target.confidence = "Medium"
            target.requires_typed_confirm = True
            target.typed_confirm_phrase = "STOP PROCESS 77"
            target.view_only = False
            with mock.patch(
                "hub.repository_workspace.service.detect_repository_processes",
                return_value=[target],
            ):
                with self.assertRaises(WorkspaceSecurityError) as ctx2:
                    svc.stop_detected_process(
                        repo,
                        pid=77,
                        identity_token=target.identity_token,
                        confirm=True,
                        typed_confirm="wrong",
                    )
                self.assertEqual(ctx2.exception.code, "typed_confirm_required")

            # PID reuse: token mismatch
            with self.assertRaises(WorkspaceSecurityError) as ctx3:
                verify_process_identity(
                    77,
                    "deadbeef",
                    os_processes=inventory,
                )
            self.assertEqual(ctx3.exception.code, "pid_reuse")

            # Low view-only
            target.confidence = "Low"
            target.view_only = True
            with mock.patch(
                "hub.repository_workspace.service.detect_repository_processes",
                return_value=[target],
            ):
                with self.assertRaises(WorkspaceSecurityError) as ctx4:
                    svc.stop_detected_process(
                        repo,
                        pid=77,
                        identity_token=target.identity_token,
                        confirm=True,
                    )
                self.assertEqual(ctx4.exception.code, "view_only")

    def test_graceful_and_force_stop_verify_end(self) -> None:
        inventory = [
            RawProcess(
                pid=88,
                name="python.exe",
                executable=r"C:\Python\python.exe",
                command_line=r"C:\Python\python.exe C:\repo\app.py",
            )
        ]
        token = _identity_token(
            pid=88,
            executable=inventory[0].executable,
            command_line=inventory[0].command_line,
        )
        with mock.patch(
            "hub.repository_workspace.process_detect.subprocess.run"
        ) as run_mock, mock.patch(
            "hub.repository_workspace.process_detect._pid_alive",
            side_effect=[True, False],
        ), mock.patch(
            "hub.repository_workspace.process_detect.verify_process_identity",
            return_value=inventory[0],
        ), mock.patch(
            "hub.repository_workspace.process_detect.port_available",
            return_value=True,
        ):
            result = stop_external_process(
                pid=88,
                identity_token=token,
                force=False,
                port=5050,
                os_processes=inventory,
            )
            self.assertTrue(result["ended"])
            self.assertTrue(result["port_released"])
            self.assertTrue(run_mock.called)
            first = run_mock.call_args_list[0][0][0]
            self.assertEqual(first[0], "taskkill")
            self.assertIn("/PID", first)
            self.assertIn("88", first)
            self.assertNotIn("/F", first)

        with mock.patch(
            "hub.repository_workspace.process_detect.subprocess.run"
        ) as run_mock, mock.patch(
            "hub.repository_workspace.process_detect._pid_alive",
            return_value=False,
        ), mock.patch(
            "hub.repository_workspace.process_detect.verify_process_identity",
            return_value=inventory[0],
        ):
            result = stop_external_process(
                pid=88,
                identity_token=token,
                force=True,
                os_processes=inventory,
            )
            self.assertTrue(result["ended"])
            args = run_mock.call_args[0][0]
            self.assertIn("/F", args)

    def test_audit_detail_redacts_secrets(self) -> None:
        detail = redact_audit_detail(
            "pid=9 cmd=python app.py TOKEN=super-secret-value path=C:/repo"
        )
        self.assertNotIn("super-secret-value", detail)
        self.assertIn("[REDACTED]", detail)


if __name__ == "__main__":
    unittest.main()
