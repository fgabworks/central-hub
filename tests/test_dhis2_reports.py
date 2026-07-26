"""DHIS2 Report Workspace focused tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.dhis2_reports.catalog import load_report_catalog, parse_report
from hub.dhis2_reports.db import ReportsDatabase
from hub.dhis2_reports.security import (
    ReportSecurityError,
    build_dhis2_report_url,
    iframe_sandbox_flags,
    resolve_report_html,
    scrub_parameters,
    validate_org_unit,
    validate_period,
)
from hub.dhis2_reports.service import Dhis2ReportsService
from hub.dhis2_reports.store import ReportsStore


class CatalogTests(unittest.TestCase):
    def test_catalog_loads_and_validates(self) -> None:
        reports = load_report_catalog()
        self.assertGreaterEqual(len(reports), 1)
        ids = {r.id for r in reports}
        self.assertIn("dhis2-standard-pivot", ids)
        with self.assertRaises(ReportSecurityError):
            parse_report({"id": "x", "name": "x", "type": "nope"})


class SecurityTests(unittest.TestCase):
    def test_period_and_org_unit(self) -> None:
        self.assertEqual(validate_period("202601"), "202601")
        self.assertEqual(validate_period("2026Q1"), "2026Q1")
        with self.assertRaises(ReportSecurityError):
            validate_period("not-a-period", required=True)
        self.assertEqual(validate_org_unit("abcdefghijk"), "abcdefghijk")
        with self.assertRaises(ReportSecurityError):
            validate_org_unit("short", required=True)

    def test_dhis2_url_generation_and_host_allowlist(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "STAGE_DHIS2_URL": "https://stage.example.org",
                "LIVE_DHIS2_URL": "https://live.example.org",
            },
            clear=False,
        ):
            url = build_dhis2_report_url(
                base_url="https://stage.example.org/app",
                url_template="/dhis-web-reports/index.html",
                parameters={"period": "202601", "orgUnit": "abcdefghijk"},
            )
            self.assertEqual(url, "https://stage.example.org/app/dhis-web-reports/index.html")
            url2 = build_dhis2_report_url(
                base_url="https://stage.example.org/app",
                url_template="{base_url}/dhis-web-data-visualizer/index.html",
                parameters={},
            )
            self.assertEqual(
                url2, "https://stage.example.org/app/dhis-web-data-visualizer/index.html"
            )
            self.assertNotIn("password", url.lower())
            with self.assertRaises(ReportSecurityError):
                build_dhis2_report_url(
                    base_url="https://evil.example.org",
                    url_template="/x",
                    parameters={},
                )

    def test_html_path_jail_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "out.html"
            good.write_text("<html>ok</html>", encoding="utf-8")
            (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            resolved = resolve_report_html(str(good), roots=[root])
            self.assertEqual(resolved, good.resolve())
            with self.assertRaises(ReportSecurityError):
                resolve_report_html(str(root / ".env"), roots=[root])
            outside = Path(tempfile.gettempdir()) / "central-hub-escape.html"
            outside.write_text("nope", encoding="utf-8")
            try:
                with self.assertRaises(ReportSecurityError):
                    resolve_report_html(str(outside), roots=[root])
            finally:
                outside.unlink(missing_ok=True)

    def test_iframe_sandbox_and_scrub(self) -> None:
        self.assertIn("allow-same-origin", iframe_sandbox_flags(allow_scripts=False))
        self.assertNotIn("allow-scripts", iframe_sandbox_flags(allow_scripts=False))
        self.assertIn("allow-scripts", iframe_sandbox_flags(allow_scripts=True))
        cleaned = scrub_parameters({"period": "2026", "password": "x", "token": "y"})
        self.assertEqual(cleaned, {"period": "2026"})


class ServiceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ReportsDatabase(Path(self.tmp.name) / "reports.db")
        self.store = ReportsStore(self.db)
        self.svc = Dhis2ReportsService(
            self.store,
            get_dhis2_base_url=lambda env: (
                "https://stage.example.org" if env == "stage" else "https://live.example.org"
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stage_live_and_live_confirm(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "STAGE_DHIS2_URL": "https://stage.example.org",
                "LIVE_DHIS2_URL": "https://live.example.org",
            },
            clear=False,
        ):
            preview = self.svc.preview(
                "dhis2-standard-pivot",
                environment="stage",
            )
            self.assertIn("stage.example.org", preview["resolved"]["resolved_url"])
            self.assertNotIn(
                "stage.example.org/https://", preview["resolved"]["resolved_url"]
            )
            with self.assertRaises(ReportSecurityError) as ctx:
                self.svc.generate(
                    "dhis2-standard-pivot",
                    environment="live",
                    confirm_live=False,
                )
            self.assertEqual(ctx.exception.code, "confirm_required")
            run = self.svc.generate(
                "dhis2-standard-pivot",
                environment="live",
                confirm_live=True,
                actor="tester",
            )
            self.assertEqual(run["status"], "completed")
            self.assertTrue(run["output_url"].startswith("https://live.example.org"))

    def test_presets_and_history_redaction(self) -> None:
        preset = self.store.save_preset(
            name="Monthly",
            report_id="dhis2-standard-pivot",
            environment="stage",
            period="202601",
            org_unit="abcdefghijk",
            parameters={"password": "nope", "period": "202601"},
        )
        self.assertNotIn("password", preset["parameters"])
        dup = self.store.duplicate_preset(preset["id"])
        self.assertNotEqual(dup["id"], preset["id"])
        self.assertTrue(self.store.delete_preset(dup["id"]))

        with mock.patch.dict(
            os.environ,
            {"STAGE_DHIS2_URL": "https://stage.example.org"},
            clear=False,
        ):
            run = self.svc.generate(
                "dhis2-standard-pivot",
                environment="stage",
                period="202601",
                actor="tester",
            )
        self.assertEqual(run["status"], "completed")
        listed = self.store.list_runs(limit=10)
        self.assertTrue(any(r["id"] == run["id"] for r in listed))

    def test_static_html_and_missing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html = root / "index.html"
            html.write_text("<html><body>Report</body></html>", encoding="utf-8")
            with mock.patch(
                "hub.dhis2_reports.service.get_report"
            ) as mocked_get, mock.patch(
                "hub.dhis2_reports.service.configured_output_roots",
                return_value=[root],
            ):
                from hub.dhis2_reports.models import ReportDefinition

                mocked_get.return_value = ReportDefinition(
                    id="static-one",
                    name="Static",
                    report_type="static_html",
                    static_relative_path="index.html",
                    environments=("stage",),
                )
                run = self.svc.generate("static-one", environment="stage", actor="t")
                self.assertEqual(run["status"], "completed")
                viewer = self.svc.viewer_payload(run_id=run["id"])
                self.assertEqual(viewer["kind"], "html")
                self.assertIn("Report", viewer["html"])
                self.assertNotIn("allow-top-navigation", viewer["sandbox"])

                mocked_get.return_value = ReportDefinition(
                    id="static-missing",
                    name="Missing",
                    report_type="static_html",
                    static_relative_path="missing.html",
                    environments=("stage",),
                )
                with self.assertRaises(ReportSecurityError) as ctx:
                    self.svc.generate("static-missing", environment="stage")
                self.assertIn(ctx.exception.code, {"missing_output", "not_found", "path_escape"})


class RouteNavTests(unittest.TestCase):
    def test_report_workspace_routes_and_nav(self) -> None:
        from app import create_app

        app = create_app()
        client = app.test_client()
        for path in (
            "/dhis2/reports",
            "/dhis2/reports/run",
            "/dhis2/reports/presets",
            "/dhis2/reports/history",
            "/dhis2/reports/settings",
            "/work",
        ):
            resp = client.get(path)
            self.assertIn(resp.status_code, {200, 302}, path)
        work = client.get("/work")
        if work.status_code == 200:
            body = work.get_data(as_text=True)
            self.assertTrue("Reports" in body or "Report Workspace" in body)
            self.assertIn("dhis2/reports", body)
        lib = client.get("/dhis2/reports")
        self.assertEqual(lib.status_code, 200)
        text = lib.get_data(as_text=True)
        self.assertIn("DHIS2 Reports", text)
        self.assertIn("Report Library", text)


if __name__ == "__main__":
    unittest.main()
