"""Allowlisted external open actions (VS Code / Cursor / File Explorer)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hub.repository_workspace.security import WorkspaceSecurityError, safe_join
from hub.repository_workspace.settings import WorkspaceSettings

ALLOWED_TARGETS = ("vscode", "cursor", "explorer")


class ExternalOpener:
    def __init__(self, repo_root: Path, settings: WorkspaceSettings) -> None:
        self.root = repo_root.resolve()
        self.settings = settings

    def open(self, target: str, rel_path: str | None = None) -> dict[str, Any]:
        kind = (target or "").strip().lower()
        if kind not in ALLOWED_TARGETS:
            raise WorkspaceSecurityError("Unsupported open target.", code="bad_target")
        if rel_path:
            path = safe_join(self.root, rel_path)
            if not path.exists():
                raise WorkspaceSecurityError("Path not found.", code="not_found")
        else:
            path = self.root
            if not path.exists():
                raise WorkspaceSecurityError("Path not found.", code="not_found")

        # Never spawn File Explorer / editors during automated tests — leftover
        # Explorer windows on TemporaryDirectory paths spam "Location is not available".
        if os.environ.get("CENTRAL_HUB_TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
            return {"ok": True, "target": kind, "path": str(path), "dry_run": True}

        if kind == "explorer":
            return self._open_explorer(path)
        if kind == "vscode":
            return self._open_editor(["code", "code.cmd"], path)
        return self._open_editor(["cursor", "cursor.cmd"], path)

    def _run(self, argv: list[str]) -> None:
        subprocess.run(
            argv,
            shell=False,
            cwd=str(self.root),
            timeout=self.settings.open_timeout_seconds,
            check=False,
            capture_output=True,
        )

    def _open_editor(self, candidates: list[str], path: Path) -> dict[str, Any]:
        exe = None
        for name in candidates:
            exe = shutil.which(name)
            if exe:
                break
        if not exe:
            raise WorkspaceSecurityError(
                f"Editor command not found ({candidates[0]}).", code="editor_missing"
            )
        self._run([exe, str(path)])
        return {"ok": True, "target": candidates[0], "path": str(path)}

    def _open_explorer(self, path: Path) -> dict[str, Any]:
        if os.name == "nt":
            # explorer.exe /select,file highlights a file; folders open directly.
            exe = shutil.which("explorer") or "explorer.exe"
            if path.is_file():
                self._run([exe, "/select,", str(path)])
            else:
                self._run([exe, str(path)])
            return {"ok": True, "target": "explorer", "path": str(path)}
        if sys_platform_open := shutil.which("open"):
            self._run([sys_platform_open, str(path if path.is_dir() else path.parent)])
            return {"ok": True, "target": "explorer", "path": str(path)}
        xdg = shutil.which("xdg-open")
        if xdg:
            self._run([xdg, str(path if path.is_dir() else path.parent)])
            return {"ok": True, "target": "explorer", "path": str(path)}
        raise WorkspaceSecurityError("File manager command not found.", code="explorer_missing")
