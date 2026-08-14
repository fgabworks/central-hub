"""Read-only safety helpers for Codex CLI runs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from hub.agent_center.redact import redact_text

_FORBIDDEN_ARGV = {
    "--yolo",
    "yolo",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "danger-full-access",
    "workspace-write",
    "--full-auto",
    "full-auto",
}

_FORBIDDEN_APPROVALS = {"never"}  # automatic approvals are not allowed in hub MVP

CODEX_CODE_MODE_HOST_WIN = "codex-code-mode-host.exe"
INCOMPLETE_CODEX_HOST_DETAIL = (
    "Codex installation incomplete: codex-code-mode-host.exe is missing"
)


def codex_home() -> Path:
    raw = (os.environ.get("CODEX_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def windows_codex_host_path(executable: str | Path) -> Path:
    return Path(executable).expanduser().parent / CODEX_CODE_MODE_HOST_WIN


def is_complete_codex_runtime(executable: str | Path, *, windows: bool | None = None) -> bool:
    """Windows native Codex needs code-mode-host beside the CLI; Unix is unchanged."""
    path = Path(executable)
    if not path.is_file():
        return False
    if (os.name == "nt") if windows is None else windows:
        return windows_codex_host_path(path).is_file()
    return True


def windows_official_codex_executable() -> Path | None:
    """Official Windows standalone installer — no username hardcoded."""
    if os.name != "nt":
        return None
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        return None
    return Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"


def _codex_search_order(configured: str) -> list[tuple[str, Path]]:
    """PATH, then official standalone, then portable home installs. sandbox-bin last."""
    from hub.agent_center.adapters.base import which_executable

    name = (configured or "codex").strip() or "codex"
    ordered: list[tuple[str, Path]] = []
    found = which_executable(name)
    if found:
        ordered.append(("path", Path(found)))
    official = windows_official_codex_executable()
    if official is not None:
        ordered.append(("official_standalone", official))
    home = codex_home()
    exe_name = "codex.exe" if os.name == "nt" else "codex"
    ordered.append(("codex_home_bin", home / "bin" / exe_name))
    ordered.append(("sandbox_bin", home / ".sandbox-bin" / exe_name))
    return ordered


def inspect_codex_installation(configured: str = "codex") -> dict[str, Any]:
    """Resolve a usable Codex CLI. PATH wins; incomplete Windows installs are skipped."""
    name = (configured or "codex").strip() or "codex"
    missing = {
        "executable": None,
        "installed": False,
        "complete": False,
        "error_code": "missing_cli",
        "detail": "Codex CLI is not installed or not discoverable",
        "incomplete_path": "",
        "source": "",
        "host_path": "",
        "runtime_health": "missing",
    }
    if Path(name).name != name:
        # Config must be a bare executable name, not a user-supplied absolute path.
        return missing

    windows = os.name == "nt"
    seen: set[str] = set()
    incomplete_path = ""
    incomplete_source = ""
    for source, candidate in _codex_search_order(name):
        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except OSError:
            continue
        key = str(resolved).replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        if not resolved.is_file():
            continue
        if is_complete_codex_runtime(resolved, windows=windows):
            display = str(candidate.expanduser())
            host_display = str(Path(display).parent / CODEX_CODE_MODE_HOST_WIN) if windows else ""
            return {
                "executable": display,
                "installed": True,
                "complete": True,
                "error_code": "",
                "detail": "",
                "incomplete_path": "",
                "source": source,
                "host_path": host_display,
                "runtime_health": "ok",
            }
        if not incomplete_path:
            incomplete_path = str(candidate.expanduser())
            incomplete_source = source
    if incomplete_path and windows:
        return {
            "executable": None,
            "installed": True,
            "complete": False,
            "error_code": "incomplete_cli",
            "detail": INCOMPLETE_CODEX_HOST_DETAIL,
            "incomplete_path": incomplete_path,
            "source": incomplete_source,
            "host_path": str(Path(incomplete_path).parent / CODEX_CODE_MODE_HOST_WIN),
            "runtime_health": "incomplete_host",
        }
    return {**missing, "incomplete_path": incomplete_path, "source": incomplete_source}


def discover_codex_executable(configured: str = "codex") -> str | None:
    """Resolve Codex from PATH or the known local install dir. Never accept arbitrary paths."""
    return inspect_codex_installation(configured).get("executable") or None


def assert_safe_codex_argv(argv: list[str], *, require_ephemeral: bool = True) -> None:
    lowered = [part.lower() for part in argv]
    joined = " ".join(lowered)
    for bad in _FORBIDDEN_ARGV:
        if bad.lower() in lowered or bad.lower() in joined:
            raise ValueError(f"Rejected unsafe Codex argv token: {bad}")
    if "--ask-for-approval" in lowered:
        idx = lowered.index("--ask-for-approval")
        if idx + 1 < len(lowered) and lowered[idx + 1] in _FORBIDDEN_APPROVALS:
            raise ValueError("Rejected automatic Codex approvals")
    if "-a" in argv:
        idx = argv.index("-a")
        if idx + 1 < len(argv) and argv[idx + 1].lower() in _FORBIDDEN_APPROVALS:
            raise ValueError("Rejected automatic Codex approvals")
    if "--sandbox" in lowered:
        idx = lowered.index("--sandbox")
        if idx + 1 >= len(lowered) or lowered[idx + 1] != "read-only":
            raise ValueError("Codex MVP requires --sandbox read-only")
    else:
        raise ValueError("Codex MVP requires --sandbox read-only")
    if require_ephemeral and "--ephemeral" not in lowered:
        raise ValueError("Codex MVP requires --ephemeral")
    if "--json" not in lowered:
        raise ValueError("Codex MVP requires --json")
    if "-c" in argv:
        # -C is cd; -c is config override — reject config overrides that widen sandbox.
        for i, part in enumerate(argv):
            if part == "-c" and i + 1 < len(argv):
                value = argv[i + 1].lower()
                if "danger" in value or "workspace-write" in value or "yolo" in value:
                    raise ValueError("Rejected Codex config override that weakens sandbox")


def resolve_approved_repo_cwd(path: str | Path, approved_roots: list[str | Path]) -> Path:
    """Jail cwd to an approved connected repository root (no traversal)."""
    target = Path(path).expanduser().resolve(strict=False)
    if ".." in Path(path).parts:
        raise ValueError("Path traversal is not allowed")
    for root in approved_roots:
        root_path = Path(root).expanduser().resolve(strict=False)
        try:
            target.relative_to(root_path)
            return root_path
        except ValueError:
            continue
    raise ValueError("Working directory is outside approved connected repositories")


def git_status_snapshot(repo: Path) -> dict[str, Any]:
    """Record porcelain git status for before/after safety checks."""
    repo = Path(repo)
    if not (repo / ".git").exists() and not _is_git_worktree(repo):
        return {"ok": False, "error": "Not a git repository", "porcelain": "", "files": []}
    try:
        from hub.agent_center.adapters.cli_common import run_cli_capture

        result = run_cli_capture(
            ["git", "status", "--porcelain", "-uall"],
            timeout=20.0,
            cwd=str(repo),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": redact_text(str(exc), limit=240), "porcelain": "", "files": []}
    porcelain = result.stdout or ""
    files = [line[3:] for line in porcelain.splitlines() if len(line) >= 4]
    return {
        "ok": result.returncode == 0,
        "error": "" if result.returncode == 0 else redact_text((result.stderr or "").strip(), limit=240),
        "porcelain": porcelain,
        "files": files,
    }


def assert_git_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    if before.get("porcelain") != after.get("porcelain"):
        changed = sorted(set(after.get("files") or []) | set(before.get("files") or []))
        raise RuntimeError(
            "Read-only safety check failed: git status changed"
            + (f" ({', '.join(changed[:8])})" if changed else "")
        )


def _is_git_worktree(repo: Path) -> bool:
    try:
        from hub.agent_center.adapters.cli_common import run_cli_capture

        result = run_cli_capture(
            ["git", "rev-parse", "--is-inside-work-tree"],
            timeout=10.0,
            cwd=str(repo),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "true" in (result.stdout or "").lower()
