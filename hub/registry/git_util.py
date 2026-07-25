"""Git URL normalization and local checkout discovery (no clone/pull)."""

from __future__ import annotations

import re
from pathlib import Path

_SSH_RE = re.compile(r"^git@([^:]+):(.+)$", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def normalize_git_url(url: str | None) -> str:
    """Normalize Git remotes for duplicate / checkout matching."""
    raw = (url or "").strip()
    if not raw:
        return ""
    text = raw.rstrip("/")
    ssh = _SSH_RE.match(text)
    if ssh:
        host = ssh.group(1).lower()
        path = ssh.group(2)
        text = f"https://{host}/{path}"
    if text.lower().endswith(".git"):
        text = text[:-4]
    # Drop credentials if present in URL.
    text = re.sub(r"^(https?://)[^/@]+@", r"\1", text, flags=re.IGNORECASE)
    if _SCHEME_RE.match(text):
        scheme, rest = text.split("://", 1)
        rest = rest.lower()
        return f"{scheme.lower()}://{rest}"
    return text.lower()


def git_urls_match(a: str | None, b: str | None) -> bool:
    na = normalize_git_url(a)
    nb = normalize_git_url(b)
    return bool(na) and na == nb


def read_origin_url(repo_path: Path) -> str | None:
    """Read remote.origin.url from .git/config without spawning git."""
    git_dir = repo_path / ".git"
    if git_dir.is_file():
        # Worktree / gitfile pointer — skip deep resolution for Phase safety.
        return None
    config_path = git_dir / "config"
    if not config_path.is_file():
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_origin = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped.lower() in {'[remote "origin"]', "[remote \"origin\"]"}
            continue
        if in_origin and stripped.lower().startswith("url"):
            _, _, value = stripped.partition("=")
            value = value.strip()
            return value or None
    return None


def find_local_checkout(
    git_url: str,
    search_roots: list[Path],
    *,
    max_depth: int = 1,
) -> Path | None:
    """
    Find an existing checkout whose origin remote matches git_url.
    Does not clone or pull. Scans immediate children of each root (depth 1).
    """
    target = normalize_git_url(git_url)
    if not target:
        return None
    seen: set[Path] = set()
    for root in search_roots:
        try:
            root = root.expanduser().resolve()
        except OSError:
            continue
        if not root.is_dir():
            continue
        candidates = [root]
        if max_depth >= 1:
            try:
                candidates.extend(
                    p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                )
            except OSError:
                pass
        for path in candidates:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            origin = read_origin_url(resolved)
            if origin and normalize_git_url(origin) == target:
                return resolved
    return None


def default_search_roots(*, live_processing_path: str | None = None) -> list[Path]:
    roots: list[Path] = []
    if live_processing_path:
        lp = Path(live_processing_path).expanduser()
        roots.append(lp.parent if lp.parent else lp)
        roots.append(lp)
    roots.extend(
        [
            Path("C:/PMNP"),
            Path.home() / "PMNP",
            Path.cwd(),
        ]
    )
    # De-dupe while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def slugify_repo_id(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "repository"
