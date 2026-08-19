"""AI Provider Settings: dynamic registry UI and secret-safe APIs."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.adapters.gemini_api import GeminiApiAdapter
from hub.agent_center.connections import AgentConnectionRegistry
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.provider_catalog import (
    decorate_settings_card,
    format_last_check_label,
    public_provider_metadata,
    scrub_public_payload,
)
from hub.agent_center.provider_secrets import (
    dotenv_path,
    load_secrets_into_environ,
    remove_secrets,
    set_secret,
)
from hub.agent_center.provider_settings import ProviderSettingsError, ProviderSettingsService
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore

ROOT = Path(__file__).resolve().parents[1]
SECRET = "hub-test-secret-KEY-9f3a2c1b"
REPLACEMENT = "hub-test-secret-KEY-replaced99"
MALFORMED = "bad-key-leak-XYZ-12345"


class FakeApiAdapter:
    is_api_adapter = True
    credential_type = "api_key"
    env_keys = ("FAKE_PROVIDER_API_KEY",)
    preferred_write_key = "FAKE_PROVIDER_API_KEY"
    enable_when_key_set = False
    settings_help = "Fake provider for settings tests."

    def __init__(self) -> None:
        self.descriptor = AgentDescriptor(
            id="fake-api",
            label="Fake API",
            provider="fake_api",
            executable="",
            modes=["ask"],
        )
        self.settings = None
        self.reload_calls = 0
        self.test_detail = "connected"

    def capabilities(self):
        return {"dynamic_models": True, "read_only": True}

    def reload_settings(self):
        self.reload_calls += 1

    def connection_status(self, *, force_refresh: bool = False):
        del force_refresh
        configured = bool((os.getenv("FAKE_PROVIDER_API_KEY") or "").strip())
        if not configured:
            return {
                "state": "authentication_required",
                "detail": "Set FAKE_PROVIDER_API_KEY on the server",
                "installed": True,
                "available": False,
            }
        return {
            "state": "connected",
            "detail": self.test_detail,
            "installed": True,
            "available": True,
        }

    def test_connection(self):
        status = self.connection_status(force_refresh=True)
        return {"ok": status["state"] == "connected", **status}

    def list_model_details(self, *, mode="ask", force_refresh=False):
        del mode, force_refresh
        return {
            "models": ["fake-model-a", "fake-model-b"],
            "model_details": [],
            "models_source": "discovered",
            "error": "",
        }

    def availability(self):
        status = self.connection_status()
        return AgentAvailability(
            self.descriptor.id,
            self.descriptor.label,
            "available" if status["state"] == "connected" else "unavailable",
            status["detail"],
            True,
            ["ask"],
            ["fake-model-a"],
            "discovered",
        )


class SettingsHub:
    def __init__(self, adapters, store, audit_rows):
        self.adapters = adapters
        self.api_runners = {}
        self.audit_rows = audit_rows
        self.connections = AgentConnectionRegistry(
            adapters, store, audit=lambda **row: audit_rows.append(row)
        )

    def reload_provider_runtime(self, provider_id: str) -> None:
        adapter = self.connections.adapters.get(provider_id)
        if adapter is not None and hasattr(adapter, "reload_settings"):
            adapter.reload_settings()
        self.connections.invalidate(provider_id)

    def audit(self, **row):
        self.audit_rows.append(row)


class ProviderSecretsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secrets = Path(self.temp.name) / "ai_provider_secrets.env"
        self.dotenv = Path(self.temp.name) / ".env"
        self.env = mock.patch.dict(
            os.environ,
            {
                "CENTRAL_HUB_AI_PROVIDER_SECRETS": str(self.secrets),
                "CENTRAL_HUB_DOTENV": str(self.dotenv),
            },
            clear=False,
        )
        self.env.start()
        os.environ.pop("FAKE_PROVIDER_API_KEY", None)

    def tearDown(self):
        os.environ.pop("FAKE_PROVIDER_API_KEY", None)
        self.env.stop()
        self.temp.cleanup()

    def test_set_and_remove_never_returns_secret(self):
        allow = {"FAKE_PROVIDER_API_KEY"}
        set_secret("FAKE_PROVIDER_API_KEY", SECRET, allowlist=allow)
        self.assertEqual(os.environ.get("FAKE_PROVIDER_API_KEY"), SECRET)
        stored = self.secrets.read_text(encoding="utf-8")
        self.assertIn("FAKE_PROVIDER_API_KEY=", stored)
        remove_secrets(["FAKE_PROVIDER_API_KEY"], allowlist=allow)
        self.assertIsNone(os.environ.get("FAKE_PROVIDER_API_KEY"))
        self.assertFalse(self.secrets.read_text(encoding="utf-8").strip())

    def test_remove_strips_allowlisted_dotenv_keys_only(self):
        self.dotenv.write_text(
            "FAKE_PROVIDER_API_KEY=from-dotenv-value\nUNRELATED=keep-me\n",
            encoding="utf-8",
        )
        allow = {"FAKE_PROVIDER_API_KEY"}
        os.environ["FAKE_PROVIDER_API_KEY"] = "from-dotenv-value"
        remove_secrets(["FAKE_PROVIDER_API_KEY"], allowlist=allow)
        text = self.dotenv.read_text(encoding="utf-8")
        self.assertNotIn("FAKE_PROVIDER_API_KEY", text)
        self.assertIn("UNRELATED=keep-me", text)
        self.assertEqual(dotenv_path(), self.dotenv)

    def test_load_secrets_overrides_env(self):
        self.secrets.write_text(f"FAKE_PROVIDER_API_KEY={SECRET}\n", encoding="utf-8")
        os.environ["FAKE_PROVIDER_API_KEY"] = "older-value-xxxxx"
        load_secrets_into_environ()
        self.assertEqual(os.environ.get("FAKE_PROVIDER_API_KEY"), SECRET)

    def test_malformed_key_rejected_without_echo(self):
        with self.assertRaises(ValueError) as ctx:
            set_secret("FAKE_PROVIDER_API_KEY", "nope", allowlist={"FAKE_PROVIDER_API_KEY"})
        self.assertNotIn("nope", str(ctx.exception))


class ProviderSettingsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secrets = Path(self.temp.name) / "ai_provider_secrets.env"
        self.dotenv = Path(self.temp.name) / ".env"
        self.env = mock.patch.dict(
            os.environ,
            {
                "CENTRAL_HUB_AI_PROVIDER_SECRETS": str(self.secrets),
                "CENTRAL_HUB_DOTENV": str(self.dotenv),
            },
            clear=False,
        )
        self.env.start()
        os.environ.pop("FAKE_PROVIDER_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.audit = []
        self.adapter = FakeApiAdapter()
        store = AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "agent.db"))
        self.hub = SettingsHub([self.adapter], store, self.audit)
        self.service = ProviderSettingsService(self.hub)

    def tearDown(self):
        os.environ.pop("FAKE_PROVIDER_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.env.stop()
        self.temp.cleanup()

    def test_metadata_has_no_secret_fields(self):
        os.environ["FAKE_PROVIDER_API_KEY"] = SECRET
        meta = public_provider_metadata(self.adapter)
        blob = json.dumps(meta)
        self.assertNotIn(SECRET, blob)
        self.assertTrue(meta["configured"])
        self.assertEqual(meta["env_keys"], ["FAKE_PROVIDER_API_KEY"])
        self.assertNotIn("api_key", meta)

    def test_set_replace_remove_and_audit_are_secret_safe(self):
        first = self.service.set_key("fake-api", SECRET)
        self.assertTrue(first["configured"])
        self.assertNotIn(SECRET, json.dumps(first))
        self.assertEqual(os.environ.get("FAKE_PROVIDER_API_KEY"), SECRET)
        self.assertEqual(self.adapter.reload_calls, 1)

        replaced = self.service.set_key("fake-api", REPLACEMENT)
        self.assertNotIn(SECRET, json.dumps(replaced))
        self.assertNotIn(REPLACEMENT, json.dumps(replaced))
        self.assertEqual(os.environ.get("FAKE_PROVIDER_API_KEY"), REPLACEMENT)
        self.assertNotIn(SECRET, self.secrets.read_text(encoding="utf-8"))

        removed = self.service.remove_key("fake-api")
        self.assertFalse(removed["configured"])
        self.assertIsNone(os.environ.get("FAKE_PROVIDER_API_KEY"))
        audit_blob = json.dumps(self.audit)
        self.assertNotIn(SECRET, audit_blob)
        self.assertNotIn(REPLACEMENT, audit_blob)

    def test_cli_provider_rejects_key_mutation(self):
        self.adapter.credential_type = "cli"
        with self.assertRaises(ProviderSettingsError):
            self.service.set_key("fake-api", SECRET)

    def test_malformed_connection_error_does_not_leak_key(self):
        self.service.set_key("fake-api", MALFORMED)

        def boom():
            return {
                "ok": False,
                "state": "error",
                "detail": f"provider rejected {MALFORMED}",
                "installed": True,
                "available": False,
            }

        self.adapter.test_connection = boom
        result = self.service.test_connection("fake-api")
        blob = json.dumps(result)
        self.assertNotIn(MALFORMED, blob)
        self.assertFalse(result["ok"])
        self.assertEqual(result["provider"]["status_label"], "Connection failed")
        self.assertIn("Authentication failed", result["provider"]["last_error"])
        self.assertNotIn("provider rejected", blob)

    def test_list_excludes_cli_and_includes_planned_cards(self):
        listed = self.service.list_providers()
        ids = [row["id"] for row in listed]
        self.assertEqual(ids[0], "fake-api")
        self.assertIn("local-models", ids)
        self.assertNotIn("claude-code", ids)
        by_id = {row["id"]: row for row in listed}
        self.assertEqual(by_id["fake-api"]["status_label"], "Not configured")
        self.assertEqual(by_id["fake-api"]["credential_status"], "Missing")
        self.assertEqual(by_id["local-models"]["credential_status"], "Optional")
        self.assertEqual(by_id["local-models"]["status_label"], "Not configured")
        self.assertNotIn("anthropic-api", ids)
        self.assertTrue(by_id["local-models"]["planned"])
        self.assertFalse(by_id["local-models"]["supports_connection_test"])

        gemini_like = decorate_settings_card(
            {
                "configured": False,
                "enabled": False,
                "state": "authentication_required",
                "credential_type": "api_key",
                "credential_required": True,
                "display_name": "Gemini",
            }
        )
        self.assertEqual(gemini_like["status_label"], "Not configured")

    def test_planned_local_models_cannot_store_a_key(self):
        with self.assertRaises(ProviderSettingsError):
            self.service.set_key("anthropic-api", SECRET)
        with self.assertRaises(ProviderSettingsError):
            self.service.set_key("local-models", SECRET)

    def test_connection_success_uses_existing_adapter(self):
        self.service.set_key("fake-api", SECRET)
        result = self.service.test_connection("fake-api")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"]["status_label"], "Connected")
        self.assertEqual(result["provider"]["test_summary"], "Connection successful")
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertGreaterEqual(result["provider"]["models_count"], 2)


class GeminiUnchangedTests(unittest.TestCase):
    def test_settings_reuses_gemini_adapter_and_key_precedence(self):
        adapter = GeminiApiAdapter()
        self.assertEqual(adapter.env_keys, ("GOOGLE_API_KEY", "GEMINI_API_KEY"))
        self.assertEqual(adapter.preferred_write_key, "GEMINI_API_KEY")
        with mock.patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "gemini-key-value", "GOOGLE_API_KEY": "google-key-value"},
            clear=False,
        ):
            adapter.reload_settings()
            self.assertEqual(adapter.settings.api_key, "google-key-value")


class RedactionAndGitignoreTests(unittest.TestCase):
    def test_known_key_patterns_are_redacted(self):
        blob = redact_text(
            "XAI_API_KEY=xai-secretvaluehere GEMINI_API_KEY=secret-value "
            "ANTHROPIC_API_KEY=sk-ant-secretvalue AIzaSyDummyGoogleApiKeyValue0000001"
        )
        self.assertNotIn("xai-secretvaluehere", blob)
        self.assertNotIn("secret-value", blob)
        self.assertNotIn("sk-ant-secretvalue", blob)
        self.assertNotIn("AIzaSyDummyGoogleApiKeyValue0000001", blob)

    def test_secrets_file_is_gitignored(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn("data/", gitignore)
        self.assertIn("ai_provider_secrets.env", gitignore)
        models = (ROOT / "hub" / "agent_center" / "models.py").read_text(encoding="utf-8")
        self.assertIn("ai_provider_secrets.env", models)

    def test_frontend_script_does_not_store_keys(self):
        script = (ROOT / "static" / "js" / "settings_ai_providers.js").read_text(encoding="utf-8")
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
        self.assertIn("clearKeyInput", script)
        self.assertIn("Save Key", (ROOT / "templates" / "settings_ai_providers.html").read_text(encoding="utf-8"))
        self.assertIn("Add Key", (ROOT / "templates" / "settings_ai_providers.html").read_text(encoding="utf-8"))

    def test_last_check_label_is_local_and_secret_free(self):
        label = format_last_check_label("2026-08-17T06:34:00+00:00")
        self.assertTrue(label)
        self.assertNotIn("2026-08-17T", label)
        decorated = decorate_settings_card(
            {
                "configured": True,
                "enabled": True,
                "state": "error",
                "credential_type": "api_key",
                "credential_required": True,
                "last_error": f"raw body {SECRET}",
                "detail": f"provider said {SECRET}",
                "display_name": "Gemini",
            }
        )
        self.assertEqual(decorated["status_label"], "Connection failed")
        self.assertNotIn(SECRET, decorated["last_error"])
        self.assertNotIn(SECRET, decorated["detail"])


class AiProviderSettingsHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.secrets = Path(self.temp.name) / "ai_provider_secrets.env"
        self.dotenv = Path(self.temp.name) / ".env"
        self.saved = {
            key: os.environ.get(key)
            for key in (
                "CENTRAL_HUB_AI_PROVIDER_SECRETS",
                "CENTRAL_HUB_DOTENV",
                "CENTRAL_HUB_AGENT_DATABASE",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "GEMINI_ENABLED",
                "OPENAI_API_KEY",
                "OPENAI_ENABLED",
                "XAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "FAKE_PROVIDER_API_KEY",
            )
        }
        os.environ["CENTRAL_HUB_AI_PROVIDER_SECRETS"] = str(self.secrets)
        os.environ["CENTRAL_HUB_DOTENV"] = str(self.dotenv)
        os.environ["CENTRAL_HUB_AGENT_DATABASE"] = str(Path(self.temp.name) / "agent_center.db")
        from app import create_app

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        svc = self.app.config.get("AGENT_CENTER")
        if svc is not None:
            for provider_id in ("gemini", "openai-api", "grok", "anthropic-api"):
                svc.reload_provider_runtime(provider_id)
        self.temp.cleanup()

    def test_page_and_api_are_registry_driven_and_secret_safe(self):
        page = self.client.get("/settings/ai-providers")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("AI Providers", html)
        self.assertIn("General", html)
        self.assertIn("Add Key", html)
        self.assertIn("Save Key", html)
        self.assertIn("Test Connection", html)
        self.assertIn("Local Models", html)
        self.assertIn("Claude / Anthropic", html)
        self.assertIn("Grok / xAI", html)
        self.assertIn("stored locally on this server", html)
        self.assertNotIn("encrypted", html.lower())
        self.assertIn("data-provider-id=\"gemini\"", html)
        self.assertIn("data-provider-id=\"grok\"", html)
        self.assertIn("data-provider-id=\"openai-api\"", html)
        self.assertIn("data-provider-id=\"anthropic-api\"", html)
        self.assertIn("data-provider-id=\"local-models\"", html)
        self.assertNotIn("data-provider-id=\"claude-code\"", html)
        self.assertNotIn("data-provider-id=\"codex\"", html)
        self.assertNotIn(SECRET, html)
        self.assertNotIn("localStorage", html)

        listed = self.client.get("/api/settings/ai-providers")
        self.assertEqual(listed.status_code, 200)
        payload = listed.get_json()
        ids = [row["id"] for row in payload["providers"]]
        self.assertIn("gemini", ids)
        self.assertIn("grok", ids)
        self.assertIn("openai-api", ids)
        self.assertIn("anthropic-api", ids)
        self.assertIn("local-models", ids)
        self.assertNotIn("claude-code", ids)
        blob = listed.get_data(as_text=True)
        self.assertNotIn('"api_key": "', blob)
        for row in payload["providers"]:
            self.assertNotIn("api_key", row)
            self.assertIn("configured", row)
            self.assertIn("env_keys", row)
            self.assertIn("status_label", row)
            self.assertIn("credential_status", row)

        unique = SECRET
        created = self.client.post(
            "/api/settings/ai-providers/gemini/key",
            json={"api_key": unique},
        )
        self.assertEqual(created.status_code, 200)
        created_text = created.get_data(as_text=True)
        self.assertNotIn(unique, created_text)
        body = created.get_json()
        self.assertTrue(body["provider"]["configured"])
        self.assertNotIn("api_key", body["provider"])

        listed_after = self.client.get("/api/settings/ai-providers")
        self.assertNotIn(unique, listed_after.get_data(as_text=True))

        gemini = self.app.config["AGENT_CENTER"].connections.adapters["gemini"]
        self.assertIsInstance(gemini, GeminiApiAdapter)
        failed = {
            "ok": False,
            "state": "error",
            "detail": f"invalid key {unique}",
            "installed": True,
            "available": False,
        }
        gemini.test_connection = lambda: dict(failed)
        gemini.connection_status = lambda force_refresh=False: dict(failed)
        tested = self.client.post("/api/settings/ai-providers/gemini/test")
        tested_text = tested.get_data(as_text=True)
        self.assertNotIn(unique, tested_text)
        self.assertFalse(tested.get_json().get("ok"))
        self.assertIn("Authentication failed", tested_text)
        self.assertNotIn("invalid key", tested_text)

        anthropic = self.client.post(
            "/api/settings/ai-providers/anthropic-api/key",
            json={"api_key": unique},
        )
        self.assertEqual(anthropic.status_code, 200)
        self.assertNotIn(unique, anthropic.get_data(as_text=True))
        local = self.client.post(
            "/api/settings/ai-providers/local-models/key",
            json={"api_key": unique},
        )
        self.assertEqual(local.status_code, 400)
        self.assertNotIn(unique, local.get_data(as_text=True))

        removed = self.client.delete("/api/settings/ai-providers/gemini/key")
        self.assertEqual(removed.status_code, 200)
        self.assertNotIn(unique, removed.get_data(as_text=True))
        self.assertNotEqual(os.environ.get("GEMINI_API_KEY"), unique)
        self.assertNotEqual(os.environ.get("GOOGLE_API_KEY"), unique)

    def test_cli_key_routes_are_rejected(self):
        resp = self.client.post(
            "/api/settings/ai-providers/claude-code/key",
            json={"api_key": SECRET},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn(SECRET, resp.get_data(as_text=True))

    def test_scrub_drops_secret_fields(self):
        cleaned = scrub_public_payload({"api_key": SECRET, "detail": f"using {SECRET}"})
        self.assertEqual(cleaned["api_key"], "configured")
        self.assertNotIn(SECRET, cleaned["detail"])

    def test_settings_tabs_and_overview_still_render(self):
        overview = self.client.get("/settings")
        self.assertEqual(overview.status_code, 200)
        html = overview.get_data(as_text=True)
        self.assertIn("AI Providers", html)
        self.assertIn("General", html)
        self.assertIn("settings-nav", html)
        self.assertIn("Environment and registry configuration overview.", html)
        self.assertIn("/settings/ai-providers", html)
        self.assertIn("/settings/branding", html)
        self.assertIn("Branding", html)


class FutureProviderPlugInTests(unittest.TestCase):
    def test_new_adapter_metadata_is_enough_for_settings_card(self):
        adapter = FakeApiAdapter()
        adapter.descriptor = AgentDescriptor(
            id="anthropic-api",
            label="Claude API",
            provider="anthropic_api",
            executable="",
            modes=["ask"],
        )
        adapter.env_keys = ("ANTHROPIC_API_KEY",)
        adapter.preferred_write_key = "ANTHROPIC_API_KEY"
        meta = public_provider_metadata(adapter)
        self.assertEqual(meta["id"], "anthropic-api")
        self.assertEqual(meta["display_name"], "Claude API")
        self.assertEqual(meta["env_keys"], ["ANTHROPIC_API_KEY"])
        self.assertTrue(meta["supports_models"])
        self.assertTrue(meta["supports_connection_test"])
        self.assertFalse(meta["configured"])


if __name__ == "__main__":
    unittest.main()
