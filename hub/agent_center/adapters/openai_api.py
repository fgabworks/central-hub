"""OpenAI API adapter for Prompting & Agent Center."""

from __future__ import annotations

from typing import Any

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.models import MODES
from hub.agent_center.openai_catalog import (
    GROUP_ORDER,
    REASONING_EFFORTS,
    build_grouped_models,
    catalog_ids,
    get_spec,
    intersect_accessible,
    recommend_model_id,
)
from hub.agent_center.openai_client import OpenAIClient, OpenAIClientError
from hub.agent_center.openai_settings import OpenAISettings, load_openai_settings


class OpenAIApiAdapter:
    """Responses API adapter — curated catalog ∩ GET /v1/models."""

    is_api_adapter = True

    def __init__(
        self,
        descriptor: AgentDescriptor | None = None,
        *,
        settings: OpenAISettings | None = None,
        client: OpenAIClient | None = None,
    ) -> None:
        self.settings = settings or load_openai_settings()
        self.client = client or OpenAIClient(self.settings)
        self.descriptor = descriptor or AgentDescriptor(
            id="openai-api",
            label="OpenAI API",
            provider="openai_api",
            executable="",
            modes=list(MODES),
            models_managed=[],
            enabled=True,
            notes="OpenAI Responses API with curated Hub catalog. Edit/terminal/SQL exec disabled.",
        )
        self._last_list_error: str = ""

    def list_models(self) -> tuple[list[str], str]:
        """Accessible curated model IDs only."""
        details = self.list_model_details(mode="ask")
        return list(details.get("models") or []), str(details.get("models_source") or "none")

    def list_model_details(
        self,
        *,
        mode: str = "ask",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Rich model payload for UI: groups, recommendations, reasoning support."""
        empty = {
            "models": [],
            "model_details": [],
            "groups": {g: [] for g in GROUP_ORDER},
            "recommended_model": None,
            "recommendation_reason": "none",
            "models_source": "none",
            "reasoning_efforts": list(REASONING_EFFORTS),
            "catalog_ids": list(catalog_ids()),
            "error": "",
        }
        if not self.settings.enabled:
            empty["models_source"] = "disabled"
            return empty
        if not self.settings.api_key:
            empty["models_source"] = "none"
            empty["error"] = "OPENAI_API_KEY is missing"
            return empty

        try:
            api_ids, source = self.client.list_model_ids(force_refresh=force_refresh)
            self._last_list_error = ""
        except OpenAIClientError as exc:
            self._last_list_error = str(exc)
            empty["models_source"] = "error"
            empty["error"] = str(exc)
            # Never advertise catalog models we could not verify for this key.
            return empty

        accessible_specs = intersect_accessible(
            api_ids,
            allowed=self.settings.allowed_models,
        )
        accessible_ids = [s.id for s in accessible_specs]
        recommended, reason = recommend_model_id(
            mode,
            accessible_ids,
            default_model=self.settings.default_model,
        )
        groups = build_grouped_models(
            accessible_specs,
            mode=mode,
            recommended_id=recommended,
        )
        return {
            "models": accessible_ids,
            "model_details": [s.public_dict(availability="available") for s in accessible_specs],
            "groups": groups,
            "recommended_model": recommended,
            "recommendation_reason": reason,
            "models_source": source,
            "reasoning_efforts": list(REASONING_EFFORTS),
            "catalog_ids": list(catalog_ids()),
            "error": "",
        }

    def resolve_run_model(
        self,
        *,
        mode: str,
        requested_model: str | None,
        force_refresh: bool = True,
    ) -> dict[str, Any]:
        """Revalidate availability and resolve model + run options before a run."""
        details = self.list_model_details(mode=mode, force_refresh=force_refresh)
        accessible = set(details.get("models") or [])
        requested = (requested_model or "").strip()
        model_id, reason = recommend_model_id(
            mode,
            accessible,
            user_override=requested or None,
            default_model=self.settings.default_model,
        )
        if requested and reason == "override_unavailable":
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": (
                    f"Model {requested!r} is not accessible with this API key "
                    "(unavailable, restricted, or not in the Hub catalog)."
                ),
                "model": None,
            }
        if not model_id:
            return {
                "ok": False,
                "code": "model_unavailable",
                "error": details.get("error")
                or "No curated OpenAI models are accessible for this API key.",
                "model": None,
            }

        spec = get_spec(model_id)
        is_pro = bool(spec and spec.is_pro)
        supports_effort = bool(spec and spec.supports_reasoning_effort)
        timeout = (
            self.settings.pro_model_timeout_seconds
            if is_pro
            else self.settings.timeout_seconds
        )
        return {
            "ok": True,
            "code": "ok",
            "error": "",
            "model": model_id,
            "reason": reason,
            "is_pro": is_pro,
            "supports_reasoning_effort": supports_effort,
            "background": is_pro,
            "timeout_seconds": float(timeout),
            "spec": spec.public_dict(availability="available") if spec else None,
            "models_source": details.get("models_source"),
        }

    def availability(self) -> AgentAvailability:
        desc = self.descriptor
        details = self.list_model_details(mode="ask")
        models = list(details.get("models") or [])
        source = str(details.get("models_source") or "none")
        if not self.settings.enabled:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="disabled",
                detail="OPENAI_ENABLED is false",
                executable_found=False,
                modes=list(MODES),
                models=models,
                models_source=source,
            )
        if not self.settings.api_key:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="unavailable",
                detail="OPENAI_API_KEY is missing",
                executable_found=False,
                modes=list(MODES),
                models=models,
                models_source=source,
            )
        if source == "error":
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="degraded",
                detail=details.get("error") or "OpenAI model list failed",
                executable_found=True,
                modes=list(MODES),
                models=[],
                models_source=source,
            )
        if not models:
            return AgentAvailability(
                id=desc.id,
                label=desc.label,
                status="degraded",
                detail=(
                    "No curated Hub models are accessible for this API key. "
                    "Grant access or adjust OPENAI_ALLOWED_MODELS."
                ),
                executable_found=True,
                modes=list(MODES),
                models=[],
                models_source=source,
            )
        status = "available" if source.startswith("discovered") or source.startswith("cache") else "degraded"
        detail = f"OpenAI curated catalog · {len(models)} accessible · source={source}"
        if self.settings.default_model:
            detail += f" · default={self.settings.default_model}"
        return AgentAvailability(
            id=desc.id,
            label=desc.label,
            status=status,
            detail=detail,
            executable_found=True,
            modes=list(MODES),
            models=models,
            models_source=source,
        )

    def build_argv(self, *, mode: str, prompt: str, model: str, cwd: str, prompt_file: str = "") -> list[str]:
        raise ValueError("OpenAI API adapter does not use CLI argv")
