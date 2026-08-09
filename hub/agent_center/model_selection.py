"""Provider-agnostic model selection for Agent Center / AiriX.

Ensures UI-selected models are validated and passed through to adapters.
Never silently substitutes a different model for a user selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("hub.agent_center.model_selection")


class SupportsModelResolve(Protocol):
    def list_models(self) -> tuple[list[str], str]: ...

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ModelResolution:
    ok: bool
    selected_model: str
    resolved_model: str
    reason: str
    fallback_reason: str = ""
    error: str = ""
    code: str = ""
    details: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "selected_model": self.selected_model,
            "resolved_model": self.resolved_model,
            "reason": self.reason,
            "fallback_reason": self.fallback_reason,
            "error": self.error,
            "code": self.code,
        }


def provider_configured_default(adapter: Any) -> str:
    """Resolve the provider's configured default dynamically (env/settings/registry)."""
    settings = getattr(adapter, "settings", None)
    default = str(getattr(settings, "default_model", "") or "").strip()
    if default:
        return default
    token = getattr(adapter, "default_model_token", None)
    if token:
        return str(token).strip()
    managed = list(getattr(getattr(adapter, "descriptor", None), "models_managed", None) or [])
    if managed:
        return str(managed[0]).strip()
    return ""


def log_model_selection(
    *,
    agent_id: str,
    selected_model: str,
    resolved_model: str,
    reason: str,
    fallback_reason: str = "",
    provider: str = "",
) -> None:
    logger.info(
        "airix_model_selection agent=%s provider=%s selected=%s resolved=%s reason=%s fallback=%s",
        agent_id or "-",
        provider or "-",
        selected_model or "(none)",
        resolved_model or "(none)",
        reason or "-",
        fallback_reason or "-",
    )


def resolve_model_for_run(
    adapter: Any,
    *,
    agent_id: str,
    mode: str,
    selected_model: str | None,
    force_refresh: bool = True,
    provider_changed: bool = False,
    previous_provider: str = "",
) -> ModelResolution:
    """
    Resolve the model that will be sent to the provider.

    - If the UI selected a model, it must belong to this provider and be available.
    - If unavailable → clear error (no silent substitute).
    - If no selection → provider configured default, else first available from the adapter.
    - When the provider changed (fallback), drop the prior selection and use the new
      provider default (logged as fallback_reason).
    """
    selected = (selected_model or "").strip()
    fallback_reason = ""
    if provider_changed:
        # Prior model belongs to another provider — do not carry it over silently.
        if selected:
            fallback_reason = (
                f"provider_fallback:{previous_provider or 'unknown'}->{agent_id};"
                "dropping_prior_model"
            )
        selected = ""

    provider = str(getattr(getattr(adapter, "descriptor", None), "provider", "") or "")

    if getattr(adapter, "is_api_adapter", False) and hasattr(adapter, "resolve_run_model"):
        resolved = adapter.resolve_run_model(
            mode=mode,
            requested_model=selected or None,
            force_refresh=force_refresh,
        )
        if not resolved.get("ok"):
            err = str(resolved.get("error") or "Model unavailable")
            code = str(resolved.get("code") or "model_unavailable")
            log_model_selection(
                agent_id=agent_id,
                selected_model=selected,
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason or err,
                provider=provider,
            )
            return ModelResolution(
                ok=False,
                selected_model=selected,
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason,
                error=err,
                code=code,
                details=dict(resolved),
            )
        model = str(resolved.get("model") or "").strip()
        reason = str(resolved.get("reason") or ("user_selected" if selected else "provider_default"))
        if selected and model == selected:
            reason = "user_selected"
        elif not selected:
            reason = str(resolved.get("reason") or "provider_default")
        log_model_selection(
            agent_id=agent_id,
            selected_model=selected,
            resolved_model=model,
            reason=reason,
            fallback_reason=fallback_reason,
            provider=provider,
        )
        return ModelResolution(
            ok=True,
            selected_model=selected,
            resolved_model=model,
            reason=reason,
            fallback_reason=fallback_reason,
            details=dict(resolved),
        )

    # CLI / managed adapters
    models, source = adapter.list_models()
    default_token = getattr(adapter, "default_model_token", None)
    configured = provider_configured_default(adapter)
    selectable = [m for m in models if m and not str(m).startswith("__")]

    if selected:
        if default_token and selected in {default_token, "__provider_default__"}:
            # Explicit "use provider default" — omit concrete override at the CLI.
            model = str(default_token)
            reason = "provider_default_token"
        elif models and selected not in models:
            err = f"Model {selected!r} is not offered by provider {agent_id}"
            log_model_selection(
                agent_id=agent_id,
                selected_model=selected,
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason or err,
                provider=provider,
            )
            return ModelResolution(
                ok=False,
                selected_model=selected,
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason,
                error=err,
                code="model_invalid",
            )
        else:
            model = selected
            reason = "user_selected"
    else:
        # Prefer a real discovered model over a bare provider_default token when available.
        if selectable:
            details = {}
            if hasattr(adapter, "list_model_details"):
                try:
                    details = adapter.list_model_details(mode=mode, force_refresh=False) or {}
                except Exception:  # noqa: BLE001
                    details = {}
            recommended = str(details.get("recommended_model") or "").strip()
            if recommended and recommended in models and not recommended.startswith("__"):
                model = recommended
                reason = "provider_recommended"
            elif configured and configured in selectable:
                model = configured
                reason = "provider_configured_default"
            else:
                model = selectable[0]
                reason = "first_available"
                if not fallback_reason:
                    fallback_reason = f"no_selection;source={source}"
        elif default_token:
            model = str(default_token)
            reason = "provider_default_token"
        elif configured and (not models or configured in models):
            model = configured
            reason = "provider_configured_default"
        elif models:
            model = str(models[0])
            reason = "first_available"
            if not fallback_reason:
                fallback_reason = f"no_selection;source={source}"
        else:
            err = f"No model available for provider {agent_id}"
            log_model_selection(
                agent_id=agent_id,
                selected_model="",
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason or err,
                provider=provider,
            )
            return ModelResolution(
                ok=False,
                selected_model="",
                resolved_model="",
                reason="rejected",
                fallback_reason=fallback_reason,
                error=err,
                code="model_unavailable",
            )

    log_model_selection(
        agent_id=agent_id,
        selected_model=selected,
        resolved_model=model,
        reason=reason,
        fallback_reason=fallback_reason,
        provider=provider,
    )
    return ModelResolution(
        ok=True,
        selected_model=selected,
        resolved_model=model,
        reason=reason,
        fallback_reason=fallback_reason,
        details={"models_source": source, "selectable_count": len(selectable)},
    )
