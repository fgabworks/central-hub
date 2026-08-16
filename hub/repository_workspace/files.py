"""File tree, preview, and search for a jailed repository root."""

from __future__ import annotations

import html
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    is_generated_dir,
    is_supported_text_path,
    language_for,
    looks_binary,
    redact_audit_detail,
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


def _iter_search_files(
    root: Path,
    *,
    skip_path_substrings: tuple[str, ...] = (),
    max_files: int = 2_000,
) -> Iterator[tuple[Path, str]]:
    """Walk text-search candidates, pruning skipped/generated directories."""
    scanned = 0
    skip = tuple(marker.lower() for marker in skip_path_substrings if marker)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [name for name in dirnames if not should_skip_dir(name) and not name.lower().startswith(".git")]
        try:
            rel_dir = relative_posix(root, Path(dirpath))
        except ValueError:
            dirnames[:] = []
            continue
        rel_dir_l = f"/{rel_dir.lower()}".rstrip("/")
        if skip and any(marker in f"{rel_dir_l}/" for marker in skip):
            dirnames[:] = []
            continue
        if skip:
            dirnames[:] = [
                name
                for name in dirnames
                if not any(marker in f"{rel_dir_l}/{name.lower()}/" for marker in skip)
            ]
        for name in filenames:
            if scanned >= max_files:
                return
            path = Path(dirpath) / name
            try:
                rel = relative_posix(root, path)
            except ValueError:
                continue
            if is_blocked_secret(rel):
                continue
            if skip and any(marker in f"/{rel.lower()}" for marker in skip):
                continue
            scanned += 1
            yield path, rel


class RepositoryFiles:
    def __init__(self, repo_root: Path, settings: WorkspaceSettings) -> None:
        self.root = repo_root.resolve()
        self.settings = settings
        self.last_search_meta: dict[str, Any] = {
            "timed_out": False,
            "truncated": False,
            "unique_files": 0,
        }

    def build_tree(
        self,
        *,
        max_entries: int | None = None,
        include_excluded: bool = False,
    ) -> dict[str, Any]:
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
                    if name.lower().startswith(".git"):
                        continue
                    if should_skip_dir(name) and not include_excluded:
                        continue
                    # Skip secret dirs entirely
                    try:
                        rel = relative_posix(self.root, child)
                    except ValueError:
                        continue
                    safe_rel = Path(
                        *(part for part in Path(rel).parts if not is_generated_dir(part))
                    )
                    if is_blocked_secret(safe_rel):
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
                    safe_rel = Path(
                        *(part for part in Path(rel).parts if not is_generated_dir(part))
                    )
                    if is_blocked_secret(safe_rel):
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

    def search_filenames(
        self,
        query: str,
        *,
        limit: int | None = None,
        skip_path_substrings: tuple[str, ...] | list[str] | None = None,
        max_seconds: float | None = None,
        max_unique_files: int | None = None,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            self.last_search_meta = {"timed_out": False, "truncated": False, "unique_files": 0}
            return []
        cap = limit or self.settings.max_search_matches
        file_cap = max_unique_files or cap
        skip = tuple(skip_path_substrings or ())
        timeout = max_seconds if max_seconds is not None else self.settings.search_timeout_seconds
        started = time.monotonic()
        out: list[dict[str, Any]] = []
        timed_out = False
        for path, rel in _iter_search_files(
            self.root,
            skip_path_substrings=skip,
            max_files=self.settings.max_search_files,
        ):
            if timeout and (time.monotonic() - started) >= timeout:
                timed_out = True
                break
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
                if len(out) >= min(cap, file_cap):
                    break
        self.last_search_meta = {
            "timed_out": timed_out,
            "truncated": timed_out or len(out) >= cap,
            "unique_files": len(out),
        }
        return out

    def search_content(
        self,
        query: str,
        *,
        limit: int | None = None,
        skip_path_substrings: tuple[str, ...] | list[str] | None = None,
        max_seconds: float | None = None,
        max_unique_files: int | None = None,
        max_hits_per_file: int | None = None,
        max_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            self.last_search_meta = {"timed_out": False, "truncated": False, "unique_files": 0}
            return []
        q_lower = q.lower()
        cap = limit or self.settings.max_search_matches
        file_cap = max_unique_files or 64
        per_file = max_hits_per_file or 8
        char_budget = max_chars or 24_000
        skip = tuple(skip_path_substrings or ())
        timeout = max_seconds if max_seconds is not None else self.settings.search_timeout_seconds
        started = time.monotonic()
        out: list[dict[str, Any]] = []
        timed_out = False
        unique_files: set[str] = set()
        used_chars = 0
        truncated = False
        scanned = 0
        for path, rel in _iter_search_files(
            self.root,
            skip_path_substrings=skip,
            max_files=self.settings.max_search_files,
        ):
            if timeout and (time.monotonic() - started) >= timeout:
                timed_out = True
                truncated = True
                break
            if len(out) >= cap:
                truncated = True
                break
            if used_chars >= char_budget:
                truncated = True
                break
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
            hits_in_file = 0
            for idx, line in enumerate(text.splitlines(), start=1):
                if q_lower not in line.lower():
                    continue
                if rel not in unique_files and len(unique_files) >= file_cap:
                    truncated = True
                    self.last_search_meta = {
                        "timed_out": timed_out,
                        "truncated": True,
                        "unique_files": len(unique_files),
                    }
                    return out
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:199] + "…"
                snippet = redact_audit_detail(snippet, limit=200)
                out.append({"path": rel, "line": idx, "snippet": snippet})
                unique_files.add(rel)
                used_chars += len(snippet)
                hits_in_file += 1
                if len(out) >= cap or used_chars >= char_budget or hits_in_file >= per_file:
                    truncated = len(out) >= cap or used_chars >= char_budget
                    break
        self.last_search_meta = {
            "timed_out": timed_out,
            "truncated": truncated,
            "unique_files": len(unique_files),
        }
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
