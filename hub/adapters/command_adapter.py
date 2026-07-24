"""Command / local-path adapter — Phase 1 health probes only.

Safety rules for Phase 1:
- Prefer path and executable existence checks.
- Optional command probes must be allowlisted (no shell, fixed argv only).
- Never run free-form shell from the UI.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.registry.models import HealthCheckConfig, Repository
from hub.settings import ROOT_DIR

# Only these exact argv patterns may run as health probes in Phase 1.
_ALLOWED_HEALTH_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("python", "-c", "print('ok')"),
        ("python", "-c", 'print("ok")'),
        ("py", "-c", "print('ok')"),
        ("py", "-c", 'print("ok")'),
    }
)


class CommandAdapter:
    """Inspects a local command-style repository."""

    def __init__(self, repository: Repository, default_timeout: float = 5.0) -> None:
        self.repository = repository
        self.default_timeout = default_timeout

    def health_check(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        repo = self.repository
        config = repo.health_check or HealthCheckConfig(
            type="path",
            local_path=repo.local_path,
            timeout_seconds=self.default_timeout,
        )

        if config.type == "path":
            return self._check_path(config, checked_at)
        if config.type == "command":
            return self._check_command(config, checked_at)
        return _result(
            ok=False,
            status="misconfigured",
            detail=f"Command repository health_check.type must be 'path' or 'command', got {config.type!r}",
            latency_ms=0,
            checked_at=checked_at,
        )

    def _resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        return path

    def _check_path(self, config: HealthCheckConfig, checked_at: str) -> dict[str, Any]:
        started = time.perf_counter()
        target = config.local_path or self.repository.local_path
        if not target:
            return _result(
                ok=False,
                status="misconfigured",
                detail="Command repository is missing local_path",
                latency_ms=0,
                checked_at=checked_at,
            )

        path = self._resolve_path(target)
        details: list[str] = []
        ok = True

        if path.exists():
            details.append(f"path exists: {path}")
        else:
            ok = False
            details.append(f"path missing: {path}")

        executable = config.executable
        if executable:
            resolved = shutil.which(executable)
            if resolved:
                details.append(f"executable found: {resolved}")
            else:
                ok = False
                details.append(f"executable not found on PATH: {executable}")

        latency_ms = int((time.perf_counter() - started) * 1000)
        return _result(
            ok=ok,
            status="healthy" if ok else "unhealthy",
            detail="; ".join(details),
            latency_ms=latency_ms,
            checked_at=checked_at,
        )

    def _check_command(self, config: HealthCheckConfig, checked_at: str) -> dict[str, Any]:
        # First confirm local path when configured.
        path_result = self._check_path(config, checked_at)
        if not path_result["ok"] and (config.local_path or self.repository.local_path):
            return path_result

        command = list(config.command)
        if not command:
            # Fall back to path/executable-only check.
            return path_result

        normalized = tuple(command)
        if normalized not in _ALLOWED_HEALTH_COMMANDS:
            return _result(
                ok=False,
                status="blocked",
                detail=(
                    "Health command is not in the Phase 1 allowlist. "
                    "Use a path/executable check or the sample python -c print('ok') probe."
                ),
                latency_ms=0,
                checked_at=checked_at,
            )

        # Resolve the interpreter via PATH for portability (python/py fallback).
        interpreter = shutil.which(command[0])
        if not interpreter and command[0] == "python":
            interpreter = shutil.which("py")
        if not interpreter and command[0] == "py":
            interpreter = shutil.which("python")
        if not interpreter:
            return _result(
                ok=False,
                status="unhealthy",
                detail=f"interpreter not found on PATH: {command[0]}",
                latency_ms=0,
                checked_at=checked_at,
            )

        argv = [interpreter, *command[1:]]
        cwd = None
        local_path = config.local_path or self.repository.working_directory or self.repository.local_path
        if local_path:
            path = self._resolve_path(local_path)
            if path.is_dir():
                cwd = str(path)

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds or self.default_timeout,
                shell=False,
                check=False,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            ok = completed.returncode == 0 and "ok" in (completed.stdout or "").lower()
            detail = (
                f"command probe exit={completed.returncode}; "
                f"stdout={ (completed.stdout or '').strip()!r}"
            )
            return _result(
                ok=ok,
                status="healthy" if ok else "unhealthy",
                detail=detail,
                latency_ms=latency_ms,
                checked_at=checked_at,
            )
        except subprocess.TimeoutExpired:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return _result(
                ok=False,
                status="timeout",
                detail="Health command timed out",
                latency_ms=latency_ms,
                checked_at=checked_at,
            )
        except OSError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return _result(
                ok=False,
                status="unhealthy",
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
                checked_at=checked_at,
            )


def _result(
    *,
    ok: bool,
    status: str,
    detail: str,
    latency_ms: int,
    checked_at: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "detail": detail,
        "latency_ms": latency_ms,
        "checked_at": checked_at,
    }
