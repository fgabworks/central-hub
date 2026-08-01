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


def codex_home() -> Path:
    raw = (os.environ.get("CODEX_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def discover_codex_executable(configured: str = "codex") -> str | None:
    """Resolve Codex from PATH or the known local install dir. Never accept arbitrary paths."""
    name = (configured or "codex").strip() or "codex"
    if Path(name).name != name:
        # Config must be a bare executable name, not a user-supplied absolute path.
        return None
    from hub.agent_center.adapters.base import which_executable

    found = which_executable(name)
    if found:
        return found
    home = codex_home()
    for candidate in (
        home / ".sandbox-bin" / ("codex.exe" if os.name == "nt" else "codex"),
        home / "bin" / ("codex.exe" if os.name == "nt" else "codex"),
    ):
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def assert_safe_codex_argv(argv: list[str]) -> None:
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
    if "--ephemeral" not in lowered:
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
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(repo),
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
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
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo),
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "true" in (result.stdout or "").lower()
