"""Path jail, shell allowlist, origin checks, and WS tickets for interactive terminals."""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    resolve_repo_root,
    safe_join,
)
from hub.settings import ROOT_DIR


SHELL_POWERSHELL = "powershell"
SHELL_CMD = "cmd"
SHELL_BASH = "bash"
SHELL_SH = "sh"
ALLOWED_SHELLS_WIN = (SHELL_POWERSHELL, SHELL_CMD)
ALLOWED_SHELLS_UNIX = (SHELL_BASH, SHELL_SH, "zsh")


class TerminalSecurityError(WorkspaceSecurityError):
    """Terminal-specific security rejection."""


def scrub_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Inherit process env but strip obvious secrets. Never inject .env file contents."""
    child = dict(base if base is not None else os.environ)
    for key in list(child):
        upper = key.upper()
        if any(
            token in upper
            for token in (
                "PASSWORD",
                "SECRET",
                "TOKEN",
                "API_KEY",
                "PRIVATE_KEY",
                "COOKIE",
                "CREDENTIAL",
                "AUTH_HEADER",
            )
        ):
            child.pop(key, None)
    return child


def resolve_local_repo_root(repo: Any) -> Path:
    root = resolve_repo_root(getattr(repo, "local_path", None) or getattr(repo, "working_directory", None))
    if root is None:
        raise TerminalSecurityError(
            "Repository has no enabled local path.", code="repo_path_unavailable"
        )
    # Reject if the configured root itself is a symlink/junction escape from itself
    # (resolve_repo_root already resolves; re-check existence).
    if not root.exists() or not root.is_dir():
        raise TerminalSecurityError("Repository local path is not a directory.", code="repo_path_missing")
    return root


def resolve_session_cwd(repo: Any, relative_cwd: str | None = None) -> Path:
    """CWD must be the repo root or a safe_join path under it (no traversal/symlink escape)."""
    root = resolve_local_repo_root(repo)
    if not relative_cwd or str(relative_cwd).strip() in {"", ".", "./"}:
        return root
    try:
        path = safe_join(root, relative_cwd)
    except WorkspaceSecurityError as exc:
        raise TerminalSecurityError(str(exc), code=getattr(exc, "code", "cwd_forbidden")) from exc
    if not path.exists() or not path.is_dir():
        raise TerminalSecurityError("Working directory does not exist.", code="cwd_missing")
    return path


def default_shell_id() -> str:
    if os.name == "nt":
        return SHELL_POWERSHELL
    return SHELL_BASH if shutil.which("bash") else SHELL_SH


def resolve_shell_executable(shell_id: str, *, allow_cmd: bool) -> tuple[str, list[str]]:
    """Return (shell_id, argv) from an allowlisted shell id — never user-supplied paths."""
    sid = (shell_id or default_shell_id()).strip().lower()
    if os.name == "nt":
        if sid not in ALLOWED_SHELLS_WIN:
            raise TerminalSecurityError("Shell is not allowed on Windows.", code="shell_forbidden")
        if sid == SHELL_CMD and not allow_cmd:
            raise TerminalSecurityError(
                "CMD is disabled. Set WC_TERMINAL_ALLOW_CMD=true to enable.",
                code="cmd_disabled",
            )
        if sid == SHELL_POWERSHELL:
            for candidate in (
                os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe",
                shutil.which("powershell"),
                shutil.which("pwsh"),
            ):
                if candidate and Path(candidate).is_file():
                    return SHELL_POWERSHELL, [
                        candidate,
                        "-NoLogo",
                        "-NoExit",
                        "-ExecutionPolicy",
                        "Bypass",
                    ]
            raise TerminalSecurityError("PowerShell executable not found.", code="shell_missing")
        comspec = os.environ.get("ComSpec") or (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\cmd.exe")
        if not Path(comspec).is_file():
            raise TerminalSecurityError("CMD executable not found.", code="shell_missing")
        return SHELL_CMD, [comspec]
    # Unix
    if sid not in ALLOWED_SHELLS_UNIX:
        # Map powershell requests on Unix to bash for convenience in tests/docs.
        if sid == SHELL_POWERSHELL:
            sid = default_shell_id()
        else:
            raise TerminalSecurityError("Shell is not allowed.", code="shell_forbidden")
    path = shutil.which(sid)
    if not path:
        raise TerminalSecurityError(f"{sid} executable not found.", code="shell_missing")
    return sid, [path, "-i"] if sid != SHELL_SH else [path]


def origin_allowed(origin: str | None, host_header: str | None, *, hub_host: str, hub_port: int) -> bool:
    """Accept same-origin localhost requests only (Central Hub is local-first)."""
    if not origin:
        # Some native clients omit Origin; require Host to match hub bind.
        host = (host_header or "").split(":")[0].strip().lower()
        return host in {"127.0.0.1", "localhost", hub_host.strip().lower()}
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost", hub_host.strip().lower()}:
        return False
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    # Allow hub port or common same-origin cases when Host matches.
    if port == int(hub_port):
        return True
    # Also accept when Origin host matches Host header without port mismatch abuse.
    host_only = (host_header or "").split(":")[0].strip().lower()
    return hostname == host_only and hostname in {"127.0.0.1", "localhost"}


def mint_ws_ticket(
    *,
    secret: str,
    session_id: str,
    actor: str,
    ttl_seconds: int,
) -> str:
    exp = int(time.time()) + max(15, int(ttl_seconds))
    payload = f"{session_id}|{actor}|{exp}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_ws_ticket(
    ticket: str,
    *,
    secret: str,
    session_id: str,
    actor: str,
) -> bool:
    parts = (ticket or "").split("|")
    if len(parts) != 4:
        return False
    sid, ticket_actor, exp_s, sig = parts
    if sid != session_id:
        return False
    if ticket_actor != actor:
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    payload = f"{sid}|{ticket_actor}|{exp}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def assert_hub_local_bind(host: str) -> None:
    """Warn-level guard used by docs/tests; creation still allowed when host is loopback."""
    h = (host or "").strip().lower()
    if h not in {"127.0.0.1", "localhost", "::1"}:
        raise TerminalSecurityError(
            "Interactive terminals require CENTRAL_HUB_HOST to be localhost.",
            code="non_local_bind",
        )


def hub_root() -> Path:
    return ROOT_DIR
