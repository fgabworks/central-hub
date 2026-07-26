"""File tree, preview, and search for a jailed repository root."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    is_supported_text_path,
    language_for,
    looks_binary,
    relative_posix,
    safe_join,
    should_skip_dir,
)
from hub.repository_workspace.settings import WorkspaceSettings


def _mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _size(path: Path) -> int | None:
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


class RepositoryFiles:
    def __init__(self, repo_root: Path, settings: WorkspaceSettings) -> None:
        self.root = repo_root.resolve()
        self.settings = settings

    def build_tree(self, *, max_entries: int | None = None) -> dict[str, Any]:
        limit = max_entries or self.settings.max_tree_entries
        depth_limit = self.settings.max_tree_depth
        truncated = False
        count = 0

        def walk(dir_path: Path, depth: int) -> list[dict[str, Any]]:
            nonlocal truncated, count
            if truncated or depth > depth_limit:
                truncated = True
                return []
            entries: list[dict[str, Any]] = []
            try:
                children = sorted(
                    dir_path.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError:
                return []
            for child in children:
                if count >= limit:
                    truncated = True
                    break
                name = child.name
                if child.is_dir():
                    if should_skip_dir(name):
                        continue
                    # Skip secret dirs entirely
                    try:
                        rel = relative_posix(self.root, child)
                    except ValueError:
                        continue
                    if is_blocked_secret(rel):
                        continue
                    count += 1
                    node = {
                        "name": name,
                        "path": rel,
                        "type": "dir",
                        "children": walk(child, depth + 1),
                    }
                    entries.append(node)
                else:
                    try:
                        rel = relative_posix(self.root, child)
                    except ValueError:
                        continue
                    if is_blocked_secret(rel):
                        continue
                    count += 1
                    entries.append(
                        {
                            "name": name,
                            "path": rel,
                            "type": "file",
                            "size": _size(child),
                            "modified_at": _mtime_iso(child),
                            "language": language_for(child),
                            "editable": is_supported_text_path(child),
                        }
                    )
            return entries

        return {
            "root": self.root.as_posix(),
            "entries": walk(self.root, 0),
            "truncated": truncated,
            "count": count,
        }

    def search_filenames(self, query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        cap = limit or self.settings.max_search_matches
        out: list[dict[str, Any]] = []
        scanned = 0
        for path in self.root.rglob("*"):
            if scanned >= self.settings.max_search_files:
                break
            if not path.is_file():
                if path.is_dir() and should_skip_dir(path.name):
                    # rglob still descends; skip by not matching files inside via continue
                    continue
                continue
            # Skip files under blocked/skip dirs
            try:
                rel = relative_posix(self.root, path)
            except ValueError:
                continue
            if any(should_skip_dir(p) for p in Path(rel).parts[:-1]):
                continue
            if is_blocked_secret(rel):
                continue
            scanned += 1
            if q in path.name.lower() or q in rel.lower():
                out.append(
                    {
                        "path": rel,
                        "name": path.name,
                        "size": _size(path),
                        "modified_at": _mtime_iso(path),
                        "language": language_for(path),
                    }
                )
                if len(out) >= cap:
                    break
        return out

    def search_content(self, query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        q_lower = q.lower()
        cap = limit or self.settings.max_search_matches
        out: list[dict[str, Any]] = []
        scanned = 0
        for path in self.root.rglob("*"):
            if scanned >= self.settings.max_search_files or len(out) >= cap:
                break
            if not path.is_file():
                continue
            try:
                rel = relative_posix(self.root, path)
            except ValueError:
                continue
            if any(should_skip_dir(p) for p in Path(rel).parts[:-1]):
                continue
            if is_blocked_secret(rel):
                continue
            if not is_supported_text_path(path):
                continue
            size = _size(path) or 0
            if size > self.settings.max_search_file_bytes:
                continue
            scanned += 1
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if looks_binary(data[:8192]):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
            lines = text.splitlines()
            for idx, line in enumerate(lines, start=1):
                if q_lower in line.lower():
                    snippet = line.strip()
                    if len(snippet) > 200:
                        snippet = snippet[:199] + "…"
                    # Never return secret-looking line content
                    from hub.repository_workspace.security import redact_audit_detail

                    out.append(
                        {
                            "path": rel,
                            "line": idx,
                            "snippet": redact_audit_detail(snippet, limit=200),
                        }
                    )
                    if len(out) >= cap:
                        break
        return out

    def read_preview(self, rel_path: str) -> dict[str, Any]:
        path = safe_join(self.root, rel_path)
        if not path.exists() or not path.is_file():
            raise WorkspaceSecurityError("File not found.", code="not_found")
        rel = relative_posix(self.root, path)
        if is_blocked_secret(rel):
            raise WorkspaceSecurityError(
                "This path is blocked because it may contain secrets.", code="secret_blocked"
            )
        size = _size(path) or 0
        meta = {
            "path": rel,
            "name": path.name,
            "size": size,
            "modified_at": _mtime_iso(path),
            "language": language_for(path),
            "editable": False,
            "binary": False,
            "truncated": False,
            "content": "",
            "content_html": "",
            "line_count": 0,
        }
        if not is_supported_text_path(path):
            meta["binary"] = True
            meta["error"] = "Unsupported or binary file type."
            return meta
        if size > self.settings.max_preview_bytes:
            meta["truncated"] = True
            meta["error"] = (
                f"File exceeds preview limit "
                f"({self.settings.max_preview_bytes} bytes)."
            )
            return meta
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WorkspaceSecurityError("Unable to read file.", code="read_failed") from exc
        if looks_binary(data[:8192]):
            meta["binary"] = True
            meta["error"] = "File appears to be binary."
            return meta
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            meta["truncated"] = True
        meta["content"] = text
        meta["content_html"] = html.escape(text)
        meta["line_count"] = text.count("\n") + (1 if text else 0)
        meta["editable"] = size <= self.settings.max_edit_bytes and is_supported_text_path(path)
        return meta
