"""Provider-neutral connection registry for Agent Center adapters."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.agent_center.provider_catalog import credential_type_for, env_keys_for
from hub.agent_center.provider_secrets import configured_env_keys, redact_known_secrets
from hub.agent_center.redact import redact_text
from hub.climate.execution_mode import CLIMATE_ASSISTED, coerce_execution_mode
from hub.perf import TtlCache, coalesce, record_external, timed


PUBLIC_STATES = {
    "connected": "Connected",
    "authentication_required": "Authentication Required",
    "unavailable": "Unavailable",
    "error": "Error",
}

# API chat providers used by CLIMATE Chat / Code Workspace (ASK-only, no CLI).
API_CHAT_PROVIDER_IDS = ("gemini", "openai-api", "anthropic-api", "grok")

# Providers surfaced in CLIMATE's compact provider/model controls. The legacy
# constant name is retained because settings/API callers already depend on it.
CODING_CLI_PROVIDER_IDS = (
    *API_CHAT_PROVIDER_IDS,
    "codex",
    "claude-code",
    "cursor-agent",
)

PREF_DEFAULT_PROVIDER = "coding_default_provider"
PREF_DEFAULT_MODEL_PREFIX = "coding_default_model:"
PREF_CHAT_PROVIDER = "chat_default_provider"
PREF_CHAT_MODEL = "chat_default_model"
PREF_CHAT_MODE = "chat_default_mode"
PREF_WORKSPACE_PROVIDER = "workspace_default_provider"
PREF_WORKSPACE_MODEL = "workspace_default_model"
PREF_WORKSPACE_MODE = "workspace_default_mode"
PREF_SURFACE_DEFAULTS_V2 = "ai_surface_defaults_v2"

_STATUS_TTL = float(os.getenv("CENTRAL_HUB_AI_CONNECTION_CACHE_TTL", "60"))
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentConnectionRegistry:
    """Keeps provider state outside assistant profiles and routes."""

    def __init__(self, adapters: list[Any], store: Any, audit: Callable[..., None] | None = None) -> None:
        self.adapters = {a.descriptor.id: a for a in adapters}
        self.store = store
        self.audit = audit
        self._status_cache = TtlCache(ttl_seconds=_STATUS_TTL)

    def list(self, *, refresh: bool = False, probe: bool = True) -> list[dict[str, Any]]:
        return [self.get(agent_id, refresh=refresh, probe=probe) for agent_id in self.adapters]

    def get(self, agent_id: str, *, refresh: bool = False, probe: bool = True) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        saved = self.store.get_connection(agent_id)
        cache_key = f"status:{agent_id}"

        if not refresh:
            cached = self._status_cache.get(cache_key)
            if cached is not None:
                return dict(cached)
            if not probe:
                return self._cached_or_placeholder(adapter, saved)

        if saved.get("disconnected"):
            status = self._base_payload(adapter, "authentication_required", "Disconnected from Central Hub")
            # Keep install/version facts when possible without implying authenticated.
            if hasattr(adapter, "resolve_executable"):
                try:
                    exe = adapter.resolve_executable()
                except Exception:  # noqa: BLE001
                    exe = None
                status["installed"] = bool(exe)
                if exe and hasattr(adapter, "_detect_version"):
                    try:
                        status["version"] = adapter._detect_version(exe)
                    except Exception:  # noqa: BLE001
                        status["version"] = ""
            if hasattr(adapter, "_cli_command_candidates"):
                try:
                    status["cli_commands"] = list(adapter._cli_command_candidates())
                except Exception:  # noqa: BLE001
                    status["cli_commands"] = []
            if hasattr(adapter, "_install_help"):
                try:
                    status["install_help"] = str(adapter._install_help() or "")
                except Exception:  # noqa: BLE001
                    status["install_help"] = ""
            status["authenticated"] = False
            status["available"] = False
        else:
            try:
                with timed("ai_connection_probe", provider=agent_id, refresh=refresh):
                    start = time.perf_counter()

                    def _probe() -> dict[str, Any]:
                        if hasattr(adapter, "connection_status"):
                            return adapter.connection_status(force_refresh=refresh)
                        availability = adapter.availability()
                        connected = availability.status in {"available", "degraded"}
                        return {
                            "state": "connected" if connected else "unavailable",
                            "detail": availability.detail,
                            "installed": availability.executable_found,
                            "available": connected,
                        }

                    probe_result = coalesce(f"ai-probe:{agent_id}", _probe)
                    record_external(
                        (time.perf_counter() - start) * 1000.0,
                        name=f"ai_probe_{agent_id}",
                    )
                status = {**self._base_payload(adapter, "unavailable", "Provider unavailable"), **probe_result}
            except Exception as exc:
                status = self._base_payload(adapter, "error", redact_text(str(exc), limit=500))
        status = self._finalize(adapter, status, saved)
        self._status_cache.set(cache_key, status)
        return dict(status)

    def list_coding_clis(
        self, *, refresh: bool = False, probe: bool = True, include_models: bool = False
    ) -> list[dict[str, Any]]:
        """Compact status rows for CLIMATE chat/coding providers."""
        rows = []
        for agent_id in CODING_CLI_PROVIDER_IDS:
            if agent_id not in self.adapters:
                continue
            row = self.get(agent_id, refresh=refresh, probe=probe)
            if include_models and row.get("state") == "connected":
                try:
                    details = self.models(agent_id, mode="ask", refresh=False)
                    row["models"] = list(details.get("models") or [])
                    row["model_details"] = list(details.get("model_details") or [])
                    row["models_source"] = details.get("models_source") or "none"
                    row["models_error"] = str(details.get("error") or "")
                except Exception as exc:  # noqa: BLE001
                    row["models"] = []
                    row["model_details"] = []
                    row["models_source"] = "error"
                    row["models_error"] = redact_text(str(exc), limit=240)
            else:
                row.setdefault("models", [])
                row.setdefault("model_details", [])
                row.setdefault("models_source", "none")
                row.setdefault("models_error", "")
            rows.append(row)
        return rows

    def coding_defaults(self) -> dict[str, Any]:
        self._ensure_surface_defaults_migrated()
        models: dict[str, str] = {}
        for agent_id in CODING_CLI_PROVIDER_IDS:
            models[agent_id] = self.store.get_pref(f"{PREF_DEFAULT_MODEL_PREFIX}{agent_id}", "").strip()
        chat = {
            "default_provider": self._clean_provider(self.store.get_pref(PREF_CHAT_PROVIDER, "")),
            "default_model": self.store.get_pref(PREF_CHAT_MODEL, "").strip(),
            "default_mode": self._clean_mode(self.store.get_pref(PREF_CHAT_MODE, "")),
        }
        workspace = {
            "default_provider": self._clean_provider(self.store.get_pref(PREF_WORKSPACE_PROVIDER, "")),
            "default_model": self.store.get_pref(PREF_WORKSPACE_MODEL, "").strip(),
            "default_mode": self._clean_mode(self.store.get_pref(PREF_WORKSPACE_MODE, "")),
        }
        return {
            # Legacy alias: Code Workspace provider. Chat is never implied by this field.
            "default_provider": workspace["default_provider"],
            "default_models": models,
            "providers": list(CODING_CLI_PROVIDER_IDS),
            "chat": chat,
            "workspace": workspace,
        }

    def set_coding_defaults(
        self,
        *,
        default_provider: str | None = None,
        default_models: dict[str, str] | None = None,
        chat: dict[str, Any] | None = None,
        workspace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_surface_defaults_migrated()
        if chat is not None:
            self._set_surface_defaults(PREF_CHAT_PROVIDER, PREF_CHAT_MODEL, PREF_CHAT_MODE, chat)
        if workspace is not None:
            provider = self._set_surface_defaults(
                PREF_WORKSPACE_PROVIDER, PREF_WORKSPACE_MODEL, PREF_WORKSPACE_MODE, workspace
            )
            if "default_provider" in workspace:
                self.store.set_pref(PREF_DEFAULT_PROVIDER, provider)
        elif default_provider is not None:
            # Legacy callers update Code Workspace only; Chat stays independent.
            value = self._clean_provider(default_provider, required=True)
            self.store.set_pref(PREF_WORKSPACE_PROVIDER, value)
            self.store.set_pref(PREF_DEFAULT_PROVIDER, value)
        if default_models:
            for agent_id, model in default_models.items():
                if agent_id not in CODING_CLI_PROVIDER_IDS:
                    raise ValueError(f"Unknown coding provider: {agent_id}")
                self.store.set_pref(f"{PREF_DEFAULT_MODEL_PREFIX}{agent_id}", str(model or "").strip())
        if self.audit:
            self.audit(
                action="AI_CODING_DEFAULTS_UPDATE",
                detail={"defaults": self.coding_defaults()},
            )
        return self.coding_defaults()

    def _ensure_surface_defaults_migrated(self) -> None:
        if self.store.get_pref(PREF_SURFACE_DEFAULTS_V2, "").strip() == "1":
            return
        legacy_provider = self._clean_provider(self.store.get_pref(PREF_DEFAULT_PROVIDER, ""))
        self.store.set_pref(PREF_CHAT_PROVIDER, legacy_provider)
        self.store.set_pref(PREF_WORKSPACE_PROVIDER, legacy_provider)
        self.store.set_pref(PREF_CHAT_MODEL, "")
        self.store.set_pref(PREF_WORKSPACE_MODEL, "")
        self.store.set_pref(PREF_CHAT_MODE, CLIMATE_ASSISTED)
        self.store.set_pref(PREF_WORKSPACE_MODE, CLIMATE_ASSISTED)
        self.store.set_pref(PREF_SURFACE_DEFAULTS_V2, "1")

    def _set_surface_defaults(self, provider_key: str, model_key: str, mode_key: str, payload: Any) -> str:
        if not isinstance(payload, dict):
            raise ValueError("Surface defaults must be an object")
        provider = self.store.get_pref(provider_key, "")
        if "default_provider" in payload:
            provider = self._clean_provider(payload.get("default_provider"), required=True)
            self.store.set_pref(provider_key, provider)
        if "default_model" in payload:
            self.store.set_pref(model_key, str(payload.get("default_model") or "").strip())
        if "default_mode" in payload:
            self.store.set_pref(mode_key, coerce_execution_mode(payload.get("default_mode")))
        return self._clean_provider(provider)

    def _clean_mode(self, value: Any) -> str:
        try:
            return coerce_execution_mode(value)
        except ValueError:
            return CLIMATE_ASSISTED

    def _clean_provider(self, value: Any, *, required: bool = False) -> str:
        provider = str(value or "").strip()
        if not provider:
            return ""
        if provider not in CODING_CLI_PROVIDER_IDS:
            if required:
                raise ValueError("Unknown default coding provider")
            return ""
        return provider

    def action(self, agent_id: str, action: str) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        normalized = "reconnect" if action in {"reauthenticate", "re-auth", "reauth"} else action
        if normalized == "sign-out":
            normalized = "disconnect"
        if normalized in {"refresh-status", "refresh_status", "status"}:
            self._status_cache.invalidate(f"status:{agent_id}")
            connection = self.get(agent_id, refresh=True, probe=True)
            if connection.get("state") == "connected":
                try:
                    details = self.models(agent_id, mode="ask", refresh=True)
                    connection["models"] = list(details.get("models") or [])
                    connection["model_details"] = list(details.get("model_details") or [])
                    connection["models_source"] = details.get("models_source") or "none"
                    connection["models_error"] = str(details.get("error") or "")
                except Exception as exc:  # noqa: BLE001
                    connection["models"] = []
                    connection["models_error"] = redact_text(str(exc), limit=240)
            result = {
                "ok": connection.get("state") == "connected",
                "state": connection.get("state"),
                "detail": connection.get("detail") or "Status refreshed",
            }
            if self.audit:
                self.audit(
                    action="AI_CONNECTION_ACTION",
                    detail={"provider_id": agent_id, "operation": "refresh-status", "ok": bool(result.get("ok"))},
                )
            return {"result": result, "connection": connection}
        if normalized not in {"connect", "reconnect", "test", "refresh-models", "disconnect"}:
            raise ValueError("Unsupported connection action")
        try:
            if normalized in {"connect", "reconnect"}:
                result = adapter.connect()
                self.store.save_connection(agent_id, disconnected=False, last_check=_now(), last_error="")
            elif normalized == "disconnect":
                result = adapter.disconnect()
                self.store.save_connection(agent_id, disconnected=True, last_check=_now(), last_error="")
            elif normalized == "refresh-models":
                details = adapter.list_model_details(mode="ask", force_refresh=True)
                error = str(details.get("error") or "")
                result = {
                    "ok": not bool(error),
                    "state": "error" if error else "connected",
                    "detail": error or f"Loaded {len(details.get('models') or [])} models",
                    "models": details.get("models") or [],
                    "model_details": details.get("model_details") or [],
                }
            else:
                result = adapter.test_connection()
            state = str(result.get("state") or ("connected" if result.get("ok") else "error"))
            success = bool(result.get("ok")) and state == "connected"
            self.store.save_connection(
                agent_id,
                disconnected=(normalized == "disconnect"),
                last_check=_now(),
                last_successful_check=_now() if success else None,
                last_error="" if success else redact_known_secrets(str(result.get("detail") or ""), limit=500),
            )
        except Exception as exc:
            result = {"ok": False, "state": "error", "detail": redact_known_secrets(str(exc), limit=500)}
            self.store.save_connection(agent_id, last_check=_now(), last_error=result["detail"])
            normalized = action
        self._status_cache.invalidate(f"status:{agent_id}")
        if self.audit:
            self.audit(
                action="AI_CONNECTION_ACTION",
                detail={"provider_id": agent_id, "operation": normalized, "ok": bool(result.get("ok"))},
            )
        connection = self.get(agent_id, refresh=normalized != "disconnect")
        if connection.get("state") == "connected" or normalized == "refresh-models":
            models = list(result.get("models") or [])
            if models:
                connection["models"] = models
                connection["model_details"] = list(result.get("model_details") or [])
            elif connection.get("state") == "connected":
                try:
                    details = self.models(agent_id, mode="ask", refresh=normalized == "refresh-models")
                    connection["models"] = list(details.get("models") or [])
                    connection["model_details"] = list(details.get("model_details") or [])
                    connection["models_source"] = details.get("models_source") or "none"
                    connection["models_error"] = str(details.get("error") or "")
                except Exception:  # noqa: BLE001
                    connection.setdefault("models", [])
        return {"result": result, "connection": connection}

    def models(self, agent_id: str, *, mode: str, refresh: bool = False) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        connection = self.get(agent_id, probe=not refresh, refresh=refresh)
        if connection["state"] != "connected":
            return {"models": [], "model_details": [], "models_source": "none", "error": connection["detail"]}
        return adapter.list_model_details(mode=mode, force_refresh=refresh)

    def invalidate(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._status_cache.invalidate()
        else:
            self._status_cache.invalidate(f"status:{agent_id}")

    def _cached_or_placeholder(self, adapter: Any, saved: dict[str, Any]) -> dict[str, Any]:
        """Instant status from memory/store — never probes providers."""
        stale, fresh = self._status_cache.peek(f"status:{adapter.descriptor.id}")
        if stale is not None:
            payload = dict(stale)
            payload["from_cache"] = True
            payload["cache_fresh"] = fresh
            return payload
        if saved.get("disconnected"):
            state = "authentication_required"
            detail = "Disconnected from Central Hub"
        elif saved.get("last_error"):
            state = "error"
            detail = str(saved.get("last_error") or "Last check failed")
        elif saved.get("last_successful_check"):
            state = "connected"
            detail = "Cached connection status (refreshing in background)"
        else:
            state = "authentication_required"
            detail = "Not checked yet — status loads in the background"
        status = self._finalize(
            adapter,
            self._base_payload(adapter, state, detail),
            saved,
        )
        # Cheap PATH check for SSR/cache-miss so Missing CLI is accurate without auth probe.
        if hasattr(adapter, "resolve_executable"):
            try:
                status["installed"] = bool(adapter.resolve_executable())
            except Exception:  # noqa: BLE001
                status["installed"] = False
        if hasattr(adapter, "_cli_command_candidates"):
            try:
                status["cli_commands"] = list(adapter._cli_command_candidates())
            except Exception:  # noqa: BLE001
                status["cli_commands"] = status.get("cli_commands") or []
        if hasattr(adapter, "_install_help") and not status["installed"]:
            try:
                status["install_help"] = str(adapter._install_help() or "")
            except Exception:  # noqa: BLE001
                pass
        status["summary_label"] = _summary_label(status)
        status["primary_action"] = _primary_action(status)
        _attach_presentation(adapter, status)
        status["from_cache"] = True
        status["cache_fresh"] = False
        status["pending_refresh"] = True
        return status

    def _finalize(self, adapter: Any, status: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
        state = status.get("state") if status.get("state") in PUBLIC_STATES else "error"
        status["state"] = state
        status["status"] = PUBLIC_STATES[state]
        status["last_successful_check"] = saved.get("last_successful_check") or ""
        status["last_check"] = saved.get("last_check") or ""
        status["installed"] = bool(status.get("installed"))
        status["authenticated"] = (
            bool(status.get("authenticated")) if "authenticated" in status else state == "connected"
        )
        status["version"] = redact_text(str(status.get("version") or ""), limit=80)
        status["error_code"] = str(status.get("error_code") or "")
        status["available"] = bool(status.get("available")) if "available" in status else state == "connected"
        status["capabilities"] = adapter.capabilities() if hasattr(adapter, "capabilities") else {
            "modes": list(adapter.descriptor.modes), "streaming": True, "cancel": True,
            "dynamic_models": False, "read_only": True, "file_write": False,
            "command_execution": False, "sql_execution": False, "email_actions": False,
            "repository_runs": False,
        }
        status["authentication_method"] = getattr(adapter, "authentication_method", "")
        status["credential_storage"] = getattr(adapter, "credential_storage", "Provider-managed")
        status["account_label"] = redact_text(str(status.get("account_label") or ""), limit=160)
        exe_path = str(status.get("executable_path") or "")
        if not exe_path and hasattr(adapter, "resolve_executable"):
            try:
                exe_path = str(adapter.resolve_executable() or "")
            except Exception:  # noqa: BLE001
                exe_path = ""
        status["executable_path"] = redact_text(exe_path, limit=240)
        commands = status.get("cli_commands")
        if not commands and hasattr(adapter, "_cli_command_candidates"):
            try:
                commands = list(adapter._cli_command_candidates())
            except Exception:  # noqa: BLE001
                commands = []
        if not commands and adapter.descriptor.executable:
            commands = [adapter.descriptor.executable]
        status["cli_commands"] = [str(c) for c in (commands or []) if str(c).strip()]
        help_text = str(status.get("install_help") or "")
        if not help_text and hasattr(adapter, "_install_help"):
            try:
                help_text = str(adapter._install_help() or "")
            except Exception:  # noqa: BLE001
                help_text = ""
        status["install_help"] = help_text
        status.setdefault("runtime_health", "")
        status.setdefault("runtime_complete", False)
        status.setdefault("discovery_source", "")
        status.setdefault("host_path", "")
        status.setdefault("models", [])
        status.setdefault("model_details", [])
        status.setdefault("models_source", "none")
        status.setdefault("models_error", "")
        status["summary_label"] = _summary_label(status)
        status["primary_action"] = _primary_action(status)
        _attach_presentation(adapter, status)
        return status

    def _adapter(self, agent_id: str) -> Any:
        adapter = self.adapters.get(agent_id)
        if adapter is None:
            raise KeyError(f"Unknown provider: {agent_id}")
        return adapter

    @staticmethod
    def _base_payload(adapter: Any, state: str, detail: str) -> dict[str, Any]:
        return {
            "id": adapter.descriptor.id,
            "label": adapter.descriptor.label,
            "provider": adapter.descriptor.provider,
            "state": state,
            "detail": detail,
            "account_label": "",
            "installed": False,
            "authenticated": False,
            "version": "",
            "available": False,
            "error_code": "",
            "cli_commands": [],
            "install_help": "",
            "executable_path": "",
            "models": [],
            "summary_label": "",
            "primary_action": "connect",
        }


def _attach_presentation(adapter: Any, status: dict[str, Any]) -> None:
    """UI-only facts derived from the adapter. Never includes secret values."""
    cred_type = credential_type_for(adapter)
    status["credential_type"] = cred_type
    status["method_label"] = "API Key" if cred_type == "api_key" else "CLI"
    status["vendor"] = str(getattr(adapter, "settings_vendor", "") or "")
    logo = str(getattr(adapter, "settings_logo", "") or "").strip()
    if not logo:
        logo = f"img/providers/{adapter.descriptor.id}.svg"
    logo = logo.replace("\\", "/").lstrip("/")
    status["logo"] = logo if (_STATIC_DIR / logo).is_file() else ""
    status["key_configured"] = (
        bool(configured_env_keys(env_keys_for(adapter))) if cred_type == "api_key" else False
    )
    state = str(status.get("state") or "")
    if state == "connected":
        status["ui_status"] = "connected"
        status["ui_status_label"] = "Connected"
    elif state == "error":
        status["ui_status"] = "error"
        status["ui_status_label"] = "Error"
    elif (not status.get("installed")) or state == "unavailable":
        status["ui_status"] = "offline"
        status["ui_status_label"] = "Offline"
    else:
        status["ui_status"] = "available"
        status["ui_status_label"] = "Available"


def _summary_label(status: dict[str, Any]) -> str:
    if not status.get("installed"):
        return "Missing CLI"
    if status.get("state") == "connected" and status.get("authenticated"):
        return "Connected"
    if status.get("state") == "error":
        return "Error"
    if status.get("installed") and not status.get("authenticated"):
        return "Not connected"
    return str(status.get("status") or "Unavailable")


def _primary_action(status: dict[str, Any]) -> str:
    if not status.get("installed"):
        return "install_help"
    if status.get("state") == "connected":
        return "test"
    return "connect"
