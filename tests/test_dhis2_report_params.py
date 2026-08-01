"""DHIS2 report parameter discovery, periods, and Run/Detail UI helpers."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from hub.dhis2_reports.cache import ORG_UNIT_CACHE, PERIOD_CACHE
from hub.dhis2_reports.discovery import discover_report_parameters
from hub.dhis2_reports.periods import (
    default_completed_month,
    default_completed_quarter,
    list_months,
    list_quarters,
    list_years,
    normalize_period_selection,
    period_label,
    periods_payload,
    resolve_relative_period,
)
from hub.dhis2_reports.security import ReportSecurityError, validate_org_unit, validate_period
from hub.dhis2_reports.standard_models import normalize_report_payload

ROOT = Path(__file__).resolve().parents[1]


class _Report:
    def __init__(self, **kwargs):
        self.report_params = kwargs.get("report_params", {})
        self.relative_periods = kwargs.get("relative_periods", [])
        self.report_type = kwargs.get("report_type", "HTML")


class PeriodHelperTests(unittest.TestCase):
    def test_quarter_label_and_canonical_dropdown(self):
        self.assertEqual(period_label("2026Q2"), "2026 Q2")
        self.assertEqual(default_completed_quarter(as_of=date(2026, 5, 1)), "2026Q1")
        self.assertEqual(default_completed_quarter(as_of=date(2026, 2, 1)), "2025Q4")
        rows = list_quarters(years_back=1, years_forward=0)
        self.assertTrue(any(r["id"] == "2026Q1" for r in rows))
        self.assertEqual(rows[0]["label"], period_label(rows[0]["id"]))
        payload = periods_payload(period_type="quarterly", remembered="2026Q2")
        ids = {p["id"] for p in payload["periods"]}
        self.assertIn("2026Q2", ids)
        self.assertEqual(payload["default_period"], "2026Q2")
        self.assertTrue(all(p["id"] == validate_period(p["id"]) for p in payload["periods"][:5]))

    def test_monthly_and_yearly_labels(self):
        self.assertEqual(period_label("202608"), "August 2026")
        self.assertEqual(period_label("2026"), "2026")
        self.assertEqual(default_completed_month(as_of=date(2026, 8, 2)), "202607")
        months = list_months(years_back=1, years_forward=0)
        self.assertTrue(any(r["id"] == "202608" for r in months) or any(r["id"].startswith("2026") for r in months))
        years = list_years(years_back=2, years_forward=0)
        self.assertIn({"id": "2026", "label": "2026", "type": "yearly"}, years)
        monthly = periods_payload(period_type="monthly")
        self.assertEqual(monthly["period_type"], "monthly")
        self.assertTrue(all(len(p["id"]) == 6 and p["id"].isdigit() for p in monthly["periods"][:3]))
        yearly = periods_payload(period_type="yearly")
        self.assertTrue(all(len(p["id"]) == 4 and p["id"].isdigit() for p in yearly["periods"][:3]))

    def test_normalize_friendly_labels_and_reject_arbitrary(self):
        self.assertEqual(normalize_period_selection("2026 Q2"), "2026Q2")
        self.assertEqual(normalize_period_selection("August 2026"), "202608")
        with self.assertRaises(ReportSecurityError):
            validate_period("not-a-period", required=True)
        with self.assertRaises(ReportSecurityError):
            validate_period("garbage", required=False)
        # normalize returns empty for invalid free text
        self.assertEqual(normalize_period_selection("Q2 only"), "")

    def test_relative_resolves_to_absolute(self):
        self.assertEqual(resolve_relative_period("thisMonth", as_of=date(2026, 8, 2)), "202608")
        self.assertEqual(resolve_relative_period("lastQuarter", as_of=date(2026, 8, 2)), "2026Q2")
        payload = periods_payload(
            period_type="relative",
            relative_keys=["thisMonth", "lastMonth"],
        )
        self.assertTrue(payload["periods"])
        self.assertTrue(all(validate_period(p["id"]) for p in payload["periods"]))


class OrgUnitValidationTests(unittest.TestCase):
    def test_rejects_arbitrary_typed_ou(self):
        with self.assertRaises(ReportSecurityError):
            validate_org_unit("Central Visayas", required=True)
        with self.assertRaises(ReportSecurityError):
            validate_org_unit("short", required=True)
        self.assertEqual(validate_org_unit("Abcdefghijk"), "Abcdefghijk")


class DiscoveryTests(unittest.TestCase):
    def test_flags_drive_required_controls(self):
        report = _Report(
            report_params={
                "param_reporting_month": True,
                "param_organisation_unit": True,
                "param_parent_organisation_unit": False,
            }
        )
        d = discover_report_parameters(report)
        self.assertTrue(d["period_required"])
        self.assertTrue(d["org_unit_required"])
        self.assertFalse(d["incomplete"])
        self.assertIn("reportParams", d["sources"])

    def test_incomplete_metadata_shows_optional_with_warning(self):
        report = _Report(report_params={}, relative_periods=[], report_type="HTML")
        d = discover_report_parameters(report, design_html="")
        self.assertTrue(d["incomplete"])
        self.assertTrue(d["show_period"])
        self.assertTrue(d["show_org_unit"])
        self.assertFalse(d["period_required"])
        self.assertFalse(d["org_unit_required"])
        self.assertIn("optional", d["warning"].lower())

    def test_design_markers_infer_requirements(self):
        report = _Report(report_params={}, report_type="HTML")
        html = "<div data-period>{{period}}</div><span>Organisation Unit</span> Quarter Q2"
        d = discover_report_parameters(report, design_html=html)
        self.assertTrue(d["needs_period"])
        self.assertTrue(d["needs_org_unit"])
        self.assertIn("designContent", d["sources"])
        self.assertEqual(d["preferred_period_type"], "quarterly")

    def test_normalize_payload_reporting_period_key(self):
        row = normalize_report_payload(
            {
                "id": "Abcdefghijk",
                "name": "Demo",
                "type": "HTML",
                "reportParams": {"reportingPeriod": True, "organisationUnit": False},
            },
            environment="stage",
        )
        self.assertTrue(row.report_params["param_reporting_month"])
        self.assertTrue(row.needs_period)


class RunUiAssetTests(unittest.TestCase):
    def test_run_template_uses_searchable_period_and_ou_picker(self):
        html = (ROOT / "templates" / "dhis2_reports_run.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "dhis2_reports_run.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("data-periods-url", html)
        self.assertIn("dr-period-search", html)
        self.assertIn("dr-ou-chip", html)
        self.assertIn("dr-run-filters", html)
        self.assertIn("Generate &amp; View", html)
        self.assertIn("Save Preset", html)
        self.assertNotIn('type="month"', html)
        self.assertNotIn("dr-period-type", html)
        self.assertIn("periodValue", js)
        self.assertIn("periodById", js)
        self.assertIn("isValidOuUid", js)
        self.assertIn("parent_id", js)
        self.assertIn("dedupeFetch", js)
        self.assertIn("storageKey", js)
        self.assertIn("clearOuSelection", js)
        self.assertIn("dr-run-filters", css)
        self.assertIn("dr-ou-chip", css)
        self.assertIn("@media (max-width: 640px)", css)

    def test_detail_template_and_js_hooks(self):
        html = (ROOT / "templates" / "dhis2_reports_standard_detail.html").read_text(
            encoding="utf-8"
        )
        js = (ROOT / "static" / "js" / "dhis2_reports_detail.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("dr-summary-card", html)
        self.assertIn("Diagnostics", html)
        self.assertIn("Generate &amp; View", html)
        self.assertIn("dhis2_reports_detail.js", html)
        self.assertNotIn("Organisation unit UID", html)
        self.assertIn("storageKey", js)
        self.assertIn("AbortController", js)
        self.assertIn("parent_id", js)
        self.assertIn(".dr-summary-grid", css)


class PeriodApiCacheTests(unittest.TestCase):
    def test_list_periods_env_cache_isolation(self):
        from hub.dhis2_reports.service import Dhis2ReportsService

        PERIOD_CACHE.clear()
        svc = Dhis2ReportsService(store=mock.Mock(), client_factory=lambda env: mock.Mock())
        a = svc.list_periods(period_type="quarterly", environment="stage", remembered="2026Q1")
        b = svc.list_periods(period_type="quarterly", environment="live", remembered="2026Q1")
        self.assertEqual(a["cache"], "miss")
        self.assertEqual(b["cache"], "miss")
        a2 = svc.list_periods(period_type="quarterly", environment="stage", remembered="2026Q1")
        self.assertEqual(a2["cache"], "hit")
        monthly = svc.list_periods(period_type="monthly", environment="stage")
        self.assertEqual(monthly["period_type"], "monthly")
        self.assertTrue(any(p["id"].isdigit() and len(p["id"]) == 6 for p in monthly["periods"]))


class OrgUnitSearchContractTests(unittest.TestCase):
    def test_search_includes_path_and_caches_by_env(self):
        from hub.dhis2_reports.service import Dhis2ReportsService

        ORG_UNIT_CACHE.clear()

        class FakeClient:
            def __init__(self, env):
                self.env = env
                self.calls = 0

            def _get_json(self, path, params=None):
                self.calls += 1
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUid000001",
                            "displayName": "Alpha District",
                            "code": "ALP",
                            "path": "/Root/Region/Alpha",
                            "level": 3,
                            "children": 2,
                        }
                    ]
                }

        clients = {"stage": FakeClient("stage"), "live": FakeClient("live")}
        svc = Dhis2ReportsService(store=mock.Mock(), client_factory=lambda env: clients[env])
        stage = svc.search_org_units("stage", q="Alpha")
        self.assertEqual(stage["org_units"][0]["path"], "/Root/Region/Alpha")
        self.assertEqual(stage["org_units"][0]["code"], "ALP")
        self.assertTrue(stage["org_units"][0]["has_children"])
        stage2 = svc.search_org_units("stage", q="Alpha")
        self.assertEqual(stage2["cache"], "hit")
        self.assertEqual(clients["stage"].calls, 1)
        live = svc.search_org_units("live", q="Alpha")
        self.assertEqual(live["cache"], "miss")
        self.assertEqual(clients["live"].calls, 1)
        by_uid = svc.search_org_units("stage", q="OuUid000001")
        self.assertEqual(by_uid["org_units"][0]["id"], "OuUid000001")


if __name__ == "__main__":
    unittest.main()
