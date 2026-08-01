"""DHIS2 Reports rendering bridge — catalog, proxy, generate-and-view."""

from __future__ import annotations

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

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.bridge import (
    build_run_catalog,
    classify_catalog_entry,
    is_app_shell_template,
    parse_run_report_id,
    rewrite_report_html,
    validate_proxy_path,
)
from hub.dhis2_reports.cache import CATALOG_CACHE, RESULT_CACHE
from hub.dhis2_reports.catalog import get_report
from hub.dhis2_reports.db import ReportsDatabase
from hub.dhis2_reports.security import ReportSecurityError
from hub.dhis2_reports.service import Dhis2ReportsService
from hub.dhis2_reports.standard_models import normalize_report_payload
from hub.dhis2_reports.standard_sync import StandardReportSyncService
from hub.dhis2_reports.store import ReportsStore


class BridgeUnitTests(unittest.TestCase):
    def test_app_shell_detection(self):
        self.assertTrue(is_app_shell_template("/dhis-web-reports/index.html"))
        self.assertTrue(is_app_shell_template("/dhis-web-data-visualizer/index.html"))
        self.assertFalse(is_app_shell_template("/api/reports/{uid}/data.html"))
        report = get_report("dhis2-standard-reports-app")
        self.assertIsNotNone(report)
        self.assertEqual(classify_catalog_entry(report), "dhis2_app_shell")

    def test_proxy_path_ssrf_guards(self):
        self.assertEqual(validate_proxy_path("/api/reports/x/data.html"), "/api/reports/x/data.html")
        with self.assertRaises(ReportSecurityError):
            validate_proxy_path("https://evil.example/api")
        with self.assertRaises(ReportSecurityError):
            validate_proxy_path("/api/../etc/passwd")
        with self.assertRaises(ReportSecurityError):
            validate_proxy_path("/admin/secret")

    def test_rewrite_maps_relative_to_proxy(self):
        html = '<html><body><img src="/icons/logo.png"><link href="/dhis-web-commons/css/light.css"></body></html>'
        out = rewrite_report_html(
            html, environment="stage", dhis2_base="https://stage.example.org"
        )
        self.assertIn("/dhis2/reports/proxy/stage?", out)
        self.assertIn("icons", out)
        self.assertIn("dhis-web-commons", out)
        self.assertNotIn("password", out.lower())

    def test_parse_run_report_id(self):
        kind, env, uid = parse_run_report_id("std:stage:Abcdefghijk")
        self.assertEqual(kind, "native")
        self.assertEqual(env, "stage")
        self.assertEqual(uid, "Abcdefghijk")


class BridgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ReportsDatabase(Path(self.temp.name) / "reports.db")
        self.store = ReportsStore(self.db)
        self.svc = Dhis2ReportsService(
            store=self.store,
            get_dhis2_base_url=lambda env: "https://stage.example.org",
        )
        CATALOG_CACHE.clear()
        RESULT_CACHE.clear()

    def tearDown(self):
        self.temp.cleanup()

    def _seed(self):
        report = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "Alpha Report",
                "type": "HTML",
                "reportParams": {
                    "paramReportingMonth": True,
                    "paramOrganisationUnit": True,
                },
            },
            environment="stage",
        )
        self.store.upsert_synced_report(report)

    def test_run_catalog_separates_native_and_shells(self):
        self._seed()
        catalog = self.svc.list_run_catalog("stage")
        self.assertGreaterEqual(catalog["counts"]["native_standard"], 1)
        self.assertGreaterEqual(catalog["counts"]["app_shells"], 1)
        native_ids = {r["id"] for r in catalog["native_standard"]}
        self.assertIn("std:stage:Abcdefghijk", native_ids)
        shells = {r["id"] for r in catalog["app_shells"]}
        self.assertIn("dhis2-standard-reports-app", shells)
        # Second call should hit cache
        t0 = time.perf_counter()
        again = self.svc.list_run_catalog("stage")
        elapsed = (time.perf_counter() - t0) * 1000
        self.assertEqual(again["cache"], "hit")
        self.assertLess(elapsed, 500)

    def test_generate_and_view_native(self):
        self._seed()
        client = mock.Mock(spec=Dhis2Client)
        client.get_text.return_value = (
            '<html><body><img src="/icons/a.png">OK</body></html>'
        )

        self.svc.client_factory = lambda env: client
        self.svc._clients.clear()
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            result = self.svc.generate_and_view(
                "std:stage:Abcdefghijk",
                environment="stage",
                period="202601",
                org_unit="OrgUnitUid1",
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["browser_only"])
        self.assertIn("/rendered", result["viewer_url"])
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            html = self.svc.render_standard_html(
                "stage", "Abcdefghijk", period="202601", org_unit="OrgUnitUid1"
            )["html"]
        self.assertIn("OK", html)

    def test_generate_and_view_app_shell_not_rendered(self):
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            result = self.svc.generate_and_view(
                "dhis2-standard-reports-app",
                environment="stage",
            )
        self.assertTrue(result["browser_only"])
        self.assertEqual(result["source_type"], "dhis2_app_shell")
        self.assertFalse(result.get("viewer_url"))

    def test_org_unit_search(self):
        client = mock.Mock(spec=Dhis2Client)
        client._get_json.return_value = {
            "organisationUnits": [
                {"id": "OrgUnitUid1", "displayName": "Facility A", "path": "/root/a", "level": 3}
            ]
        }
        self.svc.client_factory = lambda env: client
        self.svc._clients.clear()
        data = self.svc.search_org_units("stage", q="Fac")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["org_units"][0]["id"], "OrgUnitUid1")

    def test_proxy_blocks_arbitrary_url(self):
        with self.assertRaises(ReportSecurityError):
            self.svc.proxy_dhis2_asset("stage", "https://evil.test/x")


class BridgeRouteTests(unittest.TestCase):
    def test_run_page_and_generate_view_api(self):
        from app import create_app

        app = create_app()
        app.config.update(TESTING=True)
        # Seed a synced report into the app store
        store = app.config["DHIS2_REPORTS"].store
        report = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "Alpha Report",
                "type": "HTML",
                "reportParams": {"paramReportingMonth": False, "paramOrganisationUnit": False},
            },
            environment="stage",
        )
        store.upsert_synced_report(report)
        CATALOG_CACHE.clear()

        mock_client = mock.Mock(spec=Dhis2Client)
        mock_client.get_text.return_value = "<html><body>BridgeOK</body></html>"
        mock_client._get_bytes.return_value = b"png-bytes"
        app.config["DHIS2_REPORTS"].client_factory = lambda env: mock_client
        app.config["DHIS2_REPORTS"].get_dhis2_base_url = lambda env: "https://stage.example.org"
        app.config["DHIS2_REPORTS"]._clients.clear()

        client = app.test_client()
        with mock.patch.dict(os.environ, {"STAGE_DHIS2_URL": "https://stage.example.org"}, clear=False):
            page = client.get("/dhis2/reports/run")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn("Generate &amp; View", html)
            self.assertIn("Diagnostics", html)
            self.assertIn("dr-viewer", html)

            catalog = client.get("/api/dhis2/reports/run-catalog?environment=stage")
            self.assertEqual(catalog.status_code, 200)
            body = catalog.get_json()
            self.assertGreaterEqual(body["counts"]["native_standard"], 1)

            gen = client.post(
                "/api/dhis2/reports/generate-and-view",
                json={
                    "report_id": "std:stage:Abcdefghijk",
                    "environment": "stage",
                },
            )
            self.assertEqual(gen.status_code, 200)
            gbody = gen.get_json()
            self.assertTrue(gbody.get("ok"))
            self.assertIn("/rendered", gbody.get("viewer_url", ""))

            # SSRF blocked
            blocked = client.get("/dhis2/reports/proxy/stage?path=https://evil.test/")
            self.assertEqual(blocked.status_code, 403)


if __name__ == "__main__":
    unittest.main()
