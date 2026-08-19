"""Safe text-file create / edit / rename / delete with diff preview."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    is_supported_text_path,
    language_for,
    looks_binary,
    redact_audit_detail,
    relative_posix,
    safe_join,
)
from hub.repository_workspace.settings import WorkspaceSettings


def unified_diff(path: str, before: str, after: str, *, max_lines: int = 2000) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    lines = list(diff)
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["\n… diff truncated …\n"]
    return "\n".join(lines)


class RepositoryEditor:
    def __init__(self, repo_root: Path, settings: WorkspaceSettings) -> None:
        self.root = repo_root.resolve()
        self.settings = settings

    def _require_text_file(self, path: Path, *, must_exist: bool = True) -> str:
        if must_exist and (not path.exists() or not path.is_file()):
            raise WorkspaceSecurityError("File not found.", code="not_found")
        rel = relative_posix(self.root, path) if path.exists() else ""
        if rel and is_blocked_secret(rel):
            raise WorkspaceSecurityError(
                "This path is blocked because it may contain secrets.", code="secret_blocked"
            )
        if path.exists() and not is_supported_text_path(path):
            raise WorkspaceSecurityError("Only supported text files can be edited.", code="unsupported")
        return rel

    def read_for_edit(self, rel_path: str) -> dict[str, Any]:
        path = safe_join(self.root, rel_path)
        rel = self._require_text_file(path)
        size = path.stat().st_size
        if size > self.settings.max_edit_bytes:
            raise WorkspaceSecurityError(
                f"File exceeds edit limit ({self.settings.max_edit_bytes} bytes).",
                code="too_large",
            )
        data = path.read_bytes()
        if looks_binary(data[:8192]):
            raise WorkspaceSecurityError("File appears to be binary.", code="binary")
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return {
            "path": rel,
            "content": text,
            "size": size,
            "language": language_for(path),
            "modified_at": path.stat().st_mtime,
        }

    def file_state(self, rel_path: str) -> dict[str, Any]:
        """Return a bounded exact disk state for stale checks and rollback capture."""
        data = self.read_for_edit(rel_path)
        path = safe_join(self.root, rel_path)
        raw = path.read_bytes()
        newline = "crlf" if b"\r\n" in raw else "lf"
        return {
            **data,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "newline": newline,
            "encoding": "utf-8",
        }

    def preview_save(self, rel_path: str, new_content: str) -> dict[str, Any]:
        path = safe_join(self.root, rel_path)
        rel = self._require_text_file(path)
        if len(new_content.encode("utf-8")) > self.settings.max_edit_bytes:
            raise WorkspaceSecurityError(
                f"Content exceeds edit limit ({self.settings.max_edit_bytes} bytes).",
                code="too_large",
            )
        before = path.read_text(encoding="utf-8")
        diff = unified_diff(rel, before, new_content)
        return {
            "path": rel,
            "diff": diff,
            "changed": before != new_content,
            "before_bytes": len(before.encode("utf-8")),
            "after_bytes": len(new_content.encode("utf-8")),
        }

    def save(self, rel_path: str, new_content: str, *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise WorkspaceSecurityError(
                "Saving requires explicit confirmation after diff preview.",
                code="confirm_required",
            )
        preview = self.preview_save(rel_path, new_content)
        if not preview["changed"]:
            return {"path": preview["path"], "saved": False, "detail": "No changes."}
        path = safe_join(self.root, rel_path)
        original = path.read_bytes()
        if b"\r\n" in original:
            new_content = new_content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            path.write_bytes(new_content.encode("utf-8"))
        else:
            path.write_text(new_content, encoding="utf-8", newline="\n")
        return {
            "path": preview["path"],
            "saved": True,
            "bytes": preview["after_bytes"],
            "diff_lines": preview["diff"].count("\n") + 1,
        }

    def revert_to_disk(self, rel_path: str) -> dict[str, Any]:
        """Return last-saved disk content (client uses this to discard buffer)."""
        data = self.read_for_edit(rel_path)
        return {"path": data["path"], "content": data["content"]}

    def create_file(self, rel_path: str, content: str = "", *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise WorkspaceSecurityError("Create requires explicit confirmation.", code="confirm_required")
        path = safe_join(self.root, rel_path)
        if path.exists():
            raise WorkspaceSecurityError("Path already exists.", code="exists")
        if not is_supported_text_path(path):
            raise WorkspaceSecurityError("Only supported text files can be created.", code="unsupported")
        if is_blocked_secret(rel_path):
            raise WorkspaceSecurityError(
                "This path is blocked because it may contain secrets.", code="secret_blocked"
            )
        if len(content.encode("utf-8")) > self.settings.max_edit_bytes:
            raise WorkspaceSecurityError("Content exceeds edit limit.", code="too_large")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure parent stays inside root
        safe_join(self.root, relative_posix(self.root, path.parent) if path.parent != self.root else ".")
        path.write_text(content, encoding="utf-8", newline="\n")
        return {"path": relative_posix(self.root, path), "created": True}

    def rename(self, rel_path: str, new_rel_path: str, *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise WorkspaceSecurityError("Rename requires explicit confirmation.", code="confirm_required")
        src = safe_join(self.root, rel_path)
        if not src.exists():
            raise WorkspaceSecurityError("File not found.", code="not_found")
        if is_blocked_secret(rel_path) or is_blocked_secret(new_rel_path):
            raise WorkspaceSecurityError(
                "This path is blocked because it may contain secrets.", code="secret_blocked"
            )
        dst = safe_join(self.root, new_rel_path)
        if dst.exists():
            raise WorkspaceSecurityError("Destination already exists.", code="exists")
        if src.is_file() and not is_supported_text_path(dst):
            raise WorkspaceSecurityError("Destination must be a supported text path.", code="unsupported")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return {
            "from": rel_path.replace("\\", "/"),
            "to": relative_posix(self.root, dst),
            "renamed": True,
        }

    def delete(self, rel_path: str, *, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise WorkspaceSecurityError("Delete requires explicit confirmation.", code="confirm_required")
        path = safe_join(self.root, rel_path)
        if not path.exists():
            raise WorkspaceSecurityError("File not found.", code="not_found")
        if is_blocked_secret(rel_path):
            raise WorkspaceSecurityError(
                "This path is blocked because it may contain secrets.", code="secret_blocked"
            )
        if path.is_dir():
            # Only allow deleting empty directories for Phase 1 safety
            try:
                path.rmdir()
            except OSError as exc:
                raise WorkspaceSecurityError(
                    "Only empty directories can be deleted in Phase 1.",
                    code="dir_not_empty",
                ) from exc
            return {"path": rel_path.replace("\\", "/"), "deleted": True, "type": "dir"}
        if not is_supported_text_path(path):
            raise WorkspaceSecurityError("Only supported text files can be deleted.", code="unsupported")
        path.unlink()
        return {"path": rel_path.replace("\\", "/"), "deleted": True, "type": "file"}

    def audit_safe_path(self, rel_path: str) -> str:
        return redact_audit_detail(rel_path.replace("\\", "/"), limit=240)
