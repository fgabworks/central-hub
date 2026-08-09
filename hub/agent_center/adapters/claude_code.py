from __future__ import annotations

import json
from typing import Any

from hub.agent_center.adapters.cli_common import BaseCliAdapter


class ClaudeCodeAdapter(BaseCliAdapter):
    authentication_method = "Official Claude Code CLI auth (`claude auth login`)"
    credential_storage = "Claude Code CLI managed. Hub never stores Anthropic credentials."

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        result = self._run_probe([executable, "auth", "status"])
        raw = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return {
                "state": "authentication_required",
                "detail": "Claude Code authentication required",
                "error_code": "authentication_required",
            }
        account = ""
        try:
            payload = json.loads(raw)
            account = str(payload.get("email") or payload.get("subscriptionType") or "")[:160]
        except (json.JSONDecodeError, AttributeError):
            pass
        return {
            "state": "connected",
            "detail": "Claude Code authenticated",
            "account_label": account,
        }

    def _login_argv(self, executable: str) -> list[str]:
        return [executable, "auth", "login"]

    def _logout_argv(self, executable: str) -> list[str]:
        return [executable, "auth", "logout"]

    def _cli_command_candidates(self) -> tuple[str, ...]:
        return ("claude",)

    def _install_help(self) -> str:
        return "Install the Claude Code CLI (`claude`) and use Connect to run official `claude auth login`."

    def _missing_cli_detail(self) -> str:
        return "Claude Code CLI not found on PATH (expected `claude`)."

    def list_models(self) -> tuple[list[str], str]:
        # Claude Code has no supported non-interactive model-catalog command.
        status = self.connection_status()
        return (["__provider_default__"], "provider-default") if status["state"] == "connected" else ([], "none")

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        models, source = self.list_models()
        rows = (
            [{"id": "__provider_default__", "display_name": "Provider default", "availability": "available"}]
            if models
            else []
        )
        return {
            "models": models,
            "model_details": rows,
            "groups": {},
            "recommended_model": models[0] if models else None,
            "models_source": source,
            "reasoning_efforts": [],
            "error": "",
        }

    def _default_template(self, mode: str) -> list[str]:
        # Read-only prompt mode; never passes write/edit flags.
        return [
            "{executable}",
            "-p",
            "{prompt}",
            "--output-format",
            "text",
            "--permission-mode",
            "plan",
            "--bare",
            "--disable-slash-commands",
            "--tools",
            "",
            "--model",
            "{model}",
        ]
