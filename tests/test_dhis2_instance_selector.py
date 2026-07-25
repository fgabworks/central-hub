"""DHIS2 Stage/Live instance selector: profiles, persistence, switching, redaction."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hub.dhis2.client import Dhis2Client
from hub.dhis2.instance_profiles import (
    build_dhis2_settings_for_instance,
    default_instance_selection,
    list_dhis2_instance_profiles,
    resolve_dhis2_instance,
)
from hub.dhis2.instance_store import Dhis2InstanceStore


def _env(mapping: dict[str, str]):
    def getter(key: str) -> str | None:
        return mapping.get(key)

    return getter


STAGE_COMPLETE = {
    "STAGE_DHIS2_URL": "https://stage.example.org",
    "STAGE_DHIS2_USERNAME": "stage-user",
    "STAGE_DHIS2_PASSWORD": "stage-secret",
}
LIVE_COMPLETE = {
    "LIVE_DHIS2_URL": "https://live.example.org",
    "LIVE_DHIS2_USERNAME": "live-user",
    "LIVE_DHIS2_PASSWORD": "live-secret",
}


class ProfileListTests(unittest.TestCase):
    def test_stage_and_live_available(self) -> None:
        profiles = list_dhis2_instance_profiles(_env({**STAGE_COMPLETE, **LIVE_COMPLETE}))
        by_id = {p["id"]: p for p in profiles}
        self.assertTrue(by_id["stage"]["available"])
        self.assertTrue(by_id["live"]["available"])
        dumped = json.dumps(profiles)
        self.assertNotIn("stage-secret", dumped)
        self.assertNotIn("live-secret", dumped)
        self.assertNotIn("stage-user", dumped)

    def test_missing_profiles(self) -> None:
        profiles = list_dhis2_instance_profiles(
            _env({"STAGE_DHIS2_URL": "https://stage.example.org"})
        )
        by_id = {p["id"]: p for p in profiles}
        self.assertFalse(by_id["stage"]["available"])
        self.assertIn("STAGE_DHIS2_USERNAME", by_id["stage"]["missing_fields"])
        self.assertFalse(by_id["live"]["available"])


class ResolveInstanceTests(unittest.TestCase):
    def test_stage_resolve(self) -> None:
        resolved = resolve_dhis2_instance("stage", _env(STAGE_COMPLETE))
        self.assertTrue(resolved.is_configured)
        self.assertEqual(resolved.environment, "stage")
        self.assertEqual(resolved.base_url, "https://stage.example.org")

    def test_live_resolve(self) -> None:
        resolved = resolve_dhis2_instance("live", _env(LIVE_COMPLETE))
        self.assertTrue(resolved.is_configured)
        self.assertEqual(resolved.environment, "live")

    def test_settings_force_writes_false(self) -> None:
        settings = build_dhis2_settings_for_instance(
            "stage",
            _env({**STAGE_COMPLETE, "ALLOW_DHIS2_WRITES": "true"}),
        )
        self.assertFalse(settings.allow_writes)
        client = Dhis2Client(settings)
        self.assertFalse(client.writes_allowed())
        cfg = client.public_config()
        dumped = json.dumps(cfg)
        self.assertNotIn("stage-secret", dumped)
        self.assertNotIn("stage-user", dumped)


class SelectionDefaultTests(unittest.TestCase):
    def test_persisted_wins(self) -> None:
        self.assertEqual(
            default_instance_selection(
                available_ids=["stage", "live"],
                persisted="live",
                env_default="stage",
            ),
            "live",
        )

    def test_env_default_when_no_persisted(self) -> None:
        self.assertEqual(
            default_instance_selection(
                available_ids=["stage", "live"],
                persisted=None,
                env_default="stage",
            ),
            "stage",
        )

    def test_none_when_missing(self) -> None:
        self.assertIsNone(
            default_instance_selection(
                available_ids=["stage"],
                persisted="live",
                env_default="live",
            )
        )


class PersistenceTests(unittest.TestCase):
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Dhis2InstanceStore(Path(tmp) / "active_instance.json")
            self.assertIsNone(store.get_instance())
            store.save("stage")
            self.assertEqual(store.get_instance(), "stage")
            store.save("live")
            self.assertEqual(store.get_instance(), "live")
            raw = (Path(tmp) / "active_instance.json").read_text(encoding="utf-8")
            self.assertIn('"instance": "live"', raw)
            self.assertNotIn("password", raw.lower())


class SwitchingRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        for key in (
            "DHIS2_BASE_URL",
            "DHIS2_USERNAME",
            "DHIS2_PASSWORD",
            "DHIS2_ENVIRONMENT",
        ):
            os.environ.pop(key, None)
        os.environ["STAGE_DHIS2_URL"] = "https://stage.example.org"
        os.environ["STAGE_DHIS2_USERNAME"] = "stage-user"
        os.environ["STAGE_DHIS2_PASSWORD"] = "stage-secret"
        os.environ["LIVE_DHIS2_URL"] = "https://live.example.org"
        os.environ["LIVE_DHIS2_USERNAME"] = "live-user"
        os.environ["LIVE_DHIS2_PASSWORD"] = "live-secret"
        os.environ["ALLOW_DHIS2_WRITES"] = "false"

        import importlib
        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.app.config["DHIS2_INSTANCE_STORE"] = Dhis2InstanceStore(
            root / "active_instance.json"
        )
        # Reset selection for deterministic tests
        cls.app.config["DHIS2_INSTANCE"] = None
        from hub.dhis2.instance_profiles import build_dhis2_settings_for_instance

        cls.app.config["DHIS2"] = Dhis2Client(build_dhis2_settings_for_instance(None))
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_overview_shows_selector(self) -> None:
        r = self.client.get("/dhis2")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("DHIS2 instance", html)
        self.assertIn("Central Hub environment", html)
        self.assertIn("Stage", html)
        self.assertIn("Live", html)
        self.assertNotIn("stage-secret", html)
        self.assertNotIn("live-secret", html)

    def test_discovery_disabled_until_connected(self) -> None:
        r = self.client.get("/dhis2/discover", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dhis2", r.headers.get("Location", ""))

    def test_switch_persists_and_recreates_client(self) -> None:
        fake_status = {
            "ok": True,
            "status": "online",
            "detail": "ok",
            "latency_ms": 12,
            "system": {"version": "2.40"},
            "user": None,
            "allow_writes": False,
            "mode": "readonly",
            "base_url": "https://stage.example.org",
        }
        with patch.object(Dhis2Client, "check_status", return_value=fake_status):
            r = self.client.post(
                "/dhis2",
                data={"action": "select_instance", "instance": "stage"},
                follow_redirects=False,
            )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.app.config["DHIS2_INSTANCE"], "stage")
        self.assertEqual(
            self.app.config["DHIS2_INSTANCE_STORE"].get_instance(), "stage"
        )
        self.assertTrue(self.app.config["DHIS2"].settings.is_configured)
        self.assertEqual(self.app.config["DHIS2"].settings.environment, "stage")
        self.assertFalse(self.app.config["DHIS2"].writes_allowed())

        # Switch to live
        fake_status["base_url"] = "https://live.example.org"
        with patch.object(Dhis2Client, "check_status", return_value=fake_status):
            r2 = self.client.post(
                "/dhis2",
                data={"action": "select_instance", "instance": "live"},
                follow_redirects=True,
            )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(self.app.config["DHIS2_INSTANCE"], "live")
        html = r2.get_data(as_text=True)
        self.assertIn("Live instance selected", html)
        self.assertNotIn("live-secret", html)


if __name__ == "__main__":
    unittest.main()
