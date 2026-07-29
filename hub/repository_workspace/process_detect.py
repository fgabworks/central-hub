"""Detect repository-related local processes (hub-tracked + external).

Safety rules:
- Never match only by generic runtime names (python.exe, node.exe, …).
- Never kill broad process classes — only a verified PID (+ child tree).
- Low confidence is view-only; Medium external requires typed confirmation;
  High external and hub-managed require explicit confirm flags.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hub.registry.models import Repository
from hub.repository_workspace.ports import port_available, port_listeners
from hub.repository_workspace.process_manager import ACTIVE_STATUSES, ProcessManager
from hub.repository_workspace.run_profiles import (
    RunProfile,
    merged_profiles_for_repository,
)
from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    redact_audit_detail,
    resolve_repo_root,
)

GENERIC_RUNTIMES = frozenset(
    {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "pythonw.exe",
        "py.exe",
        "node",
        "node.exe",
        "nodejs",
        "java",
        "java.exe",
        "javaw.exe",
        "ruby",
        "ruby.exe",
        "perl",
        "perl.exe",
        "dotnet",
        "dotnet.exe",
        "php",
        "php.exe",
    }
)

CONFIDENCE_HIGH = "High"
CONFIDENCE_MEDIUM = "Medium"
CONFIDENCE_LOW = "Low"


@dataclass
class RawProcess:
    pid: int
    name: str
    executable: str
    command_line: str
    cwd: str | None = None
    started_at: str | None = None


@dataclass
class DetectedProcess:
    pid: int
    executable: str
    command_redacted: str
    port: int | None
    started_at: str | None
    managed_by_hub: bool
    detection_reasons: list[str]
    confidence: str
    repo_id: str
    run_id: str | None = None
    profile_id: str | None = None
    identity_token: str = ""
    stoppable: bool = False
    view_only: bool = True
    requires_typed_confirm: bool = False
    typed_confirm_phrase: str | None = None

    def to_public(self) -> dict[str, Any]:
        data = asdict(self)
        data["managed_by_hub_label"] = "Yes" if self.managed_by_hub else "No"
        return data


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_path(value: str | Path | None) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).expanduser().resolve()).replace("\\", "/").lower()
    except Exception:  # noqa: BLE001
        return str(value).replace("\\", "/").lower()


def _basename(path: str) -> str:
    return Path(path or "").name.lower()


def _is_generic_runtime(executable: str, name: str = "") -> bool:
    return _basename(executable) in GENERIC_RUNTIMES or (name or "").lower() in GENERIC_RUNTIMES


def _redact_command(command: str) -> str:
    return redact_audit_detail(command or "", limit=500)


def _identity_token(*, pid: int, executable: str, command_line: str) -> str:
    raw = f"{pid}|{_normalize_path(executable)}|{command_line}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]


def _entry_points_from_profiles(profiles: Iterable[RunProfile]) -> set[str]:
    entries: set[str] = set()
    for profile in profiles:
        for arg in profile.args:
            text = str(arg).strip().replace("\\", "/")
            if not text or text.startswith("-") or "{" in text:
                continue
            # Likely file/module entry points
            if "/" in text or text.endswith((".py", ".js", ".ts", ".mjs", ".cjs")):
                entries.add(text.lower())
                entries.add(Path(text).name.lower())
    return entries


def _profile_ports(profiles: Iterable[RunProfile]) -> set[int]:
    ports: set[int] = set()
    for profile in profiles:
        if profile.port_mode == "none":
            continue
        if profile.fixed_port:
            ports.add(int(profile.fixed_port))
        if profile.default_port:
            ports.add(int(profile.default_port))
    return ports


def list_os_processes(*, timeout_seconds: float = 12.0) -> list[RawProcess]:
    """Best-effort local process inventory (no psutil dependency)."""
    if os.name == "nt":
        return _list_windows_processes(timeout_seconds=timeout_seconds)
    return _list_unix_processes()


def _list_windows_processes(*, timeout_seconds: float) -> list[RawProcess]:
    # Fixed argv — shell=False. ConvertTo-Json for stable parsing.
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine,CreationDate | "
        "ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    out: list[RawProcess] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("ProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        out.append(
            RawProcess(
                pid=pid,
                name=str(item.get("Name") or ""),
                executable=str(item.get("ExecutablePath") or item.get("Name") or ""),
                command_line=str(item.get("CommandLine") or ""),
                started_at=str(item.get("CreationDate") or "") or None,
            )
        )
    return out


def _list_unix_processes() -> list[RawProcess]:
    out: list[RawProcess] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return out
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmdline_raw = (entry / "cmdline").read_bytes()
            cmdline = cmdline_raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            exe = ""
            try:
                exe = str((entry / "exe").resolve())
            except Exception:  # noqa: BLE001
                exe = ""
            cwd = None
            try:
                cwd = str((entry / "cwd").resolve())
            except Exception:  # noqa: BLE001
                cwd = None
            name = ""
            try:
                status = (entry / "status").read_text(encoding="utf-8", errors="replace")
                for line in status.splitlines():
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                        break
            except Exception:  # noqa: BLE001
                name = Path(exe).name if exe else ""
            started = None
            try:
                # Approximate start from /proc/<pid>/stat field 22 (starttime) is complex;
                # use mtime of the directory as a coarse signal.
                started = datetime.fromtimestamp(
                    entry.stat().st_ctime, tz=timezone.utc
                ).isoformat()
            except Exception:  # noqa: BLE001
                started = None
            out.append(
                RawProcess(
                    pid=pid,
                    name=name,
                    executable=exe or name,
                    command_line=cmdline or exe,
                    cwd=cwd,
                    started_at=started,
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


def _command_references_repo(command: str, repo_norm: str) -> bool:
    if not repo_norm or not command:
        return False
    cmd = command.replace("\\", "/").lower()
    return repo_norm in cmd


def _command_references_entry(command: str, entries: set[str]) -> bool:
    if not command or not entries:
        return False
    cmd = command.replace("\\", "/").lower()
    for entry in entries:
        if not entry:
            continue
        # Require path-like or basename with surrounding separators to reduce false hits
        if f"/{entry}" in cmd or cmd.endswith(entry) or f" {entry} " in f" {cmd} ":
            # Basename-only hits for generic words are ignored by caller via length check
            if "/" not in entry and len(entry) < 5:
                continue
            return True
    return False


def _cwd_in_repo(cwd: str | None, repo_norm: str, repo_root: Path) -> bool:
    if not cwd or not repo_norm:
        return False
    try:
        Path(cwd).resolve().relative_to(repo_root.resolve())
        return True
    except Exception:  # noqa: BLE001
        return _normalize_path(cwd).startswith(repo_norm.rstrip("/") + "/") or _normalize_path(
            cwd
        ) == repo_norm


def _score_confidence(reasons: list[str], *, managed: bool) -> str:
    if managed or "hub_tracked" in reasons:
        return CONFIDENCE_HIGH
    strong = {
        "command_references_repository_path",
        "command_references_entry_point",
        "owns_profile_port",
        "working_directory_in_repo",
    }
    hits = [r for r in reasons if r in strong]
    if len(hits) >= 2:
        return CONFIDENCE_HIGH
    if "command_references_repository_path" in hits:
        return CONFIDENCE_HIGH
    if hits:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def detect_repository_processes(
    repo: Repository,
    *,
    process_manager: ProcessManager,
    profiles: list[RunProfile] | None = None,
    os_processes: list[RawProcess] | None = None,
    listeners: dict[int, list[int]] | None = None,
) -> list[DetectedProcess]:
    root = resolve_repo_root(repo.local_path or repo.working_directory)
    if root is None:
        raise WorkspaceSecurityError(
            "Local workspace unavailable for process detection.",
            code="unavailable",
        )
    repo_norm = _normalize_path(root)
    profile_list = profiles
    if profile_list is None:
        profile_list = merged_profiles_for_repository(
            repo.id, include_disabled=True, include_unapproved=True
        )
    entries = _entry_points_from_profiles(profile_list)
    ports = _profile_ports(profile_list)
    listener_map = listeners if listeners is not None else port_listeners(ports)
    pid_to_ports: dict[int, set[int]] = {}
    for port, pids in listener_map.items():
        for pid in pids:
            pid_to_ports.setdefault(int(pid), set()).add(int(port))

    hub_by_pid: dict[int, Any] = {}
    for run in process_manager.list_runs(repo_id=repo.id):
        if run.status not in ACTIVE_STATUSES:
            continue
        if run.pid:
            hub_by_pid[int(run.pid)] = run

    inventory = os_processes if os_processes is not None else list_os_processes()
    by_pid = {p.pid: p for p in inventory}
    detected: dict[int, DetectedProcess] = {}

    # 1) Hub-tracked first
    for pid, run in hub_by_pid.items():
        raw = by_pid.get(pid)
        exe = (raw.executable if raw else None) or run.executable_path or ""
        cmd = (raw.command_line if raw else None) or " ".join(run.argv_redacted or [])
        port = int(run.port) if run.port else None
        if port is None:
            owned = pid_to_ports.get(pid) or set()
            port = sorted(owned)[0] if owned else None
        reasons = ["hub_tracked"]
        if port:
            reasons.append("owns_profile_port")
        item = DetectedProcess(
            pid=pid,
            executable=exe or "(hub-tracked)",
            command_redacted=_redact_command(cmd),
            port=port,
            started_at=(raw.started_at if raw else None) or run.started_at,
            managed_by_hub=True,
            detection_reasons=reasons,
            confidence=CONFIDENCE_HIGH,
            repo_id=repo.id,
            run_id=run.run_id,
            profile_id=run.profile_id,
            identity_token=_identity_token(pid=pid, executable=exe, command_line=cmd),
            stoppable=True,
            view_only=False,
            requires_typed_confirm=False,
        )
        detected[pid] = item

    # 2) External candidates
    for raw in inventory:
        if raw.pid in detected:
            continue
        if raw.pid == os.getpid():
            continue
        reasons: list[str] = []
        if _cwd_in_repo(raw.cwd, repo_norm, root):
            reasons.append("working_directory_in_repo")
        if _command_references_repo(raw.command_line, repo_norm):
            reasons.append("command_references_repository_path")
        if _command_references_entry(raw.command_line, entries):
            reasons.append("command_references_entry_point")
        owned_ports = sorted(pid_to_ports.get(raw.pid) or set())
        owned_profile_ports = [p for p in owned_ports if p in ports]
        if owned_profile_ports:
            reasons.append("owns_profile_port")

        # Never match only by generic runtime names
        if not reasons:
            continue
        if reasons == ["owns_profile_port"] and _is_generic_runtime(raw.executable, raw.name):
            # Port ownership alone on python/node is Medium, still attributed — keep.
            pass
        if set(reasons) <= {"working_directory_in_repo"} and _is_generic_runtime(
            raw.executable, raw.name
        ):
            # cwd alone + generic name is too weak unless command also references repo/entry
            if "command_references_repository_path" not in reasons and (
                "command_references_entry_point" not in reasons
            ):
                # Downgrade to low by treating as weak — still show if cwd is in repo
                pass

        # Drop pure generic-name matches (no real reasons beyond name — already handled)
        confidence = _score_confidence(reasons, managed=False)
        # Generic-only cwd: Low
        if (
            confidence == CONFIDENCE_MEDIUM
            and reasons == ["working_directory_in_repo"]
            and _is_generic_runtime(raw.executable, raw.name)
        ):
            confidence = CONFIDENCE_LOW

        view_only = confidence == CONFIDENCE_LOW
        requires_typed = confidence == CONFIDENCE_MEDIUM
        item = DetectedProcess(
            pid=raw.pid,
            executable=raw.executable or raw.name or f"pid-{raw.pid}",
            command_redacted=_redact_command(raw.command_line or raw.executable),
            port=owned_profile_ports[0] if owned_profile_ports else (owned_ports[0] if owned_ports else None),
            started_at=raw.started_at,
            managed_by_hub=False,
            detection_reasons=reasons,
            confidence=confidence,
            repo_id=repo.id,
            identity_token=_identity_token(
                pid=raw.pid, executable=raw.executable, command_line=raw.command_line
            ),
            stoppable=not view_only,
            view_only=view_only,
            requires_typed_confirm=requires_typed,
            typed_confirm_phrase=f"STOP PROCESS {raw.pid}" if requires_typed else None,
        )
        detected[raw.pid] = item

    # 3) Listener PIDs on profile ports that were missing from the process inventory
    for port, pids in listener_map.items():
        if port not in ports:
            continue
        for pid in pids:
            if int(pid) in detected or int(pid) == os.getpid():
                continue
            raw = by_pid.get(int(pid))
            exe = (raw.executable if raw else "") or _process_image(int(pid)) or f"pid-{pid}"
            cmd = (raw.command_line if raw else "") or exe
            reasons = ["owns_profile_port"]
            if raw and _command_references_repo(raw.command_line, repo_norm):
                reasons.append("command_references_repository_path")
            if raw and _command_references_entry(raw.command_line, entries):
                reasons.append("command_references_entry_point")
            confidence = _score_confidence(reasons, managed=False)
            view_only = confidence == CONFIDENCE_LOW
            requires_typed = confidence == CONFIDENCE_MEDIUM
            detected[int(pid)] = DetectedProcess(
                pid=int(pid),
                executable=exe,
                command_redacted=_redact_command(cmd),
                port=int(port),
                started_at=raw.started_at if raw else None,
                managed_by_hub=False,
                detection_reasons=reasons,
                confidence=confidence,
                repo_id=repo.id,
                identity_token=_identity_token(
                    pid=int(pid), executable=exe, command_line=cmd
                ),
                stoppable=not view_only,
                view_only=view_only,
                requires_typed_confirm=requires_typed,
                typed_confirm_phrase=f"STOP PROCESS {int(pid)}" if requires_typed else None,
            )

    # 4) Occupied profile ports with no owning PID (still show advisory)
    for port in sorted(ports):
        if any(p.port == port for p in detected.values()):
            continue
        if port_available(int(port)):
            continue
        # Synthetic view-only row — cannot stop without a PID
        fake_pid = -int(port)
        detected[fake_pid] = DetectedProcess(
            pid=0,
            executable="(unknown listener)",
            command_redacted=f"Port {port} is occupied but no owning PID was identified.",
            port=int(port),
            started_at=None,
            managed_by_hub=False,
            detection_reasons=["owns_profile_port", "owner_unresolved"],
            confidence=CONFIDENCE_MEDIUM,
            repo_id=repo.id,
            identity_token="",
            stoppable=False,
            view_only=True,
            requires_typed_confirm=False,
        )

    rows = list(detected.values())
    rows.sort(
        key=lambda p: (
            0 if p.managed_by_hub else 1,
            {"High": 0, "Medium": 1, "Low": 2}.get(p.confidence, 9),
            p.pid if p.pid > 0 else 10_000_000 + abs(p.port or 0),
        )
    )
    return rows


def _process_image(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return None
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                )
                return buf.value if ok else None
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return None
    try:
        return str(Path(f"/proc/{pid}/exe").resolve())
    except Exception:  # noqa: BLE001
        return None


def find_start_conflicts(
    repo: Repository,
    *,
    process_manager: ProcessManager,
    profile: RunProfile,
    resolved_port: int | None,
    detected: list[DetectedProcess] | None = None,
) -> dict[str, Any]:
    """Return blocking conflict info before Start.

    Blocks only when:
    - fixed-port profile and the fixed port is occupied, or
    - a High/Medium process already owns the exact resolved port.

    Related processes on other ports do not block dynamic-port profiles.
    Never silently switches fixed ports.
    """
    rows = detected
    if rows is None:
        rows = detect_repository_processes(repo, process_manager=process_manager)

    conflicts: list[dict[str, Any]] = []
    for row in rows:
        if row.managed_by_hub:
            continue
        if row.confidence == CONFIDENCE_LOW:
            continue
        if resolved_port is not None and row.port == resolved_port:
            # Unresolved / synthetic listeners: fixed ports still block via bind check;
            # dynamic profiles may choose another port instead of hard-blocking here.
            if row.pid <= 0 or "owner_unresolved" in (row.detection_reasons or []):
                continue
            conflicts.append(row.to_public())

    fixed_occupied = False
    if profile.port_mode == "fixed" and resolved_port is not None:
        fixed_occupied = not port_available(int(resolved_port))

    same_port_conflict = bool(conflicts)
    blocked = fixed_occupied or same_port_conflict

    if blocked:
        if fixed_occupied and not same_port_conflict:
            message = (
                f"Fixed port {resolved_port} is occupied. "
                "Open Repository Processes, stop the PID that owns it, then Start again. "
                "Fixed-port profiles do not auto-switch ports."
            )
        elif same_port_conflict:
            message = (
                f"Port {resolved_port} is already used by a detected repository process. "
                "Open Repository Processes, review the PID, then stop it explicitly before starting."
            )
        else:
            message = (
                "Conflicting repository process detected. "
                "Open Repository Processes on the Run tab before starting."
            )
    else:
        message = None

    return {
        "blocked": blocked,
        "fixed_port_occupied": fixed_occupied,
        "resolved_port": resolved_port,
        "conflicts": conflicts,
        "message": message,
        "processes_url_hint": f"/repositories/{repo.id}/run#repository-processes",
    }


def verify_process_identity(pid: int, identity_token: str, *, os_processes: list[RawProcess] | None = None) -> RawProcess:
    inventory = os_processes if os_processes is not None else list_os_processes()
    match = next((p for p in inventory if p.pid == int(pid)), None)
    if match is None:
        raise WorkspaceSecurityError(
            "PID is not running or could not be re-identified (possible PID reuse).",
            code="pid_stale",
        )
    token = _identity_token(
        pid=match.pid, executable=match.executable, command_line=match.command_line
    )
    if token != identity_token:
        raise WorkspaceSecurityError(
            "Process identity changed since scan (PID reuse protection).",
            code="pid_reuse",
        )
    return match


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_external_process(
    *,
    pid: int,
    identity_token: str,
    force: bool = False,
    port: int | None = None,
    os_processes: list[RawProcess] | None = None,
) -> dict[str, Any]:
    """Stop a verified external PID tree only (never broad runtime kills)."""
    verify_process_identity(pid, identity_token, os_processes=os_processes)
    if os.name == "nt":
        args = ["taskkill", "/PID", str(int(pid)), "/T"]
        if force:
            args.append("/F")
        subprocess.run(
            args,
            shell=False,
            capture_output=True,
            check=False,
            timeout=12,
        )
    else:
        import signal

        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        if force:
            time.sleep(0.4)
            if _pid_alive(int(pid)):
                try:
                    # Prefer process group when pid is session leader
                    os.killpg(int(pid), signal.SIGKILL)
                except OSError:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except OSError:
                        pass
        else:
            time.sleep(0.8)
            if _pid_alive(int(pid)):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass

    time.sleep(0.25)
    # Re-verify identity before a second force pulse if still alive
    still = _pid_alive(int(pid))
    if still:
        try:
            verify_process_identity(pid, identity_token, os_processes=None)
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    shell=False,
                    capture_output=True,
                    check=False,
                    timeout=12,
                )
            else:
                import signal

                try:
                    os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
            time.sleep(0.25)
            still = _pid_alive(int(pid))
        except WorkspaceSecurityError:
            # Identity changed — do not keep signaling
            still = _pid_alive(int(pid))

    port_released = None
    if port:
        port_released = port_available(int(port))
    return {
        "pid": int(pid),
        "ended": not still,
        "port": port,
        "port_released": port_released,
        "force": bool(force),
        "checked_at": _utcnow(),
    }


def summarize_all_repositories(
    repositories: Iterable[Repository],
    *,
    process_manager: ProcessManager,
) -> list[dict[str, Any]]:
    """Read-only cross-repo process summary for Health."""
    # Enumerate once
    inventory = list_os_processes()
    listeners = port_listeners()
    summary: list[dict[str, Any]] = []
    for repo in repositories:
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        if root is None:
            continue
        try:
            rows = detect_repository_processes(
                repo,
                process_manager=process_manager,
                os_processes=inventory,
                listeners=listeners,
            )
        except WorkspaceSecurityError:
            continue
        for row in rows:
            public = row.to_public()
            public["repository_name"] = repo.name
            summary.append(public)
    return summary
