"""Focused National-scope and CSV export tests for Central Hub HCSC-RF."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.dhis2_reports.org_unit_store import OrgUnitStore
from hub.hcsc_indicators.cache import CATEGORY_CACHE, OVERVIEW_CACHE, REPORT_CACHE
from hub.hcsc_indicators.service import HcscIndicatorService
from tests.test_hcsc_indicators import FakeAnalyticsClient, SAMPLE_YAML


NATIONAL_UID = "NatUid00001"
REGION_UID = "RegUid00001"


class HcscNationalExportTests(unittest.TestCase):
    def setUp(self):
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        root = Path(self.tmp.name)
        registry = root / "registry.yaml"
        registry.write_text(SAMPLE_YAML, encoding="utf-8")
        self.store = OrgUnitStore(root / "ou.db")
        self.store.upsert_rows(
            "stage",
            [
                {"id": NATIONAL_UID, "name": "Philippines", "level": 1, "has_children": True},
                {"id": REGION_UID, "name": "Region VII", "level": 2, "has_children": True},
            ],
        )
        self.store.upsert_rows(
            "live",
            [{"id": NATIONAL_UID, "name": "Philippines", "level": 1, "has_children": True}],
        )
        self.clients = {}

        def factory(environment):
            client = self.clients.get(environment)
            if client is None:
                client = FakeAnalyticsClient(
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
                    environment=environment,
                )
                self.clients[environment] = client
            return client

        self.service = HcscIndicatorService(
            client_factory=factory, registry_path=registry, ou_store=self.store
        )

    def tearDown(self):
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()
        self.tmp.cleanup()

    def test_resolve_analytics_timeout_gives_national_headroom(self):
        from hub.hcsc_indicators.analytics import resolve_analytics_timeout

        self.assertGreaterEqual(resolve_analytics_timeout(ou_level=1), 90.0)
        self.assertGreaterEqual(resolve_analytics_timeout(ou_level=2), 60.0)
        self.assertLess(resolve_analytics_timeout(ou_level=2), resolve_analytics_timeout(ou_level=1))
        multi = resolve_analytics_timeout(org_unit=["a", "b", "c"] * 10, ou_level=2)
        self.assertGreaterEqual(multi, 60.0)
        self.store.list_children = lambda *args, **kwargs: self.fail("national report listed child OUs")
        self.store.list_descendants_at_level = (
            lambda *args, **kwargs: self.fail("national report listed descendant OUs")
        )
        result = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        self.assertEqual(result["scope_label"], "National Level")
        self.assertEqual(result["org_unit_name"], "Philippines (National)")
        self.assertEqual(result["org_unit_level"], 1)
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)
        # National analytics must not use the short 10s OU-picker DHIS2 default.
        self.assertGreaterEqual(float(self.clients["stage"].last_analytics_timeout or 0), 60.0)
        self.assertEqual(
            [r["indicator_key"] for r in result["results"]],
            [r["key"] for r in self.service.registry()["indicators"]],
        )

    def test_csv_has_required_columns_and_reuses_cached_report(self):
        self.service.report(environment="stage", period="2026Q1", org_unit=NATIONAL_UID)
        body, filename = self.service.export_csv(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        rows = list(csv.DictReader(io.StringIO(body)))
        self.assertEqual(
            list(rows[0]),
            [
                "Indicator name", "Result value", "Numerator", "Denominator",
                "Source type", "Source UID", "Organisation Unit", "Period",
                "Environment", "Last updated timestamp",
            ],
        )
        self.assertEqual(rows[0]["Organisation Unit"], "Philippines (National)")
        self.assertEqual(rows[0]["Environment"], "stage")
        self.assertIn(rows[0]["Source type"], {"PI", "IND", "DE", "SQL", "LP"})
        self.assertTrue(filename.endswith(".csv"))
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)

    def test_lower_level_and_environment_cache_isolation_remain(self):
        lower = self.service.report(
            environment="stage", period="2026Q1", org_unit=REGION_UID
        )
        national = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        live = self.service.report(
            environment="live", period="2026Q1", org_unit=NATIONAL_UID
        )
        cached = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        self.assertEqual(lower["scope_label"], "region")
        self.assertEqual(national["scope_label"], "National Level")
        self.assertEqual(live["environment"], "live")
        self.assertTrue(cached["cache"]["hit"])
        self.assertEqual(self.clients["stage"].get_analytics_calls, 2)
        self.assertEqual(self.clients["live"].get_analytics_calls, 1)


class HcscNationalUiAndRouteTests(unittest.TestCase):
    def test_picker_combines_national_with_region_and_preserves_lower_levels(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8")
        js = (root / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        picker = (root / "static" / "js" / "dhis2_org_unit_picker.js").read_text(encoding="utf-8")
        self.assertNotIn('id="hcsc-ou-national"', template)
        self.assertNotIn(">National</label>", template)
        self.assertNotIn('aria-label="National"', template)
        for control in ("region", "province", "municipality", "barangay"):
            self.assertIn(f'id="hcsc-ou-{control}"', template)
        self.assertIn("Region / National", template)
        self.assertIn('for="hcsc-ou-region">Region / National</label>', template)
        self.assertIn("Disaggregation Level", template)
        self.assertIn("Philippines (National)", picker)
        self.assertIn("includeNationalInRoots", picker)
        self.assertIn("mergeNationalFirst", picker)
        self.assertIn("normalizeNationalRows", picker)
        self.assertIn("has_children = false", picker)
        self.assertIn("selectedLevel === 1", picker)
        self.assertIn("keep Province and below disabled", picker)
        self.assertIn('1: "Region / National"', js)
        self.assertIn('2: "Region / National"', js)
        self.assertIn('3: "Province"', js)
        self.assertIn('4: "Municipality/City"', js)
        self.assertIn('5: "Barangay"', js)
        self.assertIn('{ id: "region", label: "Region / National", level: 2', js)
        self.assertIn("escapeHtml(selectedOuParameterLabel())", js)
        self.assertIn('"National Level"', js)
        self.assertIn("data-export-csv-url", template)
        self.assertIn("Download CSV", template)
        self.assertIn("Philippines (National) → lower selectors stay disabled", template)

    def test_csv_download_route(self):
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        service = app.config["HCSC_INDICATORS"]
        with mock.patch.object(
            service,
            "export_csv",
            return_value=("Indicator name,Result value\nEligible Households,100\n", "hcsc.csv"),
        ) as export:
            response = app.test_client().get(
                "/api/dhis2/hcsc-indicators/export.csv",
                query_string={
                    "environment": "stage",
                    "period": "2026Q1",
                    "orgUnit": NATIONAL_UID,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("attachment; filename=\"hcsc.csv\"", response.headers["Content-Disposition"])
        export.assert_called_once_with(
            environment="stage",
            period="2026Q1",
            org_unit=NATIONAL_UID,
            disaggregation="none",
            force_refresh=False,
        )


if __name__ == "__main__":
    unittest.main()
