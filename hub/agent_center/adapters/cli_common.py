"""Shared CLI adapter helpers (allowlisted argv templates, no shell)."""

from __future__ import annotations

from hub.agent_center.adapters.base import (
    AgentAvailability,
    AgentDescriptor,
    which_executable,
)
from hub.agent_center.models import MODES


class BaseCliAdapter:
    def __init__(self, descriptor: AgentDescriptor) -> None:
        self.descriptor = descriptor

    def list_models(self) -> tuple[list[str], str]:
        managed = list(self.descriptor.models_managed)
        if managed:
            return managed, "managed"
        return [], "none"

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
        exe = which_executable(desc.executable)
        models, source = self.list_models()
        if not exe:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="unavailable",
                detail=f"Executable not found on PATH: {desc.executable}",
                executable_found=False,
                modes=list(desc.modes),
                models=models,
                models_source=source,
            )
        status = "available"
        detail = f"Found {exe}"
        if not desc.command_templates:
            status = "degraded"
            detail += " · using built-in read-only argv template"
        return AgentAvailability(
            id=desc.id,
            label=desc.label,
            status=status,
            detail=detail,
            executable_found=True,
            modes=[m for m in desc.modes if m in MODES],
            models=models,
            models_source=source,
        )

    def resolve_executable(self) -> str | None:
        return which_executable(self.descriptor.executable)

    def build_argv(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        cwd: str,
        prompt_file: str = "",
    ) -> list[str]:
        desc = self.descriptor
        template = list(desc.command_templates.get(mode) or self._default_template(mode))
        if not template:
            raise ValueError(f"No command template for mode={mode}")
        joined = "".join(template)
        if "{prompt_file}" in joined and not prompt_file:
            raise ValueError("prompt_file is required for this agent template")
        exe = self.resolve_executable() or desc.executable
        mapping = {
            "{prompt_file}": prompt_file or "",
            "{prompt}": prompt,
            "{model}": model or "",
            "{cwd}": cwd,
            "{mode}": mode,
            "{executable}": exe,
        }
        out: list[str] = []
        for part in template:
            rendered = part
            for key, value in mapping.items():
                rendered = rendered.replace(key, value)
            if "{" in rendered and "}" in rendered:
                raise ValueError(f"Unsupported placeholder in argv template: {part}")
            out.append(rendered)
        return out

    def _default_template(self, mode: str) -> list[str]:
        # Prefer prompt file when templates are omitted (avoids huge argv strings).
        return [self.descriptor.executable, "-p", "{prompt_file}"]
