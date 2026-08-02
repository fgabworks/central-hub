"""Focused tests for Progress NPMO report comparison."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import create_app

from hub.hcsc_indicators.progress_compare import (
    COMPARE_CACHE,
    ProgressCompareService,
    clear_progress_config_cache,
    compare_values,
    load_progress_comparison_config,
    previous_quarter,
    quarter_to_months,
    sanitize_report_snapshot,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_progress_config_cache()
    COMPARE_CACHE.clear()
    yield
    clear_progress_config_cache()
    COMPARE_CACHE.clear()


def test_report_discovery_and_uid():
    meta = load_progress_comparison_config()
    assert meta.dhis2_report_uid == "IKlKwg7ZS07"
    assert "Progress of Data Collection" in meta.dhis2_report_name
    assert meta.extraction_method == "structured_analytics"
    assert meta.dhis2_report_uid != "plQxuUO8XJd1"


def test_period_ou_parameter_mapping():
    assert quarter_to_months("2026Q2") == "202604;202605;202606"
    assert previous_quarter("2026Q2") == "2026Q1"
    assert previous_quarter("2026Q1") == "2025Q4"


def test_verified_and_unresolved_mappings():
    meta = load_progress_comparison_config()
    by_key = {i["key"]: i for i in meta.indicators}
    assert by_key["eligible_households_current"]["mapping_status"] == "Verified"
    assert by_key["approved_eligible_households"]["mapping_status"] == "Verified"
    assert by_key["eligible_households_current"]["dhis2_uid"] == "fxmvSiKfEpn"
    assert by_key["approved_eligible_households"]["hcsc_indicator_key"] == "approved_eligible_households"
    assert by_key["percentage_data_validated"]["mapping_status"] == "Partial"
    assert by_key["percentage_data_validated"]["dhis2_source_type"] == "CLIENT"
    assert by_key["total_households_registered"]["mapping_status"] == "Unresolved"
    assert by_key["percentage_coverage"]["mapping_status"] == "Not Comparable"
    assert by_key["estimated_households"]["dhis2_uid"] is None


def test_structured_extraction_compare(monkeypatch):
    def fake_batch(client, *, dx_uids, period, org_unit, include_num_den=True):
        if period == "2026Q1":
            return {"values": {"fxmvSiKfEpn": 100}, "num_den": {}, "request": {}}
        values = {
            "fxmvSiKfEpn": 100,
            "BSqDSIpHhoT": 80,
            "mRQ1mcOrUER": 200,
            "YaQinfH8QpC": 5,
            "CgVXYgfxnUP": 3,
            "WxYgNXrRLIN": 10,
            "kr2A9wtz9kJ": 2,
            "StDJxe7tIiS": 80.0,
        }
        num_den = {"StDJxe7tIiS": {"numerator": 80, "denominator": 100}}
        return {"values": values, "num_den": num_den, "request": {"endpoint": "/api/analytics.json"}}

    monkeypatch.setattr(
        "hub.hcsc_indicators.progress_compare.fetch_analytics_batch",
        fake_batch,
    )
    svc = ProgressCompareService(client_factory=lambda env: MagicMock())
    payload = svc.compare(
        environment="stage",
        period="2026Q2",
        org_unit="DcGhhRsspFX",
        request_id="r1",
    )
    assert payload["ok"]
    assert payload["report"]["uid"] == "IKlKwg7ZS07"
    by_key = {r["key"]: r for r in payload["indicators"]}
    assert by_key["eligible_households_current"]["comparison_status"] == "Exact Match"
    assert by_key["approved_eligible_households"]["report"]["result"] == 80
    assert by_key["percentage_data_validated"]["report"]["result"] == 80.0
    assert by_key["total_households_registered"]["comparison_status"] == "Mapping Unresolved"
    assert by_key["percentage_coverage"]["comparison_status"] == "Not Comparable"
    assert by_key["eligible_households_current"]["report"]["numerator"] is None


def test_exact_rounding_numerator_incompatible():
    exact = compare_values(
        result_type="count", report_value=10, hcsc_value=10, mapping_status="Verified"
    )
    assert exact["status"] == "Exact Match"
    roundish = compare_values(
        result_type="percentage",
        report_value=80.0,
        hcsc_value=80.1,
        mapping_status="Verified",
    )
    assert roundish["status"] == "Rounding Difference"
    partial = compare_values(
        result_type="percentage",
        report_value=80.0,
        hcsc_value=75.0,
        mapping_status="Partial",
        notes="CLIENT vs IND",
    )
    assert partial["status"] == "Expected Logic Difference"
    unexplained = compare_values(
        result_type="count",
        report_value=10,
        hcsc_value=5,
        mapping_status="Verified",
        report_num=10,
        hcsc_num=5,
    )
    assert unexplained["status"] == "Unexplained Difference"
    assert unexplained["num_diff"] == 5
    unresolved = compare_values(
        result_type="count", report_value=1, hcsc_value=None, mapping_status="Unresolved"
    )
    assert unresolved["status"] == "Mapping Unresolved"


def test_one_generate_duplicate_and_cache(monkeypatch):
    def fake_batch(client, *, dx_uids, period, org_unit, include_num_den=True):
        return {
            "values": {"fxmvSiKfEpn": 1, "BSqDSIpHhoT": 1, "mRQ1mcOrUER": 1, "StDJxe7tIiS": 100},
            "num_den": {},
            "request": {},
        }

    monkeypatch.setattr(
        "hub.hcsc_indicators.progress_compare.fetch_analytics_batch",
        fake_batch,
    )
    svc = ProgressCompareService(client_factory=lambda env: MagicMock())
    a = svc.compare(environment="stage", period="2026Q2", org_unit="DcGhhRsspFX")
    b = svc.compare(environment="stage", period="2026Q2", org_unit="DcGhhRsspFX")
    assert a["cache"]["hit"] is False
    assert b["cache"]["hit"] is True
    c = svc.compare(environment="live", period="2026Q2", org_unit="DcGhhRsspFX")
    assert c["cache"]["hit"] is False
    assert c["environment"] == "live"


def test_html_sanitization():
    raw = (
        '<div>ok</div><script>alert(1)</script>'
        '<a href="javascript:alert(1)">x</a><img onerror="x" src="y">'
    )
    safe = sanitize_report_snapshot(raw)
    assert "<script" not in safe.lower()
    assert "javascript:" not in safe.lower()
    assert "onerror" not in safe.lower()
    assert "ok" in safe


def test_exports(monkeypatch, tmp_path: Path):
    def fake_batch(client, *, dx_uids, period, org_unit, include_num_den=True):
        return {
            "values": {"fxmvSiKfEpn": 2, "BSqDSIpHhoT": 1, "mRQ1mcOrUER": 3, "StDJxe7tIiS": 50},
            "num_den": {"StDJxe7tIiS": {"numerator": 1, "denominator": 2}},
            "request": {},
        }

    monkeypatch.setattr(
        "hub.hcsc_indicators.progress_compare.fetch_analytics_batch",
        fake_batch,
    )
    svc = ProgressCompareService(client_factory=lambda env: MagicMock())
    body, name, mime = svc.export(
        environment="stage", period="2026Q2", org_unit="DcGhhRsspFX", format="json"
    )
    assert name.endswith(".json")
    assert b"IKlKwg7ZS07" in body
    csv_body, csv_name, _ = svc.export(
        environment="stage", period="2026Q2", org_unit="DcGhhRsspFX", format="csv"
    )
    assert csv_name.endswith(".csv")
    assert b"report_label" in csv_body


def test_no_write_methods_in_extraction():
    meta = load_progress_comparison_config()
    analytics = (meta.raw.get("report") or {}).get("analytics") or {}
    assert analytics.get("method") == "GET"
    assert analytics.get("endpoint") == "/api/analytics.json"


def test_report_output_comparison_ui_contract():
    app = create_app()
    client = app.test_client()

    response = client.get("/dhis2/hcsc-indicators/compare/progress-npmo")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Report Output Comparison" in html
    assert "Progress of Data Collection and Validation" in html
    assert "DHIS2 Report Output" in html
    assert "Central Hub HCSC&ndash;RF Result" in html
    assert "Run Comparison" in html
    assert "Generate Comparison" not in html
    assert "Comparison Setup" in html
    assert 'class="pnc-source-compare"' in html
    assert 'class="pnc-results"' in html
    assert "data-section-header" in html
    assert "pnc-crumb" not in html
    assert "pnc-header" not in html
    assert html.count('id="pnc-env"') == 1
    assert html.count('id="pnc-period"') == 1
    assert html.count('id="pnc-ou"') == 1
    assert html.count('id="pnc-generate"') == 1

    css = (
        Path(app.root_path)
        / "static"
        / "css"
        / "hcsc_progress_compare.css"
    ).read_text(encoding="utf-8")
