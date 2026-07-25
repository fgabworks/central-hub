"""Mocked tests for hardened GET-only DHIS2 client transport."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.settings import Dhis2Settings


def _settings(**overrides) -> Dhis2Settings:
    base = dict(
        base_url="https://dhis2.example.org",
        username="stage_user",
        password="secret-password",
        timeout_seconds=10.0,
        allow_writes=False,
        enabled=True,
        probe_timeout_seconds=3.0,
        retry_max=2,
        retry_backoff_seconds=0.0,  # no sleep in tests
        page_size=2,
        max_pages=2,
        http_pool_maxsize=4,
    )
    base.update(overrides)
    return Dhis2Settings(**base)


def _response(status: int = 200, payload=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or ""
    if payload is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = payload
    return resp


class ClientTransportTests(unittest.TestCase):
    def test_session_uses_basic_auth_and_pool(self) -> None:
        client = Dhis2Client(_settings())
        self.assertEqual(client._session.auth, ("stage_user", "secret-password"))
        self.assertIn("https://", client._session.adapters)
        client.close()

    def test_public_config_exposes_reliability_knobs_not_password(self) -> None:
        cfg = Dhis2Client(_settings()).public_config()
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["mode"], "readonly")
        self.assertEqual(cfg["probe_timeout_seconds"], 3.0)
        self.assertEqual(cfg["retry_max"], 2)
        self.assertEqual(cfg["page_size"], 2)
        self.assertEqual(cfg["max_pages"], 2)
        self.assertNotIn("password", cfg)
        self.assertTrue(cfg["password_set"])
        self.assertFalse(cfg["allow_writes"])

    def test_writes_allowed_always_false(self) -> None:
        client = Dhis2Client(_settings(allow_writes=True))
        self.assertFalse(client.writes_allowed())

    def test_retry_on_503_then_success(self) -> None:
        client = Dhis2Client(_settings(retry_max=2, retry_backoff_seconds=0.0))
        ok = _response(200, {"version": "2.40"})
        fail = _response(503, None, text="busy")
        with patch.object(client._session, "get", side_effect=[fail, ok]) as get:
            data = client._get_json("/api/system/info")
        self.assertEqual(data["version"], "2.40")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(client.request_stats()["retry"], 1)
        self.assertGreaterEqual(client.request_stats()["get"], 2)

    def test_no_retry_on_404(self) -> None:
        client = Dhis2Client(_settings(retry_max=3, retry_backoff_seconds=0.0))
        with patch.object(
            client._session, "get", return_value=_response(404, None, text="missing")
        ) as get:
            with self.assertRaises(Dhis2Error) as ctx:
                client._get_json("/api/dataElements/AbCdEfGhIj1")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(client.request_stats()["retry"], 0)

    def test_retry_on_timeout_then_fail(self) -> None:
        client = Dhis2Client(_settings(retry_max=1, retry_backoff_seconds=0.0))
        with patch.object(
            client._session, "get", side_effect=requests.Timeout()
        ) as get:
            with self.assertRaises(Dhis2Error) as ctx:
                client._get_json("/api/system/info")
        self.assertIn("timed out", ctx.exception.message)
        self.assertEqual(get.call_count, 2)  # initial + 1 retry
        self.assertEqual(client.request_stats()["timeouts"], 2)

    def test_check_status_uses_probe_timeout(self) -> None:
        client = Dhis2Client(_settings(probe_timeout_seconds=3.0))
        info = _response(
            200,
            {
                "version": "2.40.3",
                "systemName": "Stage",
                "serverDate": "2026-07-25",
            },
        )
        me = _response(200, {"id": "u1", "username": "stage_user", "displayName": "Stage"})
        with patch.object(client._session, "get", side_effect=[info, me]) as get:
            status = client.check_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["mode"], "readonly")
        # First call is system/info with probe timeout
        self.assertEqual(get.call_args_list[0].kwargs.get("timeout"), 3.0)

    def test_iter_collection_respects_max_pages_ceiling(self) -> None:
        client = Dhis2Client(_settings(page_size=2, max_pages=2))

        def fake_get(url, params=None, timeout=None):
            page = int((params or {}).get("page") or 1)
            # Pretend 3 pages exist (6 items); client must stop at max_pages=2
            return _response(
                200,
                {
                    "dataElements": [
                        {"id": f"AbCdEfGhI{page}a", "name": f"DE{page}a"},
                        {"id": f"AbCdEfGhI{page}b", "name": f"DE{page}b"},
                    ],
                    "pager": {"page": page, "pageCount": 3, "total": 6, "pageSize": 2},
                },
            )

        with patch.object(client._session, "get", side_effect=fake_get):
            result = client.iter_collection("dataElements", page_size=2, max_pages=2)
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total"], 6)

    def test_iter_collection_stops_when_exhausted(self) -> None:
        client = Dhis2Client(_settings(page_size=10, max_pages=5))
        with patch.object(
            client._session,
            "get",
            return_value=_response(
                200,
                {
                    "optionSets": [{"id": "OpTiOnSeT01", "name": "YesNo"}],
                    "pager": {"page": 1, "pageCount": 1, "total": 1, "pageSize": 10},
                },
            ),
        ):
            result = client.iter_collection("optionSets")
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["pages_fetched"], 1)

    def test_secrets_redacted_from_error_body(self) -> None:
        client = Dhis2Client(_settings())
        with patch.object(
            client._session,
            "get",
            return_value=_response(500, None, text="failed for secret-password"),
        ):
            with self.assertRaises(Dhis2Error) as ctx:
                client._get_json("/api/me")
        self.assertNotIn("secret-password", ctx.exception.message)
        self.assertIn("***", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
