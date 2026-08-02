"""Central Hub self-process controls using Repository Workspace safety primitives."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from hub.audit import AuditStore
from hub.audit import actions as audit_actions
from hub.repository_workspace.hub_owned_registry import (
    OwnedProcessRegistry,
    extract_script_module,
    ownership_token,
)
from hub.repository_workspace.ports import port_available, port_listeners
from hub.repository_workspace.process_detect import (
    RawProcess,
    _identity_token,
    attach_listening_ports,
    list_os_processes,
    stop_external_process,
)
from hub.repository_workspace.security import redact_audit_detail
from hub.settings import ROOT_DIR, load_settings

STOP_ALL_CONFIRMATION = "STOP ALL CENTRAL HUB INSTANCES"
STOP_CENTRAL_HUB_CONFIRMATION = "STOP CENTRAL HUB"
DEFAULT_GRACE_SECONDS = 5.0
DEFAULT_HEALTH_TIMEOUT_SECONDS = 30.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_hub_process_state_dir() -> Path:
    configured = (os.environ.get("CENTRAL_HUB_PROCESS_STATE_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else ROOT_DIR / path
    return ROOT_DIR / "data" / "central_hub_process"


def _normalize(value: str | Path | None) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve()).replace("\\", "/").lower()
    except Exception:  # noqa: BLE001
        return str(value).replace("\\", "/").lower()


def _command_matches_app(raw: RawProcess, *, root: Path, app_path: Path) -> bool:
    command = (raw.command_line or "").replace("\\", "/").lower()
    if _normalize(app_path) in command:
        return True
    if raw.cwd and _normalize(raw.cwd) == _normalize(root):
        tokens = command.replace('"', " ").replace("'", " ").split()
        return any(Path(token).name.lower() == "app.py" for token in tokens)
    return False


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class HubInstance:
    pid: int
    executable: str
    command_redacted: str
    port: int
    identity_token: str
    started_at: str | None
    registered: bool
    owns_port: bool
    current: bool
    stale: bool
    verification_reasons: tuple[str, ...]

    def to_public(self) -> dict[str, Any]:
        data = asdict(self)
        data["verification_reasons"] = list(self.verification_reasons)
        data["status"] = "Current" if self.current else "Stale instance"
        data["verified"] = True
        data["label"] = "Central Hub Server"
        data["hub_owned"] = True
        data["stoppable"] = True
        data["group"] = "central_hub"
        return data


def _runtime_label(started_at: str | None) -> tuple[float | None, str]:
    if not started_at:
        return None, "—"
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        seconds = max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None, "—"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return seconds, f"{hours}h {minutes}m"
    if minutes:
        return seconds, f"{minutes}m {secs}s"
    return seconds, f"{secs}s"


def _process_health(raw: RawProcess, *, hub_port: int, owns_hub_port: bool) -> str:
    status = (raw.status or "").lower()
    if status in {"zombie", "dead"}:
        return "unhealthy"
    if owns_hub_port:
        return "listening"
    if raw.listening_ports:
        return "listening"
    if status in {"running", "sleeping", "disk-sleep", "idle", ""}:
        return "running"
    return status or "unknown"


class SingleInstanceError(RuntimeError):
    """Raised when another verified Central Hub instance is active."""


class CentralHubInstanceGuard:
    """Atomic PID/identity registry used by the executable app startup path."""

    def __init__(
        self,
        *,
        root: Path = ROOT_DIR,
        app_path: Path | None = None,
        port: int = 8080,
        state_dir: Path | None = None,
        process_loader: Callable[[], list[RawProcess]] = list_os_processes,
        listener_loader: Callable[..., dict[int, list[int]]] = port_listeners,
        pid: int | None = None,
    ) -> None:
        self.root = root.resolve()
        self.app_path = (app_path or self.root / "app.py").resolve()
        self.port = int(port)
        self.state_dir = (state_dir or default_hub_process_state_dir()).resolve()
        self.lock_path = self.state_dir / "instance.lock.json"
        self.registry = OwnedProcessRegistry(self.state_dir / "owned_processes.json")
        self.process_loader = process_loader
        self.listener_loader = listener_loader
        self.pid = int(pid or os.getpid())
        self.token: str | None = None
        self.launcher_pid = int(os.environ.get("CENTRAL_HUB_LAUNCHER_PID") or 0) or None

    def _read_lock(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _record_is_active(self, record: dict[str, Any], processes: list[RawProcess]) -> bool:
        try:
            pid = int(record.get("pid") or 0)
        except (TypeError, ValueError):
            return False
        raw = next((item for item in processes if item.pid == pid), None)
        if raw is None:
            return False
        expected = str(record.get("identity_token") or "")
        actual = _identity_token(pid=raw.pid, executable=raw.executable, command_line=raw.command_line)
        return bool(
            expected
            and expected == actual
            and _normalize(record.get("root")) == _normalize(self.root)
            and _normalize(record.get("app_path")) == _normalize(self.app_path)
            and _command_matches_app(raw, root=self.root, app_path=self.app_path)
        )

    def _log(self, action: str, *, pid: Any, result: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "guard.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": _utcnow(), "actor": "system", "action": action,
                "pid": pid, "result": result
            }, ensure_ascii=True) + "\n")

    def acquire(self) -> dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        processes = self.process_loader()
        listeners = self.listener_loader([self.port])
        existing = self._read_lock()
        if existing is None and self.lock_path.exists():
            self.lock_path.unlink(missing_ok=True)
            self._log("invalid_lock_cleaned", pid=None, result="removed")
        if existing:
            if self._record_is_active(existing, processes):
                raise SingleInstanceError(
                    f"Central Hub is already running with PID {int(existing.get('pid') or 0)}."
                )
            self.lock_path.unlink(missing_ok=True)
            self._log("stale_lock_cleaned", pid=existing.get("pid"), result="removed")
        listener_pids = {int(pid) for pid in listeners.get(self.port, [])}
        for raw in processes:
            if raw.pid != self.pid and raw.pid in listener_pids and _command_matches_app(
                raw, root=self.root, app_path=self.app_path
            ):
                raise SingleInstanceError(
                    f"Central Hub port {self.port} is owned by verified PID {raw.pid}."
                )
        current = next((item for item in processes if item.pid == self.pid), None)
        if current is None:
            current = RawProcess(
                pid=self.pid,
                name=Path(sys.executable).name,
                executable=sys.executable,
                command_line=subprocess.list2cmdline([sys.executable, *sys.argv]),
                cwd=str(self.root),
                started_at=_utcnow(),
            )
        identity = _identity_token(
            pid=current.pid, executable=current.executable, command_line=current.command_line
        )
        token = uuid.uuid4().hex
        record = {
            "pid": self.pid, "identity_token": identity, "lock_token": token,
            "executable": current.executable, "app_path": str(self.app_path),
            "root": str(self.root), "port": self.port,
            "started_at": current.started_at or _utcnow(), "registered_at": _utcnow(),
        }
        try:
            fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise SingleInstanceError("Central Hub instance lock was acquired concurrently.") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        self.token = token
        # Reconcile registry and register this server process as owned.
        self.registry.reconcile(processes, root=self.root, app_path=self.app_path)
        self.registry.register(
            raw=current,
            role="server",
            label="Central Hub Server",
            script_path=str(self.app_path),
            port=self.port,
            launcher_pid=self.launcher_pid,
        )
        self._log("instance_acquired", pid=self.pid, result="ok")
        atexit.register(self.release)
        return record

    def release(self) -> bool:
        if not self.token:
            return False
        record = self._read_lock()
        if not record or record.get("lock_token") != self.token:
            return False
        self.lock_path.unlink(missing_ok=True)
        try:
            self.registry.unregister(self.pid)
        except OSError:
            pass
        self._log("instance_released", pid=self.pid, result="ok")
        self.token = None
        return True


class CentralHubProcessManager:
    """Verified Central Hub instance inventory and audited control requests."""

    def __init__(
        self,
        *,
        root: Path = ROOT_DIR,
        app_path: Path | None = None,
        port: int = 8080,
        state_dir: Path | None = None,
        audit: AuditStore | None = None,
        process_loader: Callable[[], list[RawProcess]] = list_os_processes,
        listener_loader: Callable[..., dict[int, list[int]]] = port_listeners,
        stopper: Callable[..., dict[str, Any]] = stop_external_process,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.root = root.resolve()
        self.app_path = (app_path or self.root / "app.py").resolve()
        self.port = int(port)
        self.state_dir = (state_dir or default_hub_process_state_dir()).resolve()
        self.lock_path = self.state_dir / "instance.lock.json"
        self.registry = OwnedProcessRegistry(self.state_dir / "owned_processes.json")
        self.actions_dir = self.state_dir / "actions"
        self.actions_dir.mkdir(parents=True, exist_ok=True)
        self.audit, self.process_loader = audit, process_loader
        self.listener_loader, self.stopper = listener_loader, stopper
        self.popen_factory = popen_factory

    def _read_lock(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _audit(self, action: str, *, actor: str, detail: str, ok: bool, metadata=None) -> None:
        if self.audit is not None:
            self.audit.append(
                action=action, actor=actor, target="central-hub",
                detail=redact_audit_detail(detail), ok=ok, metadata=metadata or {},
            )

    def _instance(self, raw: RawProcess, *, registered: bool, owns_port: bool,
                  current: bool, reasons: tuple[str, ...]) -> HubInstance:
        return HubInstance(
            pid=raw.pid, executable=raw.executable or raw.name,
            command_redacted=redact_audit_detail(raw.command_line or raw.executable, limit=500),
            port=self.port,
            identity_token=_identity_token(
                pid=raw.pid, executable=raw.executable, command_line=raw.command_line
            ),
            started_at=raw.started_at, registered=registered, owns_port=owns_port,
            current=current, stale=not current, verification_reasons=reasons,
        )

    def scan(self, processes: list[RawProcess] | None = None) -> list[HubInstance]:
        processes = processes if processes is not None else self.process_loader()
        by_pid = {item.pid: item for item in processes}
        listener_pids = {
            int(pid) for pid in self.listener_loader([self.port]).get(self.port, [])
        }
        record = self._read_lock()
        registered_pid = int(record.get("pid") or 0) if record else 0
        found: dict[int, HubInstance] = {}
        if record and registered_pid > 0:
            raw = by_pid.get(registered_pid)
            if raw is not None:
                actual = _identity_token(
                    pid=raw.pid, executable=raw.executable, command_line=raw.command_line
                )
                if (
                    str(record.get("identity_token") or "") == actual
                    and _normalize(record.get("root")) == _normalize(self.root)
                    and _normalize(record.get("app_path")) == _normalize(self.app_path)
                    and _command_matches_app(raw, root=self.root, app_path=self.app_path)
                ):
                    found[raw.pid] = self._instance(
                        raw, registered=True, owns_port=raw.pid in listener_pids,
                        current=True,
                        reasons=("pid_registry", "identity_fingerprint", "app_path", "working_directory"),
                    )
        for pid in sorted(listener_pids):
            if pid in found:
                continue
            raw = by_pid.get(pid)
            if raw is None or not _command_matches_app(raw, root=self.root, app_path=self.app_path):
                continue
            found[pid] = self._instance(
                raw, registered=False, owns_port=True, current=False,
                reasons=("absolute_app_path", "command_line", "port_ownership"),
            )
        return sorted(found.values(), key=lambda item: (not item.current, item.pid))

    def _row_from_raw(
        self,
        raw: RawProcess,
        *,
        group: str,
        label: str,
        role: str,
        hub_owned: bool,
        stoppable: bool,
        registered: bool,
        current: bool,
        stale: bool,
        orphan: bool,
        verification_reasons: list[str],
        script_path: str,
        ownership: str,
        hub_listener_pids: set[int],
    ) -> dict[str, Any]:
        runtime_seconds, runtime_label = _runtime_label(raw.started_at)
        owns_hub_port = raw.pid in hub_listener_pids
        listen_ports = list(raw.listening_ports or ())
        if owns_hub_port and self.port not in listen_ports:
            listen_ports = sorted(set(listen_ports + [self.port]))
        identity = _identity_token(
            pid=raw.pid, executable=raw.executable, command_line=raw.command_line
        )
        health = _process_health(raw, hub_port=self.port, owns_hub_port=owns_hub_port)
        status = "Current" if current else ("Orphan" if orphan else ("Stale instance" if stale else (raw.status or "running")))
        if group == "other_python":
            status = raw.status or "running"
        return {
            "group": group,
            "label": label,
            "role": role,
            "pid": raw.pid,
            "ppid": raw.ppid,
            "script_module": extract_script_module(raw.command_line) or Path(script_path).name,
            "script_path": script_path,
            "executable": raw.executable or raw.name,
            "command_redacted": redact_audit_detail(raw.command_line or raw.executable, limit=500),
            "cwd": raw.cwd,
            "listening_port": listen_ports[0] if listen_ports else (self.port if owns_hub_port else None),
            "listening_ports": listen_ports,
            "started_at": raw.started_at,
            "runtime_seconds": runtime_seconds,
            "runtime_label": runtime_label,
            "status": status,
            "health": health,
            "hub_owned": hub_owned,
            "stoppable": bool(hub_owned and stoppable),
            "registered": registered,
            "owns_port": owns_hub_port,
            "current": current,
            "stale": stale,
            "orphan": orphan,
            "identity_token": identity,
            "ownership_token": ownership,
            "verification_reasons": verification_reasons,
            "verified": hub_owned,
            "port": self.port if owns_hub_port else (listen_ports[0] if listen_ports else None),
        }

    def inventory(self) -> dict[str, Any]:
        """Full Process Manager inventory: Central Hub group + other Python processes."""
        processes = self.process_loader()
        by_pid = {item.pid: item for item in processes}
        hub_listener_pids = {
            int(pid) for pid in self.listener_loader([self.port]).get(self.port, [])
        }
        # Enrich hub-related PIDs only (port listeners + registry + app.py matches).
        enrich_pids = set(hub_listener_pids)
        for entry in self.registry.entries():
            enrich_pids.add(int(entry.get("pid") or 0))
        for raw in processes:
            if _command_matches_app(raw, root=self.root, app_path=self.app_path):
                enrich_pids.add(raw.pid)
        if enrich_pids:
            processes = attach_listening_ports(
                processes, pids={pid for pid in enrich_pids if pid > 0}
            )
            by_pid = {item.pid: item for item in processes}
        reconciled = self.registry.reconcile(processes, root=self.root, app_path=self.app_path)
        if reconciled.get("recovered_count"):
            newly = [
                item for item in (reconciled.get("orphans") or [])
                if item.get("recovered") or item.get("orphan")
            ]
            self._audit(
                audit_actions.CENTRAL_HUB_PROCESS_ORPHAN_RECOVERY,
                actor="system",
                detail=(
                    f"recovered={reconciled['recovered_count']} "
                    f"orphans={reconciled['orphan_count']} "
                    f"removed_stale={reconciled['removed_count']}"
                ),
                ok=True,
                metadata={"orphan_pids": [int(item.get("pid") or 0) for item in newly]},
            )

        hub_rows: dict[int, dict[str, Any]] = {}
        instances = self.scan(processes)
        instance_by_pid = {item.pid: item for item in instances}

        for entry in reconciled.get("entries") or []:
            pid = int(entry.get("pid") or 0)
            raw = by_pid.get(pid)
            if raw is None:
                continue
            inst = instance_by_pid.get(pid)
            role = str(entry.get("role") or "worker")
            label = str(entry.get("label") or ("Central Hub Server" if role == "server" else "Central Hub Worker"))
            if role == "server" or _command_matches_app(raw, root=self.root, app_path=self.app_path):
                label = "Central Hub Server"
                role = "server"
            hub_rows[pid] = self._row_from_raw(
                raw,
                group="central_hub",
                label=label,
                role=role,
                hub_owned=True,
                stoppable=True,
                registered=bool(inst.registered) if inst else True,
                current=bool(inst.current) if inst else False,
                stale=bool(inst.stale) if inst else False,
                orphan=bool(entry.get("orphan") or entry.get("recovered")),
                verification_reasons=list(inst.verification_reasons) if inst else [
                    "owned_registry", "identity_fingerprint", "command_line", "start_time"
                ],
                script_path=str(entry.get("script_path") or self.app_path),
                ownership=str(entry.get("ownership_token") or ""),
                hub_listener_pids=hub_listener_pids,
            )

        # Include verified scan instances missing from registry (shouldn't happen often).
        for inst in instances:
            if inst.pid in hub_rows:
                continue
            raw = by_pid.get(inst.pid)
            if raw is None:
                continue
            hub_rows[inst.pid] = self._row_from_raw(
                raw,
                group="central_hub",
                label="Central Hub Server",
                role="server",
                hub_owned=True,
                stoppable=True,
                registered=inst.registered,
                current=inst.current,
                stale=inst.stale,
                orphan=False,
                verification_reasons=list(inst.verification_reasons),
                script_path=str(self.app_path),
                ownership=ownership_token(
                    pid=raw.pid,
                    executable=raw.executable,
                    command_line=raw.command_line,
                    script_path=str(self.app_path),
                    cwd=raw.cwd,
                    started_at=raw.started_at,
                ),
                hub_listener_pids=hub_listener_pids,
            )

        # Children of owned hub PIDs that look like Python workers.
        owned_pids = set(hub_rows)
        changed = True
        while changed:
            changed = False
            for raw in processes:
                if raw.pid in hub_rows:
                    continue
                if raw.ppid not in owned_pids:
                    continue
                name = (raw.name or "").lower()
                exe = (raw.executable or "").lower()
                is_python = (
                    "python" in name
                    or "python" in exe
                    or exe.endswith(("python.exe", "python3.exe", "pythonw.exe", "py.exe"))
                )
                if not is_python:
                    continue
                script = extract_script_module(raw.command_line) or "python-worker"
                hub_rows[raw.pid] = self._row_from_raw(
                    raw,
                    group="central_hub",
                    label="Central Hub Worker",
                    role="worker",
                    hub_owned=True,
                    stoppable=True,
                    registered=False,
                    current=False,
                    stale=False,
                    orphan=False,
                    verification_reasons=["parent_owned", "python_runtime", "process_tree"],
                    script_path=script,
                    ownership=ownership_token(
                        pid=raw.pid,
                        executable=raw.executable,
                        command_line=raw.command_line,
                        script_path=script,
                        cwd=raw.cwd,
                        started_at=raw.started_at,
                    ),
                    hub_listener_pids=hub_listener_pids,
                )
                owned_pids.add(raw.pid)
                # Persist child ownership for orphan recovery.
                self.registry.register(
                    raw=raw,
                    role="worker",
                    label="Central Hub Worker",
                    script_path=script,
                    port=None,
                    launcher_pid=None,
                )
                changed = True

        other_rows: list[dict[str, Any]] = []
        for raw in processes:
            if raw.pid in hub_rows:
                continue
            name = (raw.name or "").lower()
            exe = (raw.executable or "").lower()
            if not (
                "python" in name
                or "python" in exe
                or name in {"python", "python.exe", "python3", "python3.exe", "pythonw.exe", "py.exe"}
                or exe.endswith(("python.exe", "python3.exe", "pythonw.exe", "py.exe"))
            ):
                continue
            script = extract_script_module(raw.command_line) or Path(raw.executable or raw.name).name
            other_rows.append(
                self._row_from_raw(
                    raw,
                    group="other_python",
                    label=script or "Python process",
                    role="unrelated",
                    hub_owned=False,
                    stoppable=False,
                    registered=False,
                    current=False,
                    stale=False,
                    orphan=False,
                    verification_reasons=["unrelated_python"],
                    script_path=script,
                    ownership="",
                    hub_listener_pids=hub_listener_pids,
                )
            )

        hub_list = sorted(
            hub_rows.values(),
            key=lambda item: (0 if item.get("role") == "server" else 1, not item.get("current"), item["pid"]),
        )
        other_list = sorted(other_rows, key=lambda item: item["pid"])
        return {
            "hub_processes": hub_list,
            "other_python": other_list,
            "instances": [item.to_public() for item in instances],
            "current_pid": next((item.pid for item in instances if item.current), None),
            "registry": {
                "count": len(reconciled.get("entries") or []),
                "removed_stale": reconciled.get("removed_count", 0),
                "orphans": reconciled.get("orphan_count", 0),
            },
        }

    def _validate_owned_target(
        self,
        *,
        pid: int,
        identity_token: str,
        ownership_token_value: str,
    ) -> tuple[RawProcess, dict[str, Any]]:
        inventory = self.inventory()
        target = next(
            (item for item in inventory["hub_processes"] if int(item["pid"]) == int(pid)),
            None,
        )
        if target is None or not target.get("hub_owned") or not target.get("stoppable"):
            raise ValueError("Process is not a verified Central Hub-owned target.")
        if identity_token and identity_token != target.get("identity_token"):
            raise ValueError("PID identity changed; stop refused.")
        if ownership_token_value and ownership_token_value != target.get("ownership_token"):
            raise ValueError("Ownership token mismatch; stop refused.")
        processes = self.process_loader()
        raw = next((item for item in processes if item.pid == int(pid)), None)
        if raw is None:
            raise ValueError("Process is no longer running.")
        actual = _identity_token(pid=raw.pid, executable=raw.executable, command_line=raw.command_line)
        if actual != target.get("identity_token"):
            raise ValueError("PID identity changed; stop refused.")
        # Start-time ownership check
        expected_owned = ownership_token(
            pid=raw.pid,
            executable=raw.executable,
            command_line=raw.command_line,
            script_path=str(target.get("script_path") or ""),
            cwd=raw.cwd,
            started_at=raw.started_at or target.get("started_at"),
        )
        if target.get("ownership_token") and target.get("ownership_token") != expected_owned:
            # Allow match against registry-stored start time if live formatting differs.
            expected_stored = ownership_token(
                pid=raw.pid,
                executable=raw.executable,
                command_line=raw.command_line,
                script_path=str(target.get("script_path") or ""),
                cwd=raw.cwd or target.get("cwd"),
                started_at=target.get("started_at"),
            )
            if target.get("ownership_token") not in {expected_owned, expected_stored}:
                raise ValueError("Start-time ownership check failed; stop refused.")
        return raw, target

    def stop_owned(
        self,
        *,
        pid: int,
        identity_token: str,
        ownership_token_value: str,
        actor: str,
        confirm: bool,
        include_tree: bool = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Explicit confirmation is required.")
        raw, target = self._validate_owned_target(
            pid=pid,
            identity_token=identity_token,
            ownership_token_value=ownership_token_value,
        )
        # Current process cannot reliably stop itself in-request — use detached helper.
        if int(pid) == os.getpid() or (target.get("current") and target.get("role") == "server"):
            status = self.queue_control(
                action="stop_one",
                actor=actor,
                confirm=True,
                target_snapshot={
                    "pid": target["pid"],
                    "identity_token": target["identity_token"],
                    "ownership_token": target.get("ownership_token"),
                    "include_tree": include_tree,
                },
            )
            return {"ok": True, "queued": True, **status}

        result = self.stopper(
            pid=int(pid),
            identity_token=str(target["identity_token"]),
            force=False,
            port=self.port if target.get("owns_port") else None,
            os_processes=self.process_loader(),
            grace_timeout_seconds=DEFAULT_GRACE_SECONDS,
            include_tree=include_tree,
        )
        ended = bool(result.get("ended"))
        if ended:
            self.registry.unregister(int(pid))
            self._audit(
                audit_actions.CENTRAL_HUB_PROCESS_STOP,
                actor=actor,
                detail=f"stopped pid={pid} label={target.get('label')}",
                ok=True,
                metadata={"result": result},
            )
        else:
            self._audit(
                audit_actions.CENTRAL_HUB_PROCESS_STOP_FAILED,
                actor=actor,
                detail=f"failed stop pid={pid} error={result.get('error')}",
                ok=False,
                metadata={"result": result},
            )
        return {"ok": ended, "queued": False, "result": result, "pid": pid}

    def restart_owned(
        self,
        *,
        pid: int,
        identity_token: str,
        ownership_token_value: str,
        actor: str,
        confirm: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Explicit confirmation is required.")
        _raw, target = self._validate_owned_target(
            pid=pid,
            identity_token=identity_token,
            ownership_token_value=ownership_token_value,
        )
        if target.get("role") != "server" and not target.get("current"):
            raise ValueError("Restart is only available for the Central Hub Server process.")
        return self.queue_control(action="restart", actor=actor, confirm=True)

    def _stop_snapshot(self, snapshot: dict[str, Any], *, include_tree: bool = False) -> dict[str, Any]:
        pid, token = int(snapshot.get("pid") or 0), str(snapshot.get("identity_token") or "")
        tree = bool(snapshot.get("include_tree") or include_tree)
        processes = self.process_loader()
        raw = next((item for item in processes if item.pid == pid), None)
        if raw is None:
            return {"pid": pid, "ended": False, "error": "Process is no longer running."}
        # Owned workers may not be app.py — allow registry-backed identity match.
        owned_ok = False
        for entry in self.registry.entries():
            if int(entry.get("pid") or 0) != pid:
                continue
            if str(entry.get("identity_token") or "") == _identity_token(
                pid=raw.pid, executable=raw.executable, command_line=raw.command_line
            ):
                owned_ok = True
                break
        if not owned_ok and not _command_matches_app(raw, root=self.root, app_path=self.app_path):
            return {"pid": pid, "ended": False, "error": "Process identity is no longer verified."}
        actual = _identity_token(pid=pid, executable=raw.executable, command_line=raw.command_line)
        if not token or token != actual:
            return {"pid": pid, "ended": False, "error": "PID identity changed; stop refused."}
        result = self.stopper(
            pid=pid, identity_token=token, force=False, port=self.port,
            os_processes=processes, grace_timeout_seconds=DEFAULT_GRACE_SECONDS,
            include_tree=tree,
        )
        if result.get("ended"):
            try:
                self.registry.unregister(pid)
            except OSError:
                pass
        return result

    def stop_stale(self, *, actor: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Explicit confirmation is required.")
        targets = [item for item in self.scan() if item.stale]
        results = [self._stop_snapshot(item.to_public()) for item in targets]
        ok = all(bool(item.get("ended")) for item in results)
        self._audit(
            audit_actions.CENTRAL_HUB_PROCESS_STOP_STALE, actor=actor,
            detail=f"pids={[item.pid for item in targets]} stopped={sum(bool(r.get('ended')) for r in results)}",
            ok=ok, metadata={"results": results},
        )
        return {"ok": ok, "count": len(targets), "results": results}

    def action_status(self, action_id: str) -> dict[str, Any] | None:
        if not action_id or any(ch not in "0123456789abcdef" for ch in action_id.lower()):
            return None
        try:
            value = json.loads(
                (self.actions_dir / f"{action_id}.status.json").read_text(encoding="utf-8")
            )
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def queue_control(
        self,
        *,
        action: str,
        actor: str,
        confirm: bool = False,
        typed_confirmation: str = "",
        target_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if action not in {"stop_all", "restart", "stop_one", "stop_central_hub"}:
            raise ValueError("Unsupported Central Hub process action.")
        if action == "stop_all" and typed_confirmation.strip() != STOP_ALL_CONFIRMATION:
            raise ValueError(f'Type "{STOP_ALL_CONFIRMATION}" to continue.')
        if action == "stop_central_hub" and typed_confirmation.strip() != STOP_CENTRAL_HUB_CONFIRMATION:
            raise ValueError(f'Type "{STOP_CENTRAL_HUB_CONFIRMATION}" to continue.')
        if action in {"restart", "stop_one"} and not confirm:
            raise ValueError("Explicit confirmation is required.")
        inventory = self.inventory()
        if action == "stop_one":
            if not target_snapshot:
                raise ValueError("A verified process snapshot is required.")
            instances = [target_snapshot]
        elif action == "stop_central_hub":
            instances = list(inventory["hub_processes"])
            if not instances:
                raise ValueError("No Central Hub-owned process is available.")
        else:
            instances = inventory["instances"] or [
                item for item in inventory["hub_processes"] if item.get("role") == "server"
            ]
            if not instances:
                raise ValueError("No verified Central Hub instance is available.")
        action_id = uuid.uuid4().hex
        request_payload = {
            "action_id": action_id, "action": action, "actor": actor,
            "requested_at": _utcnow(), "root": str(self.root),
            "app_path": str(self.app_path), "port": self.port,
            "state_dir": str(self.state_dir),
            "audit_path": str(load_settings().audit_log_path),
            "python_executable": sys.executable,
            "instances": instances,
            "include_tree": action in {"stop_central_hub", "stop_all"},
        }
        request_path = self.actions_dir / f"{action_id}.request.json"
        status = {
            "action_id": action_id, "action": action, "actor": actor,
            "requested_at": request_payload["requested_at"], "status": "queued",
            "ok": None, "target_pids": [int(item.get("pid") or 0) for item in instances],
        }
        _atomic_json(request_path, request_payload)
        _atomic_json(self.actions_dir / f"{action_id}.status.json", status)
        creationflags, start_new_session = 0, False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        else:
            start_new_session = True
        try:
            self.popen_factory(
                [sys.executable, "-m", "hub.repository_workspace.hub_process_manager",
                 "--request", str(request_path)],
                cwd=str(self.root), shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as exc:
            status.update(status="failed", ok=False, finished_at=_utcnow(), error=str(exc))
            _atomic_json(self.actions_dir / f"{action_id}.status.json", status)
            raise ValueError("Unable to start the detached process controller.") from exc
        audit_map = {
            "stop_all": audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL,
            "restart": audit_actions.CENTRAL_HUB_PROCESS_RESTART,
            "stop_one": audit_actions.CENTRAL_HUB_PROCESS_STOP,
            "stop_central_hub": audit_actions.CENTRAL_HUB_PROCESS_STOP_CENTRAL_HUB,
        }
        self._audit(
            audit_map[action], actor=actor,
            detail=f"queued action={action} pids={status['target_pids']}", ok=True,
            metadata={"action_id": action_id, "status": "queued"},
        )
        return status


def _health_ok(port: int) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/api/healthz"
        with urlopen(Request(url), timeout=2) as response:  # noqa: S310 - loopback only
            if not (200 <= int(getattr(response, "status", 200)) < 300):
                return False
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return bool(payload.get("ok") and payload.get("service") == "central-hub")
    except Exception:  # noqa: BLE001
        return False


def execute_control_request(request_path: Path) -> dict[str, Any]:
    request_payload = json.loads(request_path.read_text(encoding="utf-8"))
    action_id = str(request_payload["action_id"])
    state_dir = Path(request_payload["state_dir"]).resolve()
    status_path = state_dir / "actions" / f"{action_id}.status.json"
    audit = AuditStore(Path(request_payload["audit_path"]))
    manager = CentralHubProcessManager(
        root=Path(request_payload["root"]), app_path=Path(request_payload["app_path"]),
        port=int(request_payload["port"]), state_dir=state_dir, audit=audit,
    )
    action = str(request_payload["action"])
    include_tree = bool(request_payload.get("include_tree") or action in {"stop_central_hub", "stop_all"})
    status = {
        "action_id": action_id, "action": action,
        "actor": request_payload["actor"], "requested_at": request_payload["requested_at"],
        "started_at": _utcnow(), "status": "running", "ok": None,
        "target_pids": [int(item.get("pid") or 0) for item in request_payload["instances"]],
    }
    _atomic_json(status_path, status)

    # Prefer leaf workers first so the tree collapses cleanly.
    snapshots = sorted(
        request_payload["instances"],
        key=lambda item: (0 if item.get("role") not in {None, "server"} else 1, int(item.get("pid") or 0)),
    )
    results = [
        manager._stop_snapshot(item, include_tree=include_tree or bool(item.get("include_tree")))
        for item in snapshots
    ]
    deadline = time.monotonic() + DEFAULT_GRACE_SECONDS
    while not port_available(manager.port) and time.monotonic() < deadline:
        time.sleep(0.1)
    released = port_available(manager.port)
    status.update(stop_results=results, port_released=released)

    if action == "restart" and released:
        log_path = state_dir / "server.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            creationflags, start_new_session = 0, False
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            else:
                start_new_session = True
            proc = subprocess.Popen(  # noqa: S603 - fixed executable and app path
                [request_payload["python_executable"], request_payload["app_path"]],
                cwd=request_payload["root"], shell=False, stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=log_handle, close_fds=True,
                creationflags=creationflags, start_new_session=start_new_session,
            )
        status["launch_pid"] = proc.pid
        health_deadline = time.monotonic() + DEFAULT_HEALTH_TIMEOUT_SECONDS
        healthy = False
        while time.monotonic() < health_deadline:
            if _health_ok(manager.port):
                healthy = True
                break
            time.sleep(0.25)
        listeners = port_listeners([manager.port]).get(manager.port, [])
        status.update(
            new_pid=int(listeners[0]) if len(listeners) == 1 else None,
            listener_count=len(listeners), health_ok=healthy,
            ok=bool(healthy and len(listeners) == 1),
        )
        if status.get("ok"):
            audit.append(
                action=audit_actions.CENTRAL_HUB_PROCESS_START,
                actor=request_payload["actor"],
                target="central-hub",
                detail=f"restarted new_pid={status.get('new_pid')}",
                ok=True,
                metadata={"action_id": action_id, "launch_pid": proc.pid},
            )
    else:
        status["ok"] = bool(
            (released or action in {"stop_one"})
            and all(bool(item.get("ended")) for item in results)
        )
        if action == "restart" and not released:
            status["error"] = "Central Hub port was not released; restart was not attempted."

    status["status"] = "completed" if status.get("ok") else "failed"
    status["finished_at"] = _utcnow()
    _atomic_json(status_path, status)
    action_name = {
        "stop_all": audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL,
        "restart": audit_actions.CENTRAL_HUB_PROCESS_RESTART,
        "stop_one": (
            audit_actions.CENTRAL_HUB_PROCESS_STOP
            if status.get("ok")
            else audit_actions.CENTRAL_HUB_PROCESS_STOP_FAILED
        ),
        "stop_central_hub": audit_actions.CENTRAL_HUB_PROCESS_STOP_CENTRAL_HUB,
    }.get(action, audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL)
    audit.append(
        action=action_name, actor=request_payload["actor"], target="central-hub",
        detail=f"action={action} pids={status['target_pids']} status={status['status']}",
        ok=bool(status.get("ok")),
        metadata={"action_id": action_id, "new_pid": status.get("new_pid"),
                  "port_released": status.get("port_released"),
                  "health_ok": status.get("health_ok")},
    )
    return status


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    try:
        result = execute_control_request(Path(args.request).resolve())
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        try:
            request_path = Path(args.request).resolve()
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            status_path = (
                Path(payload["state_dir"]).resolve()
                / "actions"
                / f"{payload['action_id']}.status.json"
            )
            status = {
                "action_id": payload["action_id"],
                "action": payload["action"],
                "actor": payload["actor"],
                "requested_at": payload["requested_at"],
                "finished_at": _utcnow(),
                "status": "failed",
                "ok": False,
                "error": f"Controller failed ({type(exc).__name__}).",
            }
            _atomic_json(status_path, status)
            AuditStore(Path(payload["audit_path"])).append(
                action=(
                    audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL
                    if payload["action"] == "stop_all"
                    else audit_actions.CENTRAL_HUB_PROCESS_RESTART
                ),
                actor=payload["actor"],
                target="central-hub",
                detail=f"action={payload['action']} controller_failed={type(exc).__name__}",
                ok=False,
                metadata={"action_id": payload["action_id"]},
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
