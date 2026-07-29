"""Registry loader: env expansion and Live Processing wiring."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.registry.loader import expand_env, load_registry
from hub.settings import ROOT_DIR


class ExpandEnvTests(unittest.TestCase):
    def test_default_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CH_TEST_URL", None)
            self.assertEqual(expand_env("${CH_TEST_URL:-http://127.0.0.1:5050}"), "http://127.0.0.1:5050")

    def test_env_overrides_default(self) -> None:
        with patch.dict(os.environ, {"CH_TEST_URL": "http://example.local:9"}, clear=False):
            self.assertEqual(expand_env("${CH_TEST_URL:-http://127.0.0.1:5050}"), "http://example.local:9")

    def test_empty_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"CH_TEST_URL": ""}, clear=False):
            self.assertEqual(expand_env("${CH_TEST_URL:-fallback}"), "fallback")

    def test_bare_var_missing_becomes_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CH_TEST_BARE", None)
            self.assertEqual(expand_env("prefix-${CH_TEST_BARE}-suffix"), "prefix--suffix")


class LiveProcessingRegistryTests(unittest.TestCase):
    def test_repositories_yaml_loads_lp_entries(self) -> None:
        registry = load_registry(ROOT_DIR / "config" / "repositories.yaml")
        by_id = {repo.id: repo for repo in registry.repositories}
        self.assertIn("live-processing", by_id)
        self.assertIn("live-processing-local", by_id)
        self.assertIn("data-script", by_id)
        self.assertIn("report-template", by_id)
        self.assertNotIn("sample-cli", by_id)

        api = by_id["live-processing"]
        self.assertEqual(api.type, "api")
        self.assertTrue(api.base_url)
        self.assertEqual(api.health_check.path if api.health_check else None, "/api/healthz")
        self.assertTrue(api.git_url)
        cap_ids = {cap.id for cap in api.capabilities}
        self.assertEqual(cap_ids, {"healthz", "bulk_apply_history", "bulk_preview"})
        for cap in api.capabilities:
            self.assertEqual(str(cap.raw.get("http_method", "")).upper(), "GET")

        local = by_id["live-processing-local"]
        self.assertEqual(local.type, "command")
        self.assertEqual(local.name, "PMNP Live Processing")
        self.assertTrue(local.local_path)
        self.assertTrue(local.git_url)
        self.assertEqual(local.capabilities, [])
        self.assertEqual(api.repository_group_id, "pmnp-live-processing")
        self.assertEqual(local.repository_group_id, "pmnp-live-processing")

    def test_env_overrides_lp_base_url(self) -> None:
        yaml_text = """
repositories:
  - id: live-processing
    name: Live Processing
    type: api
    enabled: true
    base_url: "${LIVE_PROCESSING_BASE_URL:-http://127.0.0.1:5050}"
    health_check:
      type: http
      method: GET
      path: "/api/healthz"
    capabilities: []
defaults:
  max_concurrent_jobs: 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repositories.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            with patch.dict(os.environ, {"LIVE_PROCESSING_BASE_URL": "http://lp.test:7777"}, clear=False):
                registry = load_registry(path)
            self.assertEqual(registry.repositories[0].base_url, "http://lp.test:7777")


if __name__ == "__main__":
    unittest.main()
