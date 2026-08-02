#!/usr/bin/env python3
"""Launch Central Hub with graceful Ctrl+C / terminal-close cleanup.

Usage:
  python scripts/run_central_hub.py

Starts `app.py` as a child process, registers launcher ownership, and stops the
Central Hub process tree when the terminal receives Ctrl+C or is closed.
If cleanup fails, Process Manager can still detect orphans via the owned registry.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.audit import AuditStore  # noqa: E402
from hub.audit import actions as audit_actions  # noqa: E402
from hub.repository_workspace.hub_owned_registry import (  # noqa: E402
    OwnedProcessRegistry,
)
from hub.repository_workspace.hub_process_manager import (  # noqa: E402
    default_hub_process_state_dir,
)
from hub.repository_workspace.process_detect import (  # noqa: E402
    RawProcess,
    list_os_processes,
    stop_external_process,
    _identity_token,
)
from hub.settings import load_settings  # noqa: E402


class CentralHubLauncher:
    def __init__(self) -> None:
        self.root = ROOT
        self.app_path = self.root / "app.py"
        self.state_dir = default_hub_process_state_dir()
        self.registry = OwnedProcessRegistry(self.state_dir / "owned_processes.json")
        self.child: subprocess.Popen[bytes] | None = None
        self._stopping = False
        self.settings = load_settings()
        self.audit = AuditStore(self.settings.audit_log_path)

    def _raw_for_pid(self, pid: int) -> RawProcess | None:
        return next((item for item in list_os_processes() if item.pid == pid), None)

    def _register(self, pid: int, *, role: str, label: str, script_path: str) -> None:
        raw = self._raw_for_pid(pid)
        if raw is None:
            raw = RawProcess(
                pid=pid,
                name=Path(sys.executable).name,
                executable=sys.executable,
                command_line=subprocess.list2cmdline([sys.executable, *sys.argv]),
                cwd=str(self.root),
                ppid=os.getppid() if hasattr(os, "getppid") else None,
            )
        self.registry.register(
            raw=raw,
            role=role,
            label=label,
            script_path=script_path,
            port=self.settings.port if role == "server" else None,
            launcher_pid=os.getpid() if role == "server" else None,
        )

    def start(self) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["CENTRAL_HUB_LAUNCHER_PID"] = str(os.getpid())
        self._register(
            os.getpid(),
            role="launcher",
            label="Central Hub Launcher",
            script_path=str(Path(__file__).resolve()),
        )
        self.audit.append(
            action=audit_actions.CENTRAL_HUB_PROCESS_START,
            actor="launcher",
            target="central-hub",
            detail=f"launcher_pid={os.getpid()} starting app.py",
            ok=True,
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        self.child = subprocess.Popen(  # noqa: S603
            [sys.executable, str(self.app_path)],
            cwd=str(self.root),
            env=env,
            shell=False,
            creationflags=creationflags,
        )
        # Best-effort register the child server once it appears.
        for _ in range(40):
            raw = self._raw_for_pid(self.child.pid)
            if raw is not None:
                self.registry.register(
                    raw=raw,
                    role="server",
                    label="Central Hub Server",
                    script_path=str(self.app_path),
                    port=self.settings.port,
                    launcher_pid=os.getpid(),
                )
                break
            time.sleep(0.1)
        return int(self.child.pid)

    def stop_tree(self, *, reason: str) -> None:
        if self._stopping:
            return
        self._stopping = True
        child = self.child
        targets: list[RawProcess] = []
        if child is not None and child.poll() is None:
            raw = self._raw_for_pid(child.pid)
            if raw is not None:
                targets.append(raw)
        # Also stop any registry-owned hub processes still live under this launcher.
        live = {item.pid: item for item in list_os_processes()}
        for entry in self.registry.entries():
            if int(entry.get("launcher_pid") or 0) not in {0, os.getpid()}:
                continue
            if entry.get("role") == "launcher":
                continue
            pid = int(entry.get("pid") or 0)
            raw = live.get(pid)
            if raw is None:
                continue
            if raw.pid not in {item.pid for item in targets}:
                targets.append(raw)

        failures = 0
        for raw in targets:
            token = _identity_token(
                pid=raw.pid, executable=raw.executable, command_line=raw.command_line
            )
            result = stop_external_process(
                pid=raw.pid,
                identity_token=token,
                force=False,
                port=self.settings.port,
                grace_timeout_seconds=5.0,
                include_tree=True,
            )
            if result.get("ended"):
                self.registry.unregister(raw.pid)
                self.audit.append(
                    action=audit_actions.CENTRAL_HUB_PROCESS_STOP,
                    actor="launcher",
                    target="central-hub",
                    detail=f"launcher stop pid={raw.pid} reason={reason}",
                    ok=True,
                )
            else:
                failures += 1
                self.audit.append(
                    action=audit_actions.CENTRAL_HUB_PROCESS_STOP_FAILED,
                    actor="launcher",
                    target="central-hub",
                    detail=f"launcher failed stop pid={raw.pid} reason={reason}",
                    ok=False,
                    metadata={"result": result},
                )
        self.registry.unregister(os.getpid())
        if failures:
            # Leave orphan markers for Process Manager recovery.
            self.audit.append(
                action=audit_actions.CENTRAL_HUB_PROCESS_ORPHAN_RECOVERY,
                actor="launcher",
                target="central-hub",
                detail=f"cleanup incomplete failures={failures}; orphans may remain for Process Manager",
                ok=False,
            )

    def run(self) -> int:
        def _handle(_signum=None, _frame=None) -> None:
            self.stop_tree(reason="signal")

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                CTRL_CLOSE_EVENT = 2
                CTRL_LOGOFF_EVENT = 5
                CTRL_SHUTDOWN_EVENT = 6
                HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

                @HandlerRoutine
                def _console_handler(ctrl_type: int) -> bool:
                    if ctrl_type in {CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT}:
                        self.stop_tree(reason=f"console:{ctrl_type}")
                        return True
                    if ctrl_type in {0, 1}:  # CTRL_C / CTRL_BREAK
                        self.stop_tree(reason=f"console:{ctrl_type}")
                        return True
                    return False

                ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)
                self._console_handler = _console_handler  # prevent GC
            except Exception:  # noqa: BLE001
                pass

        atexit.register(lambda: self.stop_tree(reason="atexit"))
        child_pid = self.start()
        print(f"Central Hub launcher started child PID {child_pid}", flush=True)
        assert self.child is not None
        code = self.child.wait()
        self.stop_tree(reason="child_exit")
        return int(code or 0)


def main() -> int:
    return CentralHubLauncher().run()


if __name__ == "__main__":
    raise SystemExit(main())
