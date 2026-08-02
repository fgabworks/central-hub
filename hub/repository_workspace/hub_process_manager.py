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
from hub.repository_workspace.ports import port_available, port_listeners
from hub.repository_workspace.process_detect import (
    RawProcess,
    _identity_token,
    list_os_processes,
    stop_external_process,
)
from hub.repository_workspace.security import redact_audit_detail
from hub.settings import ROOT_DIR, load_settings

STOP_ALL_CONFIRMATION = "STOP ALL CENTRAL HUB INSTANCES"
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
        return data


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
        self.process_loader = process_loader
        self.listener_loader = listener_loader
        self.pid = int(pid or os.getpid())
        self.token: str | None = None

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

    def scan(self) -> list[HubInstance]:
        processes = self.process_loader()
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

    def _stop_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        pid, token = int(snapshot.get("pid") or 0), str(snapshot.get("identity_token") or "")
        processes = self.process_loader()
        raw = next((item for item in processes if item.pid == pid), None)
        if raw is None or not _command_matches_app(raw, root=self.root, app_path=self.app_path):
            return {"pid": pid, "ended": False, "error": "Process identity is no longer verified."}
        actual = _identity_token(pid=pid, executable=raw.executable, command_line=raw.command_line)
        if not token or token != actual:
            return {"pid": pid, "ended": False, "error": "PID identity changed; stop refused."}
        return self.stopper(
            pid=pid, identity_token=token, force=False, port=self.port,
            os_processes=processes, grace_timeout_seconds=DEFAULT_GRACE_SECONDS,
            include_tree=False,
        )

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
    ) -> dict[str, Any]:
        if action not in {"stop_all", "restart"}:
            raise ValueError("Unsupported Central Hub process action.")
        if action == "stop_all" and typed_confirmation.strip() != STOP_ALL_CONFIRMATION:
            raise ValueError(f'Type "{STOP_ALL_CONFIRMATION}" to continue.')
        if action == "restart" and not confirm:
            raise ValueError("Explicit confirmation is required.")
        instances = self.scan()
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
            "instances": [item.to_public() for item in instances],
        }
        request_path = self.actions_dir / f"{action_id}.request.json"
        status = {
            "action_id": action_id, "action": action, "actor": actor,
            "requested_at": request_payload["requested_at"], "status": "queued",
            "ok": None, "target_pids": [item.pid for item in instances],
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
        audit_action = (
            audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL
            if action == "stop_all" else audit_actions.CENTRAL_HUB_PROCESS_RESTART
        )
        self._audit(
            audit_action, actor=actor,
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
    status = {
        "action_id": action_id, "action": request_payload["action"],
        "actor": request_payload["actor"], "requested_at": request_payload["requested_at"],
        "started_at": _utcnow(), "status": "running", "ok": None,
        "target_pids": [int(item.get("pid") or 0) for item in request_payload["instances"]],
    }
    _atomic_json(status_path, status)
    results = [manager._stop_snapshot(item) for item in request_payload["instances"]]
    deadline = time.monotonic() + DEFAULT_GRACE_SECONDS
    while not port_available(manager.port) and time.monotonic() < deadline:
        time.sleep(0.1)
    released = port_available(manager.port)
    status.update(stop_results=results, port_released=released)

    if request_payload["action"] == "restart" and released:
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
    else:
        status["ok"] = bool(released and all(bool(item.get("ended")) for item in results))
        if request_payload["action"] == "restart" and not released:
            status["error"] = "Central Hub port was not released; restart was not attempted."

    status["status"] = "completed" if status.get("ok") else "failed"
    status["finished_at"] = _utcnow()
    _atomic_json(status_path, status)
    action_name = (
        audit_actions.CENTRAL_HUB_PROCESS_STOP_ALL
        if request_payload["action"] == "stop_all"
        else audit_actions.CENTRAL_HUB_PROCESS_RESTART
    )
    audit.append(
        action=action_name, actor=request_payload["actor"], target="central-hub",
        detail=f"action={request_payload['action']} pids={status['target_pids']} status={status['status']}",
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
