"""CLI probes must decode stdout as UTF-8 with replacement (Windows cp1252 crash)."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from hub.agent_center.adapters.cli_common import BaseCliAdapter, run_cli_capture
from hub.agent_center.adapters.base import AgentDescriptor


class CliSubprocessEncodingTests(unittest.TestCase):
    def test_run_cli_capture_forces_utf8_replace(self) -> None:
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

        with patch("hub.agent_center.adapters.cli_common.subprocess.run", side_effect=_fake_run):
            run_cli_capture(["codex", "--version"], timeout=5.0)

        self.assertTrue(captured.get("text"))
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")
        self.assertTrue(captured.get("capture_output"))

    def test_run_probe_uses_utf8_replace(self) -> None:
        adapter = BaseCliAdapter(
            AgentDescriptor(
                id="codex",
                label="Codex",
                provider="codex",
                enabled=True,
                executable="codex",
                modes=["ask"],
                models_managed=[],
            )
        )
        captured: dict = {}

        def _fake_run(*args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

        with patch("hub.agent_center.adapters.cli_common.subprocess.run", side_effect=_fake_run):
            adapter._run_probe(["codex", "login", "status"])

        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")


if __name__ == "__main__":
    unittest.main()
