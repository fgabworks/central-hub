"""Shared CLI adapter helpers (allowlisted argv templates, no shell)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from hub.agent_center.adapters.base import (
    AgentAvailability,
    AgentDescriptor,
    which_executable,
)
from hub.agent_center.models import MODES


class BaseCliAdapter:
    authentication_method = "Provider CLI browser authentication"
    credential_storage = "Provider CLI managed"

    def __init__(self, descriptor: AgentDescriptor) -> None:
        self.descriptor = descriptor

    def list_models(self) -> tuple[list[str], str]:
        managed = list(self.descriptor.models_managed)
        if managed:
            return managed, "managed"
        return [], "none"

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        models, source = self.list_models()
        return {
            "models": models,
            "model_details": [{"id": model, "display_name": model, "availability": "available"} for model in models],
            "groups": {},
            "recommended_model": models[0] if models else None,
            "models_source": source,
            "reasoning_efforts": [],
            "error": "",
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "modes": list(self.descriptor.modes),
            "streaming": True,
            "cancel": True,
            "dynamic_models": True,
            "read_only": True,
            "file_write": False,
            "command_execution": False,
            "sql_execution": False,
            "email_actions": False,
            "repository_runs": False,
        }

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        exe = self.resolve_executable()
        if not exe:
            return {"state": "unavailable", "detail": f"Executable not found on PATH: {self.descriptor.executable}", "installed": False, "available": False}
        probe = self._authentication_probe(exe)
        return {"installed": True, "available": probe.get("state") == "connected", **probe}

    def test_connection(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def connect(self) -> dict[str, Any]:
        exe = self.resolve_executable()
        if not exe:
            return {"ok": False, "state": "unavailable", "detail": "Provider CLI is not installed"}
        argv = self._login_argv(exe)
        if not argv:
            return {"ok": False, "state": "authentication_required", "detail": "Use the provider's supported authentication flow"}
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
        subprocess.Popen(argv, shell=False, creationflags=flags, env=_safe_cli_env())
        return {"ok": True, "state": "authentication_required", "detail": "Provider authentication started; complete it in the provider window, then Test Connection"}

    def disconnect(self) -> dict[str, Any]:
        exe = self.resolve_executable()
        argv = self._logout_argv(exe) if exe else []
        if argv:
            subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=20, check=False, env=_safe_cli_env())
        return {"ok": True, "state": "authentication_required", "detail": "Disconnected"}

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        return {"state": "authentication_required", "detail": "Authentication status is not exposed by this CLI"}

    def _run_probe(self, argv: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=timeout, check=False, env=_safe_cli_env())

    def _login_argv(self, executable: str) -> list[str]:
        return []

    def _logout_argv(self, executable: str) -> list[str]:
        return []

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
        if model == "__provider_default__":
            cleaned: list[str] = []
            for part in out:
                if part == "__provider_default__":
                    if cleaned and cleaned[-1] == "--model":
                        cleaned.pop()
                    continue
                cleaned.append(part)
            out = cleaned
        return out

    def _default_template(self, mode: str) -> list[str]:
        # Prefer prompt file when templates are omitted (avoids huge argv strings).
        return [self.descriptor.executable, "-p", "{prompt_file}"]


def _safe_cli_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if any(marker in upper for marker in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE_KEY", "COOKIE")):
            env.pop(key, None)
    return env
