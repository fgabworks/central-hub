"""Port availability helpers for Repository Workspace runs."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from typing import Iterable


def port_available(port: int, *, host: str = "127.0.0.1") -> bool:
    """Return True when nothing is accepting and bind succeeds.

    On Windows, ``SO_REUSEADDR`` must not be used for this check — it can make
    bind succeed even while another process is listening.
    """
    if not (1 <= int(port) <= 65535):
        return False
    # If something accepts a connection, the port is occupied.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            probe.connect((host, int(port)))
            return False
        except OSError:
            pass
    # Bind without SO_REUSEADDR to catch ports reserved but not accepting yet.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def find_available_port(
    preferred: int,
    *,
    host: str = "127.0.0.1",
    search_from: int | None = None,
    search_to: int = 65535,
    exclude: Iterable[int] | None = None,
) -> int | None:
    blocked = {int(p) for p in (exclude or [])}
    preferred = int(preferred)
    if preferred not in blocked and port_available(preferred, host=host):
        return preferred
    start = int(search_from or max(1024, preferred))
    for port in range(start, min(search_to, 65535) + 1):
        if port in blocked:
            continue
        if port_available(port, host=host):
            return port
    # wrap lower range
    for port in range(1024, start):
        if port in blocked:
            continue
        if port_available(port, host=host):
            return port
    return None


_SS_PID = re.compile(r"pid=(\d+)", re.IGNORECASE)


def port_listeners(
    ports: Iterable[int] | None = None,
    *,
    timeout_seconds: float = 8.0,
) -> dict[int, list[int]]:
    """Map listening TCP ports → owning PIDs (best effort, shell=False)."""
    wanted = {int(p) for p in (ports or []) if 1 <= int(p) <= 65535}
    if os.name == "nt":
        mapping = _windows_netstat_listeners(timeout_seconds=timeout_seconds)
    else:
        mapping = _unix_ss_listeners(timeout_seconds=timeout_seconds)
    if wanted:
        return {port: pids for port, pids in mapping.items() if port in wanted}
    return mapping


def _windows_netstat_listeners(*, timeout_seconds: float) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    # Prefer Get-NetTCPConnection (more reliable than parsing netstat locales).
    script = (
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object LocalPort,OwningProcess | ConvertTo-Json -Compress"
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
        raw = (completed.stdout or "").strip()
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload = [payload]
            for item in payload or []:
                if not isinstance(item, dict):
                    continue
                try:
                    port = int(item.get("LocalPort") or 0)
                    pid = int(item.get("OwningProcess") or 0)
                except (TypeError, ValueError):
                    continue
                if port <= 0 or pid <= 0:
                    continue
                out.setdefault(port, [])
                if pid not in out[port]:
                    out[port].append(pid)
            if out:
                return out
    except Exception:  # noqa: BLE001
        pass

    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return out
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0].upper() != "TCP":
            continue
        if "LISTENING" not in line.upper() and "LISTEN" not in line.upper():
            continue
        try:
            local = parts[1]
            pid = int(parts[-1])
            port = int(local.rsplit(":", 1)[-1].rstrip("]"))
        except (TypeError, ValueError, IndexError):
            continue
        if pid <= 0:
            continue
        out.setdefault(port, [])
        if pid not in out[port]:
            out[port].append(pid)
    return out



def _unix_ss_listeners(*, timeout_seconds: float) -> dict[int, list[int]]:
    for args in (
        ["ss", "-lptn"],
        ["ss", "-ltnp"],
    ):
        try:
            completed = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        out: dict[int, list[int]] = {}
        for line in (completed.stdout or "").splitlines():
            if "LISTEN" not in line.upper():
                continue
            parts = line.split()
            local = None
            for part in parts:
                if ":" in part and not part.startswith("users:"):
                    maybe = part.rsplit(":", 1)
                    if len(maybe) == 2 and maybe[1].isdigit():
                        local = part
            if not local:
                continue
            try:
                port = int(local.rsplit(":", 1)[-1])
            except ValueError:
                continue
            pids = [int(m) for m in _SS_PID.findall(line)]
            if not pids:
                continue
            bucket = out.setdefault(port, [])
            for pid in pids:
                if pid > 0 and pid not in bucket:
                    bucket.append(pid)
        if out:
            return out
    return {}
