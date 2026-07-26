from __future__ import annotations

from hub.agent_center.adapters.cli_common import BaseCliAdapter


class ClaudeCodeAdapter(BaseCliAdapter):
    def _default_template(self, mode: str) -> list[str]:
        # Read-only prompt mode; never passes write/edit flags.
        return [
            "{executable}",
            "-p",
            "{prompt}",
            "--output-format",
            "text",
        ]
