"""Provider-neutral connection registry for Agent Center adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from hub.agent_center.redact import redact_text


PUBLIC_STATES = {
    "connected": "Connected",
    "authentication_required": "Authentication Required",
    "unavailable": "Unavailable",
    "error": "Error",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentConnectionRegistry:
    """Keeps provider state outside assistant profiles and routes."""

    def __init__(self, adapters: list[Any], store: Any, audit: Callable[..., None] | None = None) -> None:
        self.adapters = {a.descriptor.id: a for a in adapters}
        self.store = store
        self.audit = audit

    def list(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return [self.get(agent_id, refresh=refresh) for agent_id in self.adapters]

    def get(self, agent_id: str, *, refresh: bool = False) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        saved = self.store.get_connection(agent_id)
        if saved.get("disconnected"):
            status = self._base_payload(adapter, "authentication_required", "Disconnected from Central Hub")
        else:
            try:
                if hasattr(adapter, "connection_status"):
                    probe = adapter.connection_status(force_refresh=refresh)
                else:
                    availability = adapter.availability()
                    connected = availability.status in {"available", "degraded"}
                    probe = {
                        "state": "connected" if connected else "unavailable",
                        "detail": availability.detail,
                        "installed": availability.executable_found,
                        "available": connected,
                    }
                status = {**self._base_payload(adapter, "unavailable", "Provider unavailable"), **probe}
            except Exception as exc:
                status = self._base_payload(adapter, "error", redact_text(str(exc), limit=500))
        state = status.get("state") if status.get("state") in PUBLIC_STATES else "error"
        status["state"] = state
        status["status"] = PUBLIC_STATES[state]
        status["last_successful_check"] = saved.get("last_successful_check") or ""
        status["last_check"] = saved.get("last_check") or ""
        status["capabilities"] = adapter.capabilities() if hasattr(adapter, "capabilities") else {
            "modes": list(adapter.descriptor.modes), "streaming": True, "cancel": True,
            "dynamic_models": False, "read_only": True, "file_write": False,
            "command_execution": False, "sql_execution": False, "email_actions": False,
            "repository_runs": False,
        }
        status["authentication_method"] = getattr(adapter, "authentication_method", "")
        status["credential_storage"] = getattr(adapter, "credential_storage", "Provider-managed")
        status["account_label"] = redact_text(str(status.get("account_label") or ""), limit=160)
        return status

    def action(self, agent_id: str, action: str) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        if action not in {"connect", "reconnect", "test", "refresh-models", "disconnect"}:
            raise ValueError("Unsupported connection action")
        try:
            if action in {"connect", "reconnect"}:
                result = adapter.connect()
                self.store.save_connection(agent_id, disconnected=False, last_check=_now(), last_error="")
            elif action == "disconnect":
                result = adapter.disconnect()
                self.store.save_connection(agent_id, disconnected=True, last_check=_now(), last_error="")
            elif action == "refresh-models":
                details = adapter.list_model_details(mode="ask", force_refresh=True)
                error = str(details.get("error") or "")
                result = {
                    "ok": not bool(error),
                    "state": "error" if error else "connected",
                    "detail": error or f"Loaded {len(details.get('models') or [])} models",
                    "models": details.get("models") or [],
                }
            else:
                result = adapter.test_connection()
            state = str(result.get("state") or ("connected" if result.get("ok") else "error"))
            success = bool(result.get("ok")) and state == "connected"
            self.store.save_connection(
                agent_id,
                disconnected=(action == "disconnect"),
                last_check=_now(),
                last_successful_check=_now() if success else None,
                last_error="" if success else redact_text(str(result.get("detail") or ""), limit=500),
            )
        except Exception as exc:
            result = {"ok": False, "state": "error", "detail": redact_text(str(exc), limit=500)}
            self.store.save_connection(agent_id, last_check=_now(), last_error=result["detail"])
        if self.audit:
            self.audit(
                action="AI_CONNECTION_ACTION",
                detail={"provider_id": agent_id, "operation": action, "ok": bool(result.get("ok"))},
            )
        return {"result": result, "connection": self.get(agent_id, refresh=action != "disconnect")}

    def models(self, agent_id: str, *, mode: str, refresh: bool = False) -> dict[str, Any]:
        adapter = self._adapter(agent_id)
        connection = self.get(agent_id)
        if connection["state"] != "connected":
            return {"models": [], "model_details": [], "models_source": "none", "error": connection["detail"]}
        return adapter.list_model_details(mode=mode, force_refresh=refresh)

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
            "available": False,
        }
