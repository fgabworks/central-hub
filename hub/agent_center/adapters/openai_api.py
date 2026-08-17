"""OpenAI Responses API adapter with dynamic model discovery."""

from __future__ import annotations

from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.models import MODES
from hub.agent_center.openai_catalog import REASONING_EFFORTS, get_spec
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_settings import OpenAISettings, load_openai_settings


class OpenAIApiAdapter:
    is_api_adapter = True
    credential_type = "api_key"
    env_keys = ("OPENAI_API_KEY",)
    preferred_write_key = "OPENAI_API_KEY"
    enabled_env = "OPENAI_ENABLED"
    enabled_defaults_to_key = False
    enable_when_key_set = True
    authentication_method = "Server-side OPENAI_API_KEY (optional separate billing)"
    credential_storage = "Environment only"
    settings_help = (
        "Uses OPENAI_API_KEY. Saving a key also sets OPENAI_ENABLED=true for this process."
    )
    settings_display_name = "OpenAI"
    settings_mark = "O"
    settings_blurb = "Add your OpenAI API key to enable OpenAI models."

    def __init__(self, descriptor: AgentDescriptor | None = None, *, settings: OpenAISettings | None = None, client: OpenAIClient | None = None) -> None:
        self.settings = settings or load_openai_settings()
        self.client = client or OpenAIClient(self.settings)
        self.descriptor = descriptor or AgentDescriptor(
            id="openai-api", label="OpenAI API", provider="openai_api", executable="", modes=list(MODES)
        )

    def reload_settings(self) -> None:
        self.settings = load_openai_settings()
        self.client = OpenAIClient(self.settings)

    def capabilities(self) -> dict[str, Any]:
        return {
            "modes": list(self.descriptor.modes), "streaming": True, "cancel": True,
            "dynamic_models": True, "read_only": True, "api": True,
            "file_write": False, "command_execution": False, "sql_execution": False,
            "email_actions": False, "repository_runs": False,
        }

    def list_models(self) -> tuple[list[str], str]:
        details = self.list_model_details()
        return list(details["models"]), str(details["models_source"])

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        if not self.settings.enabled or not self.settings.api_key:
            return self._empty("OPENAI_API_KEY is missing", "none")
        try:
            ids, source = self.client.list_model_ids(force_refresh=force_refresh)
        except OpenAIClientError as exc:
            return self._empty(str(exc), "error")
        allowed = self.settings.allowed_models
        ids = [item for item in ids if allowed is None or item in allowed]
        recommended = self.settings.default_model if self.settings.default_model in ids else (ids[0] if ids else None)
        rows = []
        for model_id in ids:
            spec = get_spec(model_id)
            rows.append(spec.public_dict(availability="available") if spec else {
                "id": model_id, "display_name": model_id, "availability": "available",
                "supports_reasoning_effort": False,
            })
        return {
            "models": ids, "model_details": rows, "groups": {},
            "recommended_model": recommended,
            "recommendation_reason": "configured_default" if recommended == self.settings.default_model and recommended else "first_accessible",
            "models_source": source, "reasoning_efforts": list(REASONING_EFFORTS), "error": "",
        }

    def _empty(self, error: str, source: str) -> dict[str, Any]:
        return {"models": [], "model_details": [], "groups": {}, "recommended_model": None, "recommendation_reason": "none", "models_source": source, "reasoning_efforts": list(REASONING_EFFORTS), "error": error}

    def resolve_run_model(self, *, mode: str, requested_model: str | None, force_refresh: bool = True) -> dict[str, Any]:
        details = self.list_model_details(mode=mode, force_refresh=force_refresh)
        ids = list(details["models"])
        requested = (requested_model or "").strip()
        if requested and requested not in ids:
            return {"ok": False, "code": "model_unavailable", "error": f"Model {requested!r} is not accessible with this API key"}
        model = requested or details.get("recommended_model")
        if not model:
            return {"ok": False, "code": "model_unavailable", "error": details.get("error") or "No text models are accessible for this API key"}
        if requested and model != requested:
            # Never silently substitute a different model for an explicit selection.
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": f"Model {requested!r} resolved to {model!r}; refusing silent substitute",
            }
        spec = get_spec(model)
        is_pro = bool(spec and spec.is_pro)
        return {
            "ok": True, "model": model, "reason": "user_selected" if requested else details.get("recommendation_reason"),
            "is_pro": is_pro, "supports_reasoning_effort": bool(spec and spec.supports_reasoning_effort),
            "background": is_pro,
            "timeout_seconds": self.settings.pro_model_timeout_seconds if is_pro else self.settings.timeout_seconds,
            "spec": spec.public_dict(availability="available") if spec else None,
            "models_source": details.get("models_source"),
            "selected_model": requested,
            "resolved_model": model,
        }

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not self.settings.enabled:
            return {"state": "authentication_required", "detail": "Set OPENAI_ENABLED=true and OPENAI_API_KEY on the server", "installed": True, "available": False}
        if not self.settings.api_key:
            return {"state": "authentication_required", "detail": "Set OPENAI_API_KEY on the server", "installed": True, "available": False}
        details = self.list_model_details(force_refresh=force_refresh)
        if details["error"]:
            text = str(details["error"])
            state = "authentication_required" if "authentication" in text.lower() or "authorization" in text.lower() else "error"
            return {"state": state, "detail": text, "installed": True, "available": False}
        return {"state": "connected", "detail": f"OpenAI API connected; {len(details['models'])} text models", "installed": True, "available": True}

    def test_connection(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def connect(self) -> dict[str, Any]:
        return self.test_connection()

    def disconnect(self) -> dict[str, Any]:
        return {"ok": True, "state": "authentication_required", "detail": "Disabled in Central Hub; server environment was not changed"}

    def availability(self) -> AgentAvailability:
        status = self.connection_status()
        details = self.list_model_details() if status["state"] == "connected" else self._empty("", "none")
        availability_status = (
            "disabled" if not self.settings.enabled else
            ("available" if status["state"] == "connected" else "unavailable")
        )
        return AgentAvailability(
            self.descriptor.id, self.descriptor.label,
            availability_status,
            status["detail"], bool(status.get("installed")), list(self.descriptor.modes),
            list(details["models"]), str(details["models_source"]),
        )

    def build_argv(self, **_: Any) -> list[str]:
        raise ValueError("OpenAI API adapter does not use CLI argv")
