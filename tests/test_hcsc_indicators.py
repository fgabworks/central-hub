"""Central Hub HCSC–RF tests (Phase 0–3)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.assertIn("Compare Sources", html)
        self.assertIn("HCSC–RF", html)
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
        self.assertEqual(app_src.count('"endpoint": "dhis2_hcsc_indicators"'), 1)
        self.assertIn('"label": "HCSC–RF"', app_src)
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
        self.assertEqual(boot_json.get("page_subtitle"), "Indicators, Sources, and Validation")
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
        self.assertEqual(overview.data.count("HCSC–RF".encode("utf-8")), 1)
        self.assertTrue(hasattr(self.app.view_functions.get("dhis2_hcsc_indicators"), "__call__"))
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
