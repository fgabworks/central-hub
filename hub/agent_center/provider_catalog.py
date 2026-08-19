"""Public AI provider settings metadata derived from the adapter registry.

Secret values are never included.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from hub.agent_center.provider_secrets import configured_env_keys, current_secret_values
from hub.agent_center.redact import redact_text
from hub.settings import _as_bool

_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "credentials",
    "x_goog_api_key",
    "x-goog-api-key",
}

_ERROR_FIELDS = {"detail", "last_error", "error", "models_error", "message"}

# Future-ready Settings cards. A real adapter with the same id replaces the stub.
PLANNED_PROVIDER_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "local-models",
        "display_name": "Local Models",
        "mark": "L",
        "provider": "local",
        "credential_type": "none",
        "credential_required": False,
        "env_keys": (),
        "preferred_write_key": "",
        "supports_models": False,
        "supports_connection_test": False,
        "help": "Use local models through Ollama or LM Studio.",
        "blurb": "Use local models through Ollama or LM Studio.",
        "planned": True,
    },
)


def credential_type_for(adapter: Any) -> str:
    explicit = str(getattr(adapter, "credential_type", "") or "").strip()
    if explicit:
        return explicit
    if getattr(adapter, "is_api_adapter", False):
        return "api_key"
    return "cli"


def env_keys_for(adapter: Any) -> list[str]:
    return [str(item).strip() for item in (getattr(adapter, "env_keys", ()) or []) if str(item).strip()]


def preferred_write_key(adapter: Any) -> str:
    explicit = str(getattr(adapter, "preferred_write_key", "") or "").strip()
    if explicit:
        return explicit
    keys = env_keys_for(adapter)
    return keys[0] if keys else ""


def enabled_env_for(adapter: Any) -> str:
    return str(getattr(adapter, "enabled_env", "") or "").strip()


def managed_env_keys(adapter: Any) -> list[str]:
    keys = list(dict.fromkeys(env_keys_for(adapter) + ([enabled_env_for(adapter)] if enabled_env_for(adapter) else [])))
    return keys


def catalog_allowlist(adapters: list[Any]) -> set[str]:
    allowed: set[str] = set()
    for adapter in adapters:
        allowed.update(managed_env_keys(adapter))
    for spec in PLANNED_PROVIDER_SPECS:
        allowed.update(str(item) for item in (spec.get("env_keys") or ()) if str(item).strip())
    return allowed


def public_provider_metadata(adapter: Any) -> dict[str, Any]:
    capabilities = adapter.capabilities() if hasattr(adapter, "capabilities") else {}
    cred_type = credential_type_for(adapter)
    env_keys = env_keys_for(adapter)
    enabled_env = enabled_env_for(adapter)
    descriptor = adapter.descriptor
    configured_keys = configured_env_keys(env_keys)
    enabled = bool(getattr(descriptor, "enabled", True))
    if enabled_env:
        default = bool(configured_keys) if getattr(adapter, "enabled_defaults_to_key", False) else False
        enabled = _as_bool(os.getenv(enabled_env), default=default)
    return {
        "id": descriptor.id,
        "display_name": str(getattr(adapter, "settings_display_name", "") or descriptor.label),
        "mark": str(getattr(adapter, "settings_mark", "") or (descriptor.label[:1] if descriptor.label else "?")),
        "blurb": str(
            getattr(adapter, "settings_blurb", "")
            or getattr(adapter, "settings_help", "")
            or descriptor.notes
            or ""
        )[:400],
        "provider": descriptor.provider,
        "enabled": enabled,
        "credential_type": cred_type,
        "credential_required": cred_type == "api_key",
        "env_keys": env_keys,
        "preferred_write_key": preferred_write_key(adapter),
        "enabled_env": enabled_env,
        "supports_models": bool(capabilities.get("dynamic_models")),
        "supports_connection_test": hasattr(adapter, "test_connection"),
        "help": str(getattr(adapter, "settings_help", "") or descriptor.notes or "")[:400],
        "configured": bool(configured_keys),
        "configured_env_keys": configured_keys,
        "connections_url": "/system/ai-connections" if cred_type == "cli" else "",
        "planned": False,
    }


def public_provider_card(adapter: Any, connection: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = public_provider_metadata(adapter)
    row = dict(connection or {})
    models = [str(item) for item in (row.get("models") or []) if str(item).strip()]
    detail = str(row.get("detail") or "")
    last_error = str(row.get("last_error") or row.get("models_error") or "")
    state = str(row.get("state") or ("connected" if meta["configured"] else "authentication_required"))
    if meta["credential_type"] == "api_key":
        if state == "connected":
            status_label = "Connected"
        elif meta["configured"]:
            status_label = "Configured"
        else:
            status_label = "Not configured"
        credential_status = "Configured" if meta["configured"] else "Missing"
    else:
        status_label = str(row.get("summary_label") or row.get("status") or PUBLIC_STATE_LABEL.get(state, "Unknown"))
        credential_status = "CLI managed"
    card = {
        **meta,
        "state": state,
        "status_label": status_label,
        "credential_status": credential_status,
        "detail": detail,
        "models_count": len(models),
        "models": models[:24],
        "last_check": str(row.get("last_check") or ""),
        "last_successful_check": str(row.get("last_successful_check") or ""),
        "last_error": last_error,
    }
    return scrub_public_payload(decorate_settings_card(card))


PUBLIC_STATE_LABEL = {
    "connected": "Connected",
    "authentication_required": "Not configured",
    "unavailable": "Unavailable",
    "error": "Connection failed",
}


def planned_provider_spec(provider_id: str) -> dict[str, Any] | None:
    for spec in PLANNED_PROVIDER_SPECS:
        if spec["id"] == provider_id:
            return dict(spec)
    return None


def planned_settings_card(spec: dict[str, Any]) -> dict[str, Any]:
    env_keys = [str(item) for item in (spec.get("env_keys") or ()) if str(item).strip()]
    configured = bool(configured_env_keys(env_keys))
    required = bool(spec.get("credential_required"))
    cred_type = str(spec.get("credential_type") or "none")
    card = {
        "id": spec["id"],
        "display_name": spec["display_name"],
        "mark": spec.get("mark") or spec["display_name"][:1],
        "blurb": spec.get("blurb") or spec.get("help") or "",
        "provider": spec.get("provider") or spec["id"],
        "enabled": True,
        "credential_type": cred_type,
        "credential_required": required,
        "env_keys": env_keys,
        "preferred_write_key": spec.get("preferred_write_key") or (env_keys[0] if env_keys else ""),
        "enabled_env": "",
        "supports_models": bool(spec.get("supports_models")),
        "supports_connection_test": bool(spec.get("supports_connection_test")),
        "help": spec.get("help") or "",
        "configured": configured,
        "configured_env_keys": configured_env_keys(env_keys),
        "connections_url": "",
        "planned": True,
        "state": "authentication_required",
        "models_count": 0,
        "models": [],
        "last_check": "",
        "last_successful_check": "",
        "last_error": "",
        "detail": "",
    }
    return scrub_public_payload(decorate_settings_card(card))


def decorate_settings_card(card: dict[str, Any]) -> dict[str, Any]:
    """Normalize Settings-card labels without exposing raw provider errors."""
    configured = bool(card.get("configured"))
    enabled = bool(card.get("enabled", True))
    state = str(card.get("state") or "")
    models_count = int(card.get("models_count") or 0)
    cred_type = str(card.get("credential_type") or "")
    required = bool(card.get("credential_required", cred_type == "api_key"))

    if cred_type == "none" or not required:
        credential_status = "Optional"
    elif configured:
        credential_status = "Configured"
    else:
        credential_status = "Missing"

    if not enabled and configured:
        status_label = "Disabled"
        models_label = "—"
        test_summary = ""
        safe_detail = ""
        last_error = ""
    elif not enabled and cred_type != "api_key":
        status_label = "Disabled"
        models_label = "—"
        test_summary = ""
        safe_detail = ""
        last_error = ""
    elif state == "connected":
        status_label = "Connected"
        models_label = f"{models_count} available" if models_count else "—"
        test_summary = "Connection successful"
        safe_detail = f"{models_count} models available" if models_count else "Connection successful"
        last_error = ""
    elif configured and state in {"error", "unavailable"}:
        status_label = "Connection failed"
        models_label = "Unavailable"
        test_summary = "Authentication failed"
        safe_detail = "Authentication failed. Check the saved API key and try again."
        last_error = safe_detail
    else:
        status_label = "Not configured"
        models_label = "—"
        test_summary = ""
        safe_detail = ""
        last_error = ""

    card["status_label"] = status_label
    card["credential_status"] = credential_status
    card["models_label"] = models_label
    card["test_summary"] = test_summary
    card["detail"] = safe_detail
    card["last_error"] = last_error
    card["last_check_label"] = format_last_check_label(str(card.get("last_check") or ""))
    card["mark"] = str(card.get("mark") or (str(card.get("display_name") or "?")[:1]))
    card["blurb"] = str(card.get("blurb") or card.get("help") or "")
    return card


def format_last_check_label(raw: str) -> str:
    """Safe display timestamp such as 'Today, 2:34 PM'. Never includes secrets."""
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        return text[:16].replace("T", " ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone()
    now = datetime.now(local.tzinfo)
    time_part = local.strftime("%I:%M %p").lstrip("0")
    if local.date() == now.date():
        return f"Today, {time_part}"
    if local.date() == now.date() - timedelta(days=1):
        return f"Yesterday, {time_part}"
    return f"{local.strftime('%b %d, %Y')}, {time_part}"


def scrub_public_payload(payload: Any, *, secrets: set[str] | None = None) -> Any:
    """Drop or redact secret values from any settings API payload."""
    blocked = set(secrets or ())
    blocked.update(current_secret_values())
    _harvest_secrets(payload, blocked)
    return _scrub(payload, blocked)


def _harvest_secrets(payload: Any, secrets: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower().replace("-", "_")
            if lowered in _SECRET_FIELD_NAMES and isinstance(value, str) and len(value.strip()) >= 8:
                secrets.add(value.strip())
            _harvest_secrets(value, secrets)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _harvest_secrets(item, secrets)


def _scrub(payload: Any, secrets: set[str], *, field: str = "") -> Any:
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            lowered = str(key).lower().replace("-", "_")
            if lowered in _SECRET_FIELD_NAMES:
                present = bool(value) and str(value) not in {"missing", "false", "0", ""}
                out[key] = "configured" if present else "missing"
                continue
            out[key] = _scrub(value, secrets, field=lowered)
        return out
    if isinstance(payload, list):
        return [_scrub(item, secrets, field=field) for item in payload]
    if isinstance(payload, tuple):
        return [_scrub(item, secrets, field=field) for item in payload]
    if isinstance(payload, str):
        text = payload
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, "[redacted]")
        if field in _ERROR_FIELDS:
            text = redact_text(text, limit=400)
        return text
    return payload
