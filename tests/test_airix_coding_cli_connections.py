"""Focused tests for Codex / Claude Code / Cursor Agent connection lifecycle."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.agent_center.adapters.base import AgentDescriptor, which_executable
from hub.agent_center.adapters.claude_code import ClaudeCodeAdapter
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.adapters.cursor_agent import (
    CursorAgentAdapter,
    discover_cursor_agent_executable,
    looks_like_editor_cli,
    parse_cursor_status,
)
from hub.agent_center.adapters.xai_api import XaiApiAdapter
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
                self.assertEqual(ok["account_label"], "user@example.com")

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


def _write_cli(directory: Path, stem: str) -> Path:
    if os.name == "nt":
        path = directory / f"{stem}.cmd"
        path.write_text("@echo off\n", encoding="utf-8")
        return path
    path = directory / stem
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class CursorAgentDiscoveryTests(unittest.TestCase):
    def test_agent_detected_from_path(self) -> None:
        def fake_which(name, extra_dirs=None):
            return "/usr/bin/agent" if name == "agent" else None

        with patch("hub.agent_center.adapters.cursor_agent.which_executable", side_effect=fake_which):
            with patch("hub.agent_center.adapters.cursor_agent.official_cursor_agent_dirs", return_value=[]):
                self.assertEqual(discover_cursor_agent_executable("agent"), "/usr/bin/agent")

    def test_cursor_agent_fallback_when_agent_missing(self) -> None:
        def fake_which(name, extra_dirs=None):
            return "/usr/bin/cursor-agent" if name == "cursor-agent" else None

        with patch("hub.agent_center.adapters.cursor_agent.which_executable", side_effect=fake_which):
            with patch("hub.agent_center.adapters.cursor_agent.official_cursor_agent_dirs", return_value=[]):
                self.assertEqual(discover_cursor_agent_executable("agent"), "/usr/bin/cursor-agent")

    def test_official_install_dir_when_path_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = _write_cli(root, "agent")
            with patch("hub.agent_center.adapters.cursor_agent.which_executable", return_value=None):
                with patch("hub.agent_center.adapters.cursor_agent.official_cursor_agent_dirs", return_value=[root]):
                    found = discover_cursor_agent_executable("agent")
            self.assertEqual(Path(found).resolve(), cli.resolve())

    def test_cursor_agent_official_dir_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = _write_cli(root, "cursor-agent")
            with patch("hub.agent_center.adapters.cursor_agent.which_executable", return_value=None):
                with patch("hub.agent_center.adapters.cursor_agent.official_cursor_agent_dirs", return_value=[root]):
                    found = discover_cursor_agent_executable("agent")
            self.assertEqual(Path(found).resolve(), cli.resolve())

    def test_rejects_ide_cursor_binary(self) -> None:
        self.assertTrue(
            looks_like_editor_cli(r"C:\Users\x\AppData\Local\Programs\cursor\resources\app\bin\cursor.exe")
        )
        self.assertTrue(looks_like_editor_cli("/usr/share/cursor/resources/app/bin/cursor"))
        self.assertFalse(looks_like_editor_cli(r"C:\Users\x\AppData\Local\cursor-agent\agent.cmd"))
        self.assertFalse(looks_like_editor_cli("/usr/bin/cursor-agent"))

        def fake_which(name, extra_dirs=None):
            if name == "agent":
                return r"C:\Users\x\AppData\Local\Programs\cursor\resources\app\bin\cursor.exe"
            if name == "cursor-agent":
                return r"C:\Users\x\AppData\Local\cursor-agent\cursor-agent.cmd"
            return None

        with patch("hub.agent_center.adapters.cursor_agent.which_executable", side_effect=fake_which):
            with patch("hub.agent_center.adapters.cursor_agent.official_cursor_agent_dirs", return_value=[]):
                self.assertEqual(
                    discover_cursor_agent_executable("agent"),
                    r"C:\Users\x\AppData\Local\cursor-agent\cursor-agent.cmd",
                )

    def test_authenticated_and_unauthenticated_status(self) -> None:
        ok = parse_cursor_status("✓ Logged in as user@example.com", returncode=0)
        self.assertEqual(ok["state"], "connected")
        self.assertEqual(ok["account_label"], "user@example.com")
        self.assertNotIn("token", ok["detail"].lower())
        logged = parse_cursor_status("Logged in as user@example.com", returncode=0)
        self.assertEqual(logged["state"], "connected")
        missing = parse_cursor_status("not logged in", returncode=1)
        self.assertEqual(missing["state"], "authentication_required")
        self.assertEqual(missing["account_label"], "")
        required = parse_cursor_status("Authentication required. Run agent login.", returncode=1)
        self.assertEqual(required["state"], "authentication_required")
        tokenish = parse_cursor_status("Logged in as sk-secretvaluehere", returncode=0)
        self.assertEqual(tokenish["account_label"], "")

    def test_windows_user_path_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = _write_cli(root, "agent")
            decoy = root / "decoy"
            decoy.mkdir()
            with patch.dict(os.environ, {"PATH": str(decoy)}, clear=False):
                with patch("hub.agent_center.adapters.base._windows_user_path", return_value=str(root)):
                    found = which_executable("agent")
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).resolve(), cli.resolve())

    def test_grok_does_not_claim_cursor_cli(self) -> None:
        with patch.dict(os.environ, {"XAI_API_KEY": ""}, clear=False):
            adapter = XaiApiAdapter()
            with patch(
                "hub.agent_center.adapters.base.which_executable",
                return_value=r"C:\Users\x\AppData\Local\cursor-agent\agent.cmd",
            ) as which:
                status = adapter.connection_status()
            which.assert_not_called()
            self.assertEqual(status.get("cli_commands"), [])
            self.assertEqual(status.get("executable_path"), "")
            self.assertIn("XAI_API_KEY", status["detail"])
            self.assertNotEqual(status["state"], "connected")

            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            store = AgentCenterStore(AgentCenterDb(Path(tmp.name) / "agent.db"))

            class _Cursor:
                descriptor = _desc("cursor-agent", "cursor_agent", "agent")
                authentication_method = "CLI"
                credential_storage = "CLI"
                credential_type = "cli"

                def capabilities(self):
                    return {"modes": ["ask"], "read_only": True}

                def connection_status(self, *, force_refresh: bool = False):
                    return {
                        "state": "connected",
                        "installed": True,
                        "authenticated": True,
                        "available": True,
                        "detail": "ok",
                        "executable_path": r"C:\Users\x\AppData\Local\cursor-agent\agent.cmd",
                        "cli_commands": ["agent", "cursor-agent"],
                        "account_label": "user@example.com",
                        "version": "2026.08.11-e8db854",
                    }

                def resolve_executable(self):
                    return r"C:\Users\x\AppData\Local\cursor-agent\agent.cmd"

                def _cli_command_candidates(self):
                    return ("agent", "cursor-agent")

            registry = AgentConnectionRegistry([adapter, _Cursor()], store)
            grok = registry.get("grok", refresh=True)
            cursor = registry.get("cursor-agent", refresh=True)
            self.assertEqual(grok["credential_type"], "api_key")
            self.assertEqual(grok["method_label"], "API Key")
            self.assertEqual(grok.get("cli_commands"), [])
            self.assertEqual(grok.get("executable_path"), "")
            self.assertNotIn("agent", grok.get("cli_commands") or [])
            self.assertEqual(cursor["method_label"], "CLI")
            self.assertIn("agent", cursor["cli_commands"])
            self.assertEqual(cursor["state"], "connected")
            self.assertIn("cursor-agent\\agent.cmd", cursor["executable_path"].replace("/", "\\"))


if __name__ == "__main__":
    unittest.main()
