"""Focused tests for Prompting & Agent Center."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.base import AgentAvailability, AgentDescriptor
from hub.agent_center.adapters.cli_common import BaseCliAdapter
from hub.agent_center.context_builder import build_context_preview
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import redact_text
from hub.agent_center.secrets import filter_safe_paths, is_secret_path
from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry, RegistryDefaults, Repository


class EchoAdapter(BaseCliAdapter):
    """Test adapter that runs a harmless python print via allowlisted argv."""

    def availability(self) -> AgentAvailability:
        return AgentAvailability(
            id=self.descriptor.id,
            label=self.descriptor.label,
            status="available",
            detail="test echo adapter",
            executable_found=True,
            modes=list(self.descriptor.modes),
            models=list(self.descriptor.models_managed),
            models_source="managed",
        )

    def _default_template(self, mode: str) -> list[str]:
        return [sys.executable, "-c", "import sys; print(sys.argv[1])", "{prompt}"]


class UnavailableAdapter(BaseCliAdapter):
    def availability(self) -> AgentAvailability:
        return AgentAvailability(
            id=self.descriptor.id,
            label=self.descriptor.label,
            status="unavailable",
            detail="Executable not found on PATH: missing-agent",
            executable_found=False,
            modes=list(self.descriptor.modes),
            models=list(self.descriptor.models_managed),
            models_source="managed",
        )


def _repo(tmp: Path, repo_id: str = "demo-repo") -> Repository:
    (tmp / "AGENTS.md").write_text("# Agent rules\nDo not write secrets.\n", encoding="utf-8")
    (tmp / "README.md").write_text("Demo repository for agent center.\n", encoding="utf-8")
    (tmp / "hub_mod.py").write_text("def hello():\n    return 'ok'\n", encoding="utf-8")
    (tmp / ".env").write_text("SECRET_TOKEN=should-never-appear\n", encoding="utf-8")
    (tmp / "credentials.json").write_text('{"token":"x"}\n', encoding="utf-8")
    return Repository(
        id=repo_id,
        name="Demo Repo",
        type="command",
        enabled=True,
        description="test",
        local_path=str(tmp),
        working_directory=str(tmp),
    )


class AgentCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.repo_path = self.tmp / "repo"
        self.repo_path.mkdir()
        self.repo = _repo(self.repo_path)
        self.registry = Registry(
            repositories=[
                self.repo,
                Repository(
                    id="api-only",
                    name="API Only",
                    type="api",
                    enabled=True,
                    base_url="http://127.0.0.1:9",
                ),
            ],
            defaults=RegistryDefaults(),
        )
        self.db = AgentCenterDb(self.tmp / "agent.db")
        self.store = AgentCenterStore(self.db)
        self.audits: list[dict] = []

        def audit(**kwargs):
            self.audits.append(kwargs)

        echo = EchoAdapter(
            AgentDescriptor(
                id="echo",
                label="Echo",
                provider="generic",
                executable="python",
                modes=["find", "ask", "plan", "review"],
                models_managed=["echo-1", "echo-2"],
            )
        )
        missing = UnavailableAdapter(
            AgentDescriptor(
                id="missing",
                label="Missing Agent",
                provider="generic",
                executable="missing-agent",
                modes=["ask", "plan"],
                models_managed=["m1"],
            )
        )
        self.svc = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=[echo, missing],
            audit=audit,
            timeout_seconds=30,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_agent_availability_and_capability_filtering(self):
        agents = self.svc.list_agents()
        by_id = {a["id"]: a for a in agents}
        self.assertTrue(by_id["echo"]["runnable"])
        self.assertEqual(by_id["echo"]["status"], "available")
        self.assertFalse(by_id["missing"]["runnable"])
        ask_only = self.svc.list_agents(mode="ask")
        self.assertTrue(any(a["id"] == "echo" and a["runnable"] for a in ask_only))
        modes = self.svc.list_modes()
        enabled = {m["id"] for m in modes if m["enabled"]}
        disabled = {m["id"]: m for m in modes if not m["enabled"]}
        self.assertEqual(enabled, {"find", "ask", "plan", "review"})
        self.assertIn("edit", disabled)
        self.assertEqual(disabled["edit"]["note"], "Not yet available")

    def test_model_list_and_managed_fallback(self):
        models = self.svc.list_models("echo")
        self.assertEqual(models["models"], ["echo-1", "echo-2"])
        self.assertEqual(models["models_source"], "managed")
        with self.assertRaises(AgentCenterError):
            self.svc.list_models("nope")

    def test_single_and_multiple_repository_scope(self):
        other = self.tmp / "repo2"
        other.mkdir()
        (other / "AGENTS.md").write_text("# Other\n", encoding="utf-8")
        (other / "note.md").write_text("second repo note\n", encoding="utf-8")
        repo2 = _repo(other, "demo-repo-2")
        self.registry = Registry(
            repositories=[self.repo, repo2, self.registry.get("api-only")],
            defaults=RegistryDefaults(),
        )
        self.svc.registry = self.registry

        preview_one = self.svc.preview_context(
            {"repository_ids": ["demo-repo"], "mode": "ask", "prompt": "hello hub_mod"}
        )
        self.assertTrue(preview_one["ok"])
        self.assertEqual(preview_one["repository_ids"], ["demo-repo"])

        preview_multi = self.svc.preview_context(
            {
                "repository_ids": ["demo-repo", "demo-repo-2"],
                "mode": "plan",
                "prompt": "compare AGENTS",
            }
        )
        self.assertTrue(preview_multi["ok"])
        self.assertEqual(set(preview_multi["repository_ids"]), {"demo-repo", "demo-repo-2"})
        self.assertGreaterEqual(len(preview_multi["roots"]), 2)

        empty = self.svc.preview_context(
            {"profile_id": "okarun", "repository_ids": [], "mode": "ask", "prompt": "hello", "tool_ids": []}
        )
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["repository_ids"], [])
        self.assertEqual(empty["missing_repository_ids"], [])
        self.assertEqual(empty["scope_errors"], [])

        with self.assertRaises(AgentCenterError):
            self.svc.start_run(
                {
                    "repository_ids": ["api-only"],
                    "mode": "ask",
                    "agent_id": "echo",
                    "prompt": "nope",
                }
            )

    def test_secret_exclusion_and_instruction_loading(self):
        self.assertTrue(is_secret_path(".env"))
        self.assertTrue(is_secret_path("credentials.json"))
        self.assertTrue(is_secret_path(self.repo_path / ".env", repo_root=self.repo_path))
        self.assertFalse(is_secret_path("README.md"))
        safe = filter_safe_paths(["README.md", ".env", "hub_mod.py", "secrets/token.txt"])
        self.assertEqual(safe, ["README.md", "hub_mod.py"])

        preview = build_context_preview(
            self.registry,
            repository_ids=["demo-repo"],
            mode="ask",
            prompt="inspect hub_mod",
            explicit_files={"demo-repo": [".env", "credentials.json", "hub_mod.py"]},
        )
        excluded = " ".join(preview["excluded_secrets"])
        self.assertIn(".env", excluded)
        self.assertIn("credentials.json", excluded)
        paths = {f["path"] for f in preview["files"]}
        self.assertIn("hub_mod.py", paths)
        self.assertNotIn(".env", paths)
        instr_paths = {i["path"] for i in preview["instructions"]}
        self.assertIn("AGENTS.md", instr_paths)
        packed = preview["packed_prompt"]
        self.assertIn("Do not write secrets", packed)
        self.assertNotIn("should-never-appear", packed)

    def test_context_preview_endpoint_fields(self):
        preview = self.svc.preview_context(
            {"repository_ids": ["demo-repo"], "mode": "review", "prompt": "readme"}
        )
        self.assertIn("packed_prompt_preview", preview)
        self.assertIn("files", preview)
        self.assertTrue(preview["packed_prompt_chars"] > 0)

    def test_native_repository_investigation_distinguishes_hub_tools(self):
        native = self.svc.preview_context(
            {
                "profile_id": "okarun",
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "prompt": "Explain the implementation",
                "tool_ids": [],
                "repository_investigation": True,
            }
        )
        self.assertEqual(native["tools"]["enabled"], [])
        self.assertNotIn("Enabled read-only tools: none.", native["packed_prompt"])
        self.assertIn("Hub tools: none.", native["packed_prompt"])
        self.assertIn("Native Codex read-only repository search", native["packed_prompt"])

        packet_only = self.svc.preview_context(
            {
                "profile_id": "okarun",
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "prompt": "Explain the supplied packet",
                "tool_ids": [],
                "bounded_evidence_only": True,
            }
        )
        self.assertIn("Enabled read-only tools: none.", packet_only["packed_prompt"])
        self.assertNotIn("Native Codex read-only repository search", packet_only["packed_prompt"])
        self.assertNotIn("Hub tools: none.", packet_only["packed_prompt"])

    def test_run_cancel_error_and_unavailable(self):
        # Unavailable agent → status unavailable, no process
        run = self.svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "missing",
                "prompt": "hello",
            }
        )
        self.assertEqual(run["status"], "unavailable")
        self.assertIn("not found", (run["error"] or "").lower())

        # Invalid model
        with self.assertRaises(AgentCenterError):
            self.svc.start_run(
                {
                    "repository_ids": ["demo-repo"],
                    "mode": "ask",
                    "agent_id": "echo",
                    "model": "no-such-model",
                    "prompt": "hello",
                }
            )

        # Successful echo run
        run_ok = self.svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "echo",
                "model": "echo-1",
                "prompt": "PING_OK",
            }
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            current = self.svc.get_run(run_ok["id"])
            if current["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        current = self.svc.get_run(run_ok["id"])
        self.assertEqual(current["status"], "completed", current.get("error"))
        self.assertIn("PING_OK", current["answer"])
        self.assertTrue(current["logs"])

        # Cancel path: start a longer sleep process via monkeypatched adapter
        slow = EchoAdapter(
            AgentDescriptor(
                id="slow",
                label="Slow",
                provider="generic",
                executable="python",
                modes=["ask"],
                models_managed=["echo-1"],
                command_templates={
                    "ask": [sys.executable, "-c", "import time; time.sleep(30); print('done')"]
                },
            )
        )
        self.svc.adapters.append(slow)
        run_slow = self.svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "slow",
                "model": "echo-1",
                "prompt": "x",
            }
        )
        time.sleep(0.2)
        cancelled = self.svc.cancel_run(run_slow["id"])
        self.assertTrue(cancelled["cancel_requested"])
        deadline = time.time() + 10
        while time.time() < deadline:
            current = self.svc.get_run(run_slow["id"])
            if current["status"] in {"cancelled", "failed", "completed"}:
                break
            time.sleep(0.1)
        current = self.svc.get_run(run_slow["id"])
        self.assertEqual(current["status"], "cancelled")

    def test_history_and_audit_redaction(self):
        text = redact_text("ok\nAPI_KEY=super-secret\nTOKEN: abc\nfine")
        self.assertIn("[redacted]", text)
        self.assertNotIn("super-secret", text)
        self.assertNotIn("abc", text)

        run = self.svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "find",
                "agent_id": "echo",
                "prompt": "hist",
            }
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.svc.get_run(run["id"])["status"] == "completed":
                break
            time.sleep(0.1)
        hist = self.svc.history()
        self.assertTrue(any(h["id"] == run["id"] for h in hist))
        actions = {a.get("action") for a in self.audits}
        self.assertIn("AGENT_RUN_SUBMIT", actions)
        # Audit detail must not include packed prompt secrets
        for entry in self.audits:
            detail = entry.get("detail") or {}
            blob = str(detail)
            self.assertNotIn("should-never-appear", blob)

    def test_hub_simulator_and_cursor_not_editor(self):
        from hub.agent_center.adapters import build_adapters
        from hub.agent_center.adapters.cursor_agent import CursorAgentAdapter, looks_like_editor_cli

        adapters = {a.descriptor.id: a for a in build_adapters()}
        self.assertIn("hub-simulator", adapters)
        sim = adapters["hub-simulator"].availability()
        self.assertEqual(sim.status, "available")
        self.assertTrue(sim.executable_found)

        cursor = adapters["cursor-agent"]
        self.assertIsInstance(cursor, CursorAgentAdapter)
        self.assertTrue(looks_like_editor_cli(r"C:\Users\x\AppData\Local\Programs\cursor\resources\app\bin\cursor.exe"))
        with mock.patch.object(cursor, "resolve_executable", return_value=None):
            av = cursor.availability()
        self.assertEqual(av.status, "unavailable")
        self.assertIn("IDE", av.detail)

        svc = AgentCenterService(
            self.registry,
            store=self.store,
            adapters=list(adapters.values()),
            timeout_seconds=30,
        )
        run = svc.start_run(
            {
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "hub-simulator",
                "model": "simulator",
                "prompt": "Is this working?",
            }
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            current = svc.get_run(run["id"])
            if current["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        current = svc.get_run(run["id"])
        self.assertEqual(current["status"], "completed", current.get("error") or current.get("logs"))
        self.assertIn("Agent Center is working", current["answer"])

    def test_prompt_library(self):
        saved = self.store.save_prompt(title="T1", body="Body", mode="plan", tags=["x"])
        self.assertEqual(saved["title"], "T1")
        listed = self.store.list_prompts()
        self.assertEqual(listed[0]["id"], saved["id"])
        self.assertTrue(self.store.delete_prompt(saved["id"]))

    def test_flask_routes(self):
        os.environ.setdefault("CENTRAL_HUB_SECRET_KEY", "test-secret")
        from app import create_app

        app = create_app()
        app.config["AGENT_CENTER"] = self.svc
        client = app.test_client()
        page = client.get("/agents")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Assistant Center", page.data)
        self.assertIn(b"Not yet available", page.data)

        agents = client.get("/api/agents")
        self.assertEqual(agents.status_code, 200)
        self.assertTrue(agents.get_json()["agents"])

        models = client.get("/api/agents/echo/models")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.get_json()["models_source"], "managed")

        preview = client.post(
            "/api/agents/context/preview",
            json={"repository_ids": ["demo-repo"], "mode": "ask", "prompt": "hub_mod"},
        )
        self.assertEqual(preview.status_code, 200)
        body = preview.get_json()
        self.assertNotIn("packed_prompt", body)
        self.assertIn("packed_prompt_preview", body)

        started = client.post(
            "/api/agents/runs",
            json={
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "echo",
                "model": "echo-1",
                "prompt": "ROUTE_OK",
            },
        )
        self.assertEqual(started.status_code, 201)
        run_id = started.get_json()["run"]["id"]
        deadline = time.time() + 15
        final = None
        while time.time() < deadline:
            got = client.get(f"/api/agents/runs/{run_id}")
            final = got.get_json()["run"]
            if final["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        self.assertEqual(final["status"], "completed")
        self.assertIn("ROUTE_OK", final["answer"])

        unavail = client.post(
            "/api/agents/runs",
            json={
                "repository_ids": ["demo-repo"],
                "mode": "ask",
                "agent_id": "missing",
                "prompt": "x",
            },
        )
        self.assertEqual(unavail.status_code, 201)
        self.assertEqual(unavail.get_json()["run"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
