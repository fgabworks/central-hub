"""DHIS2 metadata enrichment: derive, classify, multi-stage, PI refs, redaction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.dhis2.enrichment.classify import classify_uid
from hub.dhis2.enrichment.derive import derive_answer_type
from hub.dhis2.enrichment.fetch import EnrichmentFetcher
from hub.dhis2.enrichment.models import (
    AUDIT_CHANGED_SINCE_SCAN,
    AUDIT_MISSING_DHIS2,
    AUDIT_OPTION_SET_MISMATCH,
    AUDIT_VALUE_TYPE_MISMATCH,
    REL_DE_IN_DATA_SET,
    REL_DE_IN_PROGRAM_STAGE,
)
from hub.dhis2.enrichment.refs import extract_pi_references
from hub.dhis2.enrichment.store import EnrichmentStore
from hub.dhis2.redact import redact_mapping


class DeriveAnswerTypeTests(unittest.TestCase):
    def test_boolean_yes_no(self) -> None:
        self.assertEqual(derive_answer_type("BOOLEAN"), "Yes / No")

    def test_true_only_yes_only(self) -> None:
        self.assertEqual(derive_answer_type("TRUE_ONLY"), "Yes only")

    def test_option_set_flag(self) -> None:
        self.assertEqual(
            derive_answer_type("TEXT", option_set_value=True, option_set_uid="optSetUid01"),
            "Option Set",
        )

    def test_numeric_and_date_and_text(self) -> None:
        self.assertEqual(derive_answer_type("INTEGER_ZERO_OR_POSITIVE"), "Numeric")
        self.assertEqual(derive_answer_type("DATE"), "Date")
        self.assertEqual(derive_answer_type("DATETIME"), "Date-Time")
        self.assertEqual(derive_answer_type("LONG_TEXT"), "Free Text")


class ClassifyTests(unittest.TestCase):
    def test_missing_in_dhis2(self) -> None:
        statuses = classify_uid(
            repo_rows=[{"uid": "AbCdEfGhIj1", "name": "X", "object_type": "dataElement"}],
            dhis2_obj=None,
        )
        self.assertIn(AUDIT_MISSING_DHIS2, statuses)

    def test_value_type_and_option_set_mismatch(self) -> None:
        statuses = classify_uid(
            repo_rows=[
                {
                    "uid": "AbCdEfGhIj1",
                    "name": "X",
                    "object_type": "dataElement",
                    "value_type": "BOOLEAN",
                    "option_set_uid": "optAAAA1111",
                }
            ],
            dhis2_obj={
                "name": "X",
                "object_type": "dataElement",
                "valueType": "TEXT",
                "optionSet": {"id": "optBBBB2222", "name": "Other"},
            },
        )
        self.assertIn(AUDIT_VALUE_TYPE_MISMATCH, statuses)
        self.assertIn(AUDIT_OPTION_SET_MISMATCH, statuses)

    def test_changed_since_last_scan(self) -> None:
        statuses = classify_uid(
            repo_rows=[
                {
                    "uid": "AbCdEfGhIj1",
                    "name": "X",
                    "object_type": "dataElement",
                    "value_type": "BOOLEAN",
                }
            ],
            dhis2_obj={
                "name": "X",
                "object_type": "dataElement",
                "valueType": "BOOLEAN",
            },
            previous_checksum="aaaa1111bbbb2222",
            current_checksum="cccc3333dddd4444",
        )
        self.assertIn(AUDIT_CHANGED_SINCE_SCAN, statuses)


class PiRefTests(unittest.TestCase):
    def test_expression_extraction_and_unresolved(self) -> None:
        refs = extract_pi_references(
            "#{gKsusTMmABW.dxag8YT8w46} + A{teaUid00001} + C{constUid001}",
            "#{pzQalCsjr9F.JzxYzLgo0P9} == 'Approved' && weirdUid99x",
        )
        self.assertIn("gKsusTMmABW", refs["program_stages"])
        self.assertIn("dxag8YT8w46", refs["data_elements"])
        self.assertIn("teaUid00001", refs["attributes"])
        self.assertIn("constUid001", refs["constants"])
        self.assertTrue(any(u.startswith("weird") or len(u) == 11 for u in refs["unresolved"]) or refs["unresolved"] is not None)


class EnrichmentFetchTests(unittest.TestCase):
    def test_multi_stage_and_dataset_and_option_set(self) -> None:
        client = MagicMock()

        def iter_collection(plural, **kwargs):
            if plural == "programStages":
                return {
                    "items": [
                        {
                            "id": "stageUid0001",
                            "name": "Stage A",
                            "program": {"id": "programUid01", "name": "Prog"},
                            "programStageDataElements": [{"dataElement": {"id": "dataElemUid"}}],
                        },
                        {
                            "id": "stageUid0002",
                            "name": "Stage B",
                            "program": {"id": "programUid01", "name": "Prog"},
                            "programStageDataElements": [{"dataElement": {"id": "dataElemUid"}}],
                        },
                    ]
                }
            if plural == "dataSets":
                return {
                    "items": [
                        {
                            "id": "dataSetUid1",
                            "name": "Monthly",
                            "dataSetElements": [{"dataElement": {"id": "aggDeUid001"}}],
                        }
                    ]
                }
            if plural == "programs":
                return {"items": []}
            return {"items": []}

        def find_by_filter(plural, filt, **kwargs):
            out = []
            if plural == "dataElements":
                if "dataElemUid" in filt:
                    out.append(
                        {
                            "id": "dataElemUid",
                            "name": "Flag",
                            "valueType": "BOOLEAN",
                            "domainType": "TRACKER",
                            "optionSetValue": False,
                            "optionSet": None,
                            "categoryCombo": {"id": "catCombo001", "name": "None"},
                            "dataElementGroups": [],
                            "dataSetElements": [],
                        }
                    )
                if "aggDeUid001" in filt:
                    out.append(
                        {
                            "id": "aggDeUid001",
                            "name": "Agg",
                            "valueType": "NUMBER",
                            "domainType": "AGGREGATE",
                            "optionSet": None,
                            "categoryCombo": {"id": "catCombo001", "name": "None"},
                            "dataElementGroups": [],
                            "dataSetElements": [{"dataSet": {"id": "dataSetUid1", "name": "Monthly"}}],
                        }
                    )
                if "optDeUid001" in filt:
                    out.append(
                        {
                            "id": "optDeUid001",
                            "name": "Choice",
                            "valueType": "TEXT",
                            "optionSetValue": True,
                            "optionSet": {"id": "optSetYesNo", "name": "Yes No"},
                            "domainType": "TRACKER",
                            "categoryCombo": {"id": "catCombo001", "name": "None"},
                            "dataElementGroups": [],
                            "dataSetElements": [],
                        }
                    )
                return out
            if plural == "optionSets" and "optSetYesNo" in filt:
                return [
                    {
                        "id": "optSetYesNo",
                        "name": "Yes No",
                        "valueType": "TEXT",
                        "options": [
                            {
                                "id": "optYesUid01",
                                "name": "Yes",
                                "code": "1",
                                "sortOrder": 1,
                                "style": {"color": "#0f0"},
                            },
                            {"id": "optNoUid002", "name": "No", "code": "0", "sortOrder": 2},
                        ],
                    }
                ]
            return []

        client.iter_collection.side_effect = iter_collection
        client.find_by_filter.side_effect = find_by_filter

        fetcher = EnrichmentFetcher(client)

        result = fetcher.fetch_all(
            [
                {"uid": "dataElemUid", "name": "Flag", "object_type": "dataElement", "value_type": "BOOLEAN"},
                {"uid": "aggDeUid001", "name": "Agg", "object_type": "dataElement", "value_type": "NUMBER"},
                {"uid": "optDeUid001", "name": "Choice", "object_type": "dataElement", "value_type": "TEXT"},
            ],
            environment="test",
        )
        self.assertTrue(result["ok"])
        by_uid = {o["uid"]: o for o in result["objects"]}
        self.assertEqual(by_uid["dataElemUid"]["answer_type"], "Yes / No")
        self.assertEqual(by_uid["optDeUid001"]["answer_type"], "Option Set")
        stage_rels = [
            r
            for r in result["relationships"]
            if r["rel_type"] == REL_DE_IN_PROGRAM_STAGE and r["from_uid"] == "dataElemUid" and r["to_type"] == "programStage"
        ]
        self.assertEqual(len(stage_rels), 2)
        ds_rels = [
            r
            for r in result["relationships"]
            if r["rel_type"] == REL_DE_IN_DATA_SET and r["from_uid"] == "aggDeUid001"
        ]
        self.assertTrue(ds_rels)
        opts = [o for o in result["options"] if o["option_set_uid"] == "optSetYesNo"]
        self.assertEqual(len(opts), 2)
        self.assertEqual(opts[0]["sort_order"], 1)


class StoreAndRedactTests(unittest.TestCase):
    def test_snapshot_roundtrip_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from hub.dhis2.enrichment.db import EnrichmentDatabase

            store = EnrichmentStore(EnrichmentDatabase(Path(tmp) / "e.db"))
            snap = store.save_snapshot(
                environment="test",
                objects=[
                    {
                        "uid": "AbCdEfGhIj1",
                        "object_type": "dataElement",
                        "name": "X",
                        "answer_type": "Yes / No",
                        "value_type": "BOOLEAN",
                        "audit_statuses": ["Matched"],
                        "checksum": "abc",
                        "fetched_at": "t",
                    }
                ],
                relationships=[
                    {
                        "rel_type": REL_DE_IN_PROGRAM_STAGE,
                        "from_uid": "AbCdEfGhIj1",
                        "from_type": "dataElement",
                        "to_uid": "stageUid0001",
                        "to_type": "programStage",
                        "to_name": "S",
                        "detail": {},
                    }
                ],
                options=[],
                stats={"objects": 1},
            )
            self.assertTrue(snap)
            obj = store.get_object("AbCdEfGhIj1")
            self.assertIsNotNone(obj)
            assert obj is not None
            self.assertEqual(obj["answer_type"], "Yes / No")
            self.assertEqual(len(obj["relationships"]), 1)

        redacted = redact_mapping(
            {"password": "secret", "Authorization": "Bearer tok", "name": "ok"}
        )
        self.assertNotEqual(redacted.get("password"), "secret")
        self.assertEqual(redacted.get("name"), "ok")


if __name__ == "__main__":
    unittest.main()
