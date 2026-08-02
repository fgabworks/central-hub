"""Runtime registry of Central Hub-owned process identities.

Ownership is validated with PID + command + script path + cwd + start time so a
reused PID cannot pass a stale registry entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.repository_workspace.process_detect import RawProcess, _identity_token, _normalize_path


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ownership_token(
    *,
    pid: int,
    executable: str,
    command_line: str,
    script_path: str,
    cwd: str | None,
    started_at: str | None,
) -> str:
    raw = (
        f"{int(pid)}|{_normalize_path(executable)}|{command_line}|"
        f"{_normalize_path(script_path)}|{_normalize_path(cwd)}|{started_at or ''}"
    )
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def extract_script_module(command_line: str) -> str:
    text = (command_line or "").strip()
    if not text:
        return ""
    lowered = text.replace("\\", "/")
    tokens: list[str] = []
    current = ""
    in_quote = ""
    for ch in text:
        if in_quote:
            if ch == in_quote:
                in_quote = ""
            else:
                current += ch
            continue
        if ch in {'"', "'"}:
            in_quote = ch
            continue
        if ch.isspace():
            if current:
                tokens.append(current)
                current = ""
            continue
        current += ch
    if current:
        tokens.append(current)
    for idx, token in enumerate(tokens):
        if token in {"-m", "--module"} and idx + 1 < len(tokens):
            return tokens[idx + 1]
        if token.lower().endswith(".py"):
            return Path(token).name
    # Fallback: first non-runtime token
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        return Path(token).name or token
    if "app.py" in lowered.lower():
        return "app.py"
    return Path(tokens[0]).name if tokens else ""


class OwnedProcessRegistry:
    """Persistent registry of Central Hub-owned PIDs with reconcile-on-read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("entries"), list):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "entries": [], "updated_at": None}

    def _write(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["version"] = 1
        payload["updated_at"] = _utcnow()
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def entries(self) -> list[dict[str, Any]]:
        return list(self._read().get("entries") or [])

    def register(
        self,
        *,
        raw: RawProcess,
        role: str,
        label: str,
        script_path: str,
        port: int | None = None,
        launcher_pid: int | None = None,
    ) -> dict[str, Any]:
        identity = _identity_token(
            pid=raw.pid, executable=raw.executable, command_line=raw.command_line
        )
        owned = ownership_token(
            pid=raw.pid,
            executable=raw.executable,
            command_line=raw.command_line,
            script_path=script_path,
            cwd=raw.cwd,
            started_at=raw.started_at,
        )
        entry = {
            "pid": int(raw.pid),
            "ppid": raw.ppid,
            "role": role,
            "label": label,
            "identity_token": identity,
            "ownership_token": owned,
            "executable": raw.executable,
            "command_line": raw.command_line,
            "script_path": script_path,
            "cwd": raw.cwd,
            "started_at": raw.started_at,
            "registered_at": _utcnow(),
            "port": port,
            "launcher_pid": launcher_pid,
        }
        payload = self._read()
        entries = [item for item in payload.get("entries") or [] if int(item.get("pid") or 0) != raw.pid]
        entries.append(entry)
        payload["entries"] = entries
        self._write(payload)
        return entry

    def unregister(self, pid: int) -> None:
        payload = self._read()
        payload["entries"] = [
            item for item in payload.get("entries") or [] if int(item.get("pid") or 0) != int(pid)
        ]
        self._write(payload)

    def clear(self) -> None:
        self._write({"entries": []})

    def reconcile(
        self,
        processes: list[RawProcess],
        *,
        root: Path,
        app_path: Path,
    ) -> dict[str, Any]:
        """Drop stale/reused registry rows; keep live matches and recover orphans."""
        by_pid = {item.pid: item for item in processes}
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        orphans: list[dict[str, Any]] = []
        for entry in self.entries():
            try:
                pid = int(entry.get("pid") or 0)
            except (TypeError, ValueError):
                removed.append(entry)
                continue
            raw = by_pid.get(pid)
            if raw is None:
                removed.append(entry)
                continue
            expected_identity = str(entry.get("identity_token") or "")
            actual_identity = _identity_token(
                pid=raw.pid, executable=raw.executable, command_line=raw.command_line
            )
            expected_owned = str(entry.get("ownership_token") or "")
            actual_owned = ownership_token(
                pid=raw.pid,
                executable=raw.executable,
                command_line=raw.command_line,
                script_path=str(entry.get("script_path") or ""),
                cwd=raw.cwd,
                started_at=raw.started_at or entry.get("started_at"),
            )
            # Prefer live start time; if registry start matches either live or stored, accept.
            start_ok = (
                not entry.get("started_at")
                or not raw.started_at
                or str(entry.get("started_at")) == str(raw.started_at)
            )
            cwd_ok = (
                not entry.get("cwd")
                or not raw.cwd
                or _normalize_path(entry.get("cwd")) == _normalize_path(raw.cwd)
            )
            script = str(entry.get("script_path") or "")
            script_ok = (not script) or (
                _normalize_path(script) in _normalize_path(raw.command_line)
                or Path(script).name.lower() in (raw.command_line or "").replace("\\", "/").lower()
            )
            if expected_identity != actual_identity or not start_ok or not cwd_ok or not script_ok:
                removed.append(entry)
                continue
            # Refresh mutable fields while preserving ownership when start drifted format-only.
            refreshed = dict(entry)
            refreshed.update(
                {
                    "ppid": raw.ppid,
                    "executable": raw.executable,
                    "command_line": raw.command_line,
                    "cwd": raw.cwd,
                    "started_at": raw.started_at or entry.get("started_at"),
                    "identity_token": actual_identity,
                    "ownership_token": actual_owned if actual_owned else expected_owned,
                    "listening_ports": list(raw.listening_ports or ()),
                    "status": raw.status,
                }
            )
            parent = by_pid.get(int(raw.ppid or 0)) if raw.ppid else None
            launcher_pid = entry.get("launcher_pid")
            if launcher_pid and int(launcher_pid) not in by_pid:
                refreshed["orphan"] = True
                orphans.append(refreshed)
            elif parent is None and refreshed.get("role") not in {"server", "launcher"}:
                # Parent gone but process still matches ownership — orphan recovery candidate.
                if refreshed.get("role") in {"worker", "helper"}:
                    refreshed["orphan"] = True
                    orphans.append(refreshed)
            if refreshed.get("role") in {"server", "launcher"}:
                refreshed["orphan"] = False
            kept.append(refreshed)

        # Auto-register verified app.py processes under this hub root that were missing.
        root_n = _normalize_path(root)
        app_n = _normalize_path(app_path)
        known = {int(item.get("pid") or 0) for item in kept}
        for raw in processes:
            if raw.pid in known:
                continue
            command = (raw.command_line or "").replace("\\", "/").lower()
            is_app = app_n and app_n in command
            is_hub_cwd = raw.cwd and _normalize_path(raw.cwd) == root_n and "app.py" in command
            is_hub_module = root_n and root_n in command and (
                "hub.repository_workspace.hub_process_manager" in command
                or "scripts/run_central_hub" in command
                or "run_central_hub.py" in command
            )
            if not (is_app or is_hub_cwd or is_hub_module):
                continue
            role = "server" if (is_app or is_hub_cwd) else "helper"
            label = "Central Hub Server" if role == "server" else "Central Hub Helper"
            script = str(app_path) if role == "server" else extract_script_module(raw.command_line)
            entry = {
                "pid": raw.pid,
                "ppid": raw.ppid,
                "role": role,
                "label": label,
                "identity_token": _identity_token(
                    pid=raw.pid, executable=raw.executable, command_line=raw.command_line
                ),
                "ownership_token": ownership_token(
                    pid=raw.pid,
                    executable=raw.executable,
                    command_line=raw.command_line,
                    script_path=script,
                    cwd=raw.cwd,
                    started_at=raw.started_at,
                ),
                "executable": raw.executable,
                "command_line": raw.command_line,
                "script_path": script,
                "cwd": raw.cwd,
                "started_at": raw.started_at,
                "registered_at": _utcnow(),
                "port": None,
                "orphan": role != "server",
                "recovered": True,
            }
            kept.append(entry)
            orphans.append(entry)

        self._write({"entries": kept})
        recovered_now = [item for item in orphans if item.get("recovered")]
        # Clear one-shot recovered flags so audits are not repeated every scan.
        if recovered_now:
            for item in kept:
                item.pop("recovered", None)
            self._write({"entries": kept})
        return {
            "entries": kept,
            "removed": removed,
            "orphans": orphans,
            "removed_count": len(removed),
            "orphan_count": len(orphans),
            "recovered_count": len(recovered_now),
        }
