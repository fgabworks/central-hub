from __future__ import annotations

from hub.agent_center.adapters.base import AgentAvailability, which_executable
from hub.agent_center.adapters.cli_common import BaseCliAdapter


class CursorAgentAdapter(BaseCliAdapter):
    """Cursor Agent CLI only — never the IDE `cursor` editor binary."""

    _CANDIDATES = ("agent", "cursor-agent")
    authentication_method = "Cursor CLI browser authentication"

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        result = self._run_probe([executable, "status"])
        text = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0 and not any(word in text.lower() for word in ("not logged", "unauthenticated", "login required")):
            return {"state": "connected", "detail": "Cursor Agent authenticated"}
        return {"state": "authentication_required", "detail": "Cursor Agent authentication required"}

    def _login_argv(self, executable: str) -> list[str]:
        return [executable, "login"]

    def _logout_argv(self, executable: str) -> list[str]:
        return [executable, "logout"]

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
        models, source = self.list_models()
        exe = self.resolve_executable()
        if not exe:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="unavailable",
                detail=(
                    "Cursor Agent CLI not found on PATH (expected `agent` or `cursor-agent`). "
                    "The IDE `cursor` binary is not an agent runner."
                ),
                executable_found=False,
                modes=list(desc.modes),
                models=models,
                models_source=source,
            )
        return AgentAvailability(
            id=desc.id,
            label=desc.label,
            status="available",
            detail=f"Found {exe}",
            executable_found=True,
            modes=list(desc.modes),
            models=models,
            models_source=source,
        )

    def _default_template(self, mode: str) -> list[str]:
        # Prompt is large; pass via file path for the CLI when supported.
        return [
            "{executable}", "-p", "{prompt}", "--mode=ask",
            "--output-format", "text", "--model", "{model}",
        ]

    @staticmethod
    def _looks_like_editor_cli(path: str) -> bool:
        lowered = path.replace("\\", "/").lower()
        # Windows ships cursor.CMD / cursor.exe as the editor launcher.
        if lowered.endswith("/cursor.cmd") or lowered.endswith("/cursor.exe"):
            return True
        if lowered.endswith("/cursor") and "/resources/app/bin/" in lowered:
            return True
        return False
