"""Focused tests for Codex / Claude Code / Cursor Agent connection lifecycle."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.base import AgentDescriptor
from hub.agent_center.adapters.claude_code import ClaudeCodeAdapter
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.adapters.cursor_agent import CursorAgentAdapter
from hub.agent_center.connections import AgentConnectionRegistry
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing import AgentRouterService
from hub.agent_center.store import AgentCenterStore


def _desc(agent_id: str, provider: str, executable: str = "") -> AgentDescriptor:
    return AgentDescriptor(
        id=agent_id,
        label=agent_id.replace("-", " ").title(),
        provider=provider,
        executable=executable,
        modes=["ask", "find", "plan", "review"],
    )


class _Probe:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class MissingCliTests(unittest.TestCase):
    def test_cursor_missing_cli_status(self) -> None:
        adapter = CursorAgentAdapter(_desc("cursor-agent", "cursor_agent", "agent"))
        with patch.object(adapter, "resolve_executable", return_value=None):
            status = adapter.connection_status()
        self.assertFalse(status["installed"])
        self.assertFalse(status["authenticated"])
        self.assertFalse(status["available"])
        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["error_code"], "missing_cli")
        self.assertIn("agent", status["cli_commands"])
        self.assertIn("cursor-agent", status["cli_commands"])
        self.assertIn("Install", status["install_help"])

    def test_claude_missing_cli_status(self) -> None:
        adapter = ClaudeCodeAdapter(_desc("claude-code", "claude_code", "claude"))
        with patch.object(adapter, "resolve_executable", return_value=None):
            status = adapter.connection_status()
        self.assertEqual(status["state"], "unavailable")
        self.assertIn("claude", status["cli_commands"])

    def test_codex_missing_cli_status(self) -> None:
        adapter = CodexAdapter(_desc("codex", "codex", "codex"))
        missing = {
            "executable": None,
            "installed": False,
            "complete": False,
            "error_code": "missing_cli",
            "detail": "Codex CLI is not installed or not discoverable",
            "incomplete_path": "",
        }
        with patch.object(adapter, "resolve_executable", return_value=None):
            with patch("hub.agent_center.adapters.codex.inspect_codex_installation", return_value=missing):
                status = adapter.connection_status()
        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["error_code"], "missing_cli")
        self.assertIn("OPENAI_API_KEY", status["install_help"])


class AuthLifecycleTests(unittest.TestCase):
    def test_cursor_auth_failure_and_success(self) -> None:
        adapter = CursorAgentAdapter(_desc("cursor-agent", "cursor_agent", "agent"))

        def _probe(argv, *, timeout=15.0):
            cmd = " ".join(argv)
            if "--version" in cmd or cmd.endswith(" version") or cmd.endswith(" -v"):
                return _Probe(0, "agent 1.2.3\n")
            if "status" in argv:
                return _Probe(1, "", "not logged in")
            return _Probe(1, "", "fail")

        with patch.object(adapter, "resolve_executable", return_value="/bin/agent"):
            with patch.object(adapter, "_run_probe", side_effect=_probe):
                failed = adapter.connection_status()
                self.assertTrue(failed["installed"])
                self.assertFalse(failed["authenticated"])
                self.assertEqual(failed["state"], "authentication_required")
                self.assertEqual(failed["version"], "agent 1.2.3")

        def _probe_ok(argv, *, timeout=15.0):
            cmd = " ".join(argv)
            if "--version" in cmd or cmd.endswith(" version") or cmd.endswith(" -v"):
                return _Probe(0, "agent 1.2.3\n")
            if "status" in argv:
                return _Probe(0, "Logged in as user@example.com\n")
            return _Probe(0, "ok")

        with patch.object(adapter, "resolve_executable", return_value="/bin/agent"):
            with patch.object(adapter, "_run_probe", side_effect=_probe_ok):
                ok = adapter.test_connection()
                self.assertTrue(ok["ok"])
                self.assertTrue(ok["authenticated"])
                self.assertTrue(ok["available"])
                self.assertEqual(ok["state"], "connected")

    def test_claude_auth_success_exposes_account(self) -> None:
        adapter = ClaudeCodeAdapter(_desc("claude-code", "claude_code", "claude"))

        def _probe(argv, *, timeout=15.0):
            if "--version" in argv or argv[-1] == "version":
                return _Probe(0, "claude 0.9.0\n")
            if "auth" in argv and "status" in argv:
                return _Probe(0, '{"email":"dev@example.com","subscriptionType":"pro"}')
            return _Probe(1, "", "no")

        with patch.object(adapter, "resolve_executable", return_value="/bin/claude"):
            with patch.object(adapter, "_run_probe", side_effect=_probe):
                status = adapter.connection_status()
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["account_label"], "dev@example.com")
        self.assertEqual(status["version"], "claude 0.9.0")

    def test_sign_out_and_reauthenticate_actions(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AgentCenterStore(AgentCenterDb(Path(tmp.name) / "agent.db"))

        class _Fake:
            descriptor = _desc("cursor-agent", "cursor_agent", "agent")
            authentication_method = "test"
            credential_storage = "test"
            calls: list[str] = []

            def capabilities(self):
                return {"modes": ["ask"], "read_only": True}

            def connection_status(self, *, force_refresh: bool = False):
                return {
                    "state": "authentication_required" if "disconnect" in self.calls else "connected",
                    "detail": "ok",
                    "installed": True,
                    "authenticated": "disconnect" not in self.calls,
                    "available": "disconnect" not in self.calls,
                    "version": "1.0",
                    "cli_commands": ["agent"],
                }

            def resolve_executable(self):
                return "/bin/agent"

            def connect(self):
                self.calls.append("connect")
                return {"ok": True, "state": "authentication_required", "detail": "started"}

            def disconnect(self):
                self.calls.append("disconnect")
                return {"ok": True, "state": "authentication_required", "detail": "signed out"}

            def test_connection(self):
                self.calls.append("test")
                return {"ok": True, **self.connection_status()}

            def list_model_details(self, **_):
                return {"models": [], "error": ""}

        fake = _Fake()
        registry = AgentConnectionRegistry([fake], store)
        signed = registry.action("cursor-agent", "sign-out")
        self.assertIn("disconnect", fake.calls)
        self.assertEqual(signed["connection"]["summary_label"], "Not connected")
        reauth = registry.action("cursor-agent", "reauthenticate")
        self.assertIn("connect", fake.calls)
        self.assertTrue(reauth["result"]["ok"])
        tested = registry.action("cursor-agent", "test")
        self.assertIn("test", fake.calls)
        self.assertTrue(tested["result"]["ok"])


class RoutingExclusionTests(unittest.TestCase):
    def test_unavailable_coding_clis_excluded_from_recommend(self) -> None:
        availability = {
            "codex": {"id": "codex", "runnable": False, "status": "unavailable"},
            "claude-code": {"id": "claude-code", "runnable": False, "status": "authentication_required"},
            "cursor-agent": {"id": "cursor-agent", "runnable": False, "status": "unavailable"},
            "grok": {"id": "grok", "runnable": True, "status": "connected"},
            "hub-simulator": {"id": "hub-simulator", "runnable": True, "status": "available"},
        }
        router = AgentRouterService(availability_loader=lambda: availability)
        # Architecture prompt would prefer Codex if available — must not when excluded.
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries"
        )
        rec = router.recommend_route(prompt, probe_providers=False)
        self.assertNotEqual(rec.recommended_agent, "codex")
        self.assertNotEqual(rec.recommended_agent, "claude-code")
        self.assertNotEqual(rec.recommended_agent, "cursor-agent")
        self.assertIn(rec.recommended_agent, {"grok", "deterministic", "low-cost", "hub-simulator"})

    def test_connected_codex_can_be_recommended(self) -> None:
        availability = {
            "codex": {"id": "codex", "runnable": True, "status": "connected"},
            "claude-code": {"id": "claude-code", "runnable": False, "status": "unavailable"},
            "cursor-agent": {"id": "cursor-agent", "runnable": False, "status": "unavailable"},
            "grok": {"id": "grok", "runnable": True, "status": "connected"},
        }
        router = AgentRouterService(availability_loader=lambda: availability)
        prompt = (
            "Redesign the architecture and perform a large refactor across 12 modules "
            "in the entire codebase, including cross-module ownership boundaries and "
            "a breaking change migration plan"
        )
        rec = router.recommend_route(prompt, probe_providers=False)
        self.assertEqual(rec.recommended_agent, "codex")


class CompactPanelContractTests(unittest.TestCase):
    def test_list_coding_clis_order_and_summary(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = AgentCenterStore(AgentCenterDb(Path(tmp.name) / "agent.db"))

        class _Row:
            def __init__(self, agent_id: str, provider: str, *, installed: bool, auth: bool) -> None:
                self.descriptor = _desc(agent_id, provider, agent_id)
                self.installed = installed
                self.auth = auth

            def capabilities(self):
                return {"modes": ["ask"], "read_only": True}

            def connection_status(self, *, force_refresh: bool = False):
                if not self.installed:
                    return {
                        "state": "unavailable",
                        "installed": False,
                        "authenticated": False,
                        "available": False,
                        "detail": "missing",
                        "cli_commands": [self.descriptor.executable],
                        "install_help": "Install help text",
                    }
                return {
                    "state": "connected" if self.auth else "authentication_required",
                    "installed": True,
                    "authenticated": self.auth,
                    "available": self.auth,
                    "version": "1.0.0",
                    "detail": "ok" if self.auth else "login required",
                    "cli_commands": [self.descriptor.executable],
                }

        adapters = [
            _Row("codex", "codex", installed=True, auth=True),
            _Row("claude-code", "claude_code", installed=True, auth=False),
            _Row("cursor-agent", "cursor_agent", installed=False, auth=False),
        ]
        registry = AgentConnectionRegistry(adapters, store)
        rows = registry.list_coding_clis(refresh=True, probe=True)
        self.assertEqual([r["id"] for r in rows], ["codex", "claude-code", "cursor-agent"])
        self.assertEqual(rows[0]["summary_label"], "Connected")
        self.assertEqual(rows[1]["summary_label"], "Not connected")
        self.assertEqual(rows[2]["summary_label"], "Missing CLI")
        self.assertEqual(rows[0]["primary_action"], "test")
        self.assertEqual(rows[1]["primary_action"], "connect")
        self.assertEqual(rows[2]["primary_action"], "install_help")


if __name__ == "__main__":
    unittest.main()
