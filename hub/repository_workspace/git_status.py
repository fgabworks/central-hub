"""Read-only Git status and diffs for a local repository checkout."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    redact_audit_detail,
    safe_join,
)
from hub.repository_workspace.settings import WorkspaceSettings

# Status letter → category
_STATUS_MAP = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
    "?": "untracked",
    "!": "ignored",
}


class RepositoryGitStatus:
    def __init__(self, repo_root: Path, settings: WorkspaceSettings) -> None:
        self.root = repo_root.resolve()
        self.settings = settings

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                cwd=str(self.root),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorkspaceSecurityError("Git is not available on PATH.", code="git_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceSecurityError("Git command timed out.", code="git_timeout") from exc

    def is_git_repo(self) -> bool:
        probe = self._run(["git", "rev-parse", "--is-inside-work-tree"])
        return probe.returncode == 0 and probe.stdout.strip() == "true"

    def summary(self) -> dict[str, Any]:
        if not self.is_git_repo():
            return {
                "is_git": False,
                "branch": None,
                "clean": True,
                "files": [],
                "counts": {
                    "modified": 0,
                    "added": 0,
                    "deleted": 0,
                    "untracked": 0,
                    "other": 0,
                },
                "detail": "Not a Git working tree.",
            }
        branch_proc = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch_proc.stdout.strip() or "HEAD"
        porcelain = self._run(["git", "status", "--porcelain=v1", "-uall"])
        files: list[dict[str, Any]] = []
        counts = {"modified": 0, "added": 0, "deleted": 0, "untracked": 0, "other": 0}
        for line in porcelain.stdout.splitlines():
            if not line:
                continue
            raw = line[0:2]
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1].strip()
            path = path.strip('"')
            if is_blocked_secret(path):
                continue
            # Prefer worktree status char, then index
            letter = (raw[1] if raw[1] not in {" ", "?"} else raw[0]).strip() or raw[0]
            if raw == "??":
                category = "untracked"
            elif "D" in raw:
                category = "deleted"
            elif "A" in raw:
                category = "added"
            elif "M" in raw:
                category = "modified"
            else:
                category = _STATUS_MAP.get(letter, "other")
            if category not in counts:
                category = "other"
            counts[category] = counts.get(category, 0) + 1
            files.append(
                {
                    "path": path,
                    "xy": raw,
                    "category": category,
                }
            )
        clean = not files
        return {
            "is_git": True,
            "branch": branch,
            "clean": clean,
            "files": files,
            "counts": counts,
            "detail": "Clean working tree." if clean else f"{len(files)} changed path(s).",
        }

    def file_status(self, rel_path: str) -> str | None:
        """Return a short git status label for one relative path, if any."""
        try:
            safe_join(self.root, rel_path)
        except WorkspaceSecurityError:
            return None
        if not self.is_git_repo():
            return None
        proc = self._run(["git", "status", "--porcelain=v1", "-uall", "--", rel_path])
        line = (proc.stdout or "").splitlines()[:1]
        if not line:
            return "clean"
        raw = line[0][0:2]
        if raw == "??":
            return "untracked"
        if "D" in raw:
            return "deleted"
        if "A" in raw:
            return "added"
        if "M" in raw:
            return "modified"
        return raw.strip() or "changed"

    def diff(
        self,
        rel_path: str | None = None,
        *,
        side_by_side: bool = False,
    ) -> dict[str, Any]:
        if not self.is_git_repo():
            raise WorkspaceSecurityError("Not a Git working tree.", code="not_git")
        args = ["git", "diff", "--no-color"]
        if side_by_side:
            # Git has no true side-by-side in plain diff; use word-diff as lighter aid
            # and let UI render split panes from unified when requested.
            args.append("--word-diff=plain")
        if rel_path:
            if is_blocked_secret(rel_path):
                raise WorkspaceSecurityError(
                    "This path is blocked because it may contain secrets.",
                    code="secret_blocked",
                )
            safe_join(self.root, rel_path)
            # Untracked: show as /dev/null → file via diff --no-index
            status = self.file_status(rel_path)
            if status == "untracked":
                path = safe_join(self.root, rel_path)
                proc = self._run(
                    ["git", "diff", "--no-color", "--no-index", "--", "/dev/null", str(path)]
                )
                # git --no-index returns 1 when differences exist
                text = proc.stdout or proc.stderr or ""
                return {
                    "path": rel_path,
                    "diff": _redact_diff(text),
                    "status": status,
                    "side_by_side": side_by_side,
                }
            args.extend(["--", rel_path])
        proc = self._run(args)
        text = proc.stdout or ""
        # Also include staged
        staged = self._run(["git", "diff", "--no-color", "--cached"] + (["--", rel_path] if rel_path else []))
        if staged.stdout:
            text = (text + "\n" + staged.stdout).strip()
        return {
            "path": rel_path,
            "diff": _redact_diff(text),
            "status": self.file_status(rel_path) if rel_path else None,
            "side_by_side": side_by_side,
        }


def _redact_diff(text: str) -> str:
    """Redact secret-looking assignment lines inside diffs."""
    from hub.repository_workspace.security import _SECRET_CONTENT_RE

    out_lines = []
    for line in (text or "").splitlines():
        if line.startswith(("+", "-", " ")) and _SECRET_CONTENT_RE.search(line):
            prefix = line[:1]
            out_lines.append(prefix + " [REDACTED SECRET LINE]")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
