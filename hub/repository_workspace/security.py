"""Path jail, secret blocking, and text/binary classification."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from hub.agent_center.models import SECRET_DIR_NAMES, SECRET_NAME_PATTERNS
from hub.agent_center.secrets import is_secret_path
from hub.settings import ROOT_DIR

# Supported editable / previewable text extensions (lowercase, with dot).
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".markdown",
        ".json",
        ".yaml",
        ".yml",
        ".py",
        ".pyi",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sql",
        ".txt",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".config",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".csv",
        ".tsv",
        ".xml",
        ".svg",
        ".sh",
        ".bat",
        ".ps1",
        ".dockerfile",
        ".makefile",
        ".r",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".vue",
        ".svelte",
        ".lock",
    }
)

TEXT_BASENAMES: frozenset[str] = frozenset(
    {
        "makefile",
        "dockerfile",
        "gemfile",
        "procfile",
        "license",
        "licence",
        "readme",
        "authors",
        "changelog",
        "agents.md",
        "claude.md",
        "codex.md",
    }
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".a",
        ".wasm",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
        ".avi",
        ".sqlite",
        ".db",
        ".pkl",
        ".pickle",
    }
)

SKIP_DIR_NAMES: frozenset[str] = frozenset(
    SECRET_DIR_NAMES
    | {
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".cursor",
        "coverage",
        ".cache",
        "eggs",
        ".eggs",
        "__pypackages__",
    }
)

GENERATED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".cache",
        "coverage",
        "dist",
        "build",
        "eggs",
        ".eggs",
        "__pypackages__",
    }
)

# Extra secret basenames beyond Agent Center patterns.
_EXTRA_SECRET_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "htpasswd",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "authorized_keys",
        "known_hosts",
        "credentials.json",
        "serviceaccount.json",
        "client_secret.json",
        "token.json",
        "oauth_token.json",
    }
)

_SECRET_CONTENT_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|private[_-]?key)\s*[:=]\s*\S+"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PII_KEY_RE = re.compile(
    r'(?i)("(?:email|e-mail|phone|mobile|username|user_name|userName|'
    r'password|secret)"\s*:\s*")([^"]+)(")'
)


class WorkspaceSecurityError(ValueError):
    """Safe, non-leaky filesystem / edit rejection."""

    def __init__(self, message: str, *, code: str = "forbidden") -> None:
        super().__init__(message)
        self.code = code


def resolve_repo_root(local_path: str | None) -> Path | None:
    """Return absolute directory for a configured local path, or None if unavailable."""
    raw = (local_path or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    else:
        path = path.resolve()
    if not path.exists() or not path.is_dir():
        return None
    return path


def is_blocked_secret(rel_path: str | Path) -> bool:
    path = Path(rel_path)
    name = path.name.lower()
    if name in _EXTRA_SECRET_NAMES:
        return True
    if name.endswith(".pem") or name.endswith(".p12") or name.endswith(".pfx"):
        return True
    # Never allow real .env (examples OK).
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    if is_secret_path(path):
        # Allow documented examples / templates that are not live secrets.
        if name.endswith(".example") or name.endswith(".sample") or name.endswith(".template"):
            if "credential" not in name and "secret" not in name and "token" not in name:
                return False
        return True
    for part in path.parts:
        low = part.lower()
        if low in SECRET_DIR_NAMES and low != ".git":
            # .git is skipped for browsing but classified separately.
            return True
        if low.startswith(".env") and not low.endswith(".example"):
            return True
    for pattern in SECRET_NAME_PATTERNS:
        pat = pattern.lower()
        if pat in {".env", ".env.local", ".env.production", ".env.development"}:
            continue
        if name == pat or (pat.startswith(".") and name.endswith(pat)):
            return True
    return False


def is_generated_dir(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in GENERATED_DIR_NAMES
        or lowered.startswith("tmp")
        or "pycache" in lowered
    )


def should_skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIR_NAMES or lowered.startswith(".git") or is_generated_dir(lowered)


def language_for(path: Path | str) -> str:
    p = Path(path)
    name = p.name.lower()
    ext = p.suffix.lower()
    if name in {"makefile", "gnumakefile"}:
        return "makefile"
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if name.endswith(".env.example"):
        return "ini"
    mapping = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "scss",
        ".sql": "sql",
        ".txt": "plaintext",
        ".toml": "toml",
        ".ini": "ini",
        ".cfg": "ini",
        ".conf": "ini",
        ".xml": "xml",
        ".svg": "xml",
        ".sh": "bash",
        ".bat": "batch",
        ".ps1": "powershell",
        ".csv": "plaintext",
        ".tsv": "plaintext",
    }
    return mapping.get(ext, "plaintext")


def is_supported_text_path(path: Path | str) -> bool:
    p = Path(path)
    name = p.name.lower()
    if name in TEXT_BASENAMES or name.startswith("dockerfile"):
        return True
    if name.endswith(".env.example"):
        return True
    ext = p.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return False
    if ext in TEXT_EXTENSIONS:
        return True
    # Extensionless text-ish names
    if not ext and name in TEXT_BASENAMES:
        return True
    return False


def looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    # High ratio of non-text bytes
    textish = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b < 127)
    return (textish / max(len(sample), 1)) < 0.75


def _is_junction_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    # Windows directory junction / reparse point
    if os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    return stat.S_ISLNK(st.st_mode)


def safe_join(repo_root: Path, rel: str | None) -> Path:
    """Resolve rel under repo_root or raise WorkspaceSecurityError.

    Rejects absolute paths, ``..`` traversal, symlink/junction escapes, and
    paths that resolve outside the repository root.
    """
    root = repo_root.resolve()
    raw = (rel or "").strip().replace("\\", "/")
    if raw in {"", "."}:
        return root

    candidate = Path(raw)
    if candidate.is_absolute() or (os.name == "nt" and len(raw) >= 2 and raw[1] == ":"):
        raise WorkspaceSecurityError("Absolute paths are not allowed.", code="absolute_path")

    parts = [p for p in Path(raw).parts if p not in {"", "."}]
    if any(p == ".." for p in parts):
        raise WorkspaceSecurityError("Path traversal is not allowed.", code="path_traversal")
    if any(p.startswith("/") or p.startswith("\\") for p in parts):
        raise WorkspaceSecurityError("Invalid path.", code="invalid_path")

    # Walk component-by-component without following links until the final resolve check.
    current = root
    for part in parts:
        nxt = current / part
        if _is_junction_or_symlink(nxt):
            # Allow reading through? No — reject symlink/junction components to prevent escape.
            raise WorkspaceSecurityError(
                "Symlink or junction paths are blocked.", code="symlink_blocked"
            )
        current = nxt

    try:
        resolved = current.resolve(strict=False)
    except OSError as exc:
        raise WorkspaceSecurityError("Unable to resolve path.", code="resolve_failed") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceSecurityError(
            "Path escapes the repository root.", code="path_escape"
        ) from exc

    # If the final path is a symlink, ensure its target stays inside root.
    if _is_junction_or_symlink(current):
        raise WorkspaceSecurityError(
            "Symlink or junction paths are blocked.", code="symlink_blocked"
        )

    rel_check = str(Path(*parts)).replace("\\", "/")
    if is_blocked_secret(rel_check):
        raise WorkspaceSecurityError(
            "This path is blocked because it may contain secrets.", code="secret_blocked"
        )

    return resolved


def relative_posix(repo_root: Path, path: Path) -> str:
    root = repo_root.resolve()
    resolved = path.resolve(strict=False)
    rel = resolved.relative_to(root)
    return rel.as_posix()


def redact_personal_detail(text: str) -> str:
    """Hide emails and record-level contact fields in search diagnostics."""
    cleaned = _EMAIL_RE.sub("[redacted-email]", str(text or ""))
    return _PII_KEY_RE.sub(r"\1[redacted]\3", cleaned)


def redact_audit_detail(text: str, *, limit: int = 400) -> str:
    """Strip secret-looking assignments from audit/error strings."""
    cleaned = _SECRET_CONTENT_RE.sub(r"\1=[REDACTED]", text or "")
    cleaned = redact_personal_detail(cleaned)
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned
