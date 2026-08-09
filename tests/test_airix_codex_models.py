"""Codex dynamic model discovery + CLI model pass-through tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.adapters.base import AgentDescriptor
from hub.agent_center.codex_models import (
    PROVIDER_DEFAULT,
    discover_codex_models,
    pick_model_for_complexity,
    _normalize_catalog,
)
from hub.agent_center.model_selection import resolve_model_for_run
from hub.agent_center.models import MODES


def _codex_adapter() -> CodexAdapter:
    return CodexAdapter(
        AgentDescriptor(
            id="codex",
            label="Codex",
            provider="codex",
            executable="codex",
            modes=list(MODES),
            models_managed=[],
        )
    )


class CodexCatalogParseTests(unittest.TestCase):
    def test_normalize_list_of_slugs(self) -> None:
        rows = _normalize_catalog(
            [
                {"slug": "gpt-5.6-luna", "visibility": "list", "display_name": "Luna"},
                {"slug": "gpt-5.6-terra", "visibility": "list"},
                {"slug": "gpt-5.6-sol", "visibility": "list"},
                {"slug": "hidden-model", "visibility": "hidden"},
            ]
        )
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"])
        self.assertIn("Luna", rows[0]["display_name"])

    def test_pick_lower_cost_vs_strong(self) -> None:
        models = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
        self.assertEqual(
            pick_model_for_complexity(models, complexity=20, task_type="coding"),
            "gpt-5.6-luna",
        )
        self.assertEqual(
            pick_model_for_complexity(models, complexity=80, task_type="architecture"),
            "gpt-5.6-sol",
        )
        self.assertEqual(
            pick_model_for_complexity(models, complexity=40, task_type="coding"),
            "gpt-5.6-terra",
        )


class CodexDiscoveryTests(unittest.TestCase):
    def test_discover_from_debug_models_json(self) -> None:
        payload = json.dumps(
            {
                "models": [
                    {"id": "gpt-5.6-luna", "visibility": "list"},
                    {"id": "gpt-5.6-terra", "visibility": "list"},
                    {"id": "gpt-5.6-sol", "visibility": "list"},
                ]
            }
        )

        def _fake_run(exe: str, *, timeout: float = 25.0):
            return payload, 0

        with patch("hub.agent_center.codex_models._run_debug_models", _fake_run):
            with patch("hub.agent_center.codex_models.read_configured_default_model", return_value=""):
                result = discover_codex_models("codex")
        self.assertEqual(result["models_source"], "cli_debug_models")
        self.assertIn("gpt-5.6-terra", result["models"])
        self.assertIn(PROVIDER_DEFAULT, result["models"])
        self.assertNotEqual(result["recommended_model"], PROVIDER_DEFAULT)
        self.assertTrue(result["dynamic_models"])

    def test_discover_falls_back_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = {
                "models": [
                    {"slug": "gpt-5.6-terra", "visibility": "list"},
                    {"slug": "gpt-5.6-sol", "visibility": "list"},
                ]
            }
            (home / "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
            with patch("hub.agent_center.codex_models._run_debug_models", return_value=("", 1)):
                with patch("hub.agent_center.codex_models.codex_home", return_value=home):
                    with patch(
                        "hub.agent_center.codex_models.read_configured_default_model",
                        return_value="gpt-5.6-terra",
                    ):
                        result = discover_codex_models("codex")
        self.assertEqual(result["models_source"], "cli_models_cache")
        self.assertEqual(result["recommended_model"], "gpt-5.6-terra")

    def test_adapter_list_models_uses_discovery(self) -> None:
        adapter = _codex_adapter()
        fake = {
            "models": ["gpt-5.6-luna", "gpt-5.6-terra", PROVIDER_DEFAULT],
            "model_details": [
                {"id": "gpt-5.6-luna", "display_name": "Luna", "availability": "available"},
                {"id": "gpt-5.6-terra", "display_name": "Terra", "availability": "available"},
                {"id": PROVIDER_DEFAULT, "display_name": "default", "availability": "available"},
            ],
            "recommended_model": "gpt-5.6-terra",
            "models_source": "cli_debug_models",
            "error": "",
            "dynamic_models": True,
            "configured_default": "",
        }
        with patch.object(adapter, "resolve_executable", return_value="codex"):
            with patch("hub.agent_center.codex_models.discover_codex_models", return_value=fake):
                models, source = adapter.list_models()
                details = adapter.list_model_details()
        self.assertEqual(source, "cli_debug_models")
        self.assertIn("gpt-5.6-luna", models)
        self.assertEqual(details["recommended_model"], "gpt-5.6-terra")
        self.assertTrue(adapter.capabilities().get("dynamic_models"))


class CodexModelResolutionAndArgvTests(unittest.TestCase):
    def test_selected_model_reaches_cli_argv(self) -> None:
        adapter = _codex_adapter()
        with patch.object(adapter, "resolve_executable", return_value="codex"):
            argv = adapter.build_argv(
                mode="ask",
                prompt="hi",
                model="gpt-5.6-sol",
                cwd="C:/tmp/repo",
                prompt_file="prompt.txt",
            )
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-sol")

    def test_provider_default_omits_model_flag(self) -> None:
        adapter = _codex_adapter()
        with patch.object(adapter, "resolve_executable", return_value="codex"):
            argv = adapter.build_argv(
                mode="ask",
                prompt="hi",
                model=PROVIDER_DEFAULT,
                cwd="C:/tmp/repo",
                prompt_file="prompt.txt",
            )
        self.assertNotIn("--model", argv)

    def test_unavailable_model_rejected_clearly(self) -> None:
        adapter = _codex_adapter()
        with patch.object(
            adapter,
            "list_models",
            return_value=(["gpt-5.6-terra", "gpt-5.6-sol", PROVIDER_DEFAULT], "cli_debug_models"),
        ):
            res = resolve_model_for_run(
                adapter,
                agent_id="codex",
                mode="ask",
                selected_model="not-a-real-model",
            )
        self.assertFalse(res.ok)
        self.assertEqual(res.code, "model_invalid")
        self.assertIn("not-a-real-model", res.error)

    def test_no_selection_prefers_recommended_over_provider_default_token(self) -> None:
        adapter = _codex_adapter()

        def _details(*, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
            return {
                "models": ["gpt-5.6-luna", "gpt-5.6-terra", PROVIDER_DEFAULT],
                "recommended_model": "gpt-5.6-terra",
                "models_source": "cli_debug_models",
            }

        with patch.object(
            adapter,
            "list_models",
            return_value=(["gpt-5.6-luna", "gpt-5.6-terra", PROVIDER_DEFAULT], "cli_debug_models"),
        ):
            with patch.object(adapter, "list_model_details", side_effect=_details):
                res = resolve_model_for_run(
                    adapter,
                    agent_id="codex",
                    mode="ask",
                    selected_model="",
                )
        self.assertTrue(res.ok)
        self.assertEqual(res.resolved_model, "gpt-5.6-terra")
        self.assertNotEqual(res.resolved_model, PROVIDER_DEFAULT)


if __name__ == "__main__":
    unittest.main()
