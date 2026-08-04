"""National regional roll-up aggregation and service tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hub.dhis2_reports.org_unit_store import OrgUnitStore
from hub.hcsc_indicators.cache import CATEGORY_CACHE, OVERVIEW_CACHE, REPORT_CACHE
from hub.hcsc_indicators.national_rollup import (
    aggregate_indicator_mapped,
    aggregate_result_rows,
    verify_national_equals_region_sums,
)
from hub.hcsc_indicators.service import HcscIndicatorService
from tests.test_hcsc_indicators import FakeAnalyticsClient, SAMPLE_YAML


NATIONAL_UID = "NatUid00001"
REGION_A = "RegUid0000A"
REGION_B = "RegUid0000B"


class AggregationUnitTests(unittest.TestCase):
    def test_sum_counts_never_average_percentages(self):
        mapped = aggregate_indicator_mapped(
            result_type="numerator_denominator_percentage",
            regional_mapped=[
                {"numerator": 10, "denominator": 40, "percentage": 25.0},
                {"numerator": 30, "denominator": 60, "percentage": 50.0},
            ],
            source_uid="qzjKcfO9J2w",
        )
        self.assertEqual(mapped["numerator"], 40.0)
        self.assertEqual(mapped["denominator"], 100.0)
        self.assertAlmostEqual(mapped["percentage"], 40.0)  # 40/100, not avg(25,50)=37.5
        self.assertNotAlmostEqual(mapped["percentage"], 37.5)

    def test_missing_nd_does_not_average_pct(self):
        mapped = aggregate_indicator_mapped(
            result_type="percentage",
            regional_mapped=[
                {"percentage": 20.0},
                {"percentage": 40.0},
            ],
        )
        self.assertIsNone(mapped["percentage"])
        self.assertIsNone(mapped["numerator"])

    def test_sum_counts(self):
        mapped = aggregate_indicator_mapped(
            result_type="count",
            regional_mapped=[{"count": 100}, {"count": 50}, {"count": None}],
        )
        self.assertEqual(mapped["count"], 150.0)

    def test_verify_national_equals_region_sums(self):
        regional = [
            [
                {"indicator_key": "a", "count": 1, "numerator": 2, "denominator": 4},
                {"indicator_key": "b", "count": 5, "numerator": None, "denominator": None},
            ],
            [
                {"indicator_key": "a", "count": 3, "numerator": 6, "denominator": 6},
                {"indicator_key": "b", "count": 7, "numerator": None, "denominator": None},
            ],
        ]
        national = [
            {"indicator_key": "a", "count": 4, "numerator": 8, "denominator": 10},
            {"indicator_key": "b", "count": 12, "numerator": None, "denominator": None},
        ]
        self.assertEqual(verify_national_equals_region_sums(national, regional), [])
        national_bad = [
            {"indicator_key": "a", "count": 99, "numerator": 8, "denominator": 10},
            {"indicator_key": "b", "count": 12, "numerator": None, "denominator": None},
        ]
        mism = verify_national_equals_region_sums(national_bad, regional)
        self.assertEqual(len(mism), 1)
        self.assertEqual(mism[0]["indicator_key"], "a")


class NationalRollupServiceTests(unittest.TestCase):
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
                {
                    "id": NATIONAL_UID,
                    "name": "Philippines",
                    "level": 1,
                    "has_children": True,
                    "path": f"/{NATIONAL_UID}",
                },
                {
                    "id": REGION_A,
                    "name": "Region A",
                    "level": 2,
                    "parent_uid": NATIONAL_UID,
                    "has_children": True,
                    "path": f"/{NATIONAL_UID}/{REGION_A}",
                },
                {
                    "id": REGION_B,
                    "name": "Region B",
                    "level": 2,
                    "parent_uid": NATIONAL_UID,
                    "has_children": True,
                    "path": f"/{NATIONAL_UID}/{REGION_B}",
                },
            ],
        )
        self.clients = {}
        self.analytics_ous = []

        def factory(environment):
            client = self.clients.get(environment)
            if client is None:
                client = FakeAnalyticsClient(
                    {
                        "fxmvSiKfEpn": 10,
                        "LOMZy9q1euI": 4,
                        "BSqDSIpHhoT": 8,
                        "qzjKcfO9J2w": 50,
                        "jkgkU9EiJ5k": 55,
                        "fgfeI3Az7zv": 11,
                        "r5cHtnYeyXd": 20,
                        "S1hLvdJSuiZ": 74.16,
                    },
                    environment=environment,
                )
                orig = client.get_analytics

                def tracking(params, timeout=None):
                    self.analytics_ous.append(params)
                    return orig(params, timeout=timeout)

                client.get_analytics = tracking
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

    def test_national_lists_regions_and_does_not_query_national_ou(self):
        result = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["scope_label"], "National Level")
        self.assertEqual((result.get("timings") or {}).get("architecture"), "regional_rollup")
        self.assertEqual((result.get("rollup") or {}).get("region_count"), 2)
        # Analytics OU dimension must be regions, never the national UID alone.
        for params in self.analytics_ous:
            ou_dims = [
                str(v)
                for k, v in params
                if k == "dimension" and str(v).startswith("ou:")
            ]
            self.assertTrue(ou_dims)
            joined = ";".join(ou_dims)
            self.assertNotIn(NATIONAL_UID, joined)
            self.assertTrue(REGION_A in joined or REGION_B in joined)

    def test_national_sums_regional_counts(self):
        # Region A and B each return fxmvSiKfEpn=10 from FakeAnalyticsClient → national 20
        result = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        by_key = {r["indicator_key"]: r for r in result["results"]}
        self.assertEqual(by_key["eligible_households"]["count"], 20.0)
        # Convergence companions: num 4+4=8, den 8+8=16 → 50%
        self.assertEqual(by_key["convergence_rate"]["numerator"], 8.0)
        self.assertEqual(by_key["convergence_rate"]["denominator"], 16.0)
        self.assertAlmostEqual(by_key["convergence_rate"]["percentage"], 50.0)
        self.assertEqual(result["rollup"]["validation_mismatches"], [])

    def test_region_path_unchanged_single_ou(self):
        result = self.service.report(
            environment="stage", period="2026Q1", org_unit=REGION_A
        )
        self.assertEqual(result["org_unit"], REGION_A)
        self.assertNotEqual((result.get("timings") or {}).get("architecture"), "regional_rollup")
        by_key = {r["indicator_key"]: r for r in result["results"]}
        self.assertEqual(by_key["eligible_households"]["count"], 10.0)

    def test_national_reuses_cached_regions(self):
        self.service.report(environment="stage", period="2026Q1", org_unit=REGION_A)
        self.service.report(environment="stage", period="2026Q1", org_unit=REGION_B)
        calls_before = self.clients["stage"].get_analytics_calls
        national = self.service.report(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        self.assertTrue(national["ok"])
        # Both regions served from REPORT_CACHE → no new analytics.
        self.assertEqual(self.clients["stage"].get_analytics_calls, calls_before)

    def test_progress_endpoint_shape(self):
        self.service.report(environment="stage", period="2026Q1", org_unit=NATIONAL_UID)
        progress = self.service.national_rollup_progress(
            environment="stage", period="2026Q1", org_unit=NATIONAL_UID
        )
        self.assertTrue(progress["found"])
        self.assertEqual(progress["progress"]["status"], "completed")
        self.assertEqual(progress["progress"]["total_regions"], 2)


class AggregateResultRowsTests(unittest.TestCase):
    def test_adapter_style_rows(self):
        indicators = [
            {"key": "a", "result_type": "count", "dhis2_uids": {"value": "x"}},
            {
                "key": "b",
                "result_type": "numerator_denominator_percentage",
                "dhis2_uids": {"value": "y", "numerator": "n", "denominator": "d"},
            },
        ]
        by_key = {
            "a": [{"count": 1}, {"count": 2}],
            "b": [
                {"numerator": 1, "denominator": 2, "percentage": 50},
                {"numerator": 1, "denominator": 2, "percentage": 50},
            ],
        }
        rows = aggregate_result_rows(indicators=indicators, regional_results_by_key=by_key)
        mapped = {r["indicator_key"]: r["mapped"] for r in rows}
        self.assertEqual(mapped["a"]["count"], 3.0)
        self.assertEqual(mapped["b"]["numerator"], 2.0)
        self.assertAlmostEqual(mapped["b"]["percentage"], 50.0)


if __name__ == "__main__":
    unittest.main()
