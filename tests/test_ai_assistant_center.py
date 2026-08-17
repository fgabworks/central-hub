"""Isolation and safety tests for Aira and Okarun."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.adapters.cli_common import BaseCliAdapter
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.openai_tools import AgentToolsContext, execute_tool, tool_definitions
from hub.agent_center.profiles import get_profile
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.audit.store import AuditStore
from hub.registry.models import Registry, Repository


class AvailableAdapter(BaseCliAdapter):
    def availability(self) -> AgentAvailability:
        return AgentAvailability(
            id="test-agent",
            label="Test Agent",
            status="available",
            detail="available",
            executable_found=True,
            modes=["ask", "find", "plan", "review"],
            models=["test-model"],
            models_source="managed",
        )


class ScopeNotebook:
    def __init__(self) -> None:
        self.scopes: list[str | None] = []

    def search(self, **kwargs):
        self.scopes.append(kwargs.get("scope"))
        return [{"id": "n1", "title": "Scoped", "scope": kwargs.get("scope")}]


class AssistantCenterIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        repo = root / "repo"
        repo.mkdir()
        (repo / "AGENTS.md").write_text("# Work rules\nRead only.\n", encoding="utf-8")
        (repo / "README.md").write_text("work repository\n", encoding="utf-8")
        (repo / ".env").write_text("API_KEY=do-not-load\n", encoding="utf-8")
        self.registry = Registry(
            [
                Repository(
                    id="work-repo",
                    name="Work Repo",
                    type="command",
                    enabled=True,
                    local_path=str(repo),
                )
            ]
        )
        self.store = AgentCenterStore(AgentCenterDb(root / "assistant.db"))
        adapter = AvailableAdapter(
            AgentDescriptor(
                id="test-agent",
                label="Test Agent",
                provider="generic",
                executable="python",
                modes=["ask", "find", "plan", "review"],
                models_managed=["test-model"],
            )
        )
        self.service = AgentCenterService(self.registry, store=self.store, adapters=[adapter])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_profile_scope_and_minimal_context_routing(self):
        aira = self.service.preview_context(
            {
                "profile_id": "aira",
                "mode": "ask",
                "prompt": "personal task",
                "tool_ids": ["notebook_lookup", "sql_lookup", "repo_search"],
            }
        )
        self.assertTrue(aira["ok"])
        self.assertEqual(aira["repository_ids"], [])
        self.assertEqual(aira["tools"]["enabled"], ["notebook_lookup"])
        self.assertIn("work_repositories", aira["excluded_sources"])
        self.assertNotIn("Work rules", aira["packed_prompt"])

        okarun = self.service.preview_context(
            {
                "profile_id": "okarun",
                "repository_ids": ["work-repo"],
                "mode": "review",
                "prompt": "review readme",
                "tool_ids": ["repo_search", "read_file"],
            }
        )
        self.assertTrue(okarun["ok"])
        self.assertIn("Work rules", okarun["packed_prompt"])
        self.assertNotIn("do-not-load", okarun["packed_prompt"])
        self.assertEqual(okarun["tools"]["enabled"], ["repo_search", "read_file"])

    def test_separate_history_summaries_and_cross_profile_denial(self):
        aira_conversation = self.store.create_conversation(profile_id="aira", title="Personal")
        okarun_conversation = self.store.create_conversation(profile_id="okarun", title="Work")
        self.store.update_conversation_summary(
            aira_conversation["id"], profile_id="aira", summary="Personal summary"
        )
        self.store.update_conversation_summary(
            okarun_conversation["id"], profile_id="okarun", summary="Work summary"
        )
        run = self.store.create_run(
            {
                "profile_id": "aira",
                "conversation_id": aira_conversation["id"],
                "status": "completed",
                "mode": "ask",
                "agent_id": "test-agent",
                "prompt": "private personal prompt",
            }
        )
        self.assertEqual(len(self.service.history(profile_id="aira")), 1)
        self.assertEqual(self.service.history(profile_id="okarun"), [])
        self.assertEqual(len(self.store.list_conversations(profile_id="aira")), 1)
        self.assertEqual(len(self.store.list_conversations(profile_id="okarun")), 1)
        with self.assertRaises(AgentCenterError):
            self.service.get_run(run["id"], profile_id="okarun")

    def test_tool_capability_filtering_and_forced_workspace(self):
        aira = get_profile("aira")
        names = {item["name"] for item in tool_definitions(set(aira.allowed_tools))}
        self.assertEqual(names, set(aira.allowed_tools))
        self.assertTrue(
            names.isdisjoint(
                {"repo_search", "read_file", "sql_execute", "jobs_lookup", "audit_lookup"}
            )
        )
        notebook = ScopeNotebook()
        ctx = AgentToolsContext(
            registry=self.registry,
            repository_ids=[],
            profile_id="aira",
            workspace="personal",
            allowed_tools={"notebook_lookup"},
            notebook=notebook,
        )
        result = json.loads(
            execute_tool("notebook_lookup", {"search": "x", "scope": "work"}, ctx)
        )
        self.assertEqual(result["notes"][0]["scope"], "personal")
        blocked = json.loads(execute_tool("sql_lookup", {"search": "x"}, ctx))
        self.assertIn("not allowlisted", blocked["error"])

    def test_navigation_and_audit_redaction(self):
        from app import create_app

        app = create_app()
        app.config["AGENT_CENTER"] = self.service
        client = app.test_client()
        personal = client.get("/personal/aira")
        work = client.get("/work/airix")
        self.assertEqual(personal.status_code, 200)
        self.assertIn(b">Aira<", personal.data)
        # Personal nav must not link to SQL Workspace (activity-rail placeholder may mention it).
        self.assertNotIn(b'href="/sql"', personal.data)
        self.assertEqual(work.status_code, 200)
        self.assertIn(b">Workspace Assistant<", work.data)
        self.assertIn(b'href="/sql"', work.data)

        audit = AuditStore(Path(self.temp.name) / "audit.jsonl")
        audit.append(action="TEST", detail="API_KEY=super-secret")
        blob = json.dumps(audit.list_recent())
        self.assertNotIn("super-secret", blob)
        self.assertIn("redacted", blob)


if __name__ == "__main__":
    unittest.main()
