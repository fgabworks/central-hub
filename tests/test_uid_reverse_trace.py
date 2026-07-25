"""Reverse metadata trace (DHIS2 GET filters) + logical storage hints."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from hub.dhis2.uid_mapping.reverse_trace import logical_storage_hint, reverse_trace_links


class LogicalStorageTests(unittest.TestCase):
    def test_tracker_de_hint(self) -> None:
        hint = logical_storage_hint(
            object_type="dataElement",
            domain_type="TRACKER",
            stage_count=2,
            program_count=1,
        )
        self.assertEqual(hint["layer"], "Tracker event value")
        self.assertIn("not in the metadata API", hint["summary"])

    def test_tea_hint(self) -> None:
        hint = logical_storage_hint(object_type="trackedEntityAttribute")
        self.assertEqual(hint["layer"], "Tracker attribute")


class ReverseTraceTests(unittest.TestCase):
    def test_data_element_reverse_stages_and_programs(self) -> None:
        client = MagicMock()

        def find_by_filter(plural: str, filter_expr: str, **kwargs: Any) -> list[dict]:
            if plural == "programStages":
                return [
                    {
                        "id": "stageUid0001",
                        "name": "Survey",
                        "program": {"id": "programUid01", "name": "Household"},
                    }
                ]
            if plural == "dataSets":
                return []
            return []

        client.find_by_filter.side_effect = find_by_filter
        result = reverse_trace_links(
            client,
            object_type="dataElement",
            uid="dataElemUid",
            dhis2_obj={
                "id": "dataElemUid",
                "domainType": "TRACKER",
                "optionSet": {"id": "optionSet01", "name": "Yes No"},
                "valueType": "BOOLEAN",
            },
        )
        self.assertTrue(result["edges"])
        types = {e["related_type"] for e in result["edges"]}
        self.assertIn("programStage", types)
        self.assertIn("program", types)
        self.assertIn("optionSet", types)
        self.assertEqual(result["storage"]["layer"], "Tracker event value")
        self.assertTrue(any("programStages" in q for q in result["queries"]))

    def test_tea_reverse_programs(self) -> None:
        client = MagicMock()
        client.find_by_filter.return_value = [
            {"id": "programUid01", "name": "Member", "programType": "WITH_REGISTRATION"}
        ]
        result = reverse_trace_links(
            client,
            object_type="trackedEntityAttribute",
            uid="teaUid00001",
            dhis2_obj={"id": "teaUid00001", "valueType": "TEXT"},
        )
        self.assertEqual(result["edges"][0]["related_type"], "program")
        self.assertIn("programTrackedEntityAttributes", result["queries"][0])


if __name__ == "__main__":
    unittest.main()
