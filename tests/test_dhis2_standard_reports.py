"""DHIS2 Standard Report Manager (Phase 1) focused tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.cache import RESULT_CACHE
from hub.dhis2_reports.db import ReportsDatabase
from hub.dhis2_reports.security import (
    ReportSecurityError,
    build_hub_standard_render_path,
    build_standard_report_data_url,
    period_to_dhis2_date,
    prepare_credentialed_report_html,
    scrub_parameters,
)
from hub.dhis2_reports.service import Dhis2ReportsService
from hub.dhis2_reports.standard_models import normalize_report_payload
from hub.dhis2_reports.standard_sync import StandardReportSyncService, detect_report_capabilities
from hub.dhis2_reports.store import ReportsStore
from hub.settings import Dhis2Settings


def _settings(url: str = "https://stage.example.org") -> Dhis2Settings:
    return Dhis2Settings(
        base_url=url,
        username="user",
        password="secret-password",
        timeout_seconds=5.0,
        allow_writes=False,
        enabled=True,
        probe_timeout_seconds=2.0,
        retry_max=0,
        retry_backoff_seconds=0.0,
        page_size=50,
        max_pages=5,
        http_pool_maxsize=2,
        environment="stage",
    )


class StandardModelTests(unittest.TestCase):
    def test_normalize_html_and_jasper(self) -> None:
        html = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "HTML Report",
                "type": "HTML",
                "designContent": "<html><body>x</body></html>",
                "reportParams": {"paramReportingMonth": True, "paramOrganisationUnit": True},
                "relativePeriods": {"thisMonth": True, "lastMonth": False},
            },
            environment="stage",
            dhis2_version="2.40.0",
            last_synced_at="2026-01-01T00:00:00+00:00",
            cache_design=True,
        )
        self.assertTrue(html.html_design_available)
        self.assertTrue(html.needs_period)
        self.assertTrue(html.needs_org_unit)
        self.assertEqual(html.relative_periods, ["thisMonth"])
        self.assertTrue(html.render_supported)

        jdbc = normalize_report_payload(
            {"id": "Bcdefghijkl", "name": "JDBC", "type": "JASPER_JDBC"},
            environment="live",
        )
        self.assertIn("JDBC", jdbc.unsupported_reason)
        self.assertFalse(jdbc.render_supported)


class PeriodUrlTests(unittest.TestCase):
    def test_period_and_data_url(self) -> None:
        self.assertEqual(period_to_dhis2_date("202601"), "2026-01-01")
        self.assertEqual(period_to_dhis2_date("2026Q2"), "2026-04-01")
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            url = build_standard_report_data_url(
                base_url="https://stage.example.org",
                uid="Abcdefghijk",
                period="202601",
                org_unit="OrgUnitUid1",
            )
            self.assertIn("/api/reports/Abcdefghijk/data.html", url)
            self.assertIn("date=2026-01-01", url)
            self.assertIn("ou=OrgUnitUid1", url)
            self.assertNotIn("secret", url.lower())
            self.assertNotIn("password", url.lower())


class SyncStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ReportsDatabase(Path(self.tmp.name) / "reports.db")
        self.store = ReportsStore(self.db)

        def factory(env: str) -> Dhis2Client:
            url = (
                "https://stage.example.org"
                if env == "stage"
                else "https://live.example.org"
            )
            return Dhis2Client(_settings(url))

        self.factory = factory
        self.sync = StandardReportSyncService(self.store, client_factory=factory)
        self.svc = Dhis2ReportsService(
            self.store,
            get_dhis2_base_url=lambda env: (
                "https://stage.example.org" if env == "stage" else "https://live.example.org"
            ),
            client_factory=factory,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _mock_client_pages(self) -> mock.MagicMock:
        client = mock.MagicMock(spec=Dhis2Client)
        client.settings = _settings()

        def get_json(path, params=None, timeout=None):
            if path.startswith("/api/system/info"):
                return {"version": "2.40.3", "systemName": "Stage"}
            raise AssertionError(path)

        client._get_json.side_effect = get_json
        client.iter_collection.side_effect = [
            # capabilities probe
            {
                "items": [{"id": "Abcdefghijk", "name": "One", "type": "HTML"}],
                "truncated": False,
            },
            # full sync page walk (simulate 2 logical pages collapsed into one return)
            {
                "items": [
                    {
                        "id": "Abcdefghijk",
                        "name": "Alpha Report",
                        "type": "HTML",
                        "designContent": "<p>hi</p>",
                        "reportParams": {"paramReportingMonth": True},
                        "relativePeriods": {"thisMonth": True},
                        "reportTable": {"id": "Rtable00001", "name": "Table A"},
                    },
                    {
                        "id": "Bcdefghijkl",
                        "name": "Beta Jasper",
                        "type": "JASPER_REPORT_TABLE",
                        "reportParams": {"paramOrganisationUnit": True},
                        "reportTable": {"id": "Rtable00002", "name": "Table B"},
                    },
                    {
                        "id": "Cdefghijklm",
                        "name": "Gamma JDBC",
                        "type": "JASPER_JDBC",
                    },
                ],
                "truncated": False,
                "pages_fetched": 2,
                "total": 3,
            },
        ]
        return client

    def test_sync_pagination_and_stage_live_separation(self) -> None:
        stage_client = self._mock_client_pages()
        live_client = self._mock_client_pages()
        # settings already set by helper; ensure live URL for clarity
        live_client.settings = _settings("https://live.example.org")

        def factory(env: str) -> Dhis2Client:
            return stage_client if env == "stage" else live_client

        sync = StandardReportSyncService(self.store, client_factory=factory)
        with mock.patch.dict(
            os.environ,
            {
                "STAGE_DHIS2_URL": "https://stage.example.org",
                "LIVE_DHIS2_URL": "https://live.example.org",
            },
            clear=False,
        ):
            result = sync.sync_environment("stage")
            self.assertEqual(result["count"], 3)
            self.assertFalse(result["writes"])
            with self.assertRaises(ReportSecurityError) as ctx:
                sync.sync_environment("live", confirm_live=False)
            self.assertEqual(ctx.exception.code, "confirm_required")
            live_result = sync.sync_environment("live", confirm_live=True)
            self.assertEqual(live_result["count"], 3)

        stage_rows = self.store.list_synced_reports(environment="stage")
        live_rows = self.store.list_synced_reports(environment="live")
        self.assertEqual(len(stage_rows), 3)
        self.assertEqual(len(live_rows), 3)
        self.assertTrue(all(r.environment == "stage" for r in stage_rows))
        self.assertTrue(all(r.environment == "live" for r in live_rows))

        lib = self.svc.list_standard_library()
        self.assertEqual(len(lib["sections"]), 2)
        self.assertEqual(lib["sections"][0]["environment"], "stage")
        self.assertEqual(lib["sections"][1]["environment"], "live")

    def test_unsupported_html_source_and_parameters(self) -> None:
        client = self._mock_client_pages()

        def factory(env: str) -> Dhis2Client:
            return client

        sync = StandardReportSyncService(self.store, client_factory=factory)
        self.svc.client_factory = factory
        self.svc.standard_sync = StandardReportSyncService(
            self.store, client_factory=factory, audit=self.svc._audit
        )
        sync.sync_environment("stage")
        jdbc = self.store.get_synced_report("stage", "Cdefghijklm")
        self.assertIsNotNone(jdbc)
        assert jdbc is not None
        self.assertFalse(jdbc.render_supported)

        html = self.store.get_synced_report("stage", "Abcdefghijk")
        assert html is not None
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            with self.assertRaises(ReportSecurityError):
                self.svc.standard_urls("stage", html.uid, period="", org_unit="")
            urls = self.svc.standard_urls(
                "stage", html.uid, period="202601", org_unit=""
            )
            self.assertIn("data.html", urls["embed_url"])
            self.assertNotIn("password", urls["embed_url"])
            self.assertNotIn("secret", urls["open_url"])

            viewer = self.svc.standard_viewer_payload(
                "stage", html.uid, period="202601"
            )
            self.assertEqual(viewer["kind"], "standard_embed")
            self.assertTrue(viewer["prefer_embed"])
            self.assertTrue(viewer["uses_env_credentials"])
            self.assertIn("/dhis2/reports/standard/stage/", viewer["embed_url"])
            self.assertIn("/rendered", viewer["embed_url"])
            self.assertNotIn("password", viewer["embed_url"].lower())
            self.assertNotIn("secret", viewer["embed_url"].lower())
            self.assertIn("data.html", viewer["external_embed_url"])
            self.assertIn(".env", viewer["fallback_hint"])

            # Cached design content
            src = self.svc.fetch_standard_html_source("stage", html.uid)
            self.assertIn("<p>hi</p>", src["html"])

            # Unauthorized / missing
            client.get_metadata_object.side_effect = Dhis2Error(
                "DHIS2 authentication failed.", status_code=403
            )
            with self.assertRaises(ReportSecurityError) as ctx:
                self.svc.refresh_standard_metadata("stage", "Zzzzzzzzzzz")
            self.assertEqual(ctx.exception.code, "unauthorized")

    def test_download_html_and_secret_redaction(self) -> None:
        client = self._mock_client_pages()
        client.get_text.return_value = "<html><body>Rendered</body></html>"

        def factory(env: str) -> Dhis2Client:
            return client

        self.svc.client_factory = factory
        self.svc.standard_sync = StandardReportSyncService(
            self.store, client_factory=factory
        )
        self.svc.standard_sync.sync_environment("stage")

        data = self.svc.download_standard_html(
            "stage", "Abcdefghijk", period="202601"
        )
        self.assertIn("Rendered", data["html"])
        client.get_text.assert_called()
        args, kwargs = client.get_text.call_args
        self.assertIn("Abcdefghijk", args[0])
        self.assertEqual((kwargs.get("params") or {}).get("date"), "2026-01-01")
        self.assertEqual((kwargs.get("params") or {}).get("pe"), "202601")

        RESULT_CACHE.clear()
        self.svc._clients.clear()
        rendered = self.svc.render_standard_html(
            "stage", "Abcdefghijk", period="202601"
        )
        self.assertIn("Rendered", rendered["html"])
        self.assertTrue(rendered["uses_env_credentials"])
        self.assertNotIn("secret-password", rendered["html"])
        self.assertNotIn("password=", rendered["html"].lower())

        client.get_text.return_value = "<html>password=leak</html>"
        with self.assertRaises(ReportSecurityError) as ctx:
            self.svc.download_standard_html("stage", "Abcdefghijk", period="202601")
        self.assertEqual(ctx.exception.code, "secret_blocked")

        cleaned = scrub_parameters({"period": "2026", "token": "x", "password": "y"})
        self.assertEqual(cleaned, {"period": "2026"})

    def test_download_falls_back_to_design_when_data_html_fails(self) -> None:
        client = self._mock_client_pages()
        client.get_text.side_effect = Dhis2Error(
            "DHIS2 returned HTTP 400. Bad Request", status_code=400
        )
        html_report = normalize_report_payload(
            {
                "id": "HtmlReport01",
                "name": "HTML Design Report",
                "type": "HTML",
                "designContent": "<html><body><h1>Design Fallback</h1></body></html>",
                "reportParams": {},
            },
            environment="stage",
            dhis2_version="2.40.0",
            last_synced_at="2026-01-01T00:00:00+00:00",
            cache_design=True,
        )
        self.store.upsert_synced_report(
            html_report,
            design_content="<html><body><h1>Design Fallback</h1></body></html>",
        )

        def factory(env: str) -> Dhis2Client:
            return client

        self.svc.client_factory = factory
        data = self.svc.download_standard_html("stage", "HtmlReport01")
        self.assertIn("Design Fallback", data["html"])
        self.assertEqual(data.get("source"), "designContent_fallback")

    def test_hub_render_path_and_live_confirm(self) -> None:
        path = build_hub_standard_render_path(
            environment="stage",
            uid="Abcdefghijk",
            period="202601",
            org_unit="OrgUnitUid1",
        )
        self.assertEqual(
            path,
            "/dhis2/reports/standard/stage/Abcdefghijk/rendered?period=202601&org_unit=OrgUnitUid1",
        )
        self.assertNotIn("password", path.lower())

        live = build_hub_standard_render_path(
            environment="live",
            uid="Abcdefghijk",
            period="202601",
            confirm_live=True,
        )
        self.assertIn("confirm_live=1", live)

        prepared = prepare_credentialed_report_html(
            "<html><head></head><body>R</body></html>",
            base_url="https://stage.example.org",
        )
        self.assertIn('<base href="https://stage.example.org/">', prepared)

        client = self._mock_client_pages()
        client.get_text.return_value = "<html><body>LiveOK</body></html>"

        def factory(env: str) -> Dhis2Client:
            return client

        self.svc.client_factory = factory
        self.svc.get_dhis2_base_url = lambda env: (
            "https://live.example.org" if env == "live" else "https://stage.example.org"
        )
        live_report = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "Alpha Report",
                "type": "HTML",
                "designContent": "<p>hi</p>",
                "reportParams": {"paramReportingMonth": True},
                "relativePeriods": {"thisMonth": True},
            },
            environment="live",
            dhis2_version="2.40.0",
            last_synced_at="2026-01-01T00:00:00+00:00",
            cache_design=True,
        )
        self.store.upsert_synced_report(live_report, design_content="<p>hi</p>")

        with self.assertRaises(ReportSecurityError) as ctx:
            self.svc.standard_viewer_payload("live", "Abcdefghijk", period="202601")
        self.assertEqual(ctx.exception.code, "confirm_required")

        with mock.patch.dict(
            os.environ,
            {"LIVE_DHIS2_URL": "https://live.example.org"},
            clear=False,
        ):
            viewer = self.svc.standard_viewer_payload(
                "live", "Abcdefghijk", period="202601", confirm_live=True
            )
        self.assertIn("confirm_live=1", viewer["embed_url"])
        self.assertTrue(viewer["uses_env_credentials"])

    def test_cache_refresh_prunes_stale(self) -> None:
        client = self._mock_client_pages()
        # First sync 3 reports
        sync = StandardReportSyncService(
            self.store, client_factory=lambda env: client
        )
        sync.sync_environment("stage")
        self.assertEqual(len(self.store.list_synced_reports(environment="stage")), 3)

        # Second sync returns only one report
        client.iter_collection.side_effect = [
            {"items": [{"id": "Abcdefghijk", "name": "One", "type": "HTML"}], "truncated": False},
            {
                "items": [
                    {
                        "id": "Abcdefghijk",
                        "name": "Alpha Report",
                        "type": "HTML",
                        "designContent": "<p>hi</p>",
                    }
                ],
                "truncated": False,
                "pages_fetched": 1,
                "total": 1,
            },
        ]
        result = sync.sync_environment("stage")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["removed"], 2)
        rows = self.store.list_synced_reports(environment="stage")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].uid, "Abcdefghijk")

    def test_no_dhis2_writes_on_client(self) -> None:
        client = Dhis2Client(_settings())
        self.assertFalse(client.writes_allowed())
        self.assertFalse(hasattr(client, "post"))
        self.assertFalse(hasattr(client, "put"))
        self.assertFalse(hasattr(client, "delete"))


class CapabilityAndRouteTests(unittest.TestCase):
    def test_detect_capabilities_failure(self) -> None:
        client = mock.MagicMock(spec=Dhis2Client)
        client.settings = _settings()
        client._get_json.side_effect = Dhis2Error("offline")
        caps = detect_report_capabilities(client)
        self.assertFalse(caps["ok"])
        self.assertFalse(caps["reports_list"])

    def test_routes_library_and_sync_api(self) -> None:
        from app import create_app

        app = create_app()
        client = app.test_client()
        lib = client.get("/dhis2/reports")
        self.assertEqual(lib.status_code, 200)
        body = lib.get_data(as_text=True)
        self.assertIn("standard reports", body.lower())
        self.assertIn("Sync Stage", body)
        self.assertNotIn("secret-password", body)
        self.assertNotIn("password=", body.lower())

        # Sync without configured mock client — expect graceful failure, not 500 with secrets
        resp = client.post(
            "/api/dhis2/reports/sync",
            json={"environment": "stage"},
        )
        self.assertIn(resp.status_code, {200, 400, 403, 409})
        data = resp.get_json() or {}
        blob = str(data).lower()
        self.assertNotIn("secret-password", blob)
        self.assertNotIn("password=", blob)

        work = client.get("/work")
        if work.status_code == 200:
            wbody = work.get_data(as_text=True)
            self.assertIn("dhis2/reports", wbody)
            self.assertTrue("Reports" in wbody or "Report" in wbody)

    def test_rendered_route_uses_env_credentials_not_browser_login(self) -> None:
        from app import create_app

        RESULT_CACHE.clear()
        app = create_app()
        store: ReportsStore = app.config["DHIS2_REPORTS"].store
        report = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "Alpha Report",
                "type": "HTML",
                "designContent": "<p>hi</p>",
                "reportParams": {"paramReportingMonth": True},
                "relativePeriods": {"thisMonth": True},
            },
            environment="stage",
            dhis2_version="2.40.0",
            last_synced_at="2026-01-01T00:00:00+00:00",
            cache_design=True,
        )
        store.upsert_synced_report(report, design_content="<p>hi</p>")

        mock_client = mock.MagicMock(spec=Dhis2Client)
        mock_client.settings = _settings()
        mock_client.get_text.return_value = (
            "<html><head></head><body><h1>Chosen Report</h1></body></html>"
        )

        app.config["DHIS2_REPORTS"].client_factory = lambda env: mock_client
        app.config["DHIS2_REPORTS"].get_dhis2_base_url = (
            lambda env: "https://stage.example.org"
        )
        app.config["DHIS2_REPORTS"]._clients.clear()

        client = app.test_client()
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            view = client.get(
                "/dhis2/reports/standard/stage/Abcdefghijk/view?period=202601"
            )
            self.assertEqual(view.status_code, 200)
            vhtml = view.get_data(as_text=True)
            self.assertIn("/dhis2/reports/standard/stage/Abcdefghijk/rendered", vhtml)
            self.assertIn("hub credentials", vhtml.lower())
            self.assertNotIn("https://stage.example.org/api/reports/", vhtml)
            self.assertNotIn("secret-password", vhtml)

            RESULT_CACHE.clear()
            rendered = client.get(
                "/dhis2/reports/standard/stage/Abcdefghijk/rendered?period=202601"
            )
        self.assertEqual(rendered.status_code, 200)
        body = rendered.get_data(as_text=True)
        self.assertIn("Chosen Report", body)
        self.assertNotIn("secret-password", body)
        self.assertNotIn("password=", body.lower())
        mock_client.get_text.assert_called()
        path_arg = mock_client.get_text.call_args[0][0]
        self.assertIn("/api/reports/Abcdefghijk/data.html", path_arg)

    def test_rendered_error_page_not_bare_werkzeug_bad_request(self) -> None:
        from app import create_app

        RESULT_CACHE.clear()
        app = create_app()
        store: ReportsStore = app.config["DHIS2_REPORTS"].store
        report = normalize_report_payload(
            {
                "id": "JasperFail001",
                "name": "Jasper Fail Report",
                "type": "JASPER_REPORT_TABLE",
                "reportParams": {"paramReportingMonth": True},
            },
            environment="stage",
            dhis2_version="2.40.0",
            last_synced_at="2026-01-01T00:00:00+00:00",
        )
        store.upsert_synced_report(report)

        mock_client = mock.MagicMock(spec=Dhis2Client)
        mock_client.settings = _settings()
        mock_client.get_text.side_effect = Dhis2Error(
            "DHIS2 returned HTTP 400. Bad Request", status_code=400
        )
        app.config["DHIS2_REPORTS"].client_factory = lambda env: mock_client
        app.config["DHIS2_REPORTS"].get_dhis2_base_url = (
            lambda env: "https://stage.example.org"
        )
        app.config["DHIS2_REPORTS"]._clients.clear()

        client = app.test_client()
        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            rendered = client.get(
                "/dhis2/reports/standard/stage/JasperFail001/rendered?period=202601"
            )
        self.assertEqual(rendered.status_code, 502)
        body = rendered.get_data(as_text=True)
        self.assertIn("Report could not be rendered", body)
        self.assertNotIn(
            "The browser (or proxy) sent a request that this server could not understand",
            body,
        )
        self.assertNotIn("secret-password", body)


if __name__ == "__main__":
    unittest.main()
