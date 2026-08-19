"""Read-only Anthropic API adapter with dynamic model discovery."""

from __future__ import annotations

from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.anthropic_client import AnthropicClient, AnthropicClientError
from hub.agent_center.anthropic_settings import AnthropicSettings, load_anthropic_settings


class AnthropicApiAdapter:
    is_api_adapter = True
    credential_type = "api_key"
    env_keys = ("ANTHROPIC_API_KEY",)
    preferred_write_key = "ANTHROPIC_API_KEY"
    enabled_env = "ANTHROPIC_ENABLED"
    enabled_defaults_to_key = True
    enable_when_key_set = True
    authentication_method = "Server-side ANTHROPIC_API_KEY"
    credential_storage = "Gitignored local server file or server environment"
    settings_help = (
        "Uses ANTHROPIC_API_KEY from CLIMATE's local secret store or server environment. "
        "Claude Code CLI login is a separate provider."
    )
    settings_display_name = "Claude / Anthropic"
    settings_mark = "C"
    settings_vendor = "Anthropic"
    settings_logo = "img/providers/claude-code.svg"
    settings_blurb = "Add your Anthropic API key to enable Claude models in CLIMATE."

    def __init__(
        self,
        descriptor: AgentDescriptor | None = None,
        *,
        settings: AnthropicSettings | None = None,
        client: AnthropicClient | None = None,
    ) -> None:
        self.settings = settings or load_anthropic_settings()
        self.client = client or AnthropicClient(self.settings)
        self.descriptor = descriptor or AgentDescriptor(
            id="anthropic-api",
            label="Anthropic",
            provider="anthropic_api",
            executable="",
            modes=["ask"],
        )

    def reload_settings(self) -> None:
        self.settings = load_anthropic_settings()
        self.client = AnthropicClient(self.settings)

    def capabilities(self) -> dict[str, Any]:
        return {
            "modes": ["ask"],
            "streaming": True,
            "cancel": True,
            "dynamic_models": True,
            "read_only": True,
            "api": True,
            "file_write": False,
            "command_execution": False,
            "sql_execution": False,
            "email_actions": False,
            "repository_runs": False,
            "native_repository_investigation": False,
        }

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        if not self.settings.is_configured:
            return self._empty("Set ANTHROPIC_API_KEY on the server", "none")
        try:
            rows, source = self.client.list_models(force_refresh=force_refresh)
        except AnthropicClientError as exc:
            return self._empty(str(exc), "error")
        if self.settings.allowed_models is not None:
            allowed = set(self.settings.allowed_models)
            rows = [row for row in rows if row["id"] in allowed]
        models = [str(row["id"]) for row in rows]
        recommended = (
            self.settings.default_model
            if self.settings.default_model in models
            else (models[0] if models else None)
        )
        return {
            "models": models,
            "model_details": rows,
            "groups": {},
            "recommended_model": recommended,
            "recommendation_reason": (
                "configured_default"
                if recommended == self.settings.default_model and recommended
                else "first_accessible"
            ),
            "models_source": source,
            "reasoning_efforts": [],
            "error": "",
        }

    def list_models(self) -> tuple[list[str], str]:
        details = self.list_model_details()
        return list(details["models"]), str(details["models_source"])

    def resolve_run_model(
        self,
        *,
        mode: str,
        requested_model: str | None,
        force_refresh: bool = True,
    ) -> dict[str, Any]:
        if mode != "ask":
            return {
                "ok": False,
                "code": "mode_unsupported",
                "error": "Anthropic v1 is read-only chat and supports Ask mode only",
            }
        details = self.list_model_details(mode=mode, force_refresh=force_refresh)
        models = list(details["models"])
        requested = (requested_model or "").strip()
        if not requested:
            return {
                "ok": False,
                "code": "model_required",
                "error": "Select an exact Anthropic model before running",
            }
        if requested not in models:
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": f"Model {requested!r} is not accessible with this Anthropic API key",
            }
        return {
            "ok": True,
            "model": requested,
            "reason": "user_selected",
            "supports_reasoning_effort": False,
            "background": False,
            "is_pro": False,
            "timeout_seconds": self.settings.timeout_seconds,
            "selected_model": requested,
            "resolved_model": requested,
            "models_source": details.get("models_source"),
        }

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not self.settings.enabled or not self.settings.api_key:
            return {
                "state": "authentication_required",
                "detail": "Set ANTHROPIC_API_KEY on the server",
                "installed": True,
                "available": False,
            }
        details = self.list_model_details(force_refresh=force_refresh)
        if details["error"]:
            text = str(details["error"])
            state = (
                "authentication_required"
                if "auth" in text.lower() or "api key" in text.lower()
                else "error"
            )
            return {
                "state": state,
                "detail": text,
                "installed": True,
                "available": False,
            }
        return {
            "state": "connected",
            "detail": f"Anthropic API connected; {len(details['models'])} text models",
            "installed": True,
            "available": True,
        }

    def test_connection(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def connect(self) -> dict[str, Any]:
        return self.test_connection()

    def disconnect(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": "authentication_required",
            "detail": "Disabled in Central Hub; server environment was not changed",
        }

    def availability(self) -> AgentAvailability:
        status = self.connection_status()
        details = (
            self.list_model_details()
            if status["state"] == "connected"
            else self._empty("", "none")
        )
        mapped = "available" if status["state"] == "connected" else "unavailable"
        return AgentAvailability(
            self.descriptor.id,
            self.descriptor.label,
            mapped,
            status["detail"],
            True,
            ["ask"],
            list(details["models"]),
            str(details["models_source"]),
        )

    def build_argv(self, **_: Any) -> list[str]:
        raise ValueError("Anthropic API adapter does not use CLI argv")

    @staticmethod
    def _empty(error: str, source: str) -> dict[str, Any]:
        return {
            "models": [],
            "model_details": [],
            "groups": {},
            "recommended_model": None,
            "recommendation_reason": "none",
            "models_source": source,
            "reasoning_efforts": [],
            "error": error,
        }
