from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, which_executable
from hub.agent_center.adapters.cli_common import BaseCliAdapter
from hub.agent_center.redact import redact_text

_CANDIDATE_NAMES = ("agent", "cursor-agent")
_NEGATIVE_AUTH = ("not logged", "unauthenticated", "login required", "not authenticated", "logged out")
_TOKENISH = re.compile(r"(?i)(token|bearer|sk-|key=|secret)")
_ACCOUNT_RE = re.compile(r"(?i)logged in as\s+(\S+)")


def official_cursor_agent_dirs() -> list[Path]:
    """Known Cursor Agent CLI install dirs. Never the IDE `cursor` binary."""
    dirs: list[Path] = []
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if os.name == "nt" and local:
        dirs.append(Path(local) / "cursor-agent")
    home_bin = Path.home() / ".local" / "bin"
    if home_bin not in dirs:
        dirs.append(home_bin)
    return dirs


def discover_cursor_agent_executable(configured: str = "agent") -> str | None:
    """PATH first (including Windows User PATH), then official install dirs."""
    names: list[str] = []
    for name in (configured, *_CANDIDATE_NAMES):
        stem = str(name or "").strip()
        if stem and stem not in names and stem.lower() != "cursor":
            names.append(stem)
    extra = [str(path) for path in official_cursor_agent_dirs()]
    for name in names:
        found = which_executable(name, extra_dirs=extra)
        if found and not looks_like_editor_cli(found):
            return found
    for directory in official_cursor_agent_dirs():
        found = _executable_in_dir(directory)
        if found:
            return found
    return None


def _executable_in_dir(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    names = []
    if os.name == "nt":
        for stem in _CANDIDATE_NAMES:
            names.extend((f"{stem}.cmd", f"{stem}.exe", f"{stem}.bat"))
    else:
        names.extend(_CANDIDATE_NAMES)
    for name in names:
        candidate = directory / name
        if candidate.is_file() and not looks_like_editor_cli(str(candidate)):
            return str(candidate)
    versions = directory / "versions"
    if versions.is_dir():
        ranked = sorted(
            (child for child in versions.iterdir() if child.is_dir()),
            key=lambda item: item.name,
            reverse=True,
        )
        for folder in ranked:
            for name in names:
                candidate = folder / name
                if candidate.is_file() and not looks_like_editor_cli(str(candidate)):
                    return str(candidate)
    return None


def looks_like_editor_cli(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    name = Path(lowered).name
    if name in {"cursor", "cursor.exe", "cursor.cmd", "cursor.bat", "cursor.ps1"}:
        return True
    if lowered.endswith("/cursor") and "/resources/app/bin/" in lowered:
        return True
    return False


def parse_cursor_status(text: str, *, returncode: int) -> dict[str, Any]:
    raw = redact_text((text or "").strip(), limit=400)
    lowered = raw.lower()
    account = _account_label(raw)
    negative = any(marker in lowered for marker in _NEGATIVE_AUTH)
    positive = bool(account) or "logged in" in lowered or "authenticated" in lowered
    if positive and not negative:
        return {
            "state": "connected",
            "detail": "Cursor Agent authenticated",
            "account_label": account,
        }
    if returncode == 0 and raw and not negative:
        return {
            "state": "connected",
            "detail": "Cursor Agent authenticated",
            "account_label": account,
        }
    reason = raw[:240] if raw else "Cursor Agent authentication required"
    return {"state": "authentication_required", "detail": reason, "account_label": ""}


def _account_label(text: str) -> str:
    match = _ACCOUNT_RE.search(text or "")
    if not match:
        return ""
    value = match.group(1).strip(" .,'\"")
    if not value or value.lower() in {"[redacted]", "redacted"} or _TOKENISH.search(value) or len(value) > 160:
        return ""
    return value[:160]


class CursorAgentAdapter(BaseCliAdapter):
    """Cursor Agent CLI only — never the IDE `cursor` editor binary."""

    _CANDIDATES = _CANDIDATE_NAMES
    authentication_method = "Official Cursor Agent CLI login (`agent login`)"
    credential_storage = "Cursor Agent CLI managed. Hub never stores Cursor credentials."
    settings_vendor = "Cursor"
    settings_logo = "img/providers/cursor-agent.svg"

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        result = self._run_probe([executable, "status"])
        text = (result.stdout or result.stderr or "").strip()
        return parse_cursor_status(text, returncode=int(result.returncode or 0))

    def _login_argv(self, executable: str) -> list[str]:
        return [executable, "login"]

    def _logout_argv(self, executable: str) -> list[str]:
        return [executable, "logout"]

    def _cli_command_candidates(self) -> tuple[str, ...]:
        return self._CANDIDATES

    def _install_help(self) -> str:
        return (
            "Install the Cursor Agent CLI so `agent` or `cursor-agent` is on PATH. "
            "The IDE `cursor` binary is not a valid AiriX runner."
        )

    def _missing_cli_detail(self) -> str:
        return (
            "Cursor Agent CLI not found on PATH (expected `agent` or `cursor-agent`). "
            "The IDE `cursor` binary is not an agent runner."
        )

    def list_models(self) -> tuple[list[str], str]:
        exe = self.resolve_executable()
        if not exe:
            return [], "none"
        result = self._run_probe([exe, "models"])
        if result.returncode != 0:
            return [], "error"
        models = []
        for raw in (result.stdout or "").splitlines():
            model = raw.strip().lstrip("*- ").split()[0] if raw.strip() else ""
            if model and not model.lower().startswith(("available", "model")):
                models.append(model)
        return list(dict.fromkeys(models)), "discovered"

    def resolve_executable(self) -> str | None:
        return discover_cursor_agent_executable(self.descriptor.executable or "agent")

    def availability(self) -> AgentAvailability:
        desc = self.descriptor
        if not desc.enabled:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="disabled",
                detail="Disabled in config/agents.yaml",
                executable_found=False,
                modes=list(desc.modes),
                models=[],
                models_source="none",
            )
        status = self.connection_status()
        models, source = self.list_models() if status.get("installed") else ([], "none")
        if not status.get("installed"):
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="unavailable",
                detail=str(status.get("detail") or self._missing_cli_detail()),
                executable_found=False,
                modes=list(desc.modes),
                models=models,
                models_source=source,
            )
        if status.get("state") != "connected":
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="unavailable",
                detail=str(status.get("detail") or "Not authenticated"),
                executable_found=True,
                modes=list(desc.modes),
                models=models,
                models_source=source,
            )
        return AgentAvailability(
            id=desc.id,
            label=desc.label,
            status="available",
            detail=f"Found {self.resolve_executable()}",
            executable_found=True,
            modes=list(desc.modes),
            models=models,
            models_source=source,
        )

    def _default_template(self, mode: str) -> list[str]:
        return [
            "{executable}",
            "-p",
            "{prompt}",
            "--mode=ask",
            "--output-format",
            "text",
            "--model",
            "{model}",
        ]

    @staticmethod
    def _looks_like_editor_cli(path: str) -> bool:
        return looks_like_editor_cli(path)
