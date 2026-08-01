"""Focused end-to-end coverage for HCSC–RF report generation.

Layer map:
  UI click → scopeQuery → GET /api/dhis2/hcsc-indicators/report → JSON → cards/table render

Mocks first (no DHIS2). Optional Live GET-only check via HCSC_LIVE_REPORT_E2E=1.
Stage is under maintenance — Live only for the optional check.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _sample_report(*, cache_hit: bool = False, empty: bool = False) -> dict:
    rows = []
    if not empty:
        rows = [
            {
                "indicator_key": "eligible_households",
                "display_name": "Eligible Households",
                "display_group": "overview",
                "value_text": "127",
                "count": 127.0,
                "source_badge": "PI",
                "source_badge_label": "Program Indicator",
                "validation_status": "Not Yet Validated",
                "last_updated": "2026-08-02T00:00:00+00:00",
                "freshness": "2026-08-02T00:00:00+00:00",
            },
            {
                "indicator_key": "convergence_rate",
                "display_name": "Overall Convergence Rate",
                "display_group": "overview",
                "value_text": "74.2%",
                "percentage": 74.2,
                "source_badge": "PI",
                "source_badge_label": "Program Indicator",
                "validation_status": "Not Yet Validated",
                "last_updated": "2026-08-02T00:00:00+00:00",
                "freshness": "2026-08-02T00:00:00+00:00",
            },
        ]
    return {
        "ok": True,
        "environment": "live",
        "period": "2026Q2",
        "org_unit": "mkvLp2ySTPb",
        "disaggregation": "none",
        "results": rows,
        "sections": [
            {"id": "overview", "label": "Overview", "results": rows},
        ],
        "freshness": "2026-08-02T00:00:00+00:00",
        "cache": {"hit": cache_hit},
        "timings": {"total_ms": 42, "http_requests": 0 if cache_hit else 1},
        "adapters_used": ["dhis2_analytics"],
        "dhis2_writes": 0,
        "retrieval": {"open_sql_workspace": False},
    }


class ReportGenerationPageContractTests(unittest.TestCase):
    """Static contracts: click wiring, request construction, terminal gen states."""

    def test_generate_click_and_request_construction(self):
        html = (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        picker = (ROOT / "static" / "js" / "dhis2_org_unit_picker.js").read_text(encoding="utf-8")

        self.assertIn('id="hcsc-run"', html)
        self.assertIn('type="button"', html)
        self.assertIn('onsubmit="return false;"', html)
        self.assertIn('id="hcsc-ou"', html)
        self.assertIn('type="hidden"', html)
        self.assertIn("data-report-url", html)
        self.assertIn("hcsc-generate-fix-1", html)
        self.assertIn("ou-sync-immediate-1", html)
        self.assertNotIn("BOOT && BOOT.environments", js)
        self.assertIn("(boot && boot.environments)", js)

        self.assertIn("function loadReport(force)", js)
        self.assertIn("function scopeQuery(force", js)
        self.assertIn('"?environment="', js)
        self.assertIn("encodeURIComponent(env)", js)
        self.assertIn("encodeURIComponent(period)", js)
        self.assertIn("encodeURIComponent(ou)", js)
        self.assertIn("data-report-url", js)
        self.assertIn("if (isActiveGeneration())", js)
        self.assertIn("state.activeRequestId !== requestId", js)
        self.assertIn("AbortController", js)
        self.assertIn("CLIENT_TIMEOUT_MS", js)
        self.assertIn("Report response could not be rendered", js)
        self.assertIn("Report returned no indicators", js)
        self.assertIn("empty_result", js)

        # Cascade must commit UID immediately (not only after child fetch).
        self.assertIn("Commit the selected UID immediately", picker)
        self.assertIn("syncSelection();", picker)

        # Terminal generation exits
        for phase in (
            "success_fresh",
            "success_cached",
            "success_stale",
            "cancelled",
            "timed_out",
            "error",
        ):
            self.assertIn(phase, js)
        self.assertIn("setGenPhase(GEN.TIMED_OUT", js)
        self.assertIn("setGenPhase(GEN.ERROR", js)
        self.assertIn("setGenPhase(GEN.CANCELLED", js)
        self.assertIn("setGenPhase(state.cacheHit ? GEN.SUCCESS_CACHED : GEN.SUCCESS_FRESH", js)


class ReportGenerationApiE2ETests(unittest.TestCase):
    """Flask-level E2E with mocked HCSC service — no DHIS2 network."""

    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.app = create_app()
        cls.app.config["TESTING"] = True

    def setUp(self):
        self.client = self.app.test_client()
        self.calls: list[dict] = []
        self.svc = self.app.config["HCSC_INDICATORS"]

    def _patch_report(self, side_effect):
        return mock.patch.object(self.svc, "report", side_effect=side_effect)

    def _recording_ok(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _sample_report(cache_hit=False)

    def test_page_opens_and_exposes_report_endpoint(self):
        page = self.client.get("/dhis2/hcsc-indicators")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Generate Report", html)
        self.assertIn("/api/dhis2/hcsc-indicators/report", html)
        self.assertRegex(html, r'id="hcsc-run"[^>]*type="button"|type="button"[^>]*id="hcsc-run"')
        self.assertIn('id="hcsc-ou"', html)
        boot = re.search(r'id="hcsc-bootstrap">(\{.*?\})</script>', html, re.S)
        self.assertIsNotNone(boot)
        import json

        payload = json.loads(boot.group(1))
        qids = [q["id"] for q in (payload.get("periods") or {}).get("quarters") or []]
        self.assertIn("2026Q2", qids)

    def test_generate_params_hit_report_exactly_once(self):
        with self._patch_report(self._recording_ok):
            r = self.client.get(
                "/api/dhis2/hcsc-indicators/report",
                query_string={
                    "environment": "live",
                    "period": "2026Q2",
                    "orgUnit": "mkvLp2ySTPb",
                    "disaggregation": "none",
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call["environment"], "live")
        self.assertEqual(call["period"], "2026Q2")
        self.assertEqual(call["org_unit"], "mkvLp2ySTPb")
        self.assertEqual(call["disaggregation"], "none")
        self.assertFalse(call.get("force_refresh"))

        # Schema required for Overview cards + table rows
        self.assertGreaterEqual(len(body["results"]), 1)
        row = body["results"][0]
        self.assertIn("indicator_key", row)
        self.assertIn("value_text", row)
        self.assertIn("display_name", row)
        self.assertTrue(any(x["indicator_key"] == "eligible_households" for x in body["results"]))
        self.assertIn("sections", body)
        self.assertEqual(body.get("dhis2_writes"), 0)

    def test_fresh_flag_forces_refresh(self):
        with self._patch_report(self._recording_ok):
            r = self.client.get(
                "/api/dhis2/hcsc-indicators/report",
                query_string={
                    "environment": "live",
                    "period": "2026Q2",
                    "orgUnit": "mkvLp2ySTPb",
                    "disaggregation": "none",
                    "fresh": "1",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.calls[0]["force_refresh"])

    def test_empty_response_schema(self):
        def empty(**kwargs):
            self.calls.append(kwargs)
            return _sample_report(empty=True)

        with self._patch_report(empty):
            r = self.client.get(
                "/api/dhis2/hcsc-indicators/report",
                query_string={
                    "environment": "live",
                    "period": "2026Q2",
                    "orgUnit": "mkvLp2ySTPb",
                    "disaggregation": "none",
                },
            )
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["results"], [])

    def test_api_error_path(self):
        from hub.dhis2_reports.security import ReportSecurityError

        def boom(**kwargs):
            self.calls.append(kwargs)
            raise ReportSecurityError(
                "Organisation unit is required.", code="invalid_org_unit"
            )

        with self._patch_report(boom):
            r = self.client.get(
                "/api/dhis2/hcsc-indicators/report",
                query_string={
                    "environment": "live",
                    "period": "2026Q2",
                    "orgUnit": "bad",
                    "disaggregation": "none",
                },
            )
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body.get("ok", True))
        self.assertTrue(body.get("error") or body.get("code"))

    def test_cached_response(self):
        def cached(**kwargs):
            self.calls.append(kwargs)
            return _sample_report(cache_hit=True)

        with self._patch_report(cached):
            r = self.client.get(
                "/api/dhis2/hcsc-indicators/report",
                query_string={
                    "environment": "live",
                    "period": "2026Q2",
                    "orgUnit": "mkvLp2ySTPb",
                    "disaggregation": "none",
                },
            )
        body = r.get_json()
        self.assertTrue(body["cache"]["hit"])
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(len(body["results"]), 1)

    def test_missing_ou_rejected(self):
        r = self.client.get(
            "/api/dhis2/hcsc-indicators/report",
            query_string={
                "environment": "live",
                "period": "2026Q2",
                "disaggregation": "none",
            },
        )
        self.assertIn(r.status_code, {400, 422})
        body = r.get_json() or {}
        self.assertFalse(body.get("ok", True))
        self.assertTrue(body.get("error") or body.get("code"))


class ReportGenerationClientStateContractTests(unittest.TestCase):
    """Client generation lifecycle contracts (duplicate, late, timeout, terminals)."""

    def test_duplicate_and_late_and_terminal_paths(self):
        js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        self.assertIn("if (isActiveGeneration()) {\n      return;", js)
        self.assertIn("late / superseded", js)
        self.assertIn("stopActiveRequest(\"timeout\")", js)
        self.assertIn("CLIENT_TIMEOUT_MS", js)
        self.assertIn("SLOW_AFTER_MS", js)
        self.assertIn("cancelGeneration", js)
        # Animation only while active
        self.assertIn("if (isActiveGeneration()) {\n        badge.innerHTML =", js)
        # Every terminal path clears active request / timers
        self.assertIn("clearGenTimers()", js)
        self.assertIn("state.activeRequestId = null", js)
        for marker in (
            "GEN.SUCCESS_FRESH",
            "GEN.SUCCESS_CACHED",
            "GEN.TIMED_OUT",
            "GEN.ERROR",
            "GEN.CANCELLED",
        ):
            self.assertIn(marker, js)


@unittest.skipUnless(
    os.environ.get("HCSC_LIVE_REPORT_E2E") == "1",
    "Optional Live GET-only report check (set HCSC_LIVE_REPORT_E2E=1)",
)
class LiveReportGenerationSafeCheck(unittest.TestCase):
    """Safe Live GET-only smoke — never Stage (maintenance)."""

    def test_live_report_get_only(self):
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.get(
            "/api/dhis2/hcsc-indicators/report",
            query_string={
                "environment": "live",
                "period": "2026Q2",
                "orgUnit": "mkvLp2ySTPb",
                "disaggregation": "none",
            },
        )
        if r.status_code == 502:
            self.skipTest("Live DHIS2 temporarily unavailable (502)")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body.get("ok"), body.get("error"))
        self.assertEqual(body.get("dhis2_writes"), 0)
        self.assertGreaterEqual(len(body.get("results") or []), 1)
        self.assertTrue(any(row.get("value_text") for row in body["results"]))


class RenderMappingSchemaTests(unittest.TestCase):
    """Response fields the UI needs to leave Generating and paint cards/table."""

    def test_mock_payload_has_card_and_table_fields(self):
        payload = _sample_report()
        card_keys = {
            "eligible_households",
            "approved_eligible_households",
            "convergent_households",
            "convergence_rate",
            "completion_validated_eligible_rate",
        }
        by_key = {r["indicator_key"]: r for r in payload["results"]}
        self.assertIn("eligible_households", by_key)
        self.assertTrue(by_key["eligible_households"]["value_text"])
        # At least one overview card key present
        self.assertTrue(card_keys.intersection(by_key))
        # Table needs display_name + value_text
        for row in payload["results"]:
            self.assertTrue(row.get("display_name"))
            self.assertTrue(row.get("value_text"))


if __name__ == "__main__":
    unittest.main()
