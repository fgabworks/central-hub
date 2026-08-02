"""Focused UI/contract tests for HCSC–RF breakdown formula lineage strip."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.hcsc_indicators.presentation import enrich_result_row, source_badge
from hub.hcsc_indicators.registry import load_registry


class BreakdownLineageUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "templates" / "hcsc_indicator_summary.html").read_text(
            encoding="utf-8"
        )
        cls.js = (ROOT / "static" / "js" / "hcsc_indicator_summary.js").read_text(
            encoding="utf-8"
        )
        cls.css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        cls.reg = load_registry(force=True)

    def test_completion_rate_registry_mappings(self):
        row = next(
            r
            for r in self.reg["indicators"]
            if r["key"] == "completion_validated_eligible_rate"
        )
        self.assertEqual(row["dhis2_uids"]["value"], "StDJxe7tIiS")
        self.assertEqual(row["dhis2_uids"]["numerator"], "BSqDSIpHhoT")
        self.assertEqual(row["dhis2_uids"]["denominator"], "fxmvSiKfEpn")
        self.assertEqual(row["numerator_label"], "Approved Eligible Households")
        self.assertEqual(row["denominator_label"], "Eligible Households")
        self.assertEqual(row["result_type"], "numerator_denominator_percentage")
        self.assertIn("BSqDSIpHhoT", row["percentage_formula_reference"])
        self.assertIn("fxmvSiKfEpn", row["percentage_formula_reference"])

        num = next(r for r in self.reg["indicators"] if r["dhis2_uids"].get("value") == "BSqDSIpHhoT")
        den = next(r for r in self.reg["indicators"] if r["dhis2_uids"].get("value") == "fxmvSiKfEpn")
        self.assertEqual(source_badge(num["source_type"])["code"], "PI")
        self.assertEqual(source_badge(den["source_type"])["code"], "PI")
        self.assertEqual(source_badge(row["source_type"])["code"], "IND")

    def test_formula_strip_and_actions_present(self):
        self.assertIn('id="hcsc-bd-formula"', self.html)
        self.assertIn('id="hcsc-bd-tip"', self.html)
        self.assertIn("data-bd-tip", self.html)
        self.assertIn("function renderBreakdownFormula", self.js)
        self.assertIn("function readableFormula", self.js)
        self.assertIn(" ÷ ", self.js)
        self.assertIn(" × 100", self.js)
        self.assertIn("data-bd-copy-uid", self.js)
        self.assertIn("data-bd-open-map", self.js)
        self.assertIn('setTab("mapping")', self.js)
        self.assertIn("hcsc-bd-formula", self.css)

    def test_count_hides_nd_percentage_shows_nd(self):
        self.assertIn('setAttribute("data-bd-mode"', self.js)
        self.assertIn('mode || "count"', self.js)
        self.assertIn('"percentage"', self.js)
        self.assertIn('"status"', self.js)
        self.assertIn("breakdownColumnMode", self.js)
        self.assertIn(
            '.hcsc-breakdown-table[data-bd-mode="count"] .hcsc-bd-col-num',
            self.css,
        )
        self.assertIn('var showND = mode === "percentage"', self.js)
        self.assertIn("Count indicator — numerator/denominator columns hidden", self.js)
        self.assertIn("Status indicator — no numerator/denominator lineage", self.js)

    def test_source_column_uses_result_badge_only(self):
        # Result source badge rendered in table Source column
        self.assertIn("sourceBadge(r.source_badge, r.source_badge_label)", self.js)
        # N/D lineage lives in formula strip / tip, not Source column
        self.assertIn("hcsc-bd-formula-part", self.js)
        self.assertIn('N: ', self.js)
        self.assertIn('D: ', self.js)
        self.assertIn("Result: ", self.js)

    def test_tooltip_copy_and_unresolved_explicit(self):
        self.assertIn("function showBdTip", self.js)
        self.assertIn("Copy UID", self.js)
        self.assertIn("Open Mapping", self.js)
        self.assertIn("is-unresolved", self.js)
        self.assertIn("Unresolved", self.js)
        self.assertIn("population", self.js)
        self.assertIn("Aggregation", self.js)
        lineage_block = self.js.split("function lineagePart", 1)[1].split(
            "function exportBreakdownCsv", 1
        )[0]
        self.assertNotIn("eval(", lineage_block)
        self.assertNotIn("new Function(", lineage_block)
    def test_no_formula_engine_in_lineage_helpers(self):
        # Client only formats registry labels; enrichment still presentation-only.
        sample = enrich_result_row(
            {
                "indicator_key": "completion_validated_eligible_rate",
                "display_name": "Completion / Validated Eligible Rate",
                "result_type": "numerator_denominator_percentage",
                "source_type": "indicator",
                "source_owner": "DHIS2",
                "numerator": 10,
                "denominator": 20,
                "percentage": 50,
                "numerator_label": "Approved Eligible Households",
                "denominator_label": "Eligible Households",
                "dhis2_uids": {
                    "value": "StDJxe7tIiS",
                    "numerator": "BSqDSIpHhoT",
                    "denominator": "fxmvSiKfEpn",
                },
                "source_uid": "StDJxe7tIiS",
                "unresolved": False,
            }
        )
        self.assertEqual(sample["source_badge"], "IND")
        self.assertIn("Approved Eligible Households", sample["calculation_basis"])
        self.assertIn("Eligible Households", sample["calculation_basis"])


if __name__ == "__main__":
    unittest.main()
