"""Settings service for dynamic AI provider credentials and connection tests."""

from __future__ import annotations

from typing import Any

from hub.agent_center.provider_catalog import (
    catalog_allowlist,
    credential_type_for,
    decorate_settings_card,
    env_keys_for,
    managed_env_keys,
    planned_provider_spec,
    planned_settings_card,
    PLANNED_PROVIDER_SPECS,
    preferred_write_key,
    public_provider_card,
    public_provider_metadata,
    scrub_public_payload,
)
from hub.agent_center.provider_secrets import (
    remove_secrets,
    set_flag,
    set_secret,
)


class ProviderSettingsError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class ProviderSettingsService:
    def __init__(self, agent_center: Any) -> None:
        self.agent_center = agent_center

    def _adapters(self) -> list[Any]:
        return list(self.agent_center.connections.adapters.values())

    def _adapter(self, provider_id: str) -> Any:
        adapter = self.agent_center.connections.adapters.get(provider_id)
        if adapter is None:
            raise ProviderSettingsError("Unknown provider", code="not_found")
        return adapter

    def _audit(self, action: str, **detail: Any) -> None:
        audit = getattr(self.agent_center, "audit", None)
        if not audit:
            return
        audit(action=action, detail=scrub_public_payload(detail))

    def list_providers(self, *, probe: bool = False) -> list[dict[str, Any]]:
        cards = []
        seen: set[str] = set()
        for adapter in self._adapters():
            if credential_type_for(adapter) != "api_key":
                continue
            connection = self.agent_center.connections.get(
                adapter.descriptor.id, probe=probe, refresh=False
            )
            card = public_provider_card(adapter, connection)
            cards.append(card)
            seen.add(card["id"])
        for spec in PLANNED_PROVIDER_SPECS:
            if spec["id"] in seen:
                continue
            cards.append(planned_settings_card(spec))
        return scrub_public_payload(cards)

    def get_provider(self, provider_id: str, *, probe: bool = False) -> dict[str, Any]:
        adapter = self.agent_center.connections.adapters.get(provider_id)
        if adapter is not None:
            connection = self.agent_center.connections.get(provider_id, probe=probe, refresh=probe)
            return public_provider_card(adapter, connection)
        spec = planned_provider_spec(provider_id)
        if spec is None:
            raise ProviderSettingsError("Unknown provider", code="not_found")
        return planned_settings_card(spec)

    def set_key(self, provider_id: str, api_key: str) -> dict[str, Any]:
        adapter = self.agent_center.connections.adapters.get(provider_id)
        spec = None if adapter is not None else planned_provider_spec(provider_id)
        if adapter is None and spec is None:
            raise ProviderSettingsError("Unknown provider", code="not_found")
        cred_type = credential_type_for(adapter) if adapter is not None else str(spec.get("credential_type") or "")
        if cred_type != "api_key":
            raise ProviderSettingsError(
                "This provider does not use a server-side API key",
                code="credential_unsupported",
            )
        if adapter is not None:
            meta = public_provider_metadata(adapter)
            target = next(iter(meta["configured_env_keys"]), "") or preferred_write_key(adapter)
            enabled_env = str(meta.get("enabled_env") or "")
            enable_when_key_set = getattr(adapter, "enable_when_key_set", True)
        else:
            target = str(spec.get("preferred_write_key") or "")
            enabled_env = ""
            enable_when_key_set = False
        if not target:
            raise ProviderSettingsError("Provider has no credential key", code="credential_unsupported")
        allowlist = catalog_allowlist(self._adapters())
        set_secret(target, api_key, allowlist=allowlist)
        if enabled_env and enable_when_key_set:
            set_flag(enabled_env, True, allowlist=allowlist)
        if adapter is not None:
            self.agent_center.reload_provider_runtime(provider_id)
        self._audit(
            "AI_PROVIDER_KEY_SET",
            provider_id=provider_id,
            env_key=target,
            configured=True,
        )
        return self.get_provider(provider_id, probe=False)

    def remove_key(self, provider_id: str) -> dict[str, Any]:
        adapter = self.agent_center.connections.adapters.get(provider_id)
        spec = None if adapter is not None else planned_provider_spec(provider_id)
        if adapter is None and spec is None:
            raise ProviderSettingsError("Unknown provider", code="not_found")
        cred_type = credential_type_for(adapter) if adapter is not None else str(spec.get("credential_type") or "")
        if cred_type != "api_key":
            raise ProviderSettingsError(
                "This provider does not use a server-side API key",
                code="credential_unsupported",
            )
        allowlist = catalog_allowlist(self._adapters())
        names = managed_env_keys(adapter) if adapter is not None else list(spec.get("env_keys") or ())
        dotenv_keys = env_keys_for(adapter) if adapter is not None else list(spec.get("env_keys") or ())
        remove_secrets(names, allowlist=allowlist, dotenv_keys=dotenv_keys)
        if adapter is not None:
            self.agent_center.reload_provider_runtime(provider_id)
        self._audit("AI_PROVIDER_KEY_REMOVE", provider_id=provider_id, configured=False)
        return self.get_provider(provider_id, probe=False)

    def test_connection(self, provider_id: str) -> dict[str, Any]:
        adapter = self.agent_center.connections.adapters.get(provider_id)
        if adapter is None:
            spec = planned_provider_spec(provider_id)
            if spec is None:
                raise ProviderSettingsError("Unknown provider", code="not_found")
            raise ProviderSettingsError("Connection test is not available", code="unsupported")
        if not hasattr(adapter, "test_connection"):
            raise ProviderSettingsError("Connection test is not available", code="unsupported")
        result = self.agent_center.connections.action(provider_id, "test")
        inner = result.get("result") or {}
        connection = result.get("connection") or {}
        public = self.get_provider(provider_id, probe=False)
        models = list(connection.get("models") or public.get("models") or [])
        ok = bool(inner.get("ok")) and str(inner.get("state") or "") == "connected"
        state = "connected" if ok else ("error" if public.get("configured") else public.get("state"))
        decorated = decorate_settings_card(
            {
                **public,
                "state": state,
                "configured": bool(public.get("configured")),
                "enabled": bool(public.get("enabled", True)),
                "models_count": len(models),
                "models": models[:24],
                "last_check": str(connection.get("last_check") or public.get("last_check") or ""),
                "last_test_ok": ok,
            }
        )
        return scrub_public_payload({"ok": ok, "provider": decorated})
