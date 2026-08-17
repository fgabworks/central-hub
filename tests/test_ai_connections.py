"""Focused tests for provider-neutral AI Connections."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.adapters.cli_common import _safe_cli_env
from hub.agent_center.adapters.claude_code import ClaudeCodeAdapter
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.adapters.cursor_agent import CursorAgentAdapter
from hub.agent_center.connections import AgentConnectionRegistry
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.service import AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, RegistryDefaults


class FakeProvider:
    def __init__(self, agent_id: str, provider: str, *, state: str = "connected") -> None:
        self.descriptor = AgentDescriptor(
            id=agent_id, label=agent_id.title(), provider=provider,
            executable=sys.executable, modes=["ask", "find", "plan", "review"],
        )
        self.state = state
        self.models = [f"{agent_id}-dynamic-a", f"{agent_id}-dynamic-b"]

    def capabilities(self):
        return {
            "modes": list(self.descriptor.modes), "streaming": True, "cancel": True,
            "dynamic_models": True, "read_only": True, "file_write": False,
            "command_execution": False, "sql_execution": False, "email_actions": False,
            "repository_runs": False,
        }

    def connection_status(self, *, force_refresh: bool = False):
        return {
            "state": self.state, "detail": f"state={self.state}", "installed": True,
            "available": self.state == "connected", "account_label": "safe-account",
            "authenticated": self.state == "connected", "version": "1.0.0",
            "executable_path": self.descriptor.executable or self.descriptor.id,
            "cli_commands": [self.descriptor.executable or self.descriptor.id],
        }

    def resolve_executable(self):
        return sys.executable

    def connect(self):
        self.state = "connected"
        return {"ok": True, **self.connection_status()}

    def disconnect(self):
        self.state = "authentication_required"
        return {"ok": True, **self.connection_status()}

    def test_connection(self):
        return {"ok": self.state == "connected", **self.connection_status()}

    def list_model_details(self, *, mode="ask", force_refresh=False):
        return {
            "models": list(self.models),
            "model_details": [{"id": item, "display_name": item} for item in self.models],
            "groups": {}, "recommended_model": self.models[0], "models_source": "discovered",
            "reasoning_efforts": [], "error": "",
        }

    def list_models(self):
        return list(self.models), "discovered"

    def availability(self):
        return AgentAvailability(
            self.descriptor.id, self.descriptor.label,
            "available" if self.state == "connected" else "unavailable",
            f"state={self.state}", True, list(self.descriptor.modes), list(self.models), "discovered",
        )

    def build_argv(self, *, prompt, **_):
        return [sys.executable, "-c", "import sys; print(sys.argv[1])", prompt]


class AiConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AgentCenterStore(AgentCenterDb(Path(self.temp.name) / "agent.db"))
        self.audit = []
        self.codex = FakeProvider("codex", "codex")
        self.claude = FakeProvider("claude-code", "claude_code")
        self.registry = AgentConnectionRegistry(
            [self.codex, self.claude], self.store, audit=lambda **row: self.audit.append(row)
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_connection_and_authentication_states(self):
        self.assertEqual(self.registry.get("codex")["status"], "Connected")
        self.claude.state = "authentication_required"
        self.assertEqual(self.registry.get("claude-code", refresh=True)["status"], "Authentication Required")
        self.claude.state = "unavailable"
        self.assertEqual(self.registry.get("claude-code", refresh=True)["status"], "Unavailable")
        self.claude.state = "error"
        self.assertEqual(self.registry.get("claude-code", refresh=True)["status"], "Error")

    def test_disconnect_is_durable_and_reconnect_clears_it(self):
        disconnected = self.registry.action("codex", "disconnect")["connection"]
        self.assertEqual(disconnected["state"], "authentication_required")
        self.codex.state = "connected"
        self.assertEqual(self.registry.get("codex")["detail"], "Disconnected from Central Hub")
        connected = self.registry.action("codex", "reconnect")["connection"]
        self.assertEqual(connected["state"], "connected")

    def test_dynamic_models_and_refresh(self):
        first = self.registry.models("codex", mode="ask")
        self.assertEqual(first["models"], ["codex-dynamic-a", "codex-dynamic-b"])
        self.codex.models = ["new-account-model"]
        refreshed = self.registry.action("codex", "refresh-models")
        self.assertEqual(refreshed["result"]["models"], ["new-account-model"])

    def test_capabilities_never_advertise_write_or_execution(self):
        for row in self.registry.list():
            caps = row["capabilities"]
            self.assertTrue(caps["read_only"])
            for key in ("file_write", "command_execution", "sql_execution", "email_actions", "repository_runs"):
                self.assertFalse(caps[key])

    def test_audit_contains_no_account_or_secret(self):
        self.registry.action("codex", "test")
        detail = str(self.audit[-1])
        self.assertNotIn("safe-account", detail)
        self.assertNotIn("token", detail.lower())
        self.assertIn("provider_id", detail)

    def test_cli_environment_excludes_secret_bearing_variables(self):
        import os
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "must-not-reach-cli"
        try:
            self.assertNotIn("OPENAI_API_KEY", _safe_cli_env())
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old

    def test_cli_invocations_enforce_provider_read_only_controls(self):
        def descriptor(agent_id, provider, executable):
            return AgentDescriptor(id=agent_id, label=agent_id, provider=provider, executable=executable, modes=["ask"])

        codex = CodexAdapter(descriptor("codex", "codex", "codex"))
        codex.resolve_executable = lambda: sys.executable
        codex_argv = codex.build_argv(mode="ask", prompt="p", model="__provider_default__", cwd=self.temp.name, prompt_file="prompt.txt")
        self.assertIn("read-only", codex_argv)
        self.assertIn("--ephemeral", codex_argv)
        self.assertIn("--json", codex_argv)
        self.assertIn("-C", codex_argv)
        self.assertNotIn("--yolo", codex_argv)
        self.assertNotIn("workspace-write", codex_argv)
        self.assertNotIn("danger-full-access", codex_argv)

        claude = ClaudeCodeAdapter(descriptor("claude-code", "claude_code", sys.executable))
        claude_argv = claude.build_argv(mode="ask", prompt="p", model="__provider_default__", cwd=self.temp.name)
        self.assertIn("--tools", claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--tools") + 1], "")
        self.assertNotIn("--model", claude_argv)

        cursor = CursorAgentAdapter(descriptor("cursor-agent", "cursor_agent", sys.executable))
        cursor._looks_like_editor_cli = lambda _: False
        cursor_argv = cursor.build_argv(mode="ask", prompt="p", model="m", cwd=self.temp.name)
        self.assertIn("--mode=ask", cursor_argv)
        self.assertNotIn("--force", cursor_argv)

    def test_system_page_and_navigation(self):
        from app import create_app
        app = create_app()
        app.config.update(TESTING=True)
        app.config["AGENT_CENTER"].connections = self.registry
        client = app.test_client()
        page = client.get("/system/ai-connections")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("AI Connections", html)
        self.assertIn("Refresh Status", html)
        self.assertIn("AI Defaults", html)
        self.assertIn("CLIMATE Chat", html)
        self.assertIn("Code Workspace", html)
        self.assertIn("Provider Overrides", html)
        self.assertIn('id="chat-default-provider"', html)
        self.assertIn('id="workspace-default-provider"', html)
        self.assertNotIn('id="coding-default-provider"', html)
        self.assertIn("Configure AI providers and default models used across CLIMATE.", html)
        self.assertIn("aic-notice", html)
        self.assertNotIn("agent-safety", html)
        self.assertIn("Save changes", html)
        self.assertIn("Test Connection", html)
        self.assertIn("Manage", html)
        self.assertIn("Reset to defaults", html)
        self.assertIn("CLI", html)
        self.assertIn("ai-provider-key-dialog", html)
        self.assertIn("img/providers/", html)
        row = self.registry.get("codex")
        self.assertEqual(row.get("credential_type"), "cli")
        self.assertEqual(row.get("method_label"), "CLI")
        self.assertNotIn("api_key", row)
        self.assertFalse(any(k for k in row if "secret" in k or k.endswith("_key")))
        providers_at = html.find("aic-providers-title")
        defaults_at = html.find("aic-defaults-title")
        self.assertGreater(providers_at, 0)
        self.assertGreater(defaults_at, providers_at)
        personal = client.get("/personal/aira")
        self.assertIn(b"/system/ai-connections", personal.data)
        self.assertIn(b">Connections<", personal.data)

    def test_ai_connections_exposes_secure_gemini_key_entry(self):
        from app import create_app

        app = create_app()
        app.config.update(TESTING=True)
        client = app.test_client()
        page = client.get("/system/ai-connections")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('data-provider-id="gemini"', html)
        self.assertIn('data-credential-type="api_key"', html)
        self.assertIn('id="ai-provider-key-dialog"', html)
        self.assertIn('id="ai-provider-key-form"', html)
        self.assertIn('type="password"', html)
        self.assertIn("API keys are never exposed after saving", html)

        script = (ROOT / "static" / "js" / "ai_connections.js").read_text(encoding="utf-8")
        self.assertIn('/api/settings/ai-providers/', script)
        self.assertIn('keyInput.value = ""', script)
        self.assertNotIn("localStorage", script)

    def test_refresh_status_and_coding_defaults(self):
        refreshed = self.registry.action("codex", "refresh-status")
        self.assertEqual(refreshed["connection"]["state"], "connected")
        self.assertIn("codex-dynamic-a", refreshed["connection"].get("models") or [])
        defaults = self.registry.set_coding_defaults(
            default_provider="claude-code",
            default_models={"claude-code": "claude-sonnet", "codex": "codex-mini"},
        )
        self.assertEqual(defaults["default_provider"], "claude-code")
        self.assertEqual(defaults["default_models"]["claude-code"], "claude-sonnet")
        self.assertEqual(self.registry.coding_defaults()["default_models"]["codex"], "codex-mini")
        self.assertEqual(defaults["workspace"]["default_provider"], "claude-code")
        self.assertEqual(defaults["chat"]["default_provider"], "")
        split = self.registry.set_coding_defaults(
            chat={"default_provider": "gemini", "default_model": "gemini-flash"},
            workspace={"default_provider": "codex", "default_model": "gpt-5"},
        )
        self.assertEqual(split["chat"]["default_provider"], "gemini")
        self.assertEqual(split["chat"]["default_model"], "gemini-flash")
        self.assertEqual(split["workspace"]["default_provider"], "codex")
        self.assertEqual(split["workspace"]["default_model"], "gpt-5")
        self.assertEqual(split["default_provider"], "codex")
        self.assertEqual(split["default_models"]["claude-code"], "claude-sonnet")
        with self.assertRaises(ValueError):
            self.registry.set_coding_defaults(default_provider="not-a-provider")
        with self.assertRaises(ValueError):
            self.registry.set_coding_defaults(chat={"default_provider": "not-a-provider"})

    def test_legacy_coding_defaults_migrate_to_both_surfaces(self):
        self.store.set_pref("coding_default_provider", "codex")
        self.store.set_pref("coding_default_model:codex", "codex-mini")
        defaults = self.registry.coding_defaults()
        self.assertEqual(defaults["chat"]["default_provider"], "codex")
        self.assertEqual(defaults["workspace"]["default_provider"], "codex")
        self.assertEqual(defaults["chat"]["default_model"], "")
        self.assertEqual(defaults["workspace"]["default_model"], "")
        self.assertEqual(defaults["default_models"]["codex"], "codex-mini")
        self.registry.set_coding_defaults(
            chat={"default_provider": "gemini", "default_model": "gemini-flash"},
        )
        after = self.registry.coding_defaults()
        self.assertEqual(after["chat"]["default_provider"], "gemini")
        self.assertEqual(after["workspace"]["default_provider"], "codex")
        self.assertEqual(after["default_models"]["codex"], "codex-mini")

    def test_provider_switching_is_preserved_per_run(self):
        service = AgentCenterService(
            Registry(repositories=[], defaults=RegistryDefaults()),
            store=self.store, adapters=[self.codex, self.claude], timeout_seconds=10,
        )
        with patch.object(service, "resolve_repository_ids", return_value=[]):
            first = service.start_run({"profile_id": "aira", "agent_id": "codex", "model": "codex-dynamic-a", "mode": "ask", "prompt": "first"})
            second = service.start_run({"profile_id": "aira", "agent_id": "claude-code", "model": "claude-code-dynamic-b", "mode": "review", "prompt": "second"})
        deadline = time.time() + 5
        while time.time() < deadline:
            if service.get_run(first["id"], profile_id="aira")["status"] in {"completed", "failed"} and service.get_run(second["id"], profile_id="aira")["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        history = service.history(profile_id="aira")
        by_id = {row["id"]: row for row in history}
        self.assertEqual(by_id[first["id"]]["agent_id"], "codex")
        self.assertEqual(by_id[first["id"]]["model"], "codex-dynamic-a")
        self.assertEqual(by_id[second["id"]]["agent_id"], "claude-code")
        self.assertEqual(by_id[second["id"]]["mode"], "review")
        self.assertEqual(service.history(profile_id="okarun"), [])


if __name__ == "__main__":
    unittest.main()
