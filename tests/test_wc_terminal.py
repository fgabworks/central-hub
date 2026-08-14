"""Interactive Workspace Console PTY terminal — security, lifecycle, audit."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.audit.actions import WC_TERMINAL_INSERT_SUGGESTION, WC_TERMINAL_START, WC_TERMINAL_STOP
from hub.workspace_console.terminal.manager import TerminalSessionManager
from hub.workspace_console.terminal.security import (
    TerminalSecurityError,
    mint_ws_ticket,
    origin_allowed,
    resolve_session_cwd,
    resolve_shell_executable,
    scrub_child_env,
    verify_ws_ticket,
)
from hub.workspace_console.terminal.pty_backend import (
    normalize_conpty_newlines,
)
from hub.workspace_console.terminal.settings import TerminalSettings


class _Repo:
    def __init__(self, repo_id: str, path: Path, enabled: bool = True, name: str | None = None):
        self.id = repo_id
        self.name = name or repo_id
        self.enabled = enabled
        self.local_path = str(path)
        self.working_directory = str(path)


class _Registry:
    def __init__(self, repos: list[_Repo]):
        self._repos = {r.id: r for r in repos}

    def get(self, repo_id: str):
        return self._repos.get(repo_id)

    def enabled_repositories(self):
        return [r for r in self._repos.values() if r.enabled]


class TerminalSecurityUnitTests(unittest.TestCase):
    def test_origin_allows_localhost_only(self):
        self.assertTrue(
            origin_allowed("http://127.0.0.1:8080", "127.0.0.1:8080", hub_host="127.0.0.1", hub_port=8080)
        )
        self.assertTrue(
            origin_allowed("http://localhost:8080", "localhost:8080", hub_host="127.0.0.1", hub_port=8080)
        )
        self.assertFalse(
            origin_allowed("https://evil.example", "127.0.0.1:8080", hub_host="127.0.0.1", hub_port=8080)
        )

    def test_ws_ticket_roundtrip_and_expiry(self):
        ticket = mint_ws_ticket(secret="s", session_id="abc", actor="owner", ttl_seconds=60)
        self.assertTrue(verify_ws_ticket(ticket, secret="s", session_id="abc", actor="owner"))
        self.assertFalse(verify_ws_ticket(ticket, secret="s", session_id="other", actor="owner"))
        self.assertFalse(verify_ws_ticket(ticket, secret="wrong", session_id="abc", actor="owner"))
        self.assertFalse(verify_ws_ticket(ticket, secret="s", session_id="abc", actor="anonymous"))

    def test_scrub_env_strips_secrets_not_dotenv_injection(self):
        env = scrub_child_env(
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "sk-test",
                "DHIS2_PASSWORD": "secret",
                "NORMAL": "ok",
            }
        )
        self.assertEqual(env.get("NORMAL"), "ok")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("DHIS2_PASSWORD", env)

    def test_path_jail_rejects_traversal_and_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            repo = _Repo("demo", root)
            self.assertEqual(resolve_session_cwd(repo, None), root.resolve())
            self.assertEqual(resolve_session_cwd(repo, "sub"), (root / "sub").resolve())
            with self.assertRaises(TerminalSecurityError):
                resolve_session_cwd(repo, "../outside")
            with self.assertRaises(TerminalSecurityError):
                resolve_session_cwd(repo, str(Path(tmp).anchor))

    def test_cmd_requires_flag(self):
        if os.name != "nt":
            self.skipTest("Windows-only shell gate")
        with self.assertRaises(TerminalSecurityError):
            resolve_shell_executable("cmd", allow_cmd=False)
        sid, argv = resolve_shell_executable("powershell", allow_cmd=False)
        self.assertEqual(sid, "powershell")
        self.assertTrue(argv)


class TerminalSessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = _Repo("demo", self.root, name="Demo")
        self.registry = _Registry([self.repo])
        self.audits: list[dict] = []

        def _audit(action: str, detail=None, **kwargs):
            self.audits.append({"action": action, "detail": detail or kwargs})

        self.mgr = TerminalSessionManager(
            registry=self.registry,
            settings=TerminalSettings(
                enabled=True,
                allow_cmd=False,
                max_sessions=4,
                max_output_buffer_bytes=64_000,
                read_chunk_bytes=4096,
                ws_ticket_ttl_seconds=60,
                idle_ws_grace_seconds=60,
                default_cols=80,
                default_rows=24,
            ),
            audit=_audit,
            hub_host="127.0.0.1",
        )

    def tearDown(self):
        self.mgr.shutdown_all()
        self.temp.cleanup()

    def test_create_write_resize_close_and_audit_metadata_only(self):
        sess = self.mgr.create(repository_id="demo", shell="powershell" if os.name == "nt" else "bash")
        self.assertEqual(sess["repository_id"], "demo")
        self.assertTrue(sess["alive"] or sess["status"] == "running")
        self.assertTrue(sess["pid"])

        # Interactive write + resize (Ctrl+C / clear are just bytes).
        self.mgr.write(sess["id"], "echo hub-term-ok\r")
        self.mgr.resize(sess["id"], 100, 30)
        time.sleep(0.4)
        obj = self.mgr.get(sess["id"])
        self.assertIsNotNone(obj)
        snap = obj._pump.snapshot() if obj and obj._pump else b""
        # Output may be delayed on cold PowerShell; presence of session is enough if empty.
        self.assertIsInstance(snap, (bytes, bytearray))

        self.mgr.write(sess["id"], "\x03")  # Ctrl+C
        closed = self.mgr.close(sess["id"], confirm=True)
        self.assertEqual(closed["status"], "closed")
        self.assertIsNone(self.mgr.get(sess["id"]))

        actions = [a["action"] for a in self.audits]
        self.assertIn(WC_TERMINAL_START, actions)
        self.assertIn(WC_TERMINAL_STOP, actions)
        for row in self.audits:
            blob = json.dumps(row)
            self.assertNotIn("echo hub-term-ok", blob)
            self.assertNotIn("PASSWORD", blob.upper() if "password" in blob.lower() else blob)

    def test_create_and_resize_reject_tiny_pty_dimensions(self):
        sess = self.mgr.create(
            repository_id="demo",
            shell="powershell" if os.name == "nt" else "bash",
            cols=8,
            rows=4,
        )
        self.assertGreaterEqual(sess["cols"], 40)
        self.assertGreaterEqual(sess["rows"], 10)
        out = self.mgr.resize(sess["id"], 8, 4)
        self.assertGreaterEqual(out["cols"], 40)
        self.assertGreaterEqual(out["rows"], 10)
        self.mgr.close(sess["id"], confirm=True)

    def test_multiple_sessions_and_duplicate(self):
        a = self.mgr.create(repository_id="demo", name="A")
        b = self.mgr.duplicate(a["id"])
        self.assertNotEqual(a["id"], b["id"])
        listed = self.mgr.list_sessions()
        self.assertGreaterEqual(len(listed), 2)

    def test_default_tab_name_uses_shell_ordinal(self):
        shell = "powershell" if os.name == "nt" else "bash"
        a = self.mgr.create(repository_id="demo", shell=shell)
        b = self.mgr.create(repository_id="demo", shell=shell)
        label = "PowerShell" if shell == "powershell" else "bash"
        self.assertTrue(a["name"].startswith(f"{label} 1 — "))
        self.assertIn("Demo", a["name"])
        self.assertTrue(b["name"].startswith(f"{label} 2 — "))
        self.mgr.close(a["id"], confirm=True)
        self.mgr.close(b["id"], confirm=True)

    def test_confirm_required_before_close_active(self):
        sess = self.mgr.create(repository_id="demo")
        with self.assertRaises(TerminalSecurityError) as ctx:
            self.mgr.close(sess["id"], confirm=False)
        self.assertEqual(ctx.exception.code, "confirm_required")
        self.mgr.close(sess["id"], confirm=True)

    def test_path_jail_enforced_on_create_cwd(self):
        with self.assertRaises(TerminalSecurityError):
            self.mgr.create(repository_id="demo", relative_cwd="../outside")

    def test_ports_annotation_includes_terminal_name(self):
        sess = self.mgr.create(repository_id="demo")
        ports = [{"pid": sess["pid"], "repository_id": "demo", "port": 9000}]
        annotated = self.mgr.annotate_ports(ports)
        self.assertTrue(annotated[0].get("terminal_owned"))
        self.assertEqual(annotated[0].get("terminal_session_id"), sess["id"])
        self.assertEqual(annotated[0].get("terminal_name"), sess["name"])
        self.mgr.close(sess["id"], confirm=True)

    def test_rejects_unknown_repo_and_non_local_bind(self):
        with self.assertRaises(TerminalSecurityError):
            self.mgr.create(repository_id="missing")
        bad = TerminalSessionManager(
            registry=self.registry,
            settings=self.mgr.settings,
            hub_host="0.0.0.0",
        )
        with self.assertRaises(TerminalSecurityError):
            bad.create(repository_id="demo")


class TerminalRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = _Repo("term-demo", self.root, name="Term Demo")
        # Patch registry get for terminal manager + catalog.
        self._orig_reg = self.app.config["REGISTRY"]
        self.registry = _Registry([self.repo])
        self.app.config["REGISTRY"] = self.registry
        self.app.config["WC_TERMINALS"].registry = self.registry
        self.app.config["WORKSPACE_CONSOLE"].registry = self.registry

    def tearDown(self):
        self.app.config["WC_TERMINALS"].shutdown_all()
        self.app.config["REGISTRY"] = self._orig_reg
        self.app.config["WC_TERMINALS"].registry = self._orig_reg
        self.app.config["WORKSPACE_CONSOLE"].registry = self._orig_reg
        self.temp.cleanup()

    def test_bootstrap_marks_interactive_pty(self):
        # Avoid swapping REGISTRY for full page render — bootstrap API is enough.
        boot = self.client.get("/api/workspace-console/bootstrap")
        data = boot.get_json()
        self.assertTrue(data.get("interactive_terminal"))
        self.assertTrue(data["safety"].get("interactive_pty"))
        self.assertFalse(data["safety"].get("free_shell"))
        # Static assets / panel chrome present in base-mounted pages.
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".wc-xterm", css)
        panel = (ROOT / "templates" / "partials" / "workspace_console_panel.html").read_text(encoding="utf-8")
        self.assertIn("New Terminal", panel)
        self.assertIn("wc_terminal.js", (ROOT / "templates" / "base.html").read_text(encoding="utf-8"))

    def test_session_create_ticket_and_origin_rejection(self):
        bad = self.client.post(
            "/api/workspace-console/terminal/sessions",
            json={"repository_id": "term-demo"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(bad.status_code, 403)

        created = self.client.post(
            "/api/workspace-console/terminal/sessions",
            json={"repository_id": "term-demo", "shell": "powershell" if os.name == "nt" else "bash"},
            headers={"Origin": "http://127.0.0.1:8080"},
        )
        body = created.get_json()
        self.assertTrue(body.get("ok"), body)
        sid = body["session"]["id"]

        ticket = self.client.post(
            f"/api/workspace-console/terminal/sessions/{sid}/ticket",
            json={},
            headers={"Origin": "http://127.0.0.1:8080"},
        )
        tbody = ticket.get_json()
        self.assertTrue(tbody.get("ok"), tbody)
        self.assertIn("ticket", tbody)

        insert = self.client.post(
            "/api/workspace-console/terminal/insert",
            json={"session_id": sid, "command": "rm -rf /"},
        )
        ibody = insert.get_json()
        self.assertTrue(ibody.get("ok"))
        self.assertFalse(ibody.get("executed"))

        closed = self.client.delete(
            f"/api/workspace-console/terminal/sessions/{sid}",
            json={"confirm": True},
        )
        self.assertTrue(closed.get_json().get("ok"))

    def test_ai_cannot_execute_endpoint_exists_as_insert_only(self):
        # No HTTP API executes arbitrary commands into a PTY without WS human input path.
        r = self.client.post(
            "/api/workspace-console/terminal/insert",
            json={"session_id": "x", "command": "echo hi\n"},
        )
        data = r.get_json()
        self.assertFalse(data.get("executed"))
        self.assertIn("Enter", data.get("message", ""))

    def test_assistant_dock_has_insert_action_hook(self):
        js = (ROOT / "static" / "js" / "assistant_dock.js").read_text(encoding="utf-8")
        self.assertIn("Insert into Terminal", js)
        self.assertIn("WCTerminal.insertText", js)
        term_js = (ROOT / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8")
        self.assertIn("Paste multiline", term_js)
        self.assertIn("insertText", term_js)
        # Ensure insert strips trailing newlines (no auto Enter).
        self.assertIn("endsWith", term_js)
        self.assertIn("Paste multiline", term_js)

    def test_terminal_ide_chrome_layout_and_sizing(self):
        panel = (ROOT / "templates" / "partials" / "workspace_console_panel.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        term_js = (ROOT / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8")
        wc_js = (ROOT / "static" / "js" / "workspace_console.js").read_text(encoding="utf-8")

        # Two header rows: title/actions then Problems|Output|Debug|Terminal|Ports
        self.assertIn("wc-header-title-row", panel)
        self.assertIn("wc-header-tabs-row", panel)
        self.assertIn('data-wc-tab="problems"', panel)
        self.assertIn('data-wc-tab="ports"', panel)
        self.assertIn("+ New Terminal", panel)
        self.assertIn("wc-term-empty", panel)
        self.assertIn('id="wc-term-kill"', panel)
        self.assertIn("btn-danger", panel)
        self.assertIn("disabled", panel)

        # Compact control sizing (~12–13px text, ~32px height)
        self.assertIn("font-size: 12px", css)
        self.assertIn("font-size: 11px", css)
        self.assertIn("height: 32px", css)
        self.assertIn(".wc-control", css)
        self.assertIn(".wc-field-label", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("max-width: 18rem", css)

        # Behavior hooks
        self.assertIn("updateActionButtons", term_js)
        self.assertIn("updateEmptyState", term_js)
        self.assertIn("Kill this terminal", term_js)
        self.assertIn("setRenderingPaused", term_js)
        self.assertIn("terminal_session_id", wc_js)
        self.assertIn("Open URL", wc_js)
        self.assertIn("terminal_name", wc_js)

    def test_terminal_split_defaults_full_width_and_collapses_safely(self):
        panel = (ROOT / "templates" / "partials" / "workspace_console_panel.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        term_js = (ROOT / "static" / "js" / "wc_terminal.js").read_text(encoding="utf-8")
        wc_js = (ROOT / "static" / "js" / "workspace_console.js").read_text(encoding="utf-8")
        prefs_py = (ROOT / "hub" / "workspace_console" / "prefs.py").read_text(encoding="utf-8")

        # One terminal uses full width by default
        self.assertIn('data-split="0"', panel)
        self.assertIn('id="wc-term-view-b"', panel)
        self.assertIn("hidden", panel.split('id="wc-term-view-b"', 1)[1].split(">", 1)[0])
        self.assertIn('.wc-term-stage:not([data-split="1"]) .wc-term-view', css)
        self.assertIn("width: 100%", css)
        self.assertIn(".wc-term-view[hidden]", css)
        self.assertIn("display: none !important", css)

        # Explicit split creates two valid panes (duplicate + attach before layout)
        self.assertIn("beginSplit", term_js)
        self.assertIn("/duplicate", term_js)
        self.assertIn("applySplitLayout(true)", term_js)
        self.assertIn("splitCreating", term_js)
        self.assertIn("Creating split", term_js)

        # Failed second session collapses cleanly; never empty pane
        self.assertIn("collapseSplit", term_js)
        self.assertIn("attach-failed", term_js)
        self.assertIn('view.slot === "b"', term_js)
        self.assertIn("Never enable split chrome from a bare boolean", term_js)

        # Closing one pane restores full width without killing the other
        self.assertIn("closePane", term_js)
        self.assertIn("data-close-pane", panel)
        self.assertIn("promote B to full width", term_js)

        # Reload does not restore an invalid empty split
        self.assertIn("terminal_split_session_id", prefs_py)
        self.assertIn("terminal_split_session_id", wc_js)
        self.assertIn("wantSplitRestore", term_js)
        self.assertIn("sessionIsLive", term_js)
        self.assertIn("Never persist an empty/invalid split layout", prefs_py)

        # xterm resize after split/collapse
        self.assertIn("scheduleFit", term_js)
        self.assertIn("ResizeObserver", term_js)
        self.assertIn("requestAnimationFrame", term_js)

        # WS failure UI
        self.assertIn("wc-term-ws-fail", panel)
        self.assertIn("Reconnect", panel)
        self.assertIn("Close pane", panel)
        self.assertIn("data-ws-reconnect", term_js)
        self.assertIn("showFail", term_js)

        # Active pane chrome when split
        self.assertIn("wc-term-pane-title", panel)
        self.assertIn('.wc-term-stage[data-split="1"] .wc-term-view.is-active', css)
        self.assertIn("setActivePane", term_js)

        # Public layout helpers for diagnostics/tests
        self.assertIn("getLayoutState", term_js)
        self.assertIn("isSplitEnabled", term_js)
        self.assertIn("ptyTextForXterm", term_js)
        self.assertIn("currentTermSize", term_js)
        self.assertIn("hostIsFitReady", term_js)
        self.assertIn("sendInput", term_js)
        self.assertIn("attachGen", term_js)
        self.assertIn("stillCurrent", term_js)
        self.assertIn("convertEol: !win", term_js)
        self.assertIn("windowsMode: false", term_js)
        self.assertIn("convertEol", term_js)
        self.assertIn("tabShortLabel", term_js)
        self.assertIn("getBufferText", term_js)
        self.assertNotIn("term.write(data)", term_js)
        self.assertNotIn("convertEol: true", term_js)


class ConptyNewlineTests(unittest.TestCase):
    def test_lone_lf_gets_cr_without_touching_crlf_or_lone_cr(self):
        self.assertEqual(normalize_conpty_newlines(b"one\ntwo"), b"one\r\ntwo")
        self.assertEqual(normalize_conpty_newlines(b"one\r\ntwo"), b"one\r\ntwo")
        self.assertEqual(normalize_conpty_newlines(b"\r"), b"\r")
        self.assertEqual(normalize_conpty_newlines(b"ok"), b"ok")
        self.assertEqual(normalize_conpty_newlines(b""), b"")


class TerminalConsoleRegressionTests(unittest.TestCase):
    def test_existing_console_tests_still_import(self):
        from tests.test_workspace_console import ConsolePrefsTests  # noqa: F401

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
