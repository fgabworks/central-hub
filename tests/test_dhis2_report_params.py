"""DHIS2 report parameter discovery, periods, and detail UI helpers."""

from __future__ import annotations

import unittest
from datetime import date

from hub.dhis2_reports.discovery import discover_report_parameters
from hub.dhis2_reports.periods import (
    default_completed_quarter,
    list_quarters,
    normalize_period_selection,
    period_label,
    periods_payload,
)
from hub.dhis2_reports.standard_models import normalize_report_payload


class _Report:
    def __init__(self, **kwargs):
        self.report_params = kwargs.get("report_params", {})
        self.relative_periods = kwargs.get("relative_periods", [])
        self.report_type = kwargs.get("report_type", "HTML")


class PeriodHelperTests(unittest.TestCase):
    def test_quarter_label_and_default(self):
        self.assertEqual(period_label("2026Q2"), "2026 Q2")
        self.assertEqual(period_label("202601"), "2026-01")
        self.assertEqual(default_completed_quarter(as_of=date(2026, 5, 1)), "2026Q1")
        self.assertEqual(default_completed_quarter(as_of=date(2026, 2, 1)), "2025Q4")
        rows = list_quarters(years_back=1, years_forward=0)
        self.assertTrue(any(r["id"] == "2026Q1" for r in rows))
        self.assertEqual(rows[0]["label"], period_label(rows[0]["id"]))

    def test_normalize_friendly_quarter_label(self):
        self.assertEqual(normalize_period_selection("2026 Q2"), "2026Q2")
        payload = periods_payload(remembered="2026Q3")
        self.assertEqual(payload["default_period"], "2026Q3")
        self.assertEqual(payload["default_label"], "2026 Q3")


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


class DetailAssetTests(unittest.TestCase):
    def test_detail_template_and_js_hooks(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        html = (root / "templates" / "dhis2_reports_standard_detail.html").read_text(
            encoding="utf-8"
        )
        js = (root / "static" / "js" / "dhis2_reports_detail.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn("dr-summary-card", html)
        self.assertIn("Diagnostics", html)
        self.assertIn("Generate &amp; View", html)
        self.assertIn("dhis2_reports_detail.js", html)
        self.assertNotIn("Organisation unit UID", html)
        self.assertIn("storageKey", js)
        self.assertIn("AbortController", js)
        self.assertIn("parent_id", js)
        self.assertIn(".dr-summary-grid", css)


if __name__ == "__main__":
    unittest.main()
