"""Secret-path exclusion for Agent Center context packing."""

from __future__ import annotations

from pathlib import Path

from hub.agent_center.models import SECRET_DIR_NAMES, SECRET_NAME_PATTERNS


def is_secret_path(path: Path | str, *, repo_root: Path | None = None) -> bool:
    """True when a path looks like credentials / env / private key material."""
    raw = Path(path)
    try:
        parts = list(raw.parts)
    except Exception:  # noqa: BLE001
        parts = [str(path)]
    lowered = [p.lower() for p in parts]
    name = raw.name.lower()

    for part in lowered:
        if part in SECRET_DIR_NAMES:
            return True
        if part.startswith(".env"):
            return True

    for pattern in SECRET_NAME_PATTERNS:
        pat = pattern.lower()
        if name == pat or name.startswith(pat) or pat in name:
            return True
        if any(pat == p or pat in p for p in lowered):
            return True

    if repo_root is not None:
        try:
            rel = raw.resolve().relative_to(repo_root.resolve())
            return is_secret_path(rel)
        except Exception:  # noqa: BLE001
            pass
    return False


def filter_safe_paths(paths: list[str], *, repo_root: Path | None = None) -> list[str]:
    return [p for p in paths if not is_secret_path(p, repo_root=repo_root)]
