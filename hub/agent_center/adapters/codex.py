from __future__ import annotations

from hub.agent_center.adapters.cli_common import BaseCliAdapter


class CodexAdapter(BaseCliAdapter):
    def _default_template(self, mode: str) -> list[str]:
        return ["{executable}", "exec", "--skip-git-repo-check", "{prompt}"]
