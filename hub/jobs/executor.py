"""Safe capability executors for command and API adapters (Phases 3–4)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from hub.registry.models import Capability, Repository
from hub.settings import ROOT_DIR

CancelCheck = Callable[[], None]

# Interpreters allowed as the first argv token for YAML command templates.
_ALLOWED_INTERPRETERS = frozenset({"python", "py"})
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./\\:+=,@% -]+$")


class CapabilityExecutionError(Exception):
    def __init__(self, message: str, *, status: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class JobCancelled(CapabilityExecutionError):
    def __init__(self, message: str = "Job cancelled") -> None:
        super().__init__(message, status="cancelled")


class JobPaused(CapabilityExecutionError):
    def __init__(self, message: str = "Job paused") -> None:
        super().__init__(message, status="paused")


def resolve_repo_root(repository: Repository) -> Path:
    raw = repository.working_directory or repository.local_path
    if not raw:
        raise CapabilityExecutionError("Repository has no local_path / working_directory")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    else:
        path = path.resolve()
    hub = ROOT_DIR.resolve()
    # Prefer jail under hub root for sample repos; allow absolute paths that exist.
    try:
        path.relative_to(hub)
    except ValueError:
        if not path.exists():
            raise CapabilityExecutionError(f"Working directory outside hub and missing: {path}")
    if not path.is_dir():
        raise CapabilityExecutionError(f"Working directory is not a directory: {path}")
    return path


def _validate_command_template(argv: list[str], repo_root: Path) -> list[str]:
    if not argv:
        raise CapabilityExecutionError("Capability command_template is empty")
    if any(not isinstance(part, str) or not part for part in argv):
        raise CapabilityExecutionError("command_template entries must be non-empty strings")
    for part in argv:
        if any(ch in part for ch in [";", "|", "&", "`", "$", "\n", "\r"]):
            raise CapabilityExecutionError("command_template contains forbidden shell metacharacters")
        if ".." in Path(part).parts:
            raise CapabilityExecutionError("command_template must not contain '..'")
    head = argv[0]
    if head in _ALLOWED_INTERPRETERS:
        interpreter = shutil.which(head)
        if not interpreter and head == "python":
            interpreter = shutil.which("py")
        if not interpreter and head == "py":
            interpreter = shutil.which("python")
        if not interpreter:
            raise CapabilityExecutionError(f"Interpreter not found on PATH: {head}")
        return [interpreter, *argv[1:]]

    # Relative script/executable under repo root only.
    candidate = (repo_root / head).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise CapabilityExecutionError("Executable must stay under the repository path") from exc
    if not candidate.exists():
        raise CapabilityExecutionError(f"Executable not found under repo: {head}")
    return [str(candidate), *argv[1:]]


def run_command_capability(
    repository: Repository,
    capability: Capability,
    *,
    dry_run: bool,
    job_id: str,
    input_dir: Path,
    result_dir: Path,
    log_append: Callable[[str], None],
    cancel_check: CancelCheck | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Execute an allowlisted YAML command_template (shell=False, cwd jailed)."""
    raw = capability.raw or {}
    template = raw.get("command_template") or raw.get("command") or []
    if not isinstance(template, list):
        raise CapabilityExecutionError("command_template must be a list")
    argv_template = [str(part) for part in template]
    if dry_run and raw.get("dry_run_command_template"):
        argv_template = [str(part) for part in raw["dry_run_command_template"]]

    repo_root = resolve_repo_root(repository)
    argv = _validate_command_template(argv_template, repo_root)
    if cancel_check:
        cancel_check()

    env = os.environ.copy()
    env["CENTRAL_HUB_JOB_ID"] = job_id
    env["CENTRAL_HUB_DRY_RUN"] = "1" if dry_run else "0"
    env["CENTRAL_HUB_INPUT_DIR"] = str(input_dir)
    env["CENTRAL_HUB_RESULT_DIR"] = str(result_dir)
    env["CENTRAL_HUB_REPO_ID"] = repository.id
    env["CENTRAL_HUB_CAPABILITY_ID"] = capability.id

    log_append(f"exec argv={argv!r} cwd={repo_root} dry_run={dry_run}")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CapabilityExecutionError(
            f"Command timed out after {timeout_seconds:g}s",
            status="failed",
        ) from exc

    if cancel_check:
        cancel_check()

    latency_ms = int((time.perf_counter() - started) * 1000)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        log_append(f"stdout: {stdout[:2000]}")
    if stderr:
        log_append(f"stderr: {stderr[:2000]}")

    # Collect any files the command wrote into result_dir.
    artifacts = sorted(
        str(path.relative_to(result_dir))
        for path in result_dir.rglob("*")
        if path.is_file()
    )
    ok = completed.returncode == 0
    if not ok:
        raise CapabilityExecutionError(
            f"Command exited {completed.returncode}: {stderr or stdout or 'no output'}"
        )
    return {
        "ok": True,
        "adapter": "command",
        "dry_run": dry_run,
        "returncode": completed.returncode,
        "stdout": stdout[:4000],
        "stderr": stderr[:4000],
        "latency_ms": latency_ms,
        "artifacts": artifacts,
    }


def run_api_capability(
    repository: Repository,
    capability: Capability,
    *,
    dry_run: bool,
    result_dir: Path,
    log_append: Callable[[str], None],
    cancel_check: CancelCheck | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute an HTTP capability from YAML. GET-only unless allow_write is true (blocked by default)."""
    if not repository.base_url:
        raise CapabilityExecutionError("API repository is missing base_url")
    raw = capability.raw or {}
    method = str(raw.get("http_method") or "GET").upper()
    path = str(raw.get("http_path") or "/health")
    allow_write = bool(raw.get("allow_write", False))

    if method not in {"GET", "HEAD"} and not allow_write:
        raise CapabilityExecutionError(
            f"HTTP {method} blocked — set allow_write: true in YAML only after confirm gates "
            "(Central Hub keeps write capabilities disabled by default)."
        )
    if method not in {"GET", "HEAD"}:
        # Even with allow_write, Phase 4 MVP stays GET-only for safety.
        raise CapabilityExecutionError(
            "Phase 4 API adapter only permits GET/HEAD. Domain write endpoints stay in connected repos."
        )

    url = urljoin(repository.base_url.rstrip("/") + "/", path.lstrip("/"))
    if cancel_check:
        cancel_check()
    mode = "dry-run probe" if dry_run else "execute"
    log_append(f"api {mode} {method} {url}")
    started = time.perf_counter()
    try:
        response = requests.request(method, url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise CapabilityExecutionError(f"API request failed: {exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    body_text = response.text[:4000]
    log_append(f"api status={response.status_code} latency_ms={latency_ms}")

    result_dir.mkdir(parents=True, exist_ok=True)
    out = result_dir / "api_response.json"
    out.write_text(
        json.dumps(
            {
                "ok": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "url": url,
                "method": method,
                "dry_run": dry_run,
                "body_preview": body_text[:2000],
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    if not (200 <= response.status_code < 300):
        raise CapabilityExecutionError(f"API returned HTTP {response.status_code}")
    return {
        "ok": True,
        "adapter": "api",
        "dry_run": dry_run,
        "status_code": response.status_code,
        "url": url,
        "latency_ms": latency_ms,
        "artifacts": ["api_response.json"],
    }


def run_capability(
    repository: Repository,
    capability: Capability,
    *,
    dry_run: bool,
    job_id: str,
    input_dir: Path,
    result_dir: Path,
    log_append: Callable[[str], None],
    cancel_check: CancelCheck | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    if capability.adapter_type == "command":
        return run_command_capability(
            repository,
            capability,
            dry_run=dry_run,
            job_id=job_id,
            input_dir=input_dir,
            result_dir=result_dir,
            log_append=log_append,
            cancel_check=cancel_check,
            timeout_seconds=timeout_seconds,
        )
    if capability.adapter_type == "api":
        return run_api_capability(
            repository,
            capability,
            dry_run=dry_run,
            result_dir=result_dir,
            log_append=log_append,
            cancel_check=cancel_check,
            timeout_seconds=min(timeout_seconds, 60.0),
        )
    raise CapabilityExecutionError(f"Unsupported adapter_type: {capability.adapter_type}")
