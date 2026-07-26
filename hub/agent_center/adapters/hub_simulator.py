"""Local demo adapter — verifies Agent Center without external CLIs."""

from __future__ import annotations

import sys

from hub.agent_center.adapters.base import AgentAvailability
from hub.agent_center.adapters.cli_common import BaseCliAdapter


class HubSimulatorAdapter(BaseCliAdapter):
    """Always-available read-only echo adapter for pipeline smoke checks."""

    def resolve_executable(self) -> str | None:
        return sys.executable

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
        models, source = self.list_models()
        return AgentAvailability(
            id=desc.id,
            label=desc.label,
            status="available",
            detail="Local demo adapter (no external CLI). Confirms context packing + run pipeline.",
            executable_found=True,
            modes=list(desc.modes),
            models=models or ["simulator"],
            models_source=source if models else "managed",
        )

    def _default_template(self, mode: str) -> list[str]:
        return [
            sys.executable,
            "-m",
            "hub.agent_center.simulator",
            "--prompt-file",
            "{prompt_file}",
            "--mode",
            "{mode}",
            "--model",
            "{model}",
            "--cwd",
            "{cwd}",
        ]
