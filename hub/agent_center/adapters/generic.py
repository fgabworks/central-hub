from __future__ import annotations

from hub.agent_center.adapters.cli_common import BaseCliAdapter


class GenericCliAdapter(BaseCliAdapter):
    """Fallback adapter for future agents declared in config/agents.yaml."""

    def _default_template(self, mode: str) -> list[str]:
        return ["{executable}", "{prompt}"]
