"""Standalone AiriX · CLIMATE Chat — separate from the Workspace Assistant."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository


class ClimateChatPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_standalone_chat_pages_are_not_the_editor(self) -> None:
        work = self.client.get("/work/chat").get_data(as_text=True)
        personal = self.client.get("/personal/chat").get_data(as_text=True)
        for html in (work, personal):
            self.assertIn("AiriX · CLIMATE Chat", html)
            self.assertIn("Ask AiriX", html)
            self.assertIn("New Chat", html)
            self.assertIn('id="ax-chat-history"', html)
            self.assertIn('id="ax-prompt"', html)
            self.assertIn('id="ax-stop"', html)
            self.assertIn('id="ax-provider"', html)
            self.assertIn('id="ax-model"', html)
            self.assertIn("climate_chat.js", html)
            self.assertRegex(html, r'"chat":\s*\{[^}]*"default_provider"')
            self.assertRegex(html, r'"workspace":\s*\{[^}]*"default_provider"')
            self.assertNotIn("climate-monaco", html)
            self.assertNotIn("EXPLORER", html)
            self.assertNotRegex(html, r"AIza[0-9A-Za-z_-]{8,}")
            self.assertNotIn('"api_key"', html)
            self.assertNotIn("apiKey", html)

    def test_workspace_assistant_remains_on_climate_routes(self) -> None:
        html = self.client.get("/work/climate").get_data(as_text=True)
        self.assertIn('id="climate-monaco"', html)
        self.assertIn("EXPLORER", html)
        self.assertIn("AiriX · CLIMATE CHAT", html)
        self.assertNotIn('id="ax-chat"', html)

    def test_nav_lists_chat_and_code_workspace(self) -> None:
        work = self.client.get("/work").get_data(as_text=True)
        side = work[work.find('class="sidebar-nav"') : work.find('class="sidebar-actions"')]
        self.assertIn("CLIMATE Chat", side)
        self.assertIn("Code Workspace", side)
        self.assertIn("/work/chat", side)
        self.assertIn("/work/climate", side)
        self.assertLess(side.index(">Dashboard<"), side.index("CLIMATE Chat"))
        self.assertLess(side.index("CLIMATE Chat"), side.index(">Tasks<"))
        self.assertLess(side.index("Settings"), side.index("Code Workspace"))
        personal = self.client.get("/personal").get_data(as_text=True)
        p_side = personal[
            personal.find('class="sidebar-nav"') : personal.find('class="sidebar-actions"')
        ]
        self.assertIn("CLIMATE Chat", p_side)
        self.assertNotIn("Code Workspace", p_side)
        self.assertNotIn("/work/climate", p_side)
        self.assertNotIn("/personal/climate", p_side)

    def test_chat_models_endpoint_does_not_leak_keys(self) -> None:
        resp = self.client.get("/api/climate/work/providers/gemini/models")
        body = resp.get_data(as_text=True)
        self.assertNotIn("AIza", body)
        self.assertNotRegex(body, r"AIza[0-9A-Za-z_-]{8,}")


class ClimateChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentCenterStore(AgentCenterDb(Path(self.tmp.name) / "agent-center.db"))
        self.center = mock.Mock()
        self.center.store = self.store
        self.center.start_run.return_value = {
            "id": "run-chat",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-test-flash",
            "conversation_id": "conversation-chat",
            "prompt": "Reply with exactly: AiriX Gemini connection successful.",
            "repository_ids": [],
        }
        self.adapter = ClimateCodingAdapter(self.center)
        self.adapter.availability = mock.Mock(
            return_value={"id": "gemini", "state": "connected"}
        )
        repo = Repository(
            id="work-repo",
            name="Work",
            type="command",
            enabled=True,
            tags=["work"],
        )
        self.service = ClimateService(
            registry=Registry(repositories=[repo]),
            repository_workspace=mock.Mock(),
            coding=self.adapter,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_chat_does_not_require_a_repository(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="Reply with exactly: AiriX Gemini connection successful.",
            display_prompt="Reply with exactly: AiriX Gemini connection successful.",
        )
        payload = self.center.start_run.call_args.args[0]
        self.assertEqual(payload["repository_ids"], [])
        self.assertEqual(payload["files"], {})
        self.assertEqual(
            payload["display_prompt"],
            "Reply with exactly: AiriX Gemini connection successful.",
        )
        self.assertNotIn("Repository context:", payload["prompt"])
        self.assertEqual(result["id"], "run-chat")
        self.assertEqual(self.service._run_scope["run-chat"], ("work", ""))

    def test_surface_chat_hides_repository_conversations(self) -> None:
        general = self.store.create_conversation(profile_id="okarun", title="General")
        repo_chat = self.store.create_conversation(profile_id="okarun", title="Repo")
        self.store.create_run({
            "mode": "ask",
            "agent_id": "gemini",
            "agent_label": "Gemini",
            "model": "gemini-test-flash",
            "repository_ids": [],
            "prompt": "hello",
            "profile_id": "okarun",
            "conversation_id": general["id"],
        })
        self.store.create_run({
            "mode": "ask",
            "agent_id": "codex",
            "agent_label": "Codex",
            "model": "m",
            "repository_ids": ["work-repo"],
            "prompt": "explain file",
            "profile_id": "okarun",
            "conversation_id": repo_chat["id"],
        })
        rows = self.adapter.conversations(workspace="work", surface="chat")
        titles = [row["title"] for row in rows]
        self.assertIn("General", titles)
        self.assertNotIn("Repo", titles)
        scoped = self.adapter.conversations(workspace="work", repository_id="work-repo")
        scoped_titles = [row["title"] for row in scoped]
        self.assertIn("Repo", scoped_titles)
        self.assertNotIn("General", scoped_titles)
        with self.assertRaises(ClimateCodingError) as caught:
            self.adapter.conversation(repo_chat["id"], workspace="work", surface="chat")
        self.assertEqual(caught.exception.code, "not_found")


class ClimateChatUiContractTests(unittest.TestCase):
    def test_chat_js_requires_exact_model_and_stop(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate_chat.js").read_text(encoding="utf-8")
        self.assertIn("/chat/runs", script)
        self.assertIn("surface=chat", script)
        self.assertIn("display_prompt", script)
        self.assertIn("Select exact model", script)
        self.assertIn("chatSurfaceDefaults", script)
        self.assertIn("preferredChatProvider", script)
        self.assertIn("preferredChatModel", script)
        self.assertIn("listedModelOrAuto", script)
        self.assertNotIn("workspaceSurfaceDefaults", script)
        self.assertNotIn('row.id === "gemini" && row.state === "connected"', script)
        self.assertIn('value="" disabled', script)
        self.assertIn("enhanceChatSelects", script)
        self.assertIn("ClimateSelect", script)
        self.assertIn("requestStop", script)
        self.assertNotIn("include_repo_context: true", script)
        template = (root / "templates" / "climate_chat.html").read_text(encoding="utf-8")
        self.assertIn("Ask AiriX", template)
        self.assertIn("climate_select.js", template)
        self.assertIn("disabled selected>Select exact model", template)
        self.assertNotIn("climate-monaco", template)
        self.assertNotIn("Repository Context", template)
        select_js = (root / "static" / "js" / "climate_select.js").read_text(encoding="utf-8")
        self.assertIn("climate-dd-menu", select_js)
        self.assertIn("is-portal", select_js)
        self.assertIn("is-placeholder", select_js)
        css = (root / "static" / "css" / "climate_chat.css").read_text(encoding="utf-8")
        self.assertIn("color-scheme: dark", css)
        self.assertIn(".ax-pill .climate-dd", css)


if __name__ == "__main__":
    unittest.main()
