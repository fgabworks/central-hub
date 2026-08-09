"""xAI Grok adapter using the supported Responses and Models APIs."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.models import MODES
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_settings import OpenAISettings, load_openai_settings


class XaiApiAdapter:
    is_api_adapter = True
    authentication_method = "Server-side XAI_API_KEY"
    credential_storage = "Environment only"

    def __init__(self, descriptor: AgentDescriptor | None = None) -> None:
        base = load_openai_settings()
        key = (os.getenv("XAI_API_KEY") or "").strip() or None
        self.settings: OpenAISettings = replace(
            base,
            enabled=bool(key),
            api_key=key,
            base_url=(os.getenv("XAI_BASE_URL") or "https://api.x.ai/v1").rstrip("/"),
            default_model=(os.getenv("XAI_DEFAULT_MODEL") or "").strip(),
            allowed_models=None,
        )
        self.client = OpenAIClient(self.settings)
        self.descriptor = descriptor or AgentDescriptor(
            id="grok", label="Grok", provider="xai_api", executable="", modes=list(MODES)
        )

    def capabilities(self) -> dict[str, Any]:
        return _readonly_capabilities(self.descriptor.modes, api=True)

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not self.settings.api_key:
            return {"state": "authentication_required", "detail": "Set XAI_API_KEY on the server", "installed": True, "available": False}
        try:
            models, _ = self.client.list_model_ids(force_refresh=force_refresh)
        except OpenAIClientError as exc:
            state = "authentication_required" if exc.code in {"auth", "unauthorized"} else "error"
            return {"state": state, "detail": str(exc), "installed": True, "available": False}
        return {"state": "connected", "detail": f"xAI API connected; {len(models)} text models", "installed": True, "available": True}

    def test_connection(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def connect(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def disconnect(self) -> dict[str, Any]:
        return {"ok": True, "state": "authentication_required", "detail": "Disabled in Central Hub; server environment was not changed"}

    def list_models(self) -> tuple[list[str], str]:
        details = self.list_model_details()
        return details["models"], details["models_source"]

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        try:
            ids, source = self.client.list_model_ids(force_refresh=force_refresh)
        except OpenAIClientError as exc:
            return {"models": [], "model_details": [], "models_source": "error", "error": str(exc)}
        rows = [{"id": item, "display_name": item, "availability": "available"} for item in ids]
        return {"models": ids, "model_details": rows, "models_source": source, "recommended_model": self.settings.default_model if self.settings.default_model in ids else (ids[0] if ids else None), "groups": {}, "reasoning_efforts": [], "error": ""}

    def resolve_run_model(self, *, mode: str, requested_model: str | None, force_refresh: bool = True) -> dict[str, Any]:
        details = self.list_model_details(mode=mode, force_refresh=force_refresh)
        models = list(details["models"] or [])
        requested = (requested_model or "").strip()
        if requested and requested not in models:
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": f"Model {requested!r} is not available to this xAI API key",
                "selected_model": requested,
                "resolved_model": "",
            }
        model = requested or details.get("recommended_model")
        if not model or model not in models:
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": details.get("error") or "No Grok models are accessible for this API key",
                "selected_model": requested,
                "resolved_model": "",
            }
        return {
            "ok": True,
            "model": model,
            "reason": "user_selected" if requested else "provider_default",
            "supports_reasoning_effort": False,
            "background": False,
            "is_pro": False,
            "timeout_seconds": self.settings.timeout_seconds,
            "selected_model": requested,
            "resolved_model": model,
            "models_source": details.get("models_source"),
        }

    def availability(self) -> AgentAvailability:
        status = self.connection_status()
        models, source = self.list_models() if status["state"] == "connected" else ([], "none")
        mapped = "available" if status["state"] == "connected" else "unavailable"
        return AgentAvailability(self.descriptor.id, self.descriptor.label, mapped, status["detail"], True, list(self.descriptor.modes), models, source)

    def build_argv(self, **_: Any) -> list[str]:
        raise ValueError("xAI API adapter does not use CLI argv")


def _readonly_capabilities(modes: list[str], *, api: bool = False) -> dict[str, Any]:
    return {
        "modes": list(modes), "streaming": True, "cancel": True, "dynamic_models": True,
        "read_only": True, "api": api, "file_write": False, "command_execution": False,
        "sql_execution": False, "email_actions": False, "repository_runs": False,
    }
