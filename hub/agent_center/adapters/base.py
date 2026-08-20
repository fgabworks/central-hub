"""Agent adapter protocol and shared helpers."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AgentDescriptor:
    id: str
    label: str
    provider: str
    executable: str
    modes: list[str] = field(default_factory=list)
    models_managed: list[str] = field(default_factory=list)
    command_templates: dict[str, list[str]] = field(default_factory=dict)
    enabled: bool = True
    notes: str = ""


@dataclass
class AgentAvailability:
    id: str
    label: str
    status: str  # available | unavailable | degraded | disabled
    detail: str
    executable_found: bool
    modes: list[str]
    models: list[str]
    models_source: str  # managed | discovered | none
    supports_cancel: bool = True
    supports_streaming: bool = True


class AgentAdapter(Protocol):
    descriptor: AgentDescriptor

    def availability(self) -> AgentAvailability: ...

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]: ...

    def capabilities(self) -> dict[str, Any]: ...

    def connect(self) -> dict[str, Any]: ...

    def test_connection(self) -> dict[str, Any]: ...

    def disconnect(self) -> dict[str, Any]: ...

    def list_models(self) -> tuple[list[str], str]:
        """Return (models, source)."""

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]: ...

    def build_argv(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        cwd: str,
        prompt_file: str = "",
    ) -> list[str]: ...


def _windows_user_path() -> str:
    """User PATH from the registry. GUI/Flask processes often miss post-install updates."""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _typ = winreg.QueryValueEx(key, "Path")
    except OSError:
        return ""
    return os.path.expandvars(str(value or ""))


def which_executable(name: str, *, extra_dirs: list[str] | tuple[str, ...] | None = None) -> str | None:
    """Resolve a bare executable name. On Windows, also search the User PATH."""
    command = (name or "").strip()
    if not command:
        return None
    found = shutil.which(command)
    if found:
        return found
    extras: list[str] = []
    if os.name == "nt":
        extras.append(_windows_user_path())
    if extra_dirs:
        extras.extend(str(item) for item in extra_dirs if str(item).strip())
    extra_path = os.pathsep.join(part for part in extras if part.strip())
    if not extra_path:
        return None
    process_path = os.environ.get("PATH") or ""
    search = extra_path if not process_path else process_path + os.pathsep + extra_path
    return shutil.which(command, path=search) or None


def public_availability(av: AgentAvailability) -> dict[str, Any]:
    return {
        "id": av.id,
        "label": av.label,
        "status": av.status,
        "detail": av.detail,
        "executable_found": av.executable_found,
        "modes": list(av.modes),
        "models": list(av.models),
        "models_source": av.models_source,
        "supports_cancel": av.supports_cancel,
        "supports_streaming": av.supports_streaming,
        "runnable": av.status in {"available", "degraded"},
    }
