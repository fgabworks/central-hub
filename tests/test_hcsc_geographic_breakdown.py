"""Focused tests for HCSC–RF geographic breakdown (OU-level children)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.dhis2.client import Dhis2Error
from hub.dhis2_reports.org_unit_store import OrgUnitStore
from hub.dhis2_reports.security import ReportSecurityError
from hub.hcsc_indicators.cache import REPORT_CACHE, report_cache_key
from hub.hcsc_indicators.geographic_breakdown import (
    BREAKDOWN_BARANGAY,
    BREAKDOWN_MUNICIPALITY,
    BREAKDOWN_NONE,
    BREAKDOWN_PROVINCE,
    BREAKDOWN_REGION,
    breakdown_thresholds,
    format_estimate,
    options_for_parent_level,
    validate_breakdown_for_parent,
)
from hub.hcsc_indicators.service import HcscIndicatorService
from hub.settings import Dhis2Settings

from tests.test_hcsc_indicators import SAMPLE_YAML


class MultiOuAnalyticsClient:
    """GET-only fake that returns one analytics row set per OU in the request."""

    def __init__(self, values_by_dx: dict[str, float], *, environment: str = "stage"):
        self._values = values_by_dx
        self.settings = Dhis2Settings(
            base_url="https://example.test",
            username="u",
            password="p",
            timeout_seconds=10.0,
            enabled=True,
            allow_writes=False,
            environment=environment,
        )
        self.get_analytics_calls = 0
        self.ou_batches: list[list[str]] = []
        self.write_calls = 0

    def get_analytics(self, params):
        self.get_analytics_calls += 1
        dx: list[str] = []
        ous: list[str] = ["OuUidParent01"]
        if isinstance(params, list):
            for k, v in params:
                if k == "dimension" and str(v).startswith("dx:"):
                    dx = [x for x in str(v)[3:].split(";") if x]
                if k == "dimension" and str(v).startswith("ou:"):
                    ous = [x for x in str(v)[3:].split(";") if x]
        self.ou_batches.append(list(ous))
        rows = []
        for ou in ous:
            for uid in dx:
                if uid not in self._values:
                    continue
                val = self._values[uid]
                # Slightly vary child values by OU suffix digit when present.
                bump = sum(ord(c) for c in ou[-2:]) % 7
                rows.append(
                    [
                        uid,
                        "2026Q1",
                        ou,
                        float(val) + bump,
                        (float(val) + bump) * 0.4,
                        100.0,
                    ]
                )
        return {
            "rows": rows,
            "headers": [
                {"name": "dx"},
                {"name": "pe"},
                {"name": "ou"},
                {"name": "value"},
                {"name": "numerator"},
                {"name": "denominator"},
            ],
        }

    def close(self):
        return None

    def writes_allowed(self):
        return False


class GeographicBreakdownRuleTests(unittest.TestCase):
    def test_valid_choices_by_ou_level(self):
        national = {o["id"] for o in options_for_parent_level(1)}
        self.assertEqual(
            national,
            {
                BREAKDOWN_NONE,
                BREAKDOWN_REGION,
                BREAKDOWN_PROVINCE,
                BREAKDOWN_MUNICIPALITY,
                BREAKDOWN_BARANGAY,
            },
        )
        region = {o["id"] for o in options_for_parent_level(2)}
        self.assertEqual(
            region,
            {
                BREAKDOWN_NONE,
                BREAKDOWN_PROVINCE,
                BREAKDOWN_MUNICIPALITY,
                BREAKDOWN_BARANGAY,
            },
        )
        province = {o["id"] for o in options_for_parent_level(3)}
        self.assertEqual(
            province,
            {BREAKDOWN_NONE, BREAKDOWN_MUNICIPALITY, BREAKDOWN_BARANGAY},
        )
        mun = {o["id"] for o in options_for_parent_level(4)}
        self.assertEqual(mun, {BREAKDOWN_NONE, BREAKDOWN_BARANGAY})
        brgy = {o["id"] for o in options_for_parent_level(5)}
        self.assertEqual(brgy, {BREAKDOWN_NONE})

    def test_invalid_levels_rejected(self):
        with self.assertRaises(ReportSecurityError):
            validate_breakdown_for_parent(parent_level=2, geographic_breakdown="region")
        with self.assertRaises(ReportSecurityError):
            validate_breakdown_for_parent(parent_level=3, geographic_breakdown="province")
        with self.assertRaises(ReportSecurityError):
            validate_breakdown_for_parent(parent_level=5, geographic_breakdown="barangay")
        with self.assertRaises(ReportSecurityError):
            validate_breakdown_for_parent(parent_level=2, geographic_breakdown="galaxy")
        self.assertEqual(
            validate_breakdown_for_parent(parent_level=2, geographic_breakdown="none"),
            BREAKDOWN_NONE,
        )
        self.assertEqual(
            validate_breakdown_for_parent(parent_level=2, geographic_breakdown="province"),
            BREAKDOWN_PROVINCE,
        )

    def test_format_estimate_and_thresholds(self):
        self.assertEqual(format_estimate(17, "province"), "17 provinces")
        self.assertEqual(format_estimate(1842, "barangay"), "1,842 barangays")
        with mock.patch.dict(
            os.environ,
            {
                "HCSC_BREAKDOWN_CONFIRM_AT": "100",
                "HCSC_BREAKDOWN_WARN_AT": "20",
                "HCSC_BREAKDOWN_OU_CHUNK": "25",
            },
            clear=False,
        ):
            th = breakdown_thresholds()
            self.assertEqual(th["confirm_at"], 100)
            self.assertEqual(th["warn_at"], 20)
            self.assertEqual(th["analytics_ou_chunk"], 25)


class GeographicBreakdownServiceTests(unittest.TestCase):
    def setUp(self):
        REPORT_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.reg_path = Path(self.tmp.name) / "reg.yaml"
        self.reg_path.write_text(SAMPLE_YAML, encoding="utf-8")
        self.ou_store = OrgUnitStore(Path(self.tmp.name) / "ou.db")
        self.clients: dict[str, MultiOuAnalyticsClient] = {}

        # Region III with 3 provinces; one province with 2 municipalities.
        self.ou_store.upsert_rows(
            "stage",
            [
                {
                    "id": "OuUidRgn003",
                    "name": "Region III",
                    "level": 2,
                    "path": "/PH/R03",
                    "path_label": "Philippines / Region III",
                    "has_children": True,
                },
                {
                    "id": "OuUidPrvA01",
                    "name": "Province A",
                    "level": 3,
                    "parent_uid": "OuUidRgn003",
                    "path": "/PH/R03/P01",
                    "path_label": "Philippines / Region III / Province A",
                },
                {
                    "id": "OuUidPrvB02",
                    "name": "Province B",
                    "level": 3,
                    "parent_uid": "OuUidRgn003",
                    "path": "/PH/R03/P02",
                    "path_label": "Philippines / Region III / Province B",
                },
                {
                    "id": "OuUidPrvC03",
                    "name": "Province C",
                    "level": 3,
                    "parent_uid": "OuUidRgn003",
                    "path": "/PH/R03/P03",
                    "path_label": "Philippines / Region III / Province C",
                },
                {
                    "id": "OuUidMunA01",
                    "name": "Municipality 1",
                    "level": 4,
                    "parent_uid": "OuUidPrvA01",
                    "path": "/PH/R03/P01/M01",
                    "path_label": "Philippines / Region III / Province A / Municipality 1",
                },
                {
                    "id": "OuUidMunA02",
                    "name": "Municipality 2",
                    "level": 4,
                    "parent_uid": "OuUidPrvA01",
                    "path": "/PH/R03/P01/M02",
                    "path_label": "Philippines / Region III / Province A / Municipality 2",
                },
                {
                    "id": "OuUidBryA01",
                    "name": "Barangay 1",
                    "level": 5,
                    "parent_uid": "OuUidMunA01",
                    "path": "/PH/R03/P01/M01/B01",
                    "path_label": "Philippines / Region III / Province A / Municipality 1 / Barangay 1",
                },
            ],
        )

        def factory(env: str):
            if env not in self.clients:
                self.clients[env] = MultiOuAnalyticsClient(
                    {
                        "fxmvSiKfEpn": 100,
                        "LOMZy9q1euI": 40,
                        "BSqDSIpHhoT": 80,
                        "qzjKcfO9J2w": 50,
                        "jkgkU9EiJ5k": 55,
                        "fgfeI3Az7zv": 11,
                        "r5cHtnYeyXd": 20,
                        "S1hLvdJSuiZ": 74.16,
                    },
                    environment=env,
                )
            return self.clients[env]

        self.svc = HcscIndicatorService(
            client_factory=factory,
            registry_path=self.reg_path,
            ou_store=self.ou_store,
        )

    def tearDown(self):
        REPORT_CACHE.clear()

    def test_none_returns_parent_only(self):
        payload = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="none",
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["results"])
        geo = payload["geographic_breakdown"]
        self.assertEqual(geo.get("mode"), "none")
        self.assertEqual(geo.get("children") or [], [])
        self.assertEqual(payload["dhis2_writes"], 0)

    def test_region_to_province_breakdown(self):
        payload = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="province",
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["results"], "parent summary must remain")
        geo = payload["geographic_breakdown"]
        self.assertTrue(geo["ok"])
        self.assertEqual(geo["mode"], "province")
        self.assertEqual(geo["target_level"], 3)
        self.assertEqual(geo["child_count"], 3)
        names = {c["org_unit_name"] for c in geo["children"]}
        self.assertEqual(names, {"Province A", "Province B", "Province C"})
        child = geo["children"][0]
        self.assertTrue(child["org_unit"])
        self.assertTrue(child["hierarchy_path"])
        self.assertTrue(child["results"])
        flat = geo["rows_flat"][0]
        for key in (
            "org_unit",
            "org_unit_name",
            "hierarchy_path",
            "indicator_key",
            "numerator",
            "denominator",
            "percentage",
            "source_badge",
            "validation_status",
            "freshness",
        ):
            self.assertIn(key, flat)
        # Parent + one batched multi-OU analytics call (3 provinces in one chunk).
        self.assertEqual(geo["timings"]["http_requests"], 1)
        self.assertGreaterEqual(payload["timings"]["http_requests"], 2)
        self.assertEqual(len(self.clients["stage"].ou_batches[-1]), 3)

    def test_province_to_municipality_breakdown(self):
        payload = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidPrvA01",
            geographic_breakdown="municipality_city",
        )
        geo = payload["geographic_breakdown"]
        self.assertTrue(geo["ok"])
        self.assertEqual(geo["child_count"], 2)
        self.assertEqual(
            {c["org_unit_name"] for c in geo["children"]},
            {"Municipality 1", "Municipality 2"},
        )

    def test_municipality_to_barangay_breakdown(self):
        payload = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidMunA01",
            geographic_breakdown="barangay",
        )
        geo = payload["geographic_breakdown"]
        self.assertTrue(geo["ok"])
        self.assertEqual(geo["child_count"], 1)
        self.assertEqual(geo["children"][0]["org_unit_name"], "Barangay 1")

    def test_barangay_allows_none_only_server(self):
        with self.assertRaises(ReportSecurityError) as ctx:
            self.svc.report(
                environment="stage",
                period="2026Q1",
                org_unit="OuUidBryA01",
                geographic_breakdown="municipality_city",
            )
        self.assertEqual(ctx.exception.code, "invalid_geographic_breakdown")
        ok = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidBryA01",
            geographic_breakdown="none",
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["geographic_breakdown"].get("mode"), "none")

    def test_batch_chunking_and_not_per_indicator(self):
        with mock.patch.dict(os.environ, {"HCSC_BREAKDOWN_OU_CHUNK": "2"}, clear=False):
            REPORT_CACHE.clear()
            payload = self.svc.report(
                environment="stage",
                period="2026Q1",
                org_unit="OuUidRgn003",
                geographic_breakdown="province",
                force_refresh=True,
            )
        geo = payload["geographic_breakdown"]
        self.assertEqual(geo["timings"]["http_requests"], 2)  # 3 OUs / chunk 2
        # Never one request per indicator (overview has multiple dx UIDs).
        self.assertLess(geo["timings"]["http_requests"], 6)

    def test_cache_key_isolation_by_breakdown(self):
        k_none = report_cache_key(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            disaggregation="none",
            geographic_breakdown="none",
        )
        k_prov = report_cache_key(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            disaggregation="none",
            geographic_breakdown="province",
        )
        self.assertNotEqual(k_none, k_prov)

        a = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="none",
        )
        b = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="province",
        )
        c = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="province",
        )
        self.assertFalse(a["cache"]["hit"])
        self.assertFalse(b["cache"]["hit"])
        self.assertTrue(c["cache"]["hit"])
        self.assertEqual(c["geographic_breakdown"]["child_count"], 3)

    def test_duplicate_inflight_prevention(self):
        started = threading.Event()
        release = threading.Event()
        real = self.clients  # noqa: F841 — factory uses self.clients

        class SlowClient(MultiOuAnalyticsClient):
            def get_analytics(self, params):
                started.set()
                release.wait(timeout=3)
                return super().get_analytics(params)

        slow = SlowClient(
            {
                "fxmvSiKfEpn": 100,
                "LOMZy9q1euI": 40,
                "BSqDSIpHhoT": 80,
                "qzjKcfO9J2w": 50,
                "jkgkU9EiJ5k": 55,
                "fgfeI3Az7zv": 11,
                "r5cHtnYeyXd": 20,
                "S1hLvdJSuiZ": 74.16,
            }
        )
        svc = HcscIndicatorService(
            client_factory=lambda env: slow,
            registry_path=self.reg_path,
            ou_store=self.ou_store,
        )
        REPORT_CACHE.clear()
        results: list[dict] = []

        def worker():
            results.append(
                svc.report(
                    environment="stage",
                    period="2026Q1",
                    org_unit="OuUidRgn003",
                    geographic_breakdown="province",
                )
            )

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        self.assertTrue(started.wait(timeout=2))
        t2.start()
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.get("ok") for r in results))
        # Second waiter should dedupe rather than double-fetch the same key.
        self.assertTrue(any(r.get("cache", {}).get("deduped") or r.get("cache", {}).get("hit") for r in results))

    def test_large_breakdown_confirmation_flag(self):
        with mock.patch.dict(os.environ, {"HCSC_BREAKDOWN_CONFIRM_AT": "2"}, clear=False):
            est = self.svc.breakdown_estimate(
                environment="stage",
                org_unit="OuUidRgn003",
                geographic_breakdown="province",
            )
        self.assertTrue(est["ok"])
        self.assertEqual(est["child_count"], 3)
        self.assertTrue(est["requires_confirmation"])
        self.assertEqual(est["estimate_label"], "3 provinces")

    def test_failed_breakdown_preserves_parent(self):
        class BoomOnMulti(MultiOuAnalyticsClient):
            def get_analytics(self, params):
                ous: list[str] = []
                if isinstance(params, list):
                    for k, v in params:
                        if k == "dimension" and str(v).startswith("ou:"):
                            ous = [x for x in str(v)[3:].split(";") if x]
                if len(ous) > 1:
                    raise Dhis2Error("analytics failed", status_code=500)
                return super().get_analytics(params)

        boom = BoomOnMulti(
            {
                "fxmvSiKfEpn": 100,
                "LOMZy9q1euI": 40,
                "BSqDSIpHhoT": 80,
                "qzjKcfO9J2w": 50,
                "jkgkU9EiJ5k": 55,
                "fgfeI3Az7zv": 11,
                "r5cHtnYeyXd": 20,
                "S1hLvdJSuiZ": 74.16,
            }
        )
        svc = HcscIndicatorService(
            client_factory=lambda env: boom,
            registry_path=self.reg_path,
            ou_store=self.ou_store,
        )
        REPORT_CACHE.clear()
        payload = svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="province",
            force_refresh=True,
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["results"])
        geo = payload["geographic_breakdown"]
        self.assertFalse(geo["ok"])
        self.assertEqual(geo["children"], [])
        self.assertIn("analytics failed", (geo.get("error") or "").lower())

    def test_no_dhis2_writes_and_no_formula_engine(self):
        payload = self.svc.report(
            environment="stage",
            period="2026Q1",
            org_unit="OuUidRgn003",
            geographic_breakdown="province",
        )
        self.assertEqual(payload["dhis2_writes"], 0)
        self.assertEqual(payload["geographic_breakdown"].get("dhis2_writes"), 0)
        boot = self.svc.bootstrap()
        self.assertTrue(boot["boundaries"]["no_formula_engine"])
        self.assertFalse(boot["boundaries"]["dhis2_writes"])
        # Service module must not implement formula evaluation helpers for breakdown.
        src = (ROOT / "hub" / "hcsc_indicators" / "geographic_breakdown.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("eval(", src)
        self.assertNotIn("formula_engine", src)


class GeographicBreakdownUiContractTests(unittest.TestCase):
    def test_ui_labels_states_export_and_cancellation(self):
        html = (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        self.assertIn("Population Filter", html)
        self.assertIn("Geographic Breakdown", html)
        self.assertIn("Selected Area Summary", html)
        self.assertIn("hcsc-breakdown-panel", html)
        self.assertIn("Retry Breakdown", html)
        self.assertIn("None (selected area total)", html)
        self.assertNotIn(">Disaggregation<", html)
        self.assertIn("geographicBreakdown=", js)
        self.assertIn("Parent ready, breakdown loading", js)
        self.assertIn("Breakdown generation failed", js)
        self.assertIn("showGeoConfirm", js)
        self.assertIn("This will generate results for ", js)
        self.assertIn("Choose a higher level", html)
        self.assertIn("AbortController", js)
        self.assertIn("activeRequestId", js)
        self.assertIn("stopActiveRequest", js)
        self.assertIn("Geographic Breakdown", js)
        self.assertIn(
            "Organisation Unit,UID,Path,Indicator,Numerator,Denominator,Result,Source,Validation,Last Updated,Environment,Period,Geographic Breakdown",
            js,
        )
        # Single Generate control — no second generate page/button.
        self.assertEqual(html.count('id="hcsc-run"'), 1)
        self.assertIn("Generate Report", html)
        self.assertNotIn("Generate Breakdown", html)


class GeographicBreakdownApiRouteTests(unittest.TestCase):
    def test_estimate_and_invalid_breakdown_via_flask(self):
        from app import create_app

        app = create_app()
        client = app.test_client()
        svc = mock.Mock()
        svc.breakdown_estimate.return_value = {
            "ok": True,
            "child_count": 17,
            "estimate_label": "17 provinces",
            "requires_confirmation": False,
        }
        svc.report.side_effect = ReportSecurityError(
            "Geographic breakdown must be below the selected organisation unit level.",
            code="invalid_geographic_breakdown",
        )
        app.config["HCSC_INDICATORS"] = svc

        resp = client.get(
            "/api/dhis2/hcsc-indicators/breakdown-estimate"
            "?environment=stage&orgUnit=OuUidRgn003&geographicBreakdown=province"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["estimate_label"], "17 provinces")

        resp = client.get(
            "/api/dhis2/hcsc-indicators/report"
            "?environment=stage&period=2026Q1&orgUnit=OuUidRgn003"
            "&geographicBreakdown=region"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json().get("ok", True))
        self.assertEqual(resp.get_json().get("code"), "invalid_geographic_breakdown")


if __name__ == "__main__":
    unittest.main()
