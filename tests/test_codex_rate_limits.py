"""Tests for Codex account rate-limit normalization and CLIMATE API wiring."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.agent_center.adapters.codex_app_server import CodexAppServerError
from hub.climate.codex_limits import (
    CodexRateLimitsService,
    normalize_rate_limits_response,
    reset_codex_rate_limits_service_for_tests,
)
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


SAMPLE_RATE_LIMITS = {
    "rateLimits": {
        "limitId": "codex",
        "limitName": None,
        "primary": {
            "usedPercent": 31,
            "windowDurationMins": 300,
            "resetsAt": 1735689720,
        },
        "secondary": {
            "usedPercent": 12,
            "windowDurationMins": 10080,
            "resetsAt": 1736294520,
        },
        "credits": {"hasCredits": True, "unlimited": False, "balance": "42.5"},
        "planType": "pro",
        "individualLimit": None,
        "rateLimitReachedType": None,
    },
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": None,
            "primary": {
                "usedPercent": 31,
                "windowDurationMins": 300,
                "resetsAt": 1735689720,
            },
            "secondary": {
                "usedPercent": 12,
                "windowDurationMins": 10080,
                "resetsAt": 1736294520,
            },
            "credits": {"hasCredits": True, "unlimited": False, "balance": "42.5"},
            "planType": "pro",
            "individualLimit": None,
            "rateLimitReachedType": None,
        },
        "codex_other": {
            "limitId": "codex_other",
            "limitName": "codex_other",
            "primary": {
                "usedPercent": 88,
                "windowDurationMins": 30,
                "resetsAt": 1735693200,
            },
            "secondary": None,
            "credits": None,
            "planType": "pro",
            "individualLimit": None,
            "rateLimitReachedType": None,
        },
    },
    "rateLimitResetCredits": None,
}


class FakeAppServerSession:
    def __init__(self, responses=None, errors=None):
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.calls = []
        self.closed = False

    def request(self, method, params=None, *, timeout=8.0):
        self.calls.append({"method": method, "params": params or {}, "timeout": timeout})
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            return {}
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class CodexRateLimitsNormalizeTests(unittest.TestCase):
    def test_normalize_multi_bucket_remaining_percent(self):
        payload = normalize_rate_limits_response(SAMPLE_RATE_LIMITS)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["remainingPercent"], 12.0)  # most constraining primary
        self.assertEqual(len(payload["buckets"]), 3)
        by_key = {(row["limitId"], row["window"]): row for row in payload["buckets"]}
        self.assertEqual(by_key[("codex", "primary")]["usedPercent"], 31)
        self.assertEqual(by_key[("codex", "primary")]["remainingPercent"], 69)
        self.assertEqual(by_key[("codex", "secondary")]["remainingPercent"], 88)
        self.assertEqual(by_key[("codex_other", "primary")]["remainingPercent"], 12)
        self.assertEqual(payload["credits"]["balance"], "42.5")
        self.assertEqual(payload["planType"], "pro")

    def test_normalize_empty_is_unavailable(self):
        payload = normalize_rate_limits_response({})
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "Codex limit unavailable")
        self.assertIsNone(payload["remainingPercent"])


class CodexRateLimitsServiceTests(unittest.TestCase):
    def setUp(self):
        reset_codex_rate_limits_service_for_tests()

    def tearDown(self):
        reset_codex_rate_limits_service_for_tests()

    def test_cache_avoids_repeat_query(self):
        session = FakeAppServerSession(responses=[SAMPLE_RATE_LIMITS])
        service = CodexRateLimitsService(
            discover_executable=lambda: "codex",
            session_factory=lambda _exe, _note: session,
            cache_ttl=60,
        )
        first = service.get(refresh=False)
        second = service.get(refresh=False)
        self.assertTrue(first["available"])
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["method"], "account/rateLimits/read")

    def test_refresh_bypasses_cache(self):
        session = FakeAppServerSession(responses=[SAMPLE_RATE_LIMITS, SAMPLE_RATE_LIMITS])
        service = CodexRateLimitsService(
            discover_executable=lambda: "codex",
            session_factory=lambda _exe, _note: session,
            cache_ttl=60,
        )
        service.get(refresh=False)
        service.get(refresh=True)
        self.assertEqual(len(session.calls), 2)

    def test_chatgpt_auth_required_is_unavailable(self):
        session = FakeAppServerSession(
            errors=[CodexAppServerError("chatgpt authentication required to read rate limits", code=-32600)]
        )
        service = CodexRateLimitsService(
            discover_executable=lambda: "codex",
            session_factory=lambda _exe, _note: session,
        )
        payload = service.get(refresh=True)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "Codex limit unavailable")
        self.assertTrue(payload["authRequired"])
        self.assertIsNone(payload["remainingPercent"])

    def test_notification_updates_cache(self):
        session = FakeAppServerSession(responses=[SAMPLE_RATE_LIMITS])
        holder = {"handler": None}

        def factory(_exe, on_note):
            holder["handler"] = on_note
            return session

        service = CodexRateLimitsService(
            discover_executable=lambda: "codex",
            session_factory=factory,
            cache_ttl=60,
        )
        service.get(refresh=True)
        holder["handler"](
            "account/rateLimits/updated",
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 50, "windowDurationMins": 300, "resetsAt": 1735689720},
                    "secondary": None,
                    "planType": "pro",
                }
            },
        )
        cached = service.get(refresh=False)
        self.assertTrue(cached["cached"])
        primary = next(
            row for row in cached["buckets"]
            if row["limitId"] == "codex" and row["window"] == "primary"
        )
        self.assertEqual(primary["remainingPercent"], 50)
        self.assertEqual(cached["source"], "account/rateLimits/updated")


class ClimateCodexRateLimitsApiTests(unittest.TestCase):
    def setUp(self):
        reset_codex_rate_limits_service_for_tests()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        work = root / "work"
        personal = root / "personal"
        work.mkdir()
        personal.mkdir()
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(work)),
            Repository(
                id="personal-repo",
                name="Personal",
                type="command",
                enabled=True,
                local_path=str(personal),
                tags=["arctic"],
            ),
        ])
        self.svc = ClimateService(
            self.registry,
            RepositoryWorkspaceService(WorkspaceSettings()),
            FakeCodingAdapter(),
        )

    def tearDown(self):
        reset_codex_rate_limits_service_for_tests()
        self.temp.cleanup()

    def test_arctic_workspace_returns_unavailable(self):
        payload = self.svc.codex_rate_limits("personal")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["message"], "Codex limit unavailable")
        self.assertIsNone(payload["remainingPercent"])

    def test_vanta_uses_rate_limits_service(self):
        with mock.patch(
            "hub.climate.service.get_codex_rate_limits_service"
        ) as get_svc:
            fake = mock.Mock()
            fake.get.return_value = normalize_rate_limits_response(SAMPLE_RATE_LIMITS)
            get_svc.return_value = fake
            payload = self.svc.codex_rate_limits("work", refresh=True)
        fake.get.assert_called_once_with(refresh=True)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["remainingPercent"], 12.0)
        self.assertEqual(payload["workspace"], "work")


class ClimateUiRateLimitMarkers(unittest.TestCase):
    def test_ui_wires_rate_limit_fetch(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        template = (root / "templates" / "climate.html").read_text(encoding="utf-8")
        limits_py = (root / "hub" / "climate" / "codex_limits.py").read_text(encoding="utf-8")
        self.assertIn("fetchCodexRateLimits", script)
        self.assertIn("/providers/codex/rate-limits", script)
        self.assertIn("account/rateLimits/read", limits_py)
        self.assertIn("account/rateLimits/updated", limits_py)
        self.assertIn("climate-usage-refresh", template)
        self.assertIn("Codex limit unavailable", template)
        self.assertIn("climate-usage-limits", template)
        self.assertNotIn("data-quota-remaining", script)
        self.assertNotIn("quota_remaining_percent", script)


if __name__ == "__main__":
    unittest.main()
