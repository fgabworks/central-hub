from __future__ import annotations

from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, which_executable
from hub.agent_center.adapters.cli_common import BaseCliAdapter


class CursorAgentAdapter(BaseCliAdapter):
    """Cursor Agent CLI only — never the IDE `cursor` editor binary."""

    _CANDIDATES = ("agent", "cursor-agent")
    authentication_method = "Official Cursor Agent CLI login (`agent login`)"
    credential_storage = "Cursor Agent CLI managed. Hub never stores Cursor credentials."
    settings_vendor = "Cursor"
    settings_logo = "img/providers/cursor-agent.svg"

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        result = self._run_probe([executable, "status"])
        text = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0 and not any(
            word in text.lower() for word in ("not logged", "unauthenticated", "login required")
        ):
            return {"state": "connected", "detail": "Cursor Agent authenticated"}
        reason = text[:240] if text else "Cursor Agent authentication required"
        return {"state": "authentication_required", "detail": reason}

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
        desc = self.descriptor
        for name in (desc.executable, *self._CANDIDATES):
            if not name:
                continue
            found = which_executable(name)
            if found and not self._looks_like_editor_cli(found):
                return found
        return None

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
        lowered = path.replace("\\", "/").lower()
        if lowered.endswith("/cursor.cmd") or lowered.endswith("/cursor.exe"):
            return True
        if lowered.endswith("/cursor") and "/resources/app/bin/" in lowered:
            return True
        return False
