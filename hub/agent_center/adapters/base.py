"""Agent adapter protocol and shared helpers."""

from __future__ import annotations

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

    def list_models(self) -> tuple[list[str], str]:
        """Return (models, source)."""

    def build_argv(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        cwd: str,
        prompt_file: str = "",
    ) -> list[str]: ...


def which_executable(name: str) -> str | None:
    return shutil.which((name or "").strip()) or None


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
