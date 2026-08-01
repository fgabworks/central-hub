"""Central Hub HCSC–RF tests (Phase 0–3)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.dhis2.client import Dhis2Client
from hub.hcsc_indicators.adapters import (
    ADAPTER_DHIS2,
    ADAPTER_SQL,
    ApprovedSqlAdapter,
    Dhis2AnalyticsAdapter,
    select_adapter,
)
from hub.hcsc_indicators.analytics import (
    map_indicator_values,
    parse_analytics_payload,
    parse_analytics_rows,
)
from hub.hcsc_indicators.cache import (
    CATEGORY_CACHE,
    OVERVIEW_CACHE,
    REPORT_CACHE,
    category_cache_key,
    overview_cache_key,
    report_cache_key,
)
from hub.hcsc_indicators.design_decode import decode_npmo_design
from hub.hcsc_indicators.presentation import (
    calculation_basis,
    enrich_result_row,
    format_number,
    source_badge,
)
from hub.hcsc_indicators.query_display import build_query_panel, build_retrieval_panel
from hub.hcsc_indicators.registry import RegistryError, collect_analytics_uids, load_registry
from hub.hcsc_indicators.service import HcscIndicatorService
from hub.hcsc_indicators.validation import compare_percentage, validate_row
from hub.settings import Dhis2Settings


SAMPLE_YAML = """
npmo_report_uid: qTQD08sNuzZ
npmo_report_name: HCSC Summary Tables (NPMO)
indicators:
  - key: eligible_households
    display_name: Eligible Households
    category: household
    section: eligible_beneficiaries
    phase: 1
    adapter: dhis2_analytics
    result_type: count
    source_owner: DHIS2
    source_type: program_indicator
    dhis2_uids: { value: fxmvSiKfEpn }
    confidence: high
    overview: true
    unresolved: false
  - key: convergence_rate
    display_name: Convergence Rate
    category: household
    section: convergence
    phase: 1
    adapter: dhis2_analytics
    result_type: numerator_denominator_percentage
    source_owner: DHIS2
    source_type: indicator
    dhis2_uids:
      value: qzjKcfO9J2w
      numerator: LOMZy9q1euI
      denominator: BSqDSIpHhoT
    numerator_label: Convergent Households
    denominator_label: Approved Eligible Households
    percentage_formula_reference: "I{LOMZy9q1euI} / I{BSqDSIpHhoT}"
    confidence: high
    overview: true
    unresolved: false
  - key: convergent_units
    display_name: Convergent Units
    category: household
    section: convergence
    phase: 1
    adapter: unresolved
    result_type: status
    source_owner: DHIS2
    source_type: report_client_computed
    dhis2_uids: {}
    confidence: low
    overview: true
    unresolved: true
    notes: "No UID in NPMO design map"
  - key: exclusive_breastfeeding_rate
    display_name: Exclusive breastfed children (%)
    category: child
    section: child_nutrition_health
    phase: 2
    adapter: dhis2_analytics
    result_type: numerator_denominator_percentage
    source_owner: DHIS2
    source_type: indicator
    dhis2_uids:
      value: jkgkU9EiJ5k
      numerator: fgfeI3Az7zv
      denominator: r5cHtnYeyXd
    numerator_label: Children exclusively breastfed
    denominator_label: Eligible children <6 months
    percentage_formula_reference: "I{fgfeI3Az7zv} / I{r5cHtnYeyXd}"
    confidence: high
    overview: false
    unresolved: false
  - key: anc_prenatal_checkup_rate
    display_name: ANC (%)
    category: maternal
    section: maternal_health
    phase: 2
    adapter: dhis2_analytics
    result_type: numerator_denominator_percentage
    source_owner: DHIS2
    source_type: indicator
    dhis2_uids: { value: S1hLvdJSuiZ }
    numerator_label: PW with prenatal
    denominator_label: Eligible PW
    validation_parity_note: "HH vs member definition gap"
    confidence: high
    overview: false
    unresolved: false
  - key: hcsc_rf_approved_sql_lineage
    display_name: HCSC RF SQL lineage
    category: convergence
    section: data_mapping
    phase: 2
    adapter: approved_sql
    result_type: status
    source_owner: data_scripts
    source_type: approved_sql
    dhis2_uids: {}
    approved_sql_query_id: "4c4f5fe8d6a14eee8777ca4496676640"
    approved_sql_reference: "SQL Workspace HCSC / RF Query"
    confidence: low
    overview: false
    unresolved: true
    notes: "Reference only — not SoT"
"""


class FakeAnalyticsClient:
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
        self.write_calls = 0

    def get_analytics(self, params):
        self.get_analytics_calls += 1
        dx = []
        if isinstance(params, list):
            for k, v in params:
                if k == "dimension" and str(v).startswith("dx:"):
                    dx = str(v)[3:].split(";")
        rows = []
        for uid in dx:
            if uid in self._values:
                val = self._values[uid]
                rows.append(
                    [
                        uid,
                        "2026Q1",
                        "OuUid000001",
                        val,
                        val * 0.4 if uid == "S1hLvdJSuiZ" else None,
                        100.0 if uid == "S1hLvdJSuiZ" else None,
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


class RegistryTests(unittest.TestCase):
    def test_registry_loading_and_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reg.yaml"
            path.write_text(SAMPLE_YAML, encoding="utf-8")
            reg = load_registry(path, force=True)
            self.assertTrue(reg["ok"])
            self.assertEqual(len(reg["indicators"]), 6)
            self.assertIn("convergent_units", reg["unresolved_keys"])
            self.assertIn("child_nutrition_health", reg["by_section"])
            uids = collect_analytics_uids(reg["overview_indicators"])
            self.assertIn("fxmvSiKfEpn", uids)
            self.assertIn("qzjKcfO9J2w", uids)
            self.assertIn("LOMZy9q1euI", uids)
            self.assertNotIn("", uids)

    def test_registry_rejects_guessed_missing_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                """
indicators:
  - key: broken
    display_name: Broken
    category: household
    result_type: count
    source_owner: DHIS2
    source_type: program_indicator
    dhis2_uids: {}
    unresolved: false
""",
                encoding="utf-8",
            )
            with self.assertRaises(RegistryError):
                load_registry(path, force=True)

    def test_production_registry_phase2_sections(self):
        from hub.hcsc_indicators.cache import REGISTRY_CACHE

        REGISTRY_CACHE.clear()
        reg = load_registry(force=True)
        keys = {r["key"] for r in reg["indicators"]}
        self.assertIn("exclusive_breastfeeding_rate", keys)
        self.assertIn("hunger_experience_rate", keys)
        self.assertIn("safely_managed_drinking_water_rate", keys)
        self.assertIn("nutritious_balanced_food_frequency", keys)
        self.assertIn("convergent_units", reg["unresolved_keys"])
        section_ids = {s["id"] for s in reg["sections"]}
        for needed in (
            "overview",
            "eligible_beneficiaries",
            "hcsc",
            "results_framework",
            "maternal_health",
            "child_nutrition_health",
            "household_wash_sbc",
            "food_security",
            "unresolved",
        ):
            self.assertIn(needed, section_ids)
        self.assertNotIn("convergence", section_ids)
        classes = {r["key"]: r["classification"] for r in reg["indicators"]}
        self.assertEqual(classes["convergence_rate"], "HCSC")
        self.assertEqual(classes["exclusive_breastfeeding_rate"], "RF")
        self.assertEqual(classes["convergent_units"], "unresolved")
        self.assertEqual(len(reg["unresolved_classifications"]), 5)
        self.assertNotIn("HCSC + RF", set(classes.values()))


class AnalyticsMappingTests(unittest.TestCase):
    def test_parse_and_map_count_and_rate(self):
        values = parse_analytics_rows(
            {
                "rows": [
                    ["fxmvSiKfEpn", "2026Q1", "OuUid000001", "100"],
                    ["LOMZy9q1euI", "2026Q1", "OuUid000001", "40"],
                    ["BSqDSIpHhoT", "2026Q1", "OuUid000001", "80"],
                    ["qzjKcfO9J2w", "2026Q1", "OuUid000001", "50"],
                ]
            }
        )
        count_ind = {
            "result_type": "count",
            "dhis2_uids": {"value": "fxmvSiKfEpn"},
            "unresolved": False,
        }
        rate_ind = {
            "result_type": "numerator_denominator_percentage",
            "dhis2_uids": {
                "value": "qzjKcfO9J2w",
                "numerator": "LOMZy9q1euI",
                "denominator": "BSqDSIpHhoT",
            },
            "unresolved": False,
        }
        mapped_c = map_indicator_values(count_ind, values)
        self.assertEqual(mapped_c["count"], 100.0)
        mapped_r = map_indicator_values(rate_ind, values)
        self.assertEqual(mapped_r["percentage"], 50.0)
        self.assertEqual(mapped_r["numerator"], 40.0)
        self.assertEqual(mapped_r["denominator"], 80.0)

    def test_include_num_den_fallback(self):
        parsed = parse_analytics_payload(
            {
                "headers": [
                    {"name": "dx"},
                    {"name": "value"},
                    {"name": "numerator"},
                    {"name": "denominator"},
                ],
                "rows": [["S1hLvdJSuiZ", "74.16", "31824", "42911"]],
            }
        )
        mapped = map_indicator_values(
            {
                "result_type": "numerator_denominator_percentage",
                "dhis2_uids": {"value": "S1hLvdJSuiZ"},
                "unresolved": False,
            },
            parsed["values"],
            num_den=parsed["num_den"],
        )
        self.assertEqual(mapped["percentage"], 74.16)
        self.assertEqual(mapped["numerator"], 31824.0)
        self.assertEqual(mapped["denominator"], 42911.0)

    def test_unresolved_uid_handling(self):
        mapped = map_indicator_values(
            {"result_type": "derived_status", "dhis2_uids": {}, "unresolved": True},
            {},
        )
        self.assertIsNone(mapped["source_uid"])
        row = validate_row(
            {
                "unresolved": True,
                "source_uid": None,
                "result_type": "derived_status",
                "notes": "no uid",
            }
        )
        self.assertEqual(row["validation_status"], "Not Yet Validated")


class ValidationTests(unittest.TestCase):
    def test_validation_statuses(self):
        self.assertEqual(compare_percentage(50.0, 50.0), "Exact Match")
        self.assertEqual(compare_percentage(50.0, 50.1), "Rounding Difference")
        self.assertEqual(compare_percentage(50.0, 60.0), "Unexplained Difference")
        row = validate_row(
            {
                "unresolved": False,
                "source_uid": "qzjKcfO9J2w",
                "result_type": "numerator_denominator_percentage",
                "numerator": 40,
                "denominator": 80,
                "percentage": 50.0,
            }
        )
        self.assertEqual(row["validation_status"], "Exact Match")
        parity = validate_row(
            {
                "unresolved": False,
                "source_uid": "S1hLvdJSuiZ",
                "result_type": "numerator_denominator_percentage",
                "numerator": 10,
                "denominator": 20,
                "percentage": 50.0,
                "validation_parity_note": "HH vs member",
            }
        )
        self.assertEqual(parity["validation_status"], "Expected Logic Difference")


class PresentationTests(unittest.TestCase):
    def test_count_only_and_percentage_basis(self):
        count_row = enrich_result_row(
            {
                "display_name": "Eligible Households",
                "result_type": "count",
                "count": 5012,
                "source_type": "program_indicator",
                "source_owner": "DHIS2",
                "source_uid": "fxmvSiKfEpn",
                "population_definition_reference": "Eligible HH",
                "unresolved": False,
            }
        )
        self.assertEqual(count_row["display_result_type"], "Count")
        self.assertEqual(count_row["value_text"], "5,012")
        self.assertIsNone(count_row["calculation_basis"])
        self.assertEqual(count_row["source_badge"], "PI")

        pct_row = enrich_result_row(
            {
                "display_name": "ANC",
                "result_type": "numerator_denominator_percentage",
                "percentage": 74.16,
                "numerator": 31824,
                "denominator": 42911,
                "numerator_label": "compliant pregnant women",
                "denominator_label": "eligible pregnant women",
                "source_type": "indicator",
                "source_owner": "DHIS2",
                "source_uid": "qzjKcfO9J2w",
                "unresolved": False,
            }
        )
        self.assertEqual(pct_row["display_result_type"], "Percentage")
        self.assertEqual(pct_row["value_text"], "74.16%")
        self.assertIn(
            "31,824 compliant pregnant women out of 42,911 eligible pregnant women",
            pct_row["calculation_basis"],
        )
        self.assertEqual(pct_row["source_badge"], "IND")

    def test_source_badges(self):
        self.assertEqual(source_badge("program_indicator")["code"], "PI")
        self.assertEqual(source_badge("indicator")["code"], "IND")
        self.assertEqual(source_badge("data_element")["code"], "DE")
        self.assertEqual(source_badge("approved_sql")["code"], "SQL")
        self.assertEqual(source_badge("live_processing_capability")["code"], "LP")
        self.assertEqual(format_number(1000), "1,000")
        self.assertIsNone(calculation_basis({"result_type": "count", "count": 1}))


class QueryDisplayTests(unittest.TestCase):
    def test_query_display_and_redaction(self):
        panel = build_query_panel(
            retrieval_method="DHIS2 Analytics",
            request_meta={
                "endpoint": "/api/analytics.json",
                "parameters": {
                    "dx": ["fxmvSiKfEpn"],
                    "pe": "2026Q1",
                    "ou": "OuUid000001",
                    "authorization": "secret-token",
                },
                "query_string": "dimension=dx:fxmvSiKfEpn&dimension=pe:2026Q1",
                "readable": "batched",
                "aggregation_request": "default",
                "password": "nope",
            },
        )
        self.assertEqual(panel["retrieval_method"], "DHIS2 Analytics")
        self.assertEqual(panel["parameters"]["authorization"], "[REDACTED]")
        self.assertNotIn("secret-token", json.dumps(panel))
        self.assertIn("tabs", panel)
        self.assertFalse(panel["tabs"]["calculation"].get("invented"))
        sql_panel = build_retrieval_panel(retrieval_method="Approved SQL", sql_text="SELECT 1")
        self.assertEqual(sql_panel["retrieval_method"], "Approved SQL")
        self.assertEqual(sql_panel["sql"], "SELECT 1")
        self.assertTrue(sql_panel["open_sql_workspace"])


class AdapterTests(unittest.TestCase):
    def test_select_adapter_and_dhis2_batch(self):
        self.assertEqual(
            select_adapter({"adapter": "dhis2_analytics", "dhis2_uids": {"value": "x"}}),
            ADAPTER_DHIS2,
        )
        self.assertEqual(
            select_adapter(
                {
                    "adapter": "approved_sql",
                    "unresolved": True,
                    "approved_sql_query_id": "abc",
                }
            ),
            ADAPTER_SQL,
        )
        client = FakeAnalyticsClient(
            {"jkgkU9EiJ5k": 55.0, "fgfeI3Az7zv": 11.0, "r5cHtnYeyXd": 20.0}
        )
        out = Dhis2AnalyticsAdapter().retrieve(
            [
                {
                    "key": "exclusive_breastfeeding_rate",
                    "result_type": "numerator_denominator_percentage",
                    "dhis2_uids": {
                        "value": "jkgkU9EiJ5k",
                        "numerator": "fgfeI3Az7zv",
                        "denominator": "r5cHtnYeyXd",
                    },
                    "unresolved": False,
                }
            ],
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            client=client,
        )
        self.assertEqual(client.get_analytics_calls, 1)
        self.assertEqual(out["dhis2_writes"], 0)
        self.assertFalse(out["invented"])
        mapped = out["rows"][0]["mapped"]
        self.assertEqual(mapped["percentage"], 55.0)
        self.assertEqual(mapped["numerator"], 11.0)
        self.assertEqual(mapped["denominator"], 20.0)

    def test_approved_sql_adapter_does_not_invent(self):
        out = ApprovedSqlAdapter().retrieve(
            [
                {
                    "key": "hcsc_rf_approved_sql_lineage",
                    "approved_sql_query_id": "4c4f5fe8d6a14eee8777ca4496676640",
                    "approved_sql_reference": "HCSC / RF Query",
                    "notes": "lineage only",
                }
            ],
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
        )
        self.assertFalse(out["executed"])
        self.assertFalse(out["invented"])
        self.assertTrue(out["rows"][0]["deferred"])


class OverviewServiceTests(unittest.TestCase):
    def setUp(self):
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.reg_path = Path(self.tmp.name) / "reg.yaml"
        self.reg_path.write_text(SAMPLE_YAML, encoding="utf-8")
        self.clients: dict[str, FakeAnalyticsClient] = {}

        def factory(env: str):
            if env not in self.clients:
                self.clients[env] = FakeAnalyticsClient(
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

        self.svc = HcscIndicatorService(client_factory=factory, registry_path=self.reg_path)

    def tearDown(self):
        self.tmp.cleanup()
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()

    def test_batched_analytics_retrieval(self):
        payload = self.svc.overview(
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            disaggregation="none",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["timings"]["http_requests"], 1)
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)
        self.assertEqual(payload["dhis2_writes"], 0)
        by_key = {r["indicator_key"]: r for r in payload["results"]}
        self.assertEqual(by_key["eligible_households"]["count"], 100.0)
        self.assertEqual(by_key["convergence_rate"]["numerator"], 40.0)
        self.assertEqual(by_key["convergence_rate"]["denominator"], 80.0)
        self.assertIn(
            "40 Convergent Households out of 80 Approved Eligible Households",
            by_key["convergence_rate"]["calculation_basis"],
        )
        self.assertTrue(by_key["convergent_units"]["unresolved"])
        self.assertEqual(payload["query"]["retrieval_method"], "DHIS2 Analytics")

    def test_phase2_report_and_category_batching(self):
        report = self.svc.report(
            environment="stage", period="2026Q1", org_unit="OuUid000001"
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["timings"]["http_requests"], 1)
        self.assertIn(ADAPTER_DHIS2, report["adapters_used"])
        self.assertIn(ADAPTER_SQL, report["adapters_used"])
        by_key = {r["indicator_key"]: r for r in report["results"]}
        self.assertEqual(by_key["exclusive_breastfeeding_rate"]["percentage"], 55.0)
        self.assertEqual(by_key["exclusive_breastfeeding_rate"]["numerator"], 11.0)
        self.assertEqual(
            by_key["anc_prenatal_checkup_rate"]["validation_status"],
            "Expected Logic Difference",
        )
        self.assertTrue(by_key["hcsc_rf_approved_sql_lineage"]["deferred"])
        self.assertTrue(by_key["convergent_units"]["unresolved"])
        section_ids = {s["id"] for s in report["sections"]}
        self.assertIn("maternal_health", section_ids)
        self.assertIn("child_nutrition_health", section_ids)
        self.assertIn("results_framework", section_ids)
        self.assertEqual(
            (report.get("package") or {}).get("package_name"),
            "Central Hub HCSC–RF Report",
        )

        cat = self.svc.category(
            section="child_nutrition_health",
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
        )
        self.assertEqual(len(cat["results"]), 1)
        self.assertEqual(cat["results"][0]["indicator_key"], "exclusive_breastfeeding_rate")
        again = self.svc.category(
            section="child_nutrition_health",
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
        )
        self.assertTrue(again["cache"]["hit"])
        self.assertNotEqual(
            category_cache_key(
                environment="stage",
                period="2026Q1",
                org_unit="OuUid000001",
                disaggregation="none",
                section="child_nutrition_health",
            ),
            report_cache_key(
                environment="stage",
                period="2026Q1",
                org_unit="OuUid000001",
                disaggregation="none",
            ),
        )

    def test_cache_isolation_stage_live(self):
        self.svc.overview(environment="stage", period="2026Q1", org_unit="OuUid000001")
        self.svc.overview(environment="live", period="2026Q1", org_unit="OuUid000001")
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)
        self.assertEqual(self.clients["live"].get_analytics_calls, 1)
        k_stage = overview_cache_key(
            environment="stage", period="2026Q1", org_unit="OuUid000001", disaggregation="none"
        )
        k_live = overview_cache_key(
            environment="live", period="2026Q1", org_unit="OuUid000001", disaggregation="none"
        )
        self.assertNotEqual(k_stage, k_live)
        again = self.svc.overview(environment="stage", period="2026Q1", org_unit="OuUid000001")
        self.assertTrue(again["cache"]["hit"])
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)

    def test_no_dhis2_writes(self):
        payload = self.svc.overview(
            environment="stage", period="2026Q1", org_unit="OuUid000001"
        )
        self.assertEqual(payload["dhis2_writes"], 0)
        self.assertTrue(payload["boundaries"]["no_formula_engine"])
        self.assertFalse(self.clients["stage"].writes_allowed())
        boot = self.svc.bootstrap()
        self.assertEqual(boot["phase"], "0-3")
        service_src = (ROOT / "hub" / "hcsc_indicators" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("def score_household", service_src)
        self.assertNotIn("process_data", service_src)


class DesignDecodeTests(unittest.TestCase):
    def test_decode_from_synced_db_when_present(self):
        db = ROOT / "data" / "dhis2_reports.db"
        if not db.is_file():
            self.skipTest("dhis2_reports.db not present")
        decoded = decode_npmo_design(db_path=db, force=True)
        if not decoded.get("ok"):
            self.skipTest(decoded.get("error") or "design missing")
        dx = decoded.get("dx_to_element") or {}
        self.assertEqual(dx.get("fxmvSiKfEpn"), "fxmvSiKfEpn")
        self.assertEqual(dx.get("ZkDVhSbzeRq"), "Number_IPs")
        self.assertIn("Number_Convergent_Bgy", decoded.get("unresolved_elements") or [])


class CycleQuarterTests(unittest.TestCase):
    def test_allowlist_and_defaults(self):
        from datetime import date

        from hub.hcsc_indicators.cache import REGISTRY_CACHE
        from hub.hcsc_indicators.quarters import (
            assert_allowed_quarter,
            cycle_periods_payload,
            default_allowed_quarter,
            allowed_quarter_ids,
        )
        from hub.hcsc_indicators.registry import load_registry
        from hub.dhis2_reports.security import ReportSecurityError

        REGISTRY_CACHE.clear()
        reg = load_registry(force=True)
        ids = allowed_quarter_ids(reg)
        self.assertEqual(ids[0], "2025Q3")
        self.assertEqual(ids[-1], "2026Q4")
        self.assertEqual(len(ids), 6)
        self.assertNotIn("2027Q1", ids)
        self.assertEqual(
            default_allowed_quarter(ids, as_of=date(2026, 8, 2)),
            "2026Q3",
        )
        self.assertEqual(
            default_allowed_quarter(ids, remembered="2026Q1", as_of=date(2026, 8, 2)),
            "2026Q1",
        )
        self.assertEqual(
            default_allowed_quarter(ids, remembered="2027Q1", as_of=date(2026, 8, 2)),
            "2026Q3",
        )
        payload = cycle_periods_payload(reg, as_of=date(2026, 5, 1))
        self.assertEqual(payload["default_period"], "2026Q2")
        self.assertEqual(payload["quarters"][0]["label"], "2025 Q3")
        self.assertEqual(payload["quarters"][-1]["id"], "2026Q4")
        self.assertEqual(assert_allowed_quarter("2026Q2", ids), "2026Q2")
        with self.assertRaises(ReportSecurityError):
            assert_allowed_quarter("2024Q4", ids)
        with self.assertRaises(ReportSecurityError):
            assert_allowed_quarter("2027Q1", ids)
        with self.assertRaises(ReportSecurityError):
            assert_allowed_quarter("August 2026", ids)


class ParamUiContractTests(unittest.TestCase):
    def test_param_row_and_ou_picker_assets(self):
        html = (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        picker = (ROOT / "static" / "js" / "dhis2_org_unit_picker.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        sidebar_js = (ROOT / "static" / "js" / "sidebar.js").read_text(encoding="utf-8")
        self.assertIn("hcsc-param-row", html)
        self.assertIn("hcsc-filter-row-primary", html)
        self.assertIn("hcsc-filter-row-secondary", html)
        self.assertIn("Generate Report", html)
        self.assertIn("dhis2_org_unit_picker.js", html)
        self.assertIn("hcsc-ou-region", html)
        self.assertIn("hcsc-ou-province", html)
        self.assertIn("hcsc-ou-municipality", html)
        self.assertIn("hcsc-ou-barangay", html)
        self.assertIn("hcsc-ou-levels", html)
        self.assertIn("hcsc-ou-retry", html)
        self.assertIn("hcsc-ou-search", html)
        self.assertIn("Refresh Organisation Units", html)
        self.assertIn("hcsc-ou-refresh-meta", html)
        self.assertIn("Municipality/City", html)
        self.assertIn("hcsc-status-strip", html)
        self.assertIn("hcsc-status-badge", html)
        self.assertIn("hcsc-category-nav", html)
        self.assertIn("Select an organisation unit to continue", html)
        self.assertIn("No report generated yet", html)
        self.assertIn("is-skeleton", html)
        self.assertIn("Clear Filters", js)
        self.assertNotIn("Filter quarters", html)
        self.assertNotIn('type="month"', html)
        self.assertNotIn("hcsc-pct-only", html)
        self.assertIn("CentralHubOuPicker", js)
        self.assertIn("validateForm", js)
        self.assertIn("selectedPeriod", js)
        self.assertIn("onEnvironmentChange", js)
        self.assertIn("updateStatusStrip", js)
        self.assertIn("Ready to generate", js)
        self.assertIn("hcsc-filter-validation", html)
        self.assertIn("OU_LEVELS", picker)
        self.assertIn("Municipality/City", picker)
        self.assertIn("limit:", picker)
        self.assertIn("AbortController", picker)
        self.assertIn("ensureRoots", picker)
        self.assertIn("lazyRoots", picker)
        self.assertIn("refreshMetadata", picker)
        self.assertIn("Refresh Organisation Units", html)
        self.assertIn("Recent / frequent", picker)
        self.assertIn("resolveHierarchyFromPath", picker)
        self.assertIn("Stage is temporarily unavailable due to maintenance.", picker)
        self.assertIn("parent_id", picker)
        self.assertIn("Region", picker)
        self.assertIn("Barangay", picker)
        self.assertIn("hcsc-param-row", css)
        self.assertIn("hcsc-filter-row-primary", css)
        self.assertIn("hcsc-ou-levels", css)
        self.assertIn("hcsc-ou-search-results", css)
        self.assertIn("hcsc-status-strip", css)
        self.assertIn("hcsc-status-badge", css)
        self.assertIn("hcsc-skel", css)
        self.assertIn("hcsc-category-nav", css)
        self.assertIn("has-ad-dock", css)
        self.assertIn("is-wc-open", css)
        # Compact permanent sidebar + collapse
        self.assertIn("hub-sidebar", base)
        self.assertIn("sidebar-collapse-btn", base)
        self.assertIn("nav-group", base)
        self.assertIn("data-nav-toggle", base)
        self.assertIn("is-sidebar-collapsed", css)
        self.assertIn("--sidebar-w: 216px", css)
        self.assertIn("position: fixed", css)
        self.assertIn("centralhub.sidebar.collapsed", sidebar_js)
        self.assertIn("is-sidebar-collapsed", sidebar_js)


class SidebarShellContractTests(unittest.TestCase):
    def test_sidebar_collapse_and_dhis2_expand_markup(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        app_src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('id="hub-sidebar"', base)
        self.assertIn("sidebar-collapse-btn", base)
        self.assertIn("nav-group-toggle", base)
        self.assertIn("expandable", app_src)
        self.assertIn('"expand_prefix": "dhis2"', app_src)
        self.assertIn("is-sidebar-collapsed", css)
        self.assertIn("--sidebar-collapsed-w", css)
        self.assertIn("margin-left: var(--sidebar-w", css)
        # Workspace Console tracks sidebar width (no overlap).
        self.assertIn("left: var(--sidebar-w", css)


class OrgUnitApiReuseTests(unittest.TestCase):
    def test_hub_org_unit_search_name_code_uid_path_and_env_cache(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE
        from hub.dhis2_reports.org_unit_store import OrgUnitStore
        from hub.dhis2_reports.service import Dhis2ReportsService
        from hub.dhis2_reports.security import ReportSecurityError, validate_org_unit

        ORG_UNIT_CACHE.clear()
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        store = OrgUnitStore(Path(tmp.name) / "ou.db")

        class FakeClient:
            def __init__(self, env):
                self.env = env
                self.calls = []

            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                self.calls.append(
                    {
                        "path": path,
                        "params": params or {},
                        "timeout": timeout,
                        "retry_max": retry_max,
                    }
                )
                if path.startswith("/api/organisationUnits/") and path != "/api/organisationUnits":
                    return {
                        "children": [
                            {
                                "id": "OuUid000002",
                                "displayName": "Beta Province",
                                "level": 3,
                            }
                        ]
                    }
                filt = str((params or {}).get("filter") or "")
                fields = str((params or {}).get("fields") or "")
                if "level:eq:1" in filt and "children[" in fields:
                    return {
                        "organisationUnits": [
                            {
                                "id": "OuUidCountry1",
                                "displayName": "Philippines",
                                "children": [
                                    {
                                        "id": "OuUidRegion1",
                                        "displayName": "Region VII",
                                        "level": 2,
                                    }
                                ],
                            }
                        ]
                    }
                rows = [
                    {
                        "id": "OuUid000001",
                        "displayName": "Alpha District",
                        "code": "ALP",
                        "path": "/Root/Region/Alpha",
                        "level": 3,
                    }
                ]
                if "id:eq:" in filt and "OuUid000001" not in filt:
                    rows = []
                if "level:eq:2" in filt:
                    rows = [
                        {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2}
                    ]
                return {"organisationUnits": rows}

        clients = {"stage": FakeClient("stage"), "live": FakeClient("live")}
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc = Dhis2ReportsService(
                store=mock.Mock(),
                client_factory=lambda env: clients[env],
                org_unit_store=store,
            )

            by_name = svc.search_org_units("stage", q="Alpha")
            self.assertEqual(by_name["org_units"][0]["name"], "Alpha District")
            self.assertEqual(by_name["org_units"][0]["path"], "/Root/Region/Alpha")
            self.assertTrue(by_name["org_units"][0]["has_children"])  # level 3 < 5
            self.assertEqual(by_name["source"], "dhis2")
            self.assertIn("identifiable:token:Alpha", str(clients["stage"].calls[0]["params"].get("filter")))
            self.assertNotIn("children::size", str(clients["stage"].calls[0]["params"].get("fields")))
            self.assertEqual(clients["stage"].calls[0]["retry_max"], 0)

            # Code/UID may hit SQLite after name sync populated the same row.
            by_code = svc.search_org_units("stage", q="ALP")
            self.assertEqual(by_code["org_units"][0]["code"], "ALP")
            self.assertIn(by_code["cache"], {"hit", "miss"})

            by_uid = svc.search_org_units("stage", q="OuUid000001")
            self.assertEqual(by_uid["org_units"][0]["id"], "OuUid000001")
            self.assertEqual(by_uid["org_units"][0]["uid"], "OuUid000001")

            regions = svc.search_org_units("stage", level=2, limit=100)
            self.assertEqual(regions["level"], 2)
            self.assertEqual(regions["org_units"][0]["id"], "OuUidRegion1")
            self.assertLessEqual(regions["count"], 80)
            region_call = next(
                c for c in clients["stage"].calls if "level:eq:1" in str(c["params"].get("filter"))
            )
            self.assertIn("children[", str(region_call["params"].get("fields")))
            self.assertEqual(region_call["retry_max"], 0)
            self.assertLessEqual(float(region_call["timeout"]), 5.0)
            self.assertNotIn("children::size", str(region_call["params"].get("fields")))

            children = svc.search_org_units("stage", parent_id="OuUid000001", limit=100)
            self.assertIn("/api/organisationUnits/OuUid000001", children and clients["stage"].calls[-1]["path"])
            self.assertEqual(children["org_units"][0]["id"], "OuUid000002")
            self.assertTrue(children["org_units"][0]["has_children"])
            self.assertIn("children[", str(clients["stage"].calls[-1]["params"].get("fields")))

            stage_calls = len(clients["stage"].calls)
            again = svc.search_org_units("stage", q="Alpha")
            self.assertEqual(again["cache"], "hit")
            self.assertEqual(again["source"], "sqlite")
            self.assertTrue(again.get("synced_at"))
            self.assertEqual(len(clients["stage"].calls), stage_calls)

            live = svc.search_org_units("live", q="Alpha")
            self.assertEqual(live["cache"], "miss")
            self.assertEqual(live["source"], "dhis2")
            self.assertEqual(len(clients["live"].calls), 1)
            self.assertEqual(live["environment"], "live")

        with self.assertRaises(ReportSecurityError):
            validate_org_unit("Central Visayas", required=True)
        self.assertEqual(validate_org_unit("OuUid000001"), "OuUid000001")


class OrgUnitSqliteCacheTests(unittest.TestCase):
    """SQLite cache-first OU loading: speed, isolation, background refresh, dedupe."""

    def setUp(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE

        ORG_UNIT_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE

        ORG_UNIT_CACHE.clear()

    def _svc(self, clients, store=None):
        from hub.dhis2_reports.org_unit_store import OrgUnitStore
        from hub.dhis2_reports.service import Dhis2ReportsService

        ou_store = store or OrgUnitStore(Path(self.tmp.name) / "ou.db")
        return Dhis2ReportsService(
            store=mock.Mock(),
            client_factory=lambda env: clients[env] if isinstance(clients, dict) else clients,
            org_unit_store=ou_store,
        ), ou_store

    def test_cached_hierarchy_and_search_are_fast(self):
        import time

        class FakeClient:
            def __init__(self):
                self.calls = 0

            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                self.calls += 1
                time.sleep(0.05)  # simulate network
                if "level:eq:1" in str((params or {}).get("filter") or ""):
                    return {
                        "organisationUnits": [
                            {
                                "id": "OuUidCountry1",
                                "displayName": "Philippines",
                                "children": [
                                    {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2, "code": "R07"}
                                ],
                            }
                        ]
                    }
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidRegion1",
                            "displayName": "Region VII",
                            "code": "R07",
                            "path": "/PH/R07",
                            "level": 2,
                        }
                    ]
                }

        fake = FakeClient()
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc, _store = self._svc({"live": fake})
            cold = svc.search_org_units("live", level=2, limit=50)
            self.assertEqual(cold["source"], "dhis2")
            t0 = time.perf_counter()
            hot = svc.search_org_units("live", level=2, limit=50)
            hierarchy_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            search = svc.search_org_units("live", q="Region")
            search_ms = (time.perf_counter() - t1) * 1000
        self.assertEqual(hot["source"], "sqlite")
        self.assertEqual(hot["cache"], "hit")
        self.assertEqual(search["source"], "sqlite")
        self.assertLess(hierarchy_ms, 300)
        self.assertLess(search_ms, 500)
        self.assertEqual(fake.calls, 1)

    def test_stage_live_sqlite_isolation(self):
        class FakeClient:
            def __init__(self, env):
                self.env = env

            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                uid = "OuUidLiveRg1" if self.env == "live" else "OuUidStgRg1"
                name = "Live Region" if self.env == "live" else "Stage Region"
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidCountry1",
                            "displayName": "Country",
                            "children": [{"id": uid, "displayName": name, "level": 2}],
                        }
                    ]
                }

        clients = {"stage": FakeClient("stage"), "live": FakeClient("live")}
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc, store = self._svc(clients)
            live = svc.search_org_units("live", level=2, limit=50)
            stage = svc.search_org_units("stage", level=2, limit=50)
        self.assertEqual(live["org_units"][0]["id"], "OuUidLiveRg1")
        self.assertEqual(stage["org_units"][0]["id"], "OuUidStgRg1")
        self.assertIsNone(store.get("stage", "OuUidLiveRg1"))
        self.assertIsNone(store.get("live", "OuUidStgRg1"))

    def test_background_refresh_and_duplicate_prevention(self):
        import threading
        import time

        from hub.dhis2_reports.org_unit_store import OrgUnitStore, utcnow_iso

        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        class SlowClient:
            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                calls["n"] += 1
                started.set()
                release.wait(timeout=2)
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidCountry1",
                            "children": [
                                {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2}
                            ],
                        }
                    ]
                }

        store = OrgUnitStore(Path(self.tmp.name) / "ou_bg.db")
        # Seed stale scope so cache-first path schedules background refresh.
        store.upsert_rows(
            "live",
            [{"id": "OuUidRegion1", "name": "Region VII", "level": 2, "has_children": True}],
        )
        store.mark_scope("live", "level:2", unit_count=1, synced_at="2000-01-01T00:00:00Z")

        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc, _ = self._svc({"live": SlowClient()}, store=store)
            first = svc.search_org_units("live", level=2, limit=50)
            self.assertEqual(first["source"], "sqlite")
            self.assertTrue(first.get("refresh_scheduled"))
            second = svc.refresh_org_units("live", level=2)
            # First background job already inflight from stale scope; duplicate blocked.
            self.assertTrue(started.wait(timeout=1))
            # Either already inflight from schedule, or second start raced — never two workers.
            third = svc.refresh_org_units("live", level=2)
            self.assertTrue(third.get("inflight") or not third.get("started"))
            release.set()
            time.sleep(0.15)
        self.assertEqual(calls["n"], 1)

    def test_dhis2_timeout_falls_back_to_sqlite(self):
        from hub.dhis2.client import Dhis2Error
        from hub.dhis2_reports.org_unit_store import OrgUnitStore

        store = OrgUnitStore(Path(self.tmp.name) / "ou_to.db")
        store.upsert_rows(
            "live",
            [{"id": "OuUidRegion1", "name": "Cached Region", "level": 2, "code": "CR", "has_children": True}],
        )
        store.mark_scope("live", "level:2", unit_count=1)

        class BoomClient:
            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                raise Dhis2Error("timeout", status_code=504)

        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc, _ = self._svc({"live": BoomClient()}, store=store)
            # Fresh scope → sqlite hit, no network.
            hit = svc.search_org_units("live", level=2, limit=50)
            self.assertEqual(hit["source"], "sqlite")
            self.assertEqual(hit["org_units"][0]["name"], "Cached Region")
            # Forced refresh must fall back to sqlite when DHIS2 errors.
            store.clear_scope("live", "level:2")
            fallback = svc.search_org_units("live", level=2, limit=50, refresh=True)
            self.assertEqual(fallback["source"], "sqlite")
            self.assertEqual(fallback["cache"], "stale")
            self.assertEqual(fallback["org_units"][0]["id"], "OuUidRegion1")

    def test_no_dhis2_writes_from_ou_paths(self):
        methods = []

        class SpyClient:
            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                methods.append(("GET", path))
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidCountry1",
                            "children": [
                                {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2}
                            ],
                        }
                    ]
                }

            def request(self, *a, **k):
                methods.append(("request", a, k))
                raise AssertionError("unexpected write path")

        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            svc, _ = self._svc({"live": SpyClient()})
            svc.search_org_units("live", level=2, limit=50)
            svc.refresh_org_units("live", level=2)
        self.assertTrue(methods)
        self.assertTrue(all(m[0] == "GET" for m in methods))


class StageMaintenanceOuTests(unittest.TestCase):
    """Stage maintenance: clear message, retain Stage cache, never bleed Live."""

    def setUp(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE

        ORG_UNIT_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE

        ORG_UNIT_CACHE.clear()

    def _store(self):
        from hub.dhis2_reports.org_unit_store import OrgUnitStore

        return OrgUnitStore(Path(self.tmp.name) / "ou.db")

    def test_maintenance_blocks_stage_network_without_cache(self):
        from hub.dhis2_reports.maintenance import STAGE_MAINTENANCE_MESSAGE
        from hub.dhis2_reports.security import ReportSecurityError
        from hub.dhis2_reports.service import Dhis2ReportsService

        client = mock.Mock()
        svc = Dhis2ReportsService(
            store=mock.Mock(),
            client_factory=lambda env: client,
            org_unit_store=self._store(),
        )
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "true"}, clear=False):
            with self.assertRaises(ReportSecurityError) as ctx:
                svc.search_org_units("stage", level=2, limit=50)
        self.assertEqual(ctx.exception.code, "maintenance")
        self.assertEqual(str(ctx.exception), STAGE_MAINTENANCE_MESSAGE)
        client._get_json.assert_not_called()

    def test_maintenance_serves_stale_stage_cache_with_synced_at(self):
        from hub.dhis2_reports.service import Dhis2ReportsService

        class FakeClient:
            def __init__(self):
                self.calls = 0

            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                self.calls += 1
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidCountry1",
                            "displayName": "Philippines",
                            "children": [
                                {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2}
                            ],
                        }
                    ]
                }

        fake = FakeClient()
        store = self._store()
        svc = Dhis2ReportsService(
            store=mock.Mock(),
            client_factory=lambda env: fake,
            org_unit_store=store,
        )
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            seeded = svc.search_org_units("stage", level=2, limit=50)
        self.assertEqual(seeded["cache"], "miss")
        self.assertTrue(seeded["synced_at"])
        synced = seeded["synced_at"]
        calls_after_seed = fake.calls

        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "true"}, clear=False):
            cached = svc.search_org_units("stage", level=2, limit=50)
        self.assertIn(cached["cache"], {"hit", "stale"})
        self.assertEqual(cached["source"], "sqlite")
        self.assertEqual(cached["synced_at"], synced)
        self.assertTrue(cached["maintenance"])
        self.assertEqual(
            cached["maintenance_message"],
            "Stage is temporarily unavailable due to maintenance.",
        )
        self.assertEqual(fake.calls, calls_after_seed)

    def test_maintenance_does_not_use_live_cache_for_stage(self):
        from hub.dhis2_reports.security import ReportSecurityError
        from hub.dhis2_reports.service import Dhis2ReportsService

        class FakeClient:
            def __init__(self, env):
                self.env = env

            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidLiveRg1",
                            "displayName": "Live Only Region",
                            "children": [
                                {
                                    "id": "OuUidLiveRg1",
                                    "displayName": "Live Only Region",
                                    "level": 2,
                                }
                            ],
                        }
                    ]
                }

        store = self._store()
        svc = Dhis2ReportsService(
            store=mock.Mock(),
            client_factory=lambda env: FakeClient(env),
            org_unit_store=store,
        )
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "false"}, clear=False):
            live = svc.search_org_units("live", level=2, limit=50)
        self.assertEqual(live["org_units"][0]["id"], "OuUidLiveRg1")
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "true"}, clear=False):
            with self.assertRaises(ReportSecurityError) as ctx:
                svc.search_org_units("stage", level=2, limit=50)
        self.assertEqual(ctx.exception.code, "maintenance")

    def test_live_still_works_during_stage_maintenance(self):
        from hub.dhis2_reports.service import Dhis2ReportsService

        class FakeClient:
            def _get_json(self, path, params=None, timeout=None, retry_max=None):
                return {
                    "organisationUnits": [
                        {
                            "id": "OuUidCountry1",
                            "children": [
                                {"id": "OuUidRegion1", "displayName": "Region VII", "level": 2}
                            ],
                        }
                    ]
                }

        svc = Dhis2ReportsService(
            store=mock.Mock(),
            client_factory=lambda env: FakeClient(),
            org_unit_store=self._store(),
        )
        with mock.patch.dict(os.environ, {"DHIS2_STAGE_MAINTENANCE": "true"}, clear=False):
            live = svc.search_org_units("live", level=2, limit=50)
        self.assertEqual(live["org_units"][0]["id"], "OuUidRegion1")
        self.assertFalse(live["maintenance"])


@unittest.skipUnless(
    os.environ.get("HCSC_LIVE_OU_INTEGRATION") == "1",
    "Optional Live read-only OU integration (set HCSC_LIVE_OU_INTEGRATION=1)",
)
class LiveOuIntegrationTests(unittest.TestCase):
    """Safe GET-only Live checks — never writes; never uses Stage."""

    def test_live_regions_read_only(self):
        from hub.dhis2_reports.cache import ORG_UNIT_CACHE
        from app import create_app

        ORG_UNIT_CACHE.clear()
        app = create_app()
        svc = app.config["DHIS2_REPORTS"]
        data = svc.search_org_units("live", level=2, limit=50)
        self.assertTrue(data["ok"])
        self.assertGreater(data["count"], 0)
        self.assertTrue(data.get("synced_at"))
        self.assertEqual(data["environment"], "live")


@unittest.skip(
    "Stage integration blocked: DHIS2 Stage is under scheduled maintenance "
    "(environment unavailable — not an application defect)."
)
class StageOuIntegrationBlockedTests(unittest.TestCase):
    def test_stage_regions_skipped(self):
        self.fail("unreachable while Stage maintenance skip is active")


class OverviewServicePeriodGateTests(unittest.TestCase):
    def setUp(self):
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.reg_path = Path(self.tmp.name) / "reg.yaml"
        sample = SAMPLE_YAML + "\nreporting_cycle:\n  quarter_start: 2025Q3\n  quarter_end: 2026Q4\n"
        self.reg_path.write_text(sample, encoding="utf-8")
        self.clients = {}

        def factory(env: str):
            if env not in self.clients:
                self.clients[env] = FakeAnalyticsClient(
                    {"fxmvSiKfEpn": 100, "qzjKcfO9J2w": 50, "LOMZy9q1euI": 40, "BSqDSIpHhoT": 80},
                    environment=env,
                )
            return self.clients[env]

        self.svc = HcscIndicatorService(client_factory=factory, registry_path=self.reg_path)

    def tearDown(self):
        self.tmp.cleanup()
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()

    def test_rejects_out_of_cycle_period(self):
        from hub.dhis2_reports.security import ReportSecurityError

        with self.assertRaises(ReportSecurityError):
            self.svc.overview(environment="stage", period="2024Q1", org_unit="OuUid000001")
        boot = self.svc.bootstrap()
        ids = [q["id"] for q in boot["periods"]["quarters"]]
        self.assertEqual(ids[0], "2025Q3")
        self.assertEqual(ids[-1], "2026Q4")
        self.assertEqual(boot["boundaries"]["org_unit_source"], "hub_dhis2_reports_org_units")


class UiContractTests(unittest.TestCase):
    def test_result_type_aware_display_and_uid_drawer_hooks(self):
        html = (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        yaml_text = (ROOT / "config" / "hcsc_indicators.yaml").read_text(encoding="utf-8")
        self.assertIn("hcsc-drawer", html)
        self.assertIn("Data Retrieval &amp; Calculation", html)
        self.assertIn("data-report-url", html)
        self.assertIn("hcsc-bootstrap", html)
        self.assertIn("head_extra", (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(encoding="utf-8"))
        self.assertIn("Open in SQL Workspace", html)
        self.assertIn("Copy UID", js)
        self.assertIn("loadReport", js)
        self.assertIn("hcsc-bootstrap", js)
        self.assertIn("loadValidation", js)
        self.assertIn("hcsc-section-row", js)
        self.assertIn("Generate Report", html)
        self.assertIn("dhis2_org_unit_picker.js", html)
        self.assertIn("hcsc-param-row", html)
        self.assertIn("hcsc-status-strip", html)
        self.assertIn("hcsc-category-nav", html)
        self.assertIn("hcsc-indicator-toolbar", html)
        self.assertIn("No indicators to display", html)
        self.assertIn("Calculation Basis", html)
        self.assertNotIn("hcsc-pct-only", html)
        self.assertNotIn("hcsc-legend muted", html)
        self.assertIn("reporting_cycle", yaml_text)
        self.assertIn("quarter_start: 2025Q3", yaml_text)
        self.assertIn("page_subtitle", html)  # rendered from bootstrap
        self.assertIn("Review Differences", html)
        self.assertIn("DHIS2 Analytics Result", html)
        self.assertIn("classificationBadge", js)
        self.assertIn("display_group", js)
        self.assertIn("is-hcsc", css)
        self.assertIn("is-rf", css)
        self.assertIn("is-drawer-open", css)
        self.assertIn("jkgkU9EiJ5k", yaml_text)
        self.assertIn("unresolved: true", yaml_text)
        self.assertNotIn(">Numerator</th>", html)
        self.assertNotIn("This Report", html)
        self.assertNotIn("This Report", js)
        overview = (ROOT / "templates" / "dhis2_overview.html").read_text(encoding="utf-8")
        self.assertIn("dhis2_tools", overview)
        app_src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("dhis2_hcsc_indicators", app_src)
        # Tools grid + Work sidebar — same endpoint, no duplicate route registration.
        self.assertEqual(app_src.count('"endpoint": "dhis2_hcsc_indicators"'), 2)
        self.assertIn('"label": "HCSC–RF"', app_src)
        # DHIS2 Reports precedes HCSC–RF inside the expandable DHIS2 group.
        reports_idx = app_src.index('"label": "DHIS2 Reports"')
        hcsc_nav_idx = app_src.index('"label": "HCSC–RF"', reports_idx)
        self.assertLess(reports_idx, hcsc_nav_idx)
        self.assertIn('"expand_prefix": "dhis2"', app_src)
        self.assertNotIn("HCSC Indicators", app_src)
        service = (ROOT / "hub" / "hcsc_indicators" / "service.py").read_text(encoding="utf-8")
        self.assertIn("no_formula_engine", service)
        self.assertNotIn("def score_household", service)


class RouteSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    def test_page_and_registry_api(self):
        page = self.client.get("/dhis2/hcsc-indicators")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Central Hub HCSC", page.data)
        self.assertIn("HCSC–RF".encode("utf-8"), page.data)
        self.assertIn(b"Indicators, Sources, and Validation", page.data)
        self.assertIn(b"DHIS2", page.data)
        self.assertIn("HCSC–RF".encode("utf-8"), page.data)
        boot = self.client.get("/api/dhis2/hcsc-indicators/bootstrap")
        self.assertEqual(boot.status_code, 200)
        boot_json = boot.get_json()
        self.assertEqual(boot_json.get("phase"), "0-3")
        self.assertEqual(boot_json.get("page_title"), "Central Hub HCSC–RF")
        self.assertEqual(boot_json.get("page_subtitle"), (
            "Indicators, Sources, and Validation — "
            "Household Convergence Scorecard and Results Framework."
        ))
        self.assertEqual(boot_json.get("nav_label"), "HCSC–RF")
        self.assertEqual(len(boot_json.get("unresolved_classifications") or []), 5)
        reg = self.client.get("/api/dhis2/hcsc-indicators/registry")
        self.assertEqual(reg.status_code, 200)
        keys = {r["key"] for r in reg.get_json().get("indicators") or []}
        self.assertEqual(len(keys), 36)
        self.assertIn("eligible_households", keys)
        self.assertIn("exclusive_breastfeeding_rate", keys)
        self.assertIn(b"hcsc_indicator_summary.js", page.data)
        self.assertIn(b"hcsc-bootstrap", page.data)
        overview = self.client.get("/dhis2")
        self.assertEqual(overview.status_code, 200)
        self.assertIn("HCSC–RF".encode("utf-8"), overview.data)
        self.assertNotIn(b"HCSC Indicators", overview.data)
        self.assertIn(b"/dhis2/hcsc-indicators", overview.data)
        # Sidebar + tools grid both link to the same route (not a duplicate page).
        self.assertGreaterEqual(overview.data.count(b"/dhis2/hcsc-indicators"), 1)
        self.assertTrue(hasattr(self.app.view_functions.get("dhis2_hcsc_indicators"), "__call__"))
        # Exactly one view function for the page endpoint (no duplicate routes).
        hcsc_views = [k for k in self.app.view_functions if k == "dhis2_hcsc_indicators"]
        self.assertEqual(hcsc_views, ["dhis2_hcsc_indicators"])

        work = self.client.get("/work")
        self.assertEqual(work.status_code, 200)
        work_html = work.get_data(as_text=True)
        self.assertIn("HCSC–RF", work_html)
        self.assertIn("/dhis2/hcsc-indicators", work_html)
        self.assertIn("DHIS2 Reports", work_html)
        # Order: DHIS2 Reports before HCSC–RF in sidebar markup.
        self.assertLess(
            work_html.index("DHIS2 Reports"),
            work_html.index("HCSC–RF"),
        )

        personal = self.client.get("/personal")
        self.assertEqual(personal.status_code, 200)
        personal_html = personal.get_data(as_text=True)
        # Work-only sidebar: HCSC–RF must not appear in Personal nav entries.
        p_side_start = personal_html.find('class="sidebar-nav"')
        p_side_end = personal_html.find('class="sidebar-actions"')
        personal_sidebar = personal_html[p_side_start:p_side_end]
        self.assertNotIn("HCSC–RF", personal_sidebar)
        self.assertNotIn("/dhis2/hcsc-indicators", personal_sidebar)

        # Active nav state on the HCSC–RF page.
        page_html = page.get_data(as_text=True)
        self.assertIn("is-active", page_html)
        self.assertIn('href="/dhis2/hcsc-indicators"', page_html)
        self.assertRegex(
            page_html,
            r'href="/dhis2/hcsc-indicators"[^>]*class="nav-link[^"]*is-active"',
        )
        self.assertIn("nav-group is-expanded", page_html)
        self.assertIn("sidebar-collapse-btn", page_html)
        self.assertIn("is-skeleton", page_html)
        self.assertIn("hcsc-filter-row-primary", page_html)
        self.assertIn("hcsc-status-badge", page_html)
        # Page still renders shell even when analytics would fail (bootstrap present).
        self.assertIn("hcsc-bootstrap", page_html)
        self.assertIn("Central Hub HCSC", page_html)
        self.assertIn("Indicators, Sources, and Validation", page_html)

        detail = self.client.get("/api/dhis2/hcsc-indicators/eligible_households")
        self.assertEqual(detail.status_code, 200)
        overview_api = self.client.get("/api/dhis2/hcsc-indicators/overview")
        self.assertIn(overview_api.status_code, {200, 400})
        self.assertNotEqual(overview_api.get_json().get("code"), "not_found")
        # Legacy category alias still works
        cat = self.client.get("/api/dhis2/hcsc-indicators/category/convergence")
        self.assertIn(cat.status_code, {200, 400})
        self.assertNotEqual((cat.get_json() or {}).get("code"), "invalid_section")

    def test_client_get_analytics_is_readonly(self):
        settings = Dhis2Settings(
            base_url="https://example.test",
            username="u",
            password="p",
            timeout_seconds=10.0,
            enabled=True,
            allow_writes=False,
        )
        client = Dhis2Client(settings)
        self.assertFalse(client.writes_allowed())
        self.assertTrue(hasattr(client, "get_analytics"))
        self.assertFalse(hasattr(client, "post_analytics"))


class Phase3ValidationTests(unittest.TestCase):
    def setUp(self):
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.reg_path = Path(self.tmp.name) / "reg.yaml"
        self.reg_path.write_text(SAMPLE_YAML, encoding="utf-8")
        self.evidence_path = Path(self.tmp.name) / "evidence.db"
        self.clients: dict[str, FakeAnalyticsClient] = {}

        def factory(env: str):
            if env not in self.clients:
                self.clients[env] = FakeAnalyticsClient(
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

        self.svc = HcscIndicatorService(client_factory=factory, registry_path=self.reg_path)

    def tearDown(self):
        self.tmp.cleanup()
        OVERVIEW_CACHE.clear()
        REPORT_CACHE.clear()
        CATEGORY_CACHE.clear()

    def test_validation_statuses_and_sources(self):
        from hub.hcsc_indicators.compare import STATUS_UNAVAILABLE, build_comparison_row

        exact = build_comparison_row(
            primary_row={
                "indicator_key": "convergence_rate",
                "display_name": "Convergence Rate",
                "result_type": "numerator_denominator_percentage",
                "percentage": 50.0,
                "numerator": 40,
                "denominator": 80,
                "numerator_label": "Convergent Households",
                "denominator_label": "Approved Eligible Households",
                "source_uid": "qzjKcfO9J2w",
                "unresolved": False,
            },
            comparison_source="analytics_num_den",
            comparison_payload={
                "numerator": 40,
                "denominator": 80,
                "numerator_label": "Convergent Households",
                "denominator_label": "Approved Eligible Households",
                "period": "2026Q1",
                "org_unit": "OuUid000001",
            },
            scope={"environment": "stage", "period": "2026Q1", "org_unit": "OuUid000001"},
        )
        self.assertEqual(exact["validation_status"], "Exact Match")

        expected = build_comparison_row(
            primary_row={
                "indicator_key": "anc",
                "display_name": "ANC",
                "result_type": "numerator_denominator_percentage",
                "percentage": 74.16,
                "numerator": 10,
                "denominator": 20,
                "source_uid": "S1hLvdJSuiZ",
                "validation_parity_note": "HH vs member",
                "unresolved": False,
            },
            comparison_source="analytics_num_den",
            comparison_payload={"numerator": 10, "denominator": 20},
            scope={"environment": "stage", "period": "2026Q1", "org_unit": "OuUid000001"},
        )
        self.assertEqual(expected["validation_status"], "Expected Logic Difference")

        unavailable = build_comparison_row(
            primary_row={
                "indicator_key": "sql",
                "display_name": "SQL",
                "result_type": "status",
                "unresolved": True,
                "notes": "lineage only",
            },
            comparison_source="approved_sql",
            comparison_payload={"unavailable": True, "reason": "not executed"},
            scope={"environment": "stage", "period": "2026Q1", "org_unit": "OuUid000001"},
        )
        self.assertEqual(unavailable["validation_status"], STATUS_UNAVAILABLE)

        workspace = self.svc.validation_workspace(
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            evidence_path=self.evidence_path,
        )
        self.assertTrue(workspace["ok"])
        self.assertEqual(workspace["dhis2_writes"], 0)
        self.assertFalse(workspace["sql_executed"])
        self.assertTrue(workspace["boundaries"]["no_sql_auto_execute"])
        self.assertEqual(
            (workspace.get("package") or {}).get("package_name"),
            "Central Hub HCSC–RF Validation",
        )
        self.assertEqual(workspace.get("compare_sources_label"), "Compare Sources")
        self.assertEqual(workspace.get("review_differences_label"), "Review Differences")
        by_key = {r["indicator_key"]: r for r in workspace["comparisons"]}
        self.assertEqual(by_key["convergence_rate"]["validation_status"], "Exact Match")
        self.assertEqual(
            by_key["convergence_rate"]["primary_source_label"],
            "DHIS2 Analytics Result",
        )
        self.assertEqual(
            by_key["convergence_rate"]["comparison_source_label"],
            "DHIS2 Analytics Result",
        )
        self.assertEqual(
            by_key["anc_prenatal_checkup_rate"]["validation_status"],
            "Expected Logic Difference",
        )
        self.assertEqual(
            by_key["hcsc_rf_approved_sql_lineage"]["validation_status"],
            STATUS_UNAVAILABLE,
        )
        self.assertEqual(self.clients["stage"].get_analytics_calls, 1)

        snap = self.svc.save_validation_snapshot(
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            note="test evidence",
            evidence_path=self.evidence_path,
        )
        self.assertTrue(snap["ok"])
        self.assertNotIn("secret-token", json.dumps(snap))
        self.assertNotIn('"password": "nope"', json.dumps(snap))

        note = self.svc.add_validation_note(
            note="Manual investigation — definitions differ",
            indicator_key="anc_prenatal_checkup_rate",
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            evidence_path=self.evidence_path,
        )
        self.assertTrue(note["ok"])

    def test_incompatible_population_and_redaction(self):
        from hub.hcsc_indicators.compare import definitions_compatible
        from hub.hcsc_indicators.evidence import save_snapshot

        ok, _ = definitions_compatible(
            {"period": "2026Q1", "org_unit": "A", "population_definition_reference": "HH"},
            {"period": "2026Q1", "org_unit": "A", "population_definition_reference": "HH"},
        )
        self.assertTrue(ok)
        bad, note = definitions_compatible(
            {
                "period": "2026Q1",
                "org_unit": "A",
                "population_definition_reference": "HH",
                "age_range": "0-5",
            },
            {
                "period": "2026Q1",
                "org_unit": "A",
                "population_definition_reference": "Member",
                "age_range": "6-23",
            },
        )
        self.assertFalse(bad)
        self.assertIn("population_definition_reference", note)
        saved = save_snapshot(
            environment="stage",
            period="2026Q1",
            org_unit="OuUid000001",
            disaggregation="none",
            comparisons=[{"indicator_key": "x", "password": "nope", "authorization": "tok"}],
            path=self.evidence_path,
        )
        from hub.hcsc_indicators.evidence import get_snapshot

        full = get_snapshot(saved["id"], path=self.evidence_path)
        self.assertEqual(full["comparisons"][0]["password"], "[REDACTED]")
        self.assertEqual(full["comparisons"][0]["authorization"], "[REDACTED]")
        self.assertFalse(full["sql_executed"])
        self.assertEqual(full["dhis2_writes"], 0)


if __name__ == "__main__":
    unittest.main()
