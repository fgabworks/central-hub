"""Mocked tests for DHIS2-2 UID mapping explorer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2.uid_mapping.compare import (
    Classification,
    classify_against_dhis2,
    classify_index_records,
    find_missing_in_repository,
    resolve_plural,
)
from hub.dhis2.uid_mapping.models import NormalizedUidRecord, checksum_for
from hub.dhis2.uid_mapping.relationships import extract_relationships, extract_uids_from_text
from hub.dhis2.uid_mapping.scan import parse_csv_text, parse_json_text
from hub.dhis2.uid_mapping.search import filter_records
from hub.dhis2.uid_mapping.store import MappingIndexStore, apply_merge, merge_preview
from hub.settings import Dhis2Settings


SAMPLE_CSV = """kind,id,code,name,domainType,valueType,program,dhis2_environment
dataElement,AbCdEfGhIj1,DE_A,Alpha Element,TRACKER,BOOLEAN,,live
dataElement,AbCdEfGhIj2,DE_B,Beta Element,AGGREGATE,NUMBER,,live
programIndicator,XyZ12345678,PI_A,Alpha PI,TRACKER,,AbCdEfGhIjP,live
"""


class SearchTests(unittest.TestCase):
    def test_filter_by_uid_name_code_and_facets(self) -> None:
        records = [
            {
                "uid": "AbCdEfGhIj1",
                "name": "Alpha Element",
                "code": "DE_A",
                "object_type": "dataElement",
                "source_repository": "live-processing",
                "source_environment": "live",
            },
            {
                "uid": "AbCdEfGhIj2",
                "name": "Beta Element",
                "code": "DE_B",
                "object_type": "dataElement",
                "source_repository": "other",
                "source_environment": "stage",
            },
            {
                "uid": "XyZ12345678",
                "name": "Alpha PI",
                "code": "PI_A",
                "object_type": "programIndicator",
                "source_repository": "live-processing",
                "source_environment": "live",
            },
        ]
        self.assertEqual(len(filter_records(records, query="AbCdEfGhIj1")), 1)
        self.assertEqual(len(filter_records(records, query="beta")), 1)
        self.assertEqual(len(filter_records(records, query="PI_A")), 1)
        self.assertEqual(len(filter_records(records, object_type="programIndicator")), 1)
        self.assertEqual(len(filter_records(records, source_repository="live-processing")), 2)
        self.assertEqual(len(filter_records(records, environment="stage")), 1)
        paged = filter_records(records, source_repository="live-processing", limit=1, offset=1)
        self.assertEqual(len(paged), 1)
        self.assertEqual(len(filter_records(records, limit=None)), len(records))


class RelationshipTests(unittest.TestCase):
    def test_extract_common_relationships(self) -> None:
        de = {
            "id": "AbCdEfGhIj1",
            "optionSet": {"id": "OpTiOnSeT01", "name": "YesNo"},
            "categoryCombo": {"id": "CaTcOmBo001", "name": "default"},
            "dataSetElements": [{"dataSet": {"id": "DaTaSeT0001", "name": "DS"}}],
        }
        rels = extract_relationships("dataElement", de)
        kinds = {r["relation"] for r in rels}
        self.assertIn("Data Element → Option Set", kinds)
        self.assertIn("Data Element → Category Combination", kinds)
        self.assertIn("Data Element → Data Set", kinds)

        pi = {
            "id": "XyZ12345678",
            "program": {"id": "PrOgRaM0001", "name": "Prog"},
            "expression": "#{AbCdEfGhIj1} + #{AbCdEfGhIj2}",
        }
        pi_rels = extract_relationships("programIndicator", pi)
        self.assertTrue(any(r["relation"] == "Program Indicator → Program" for r in pi_rels))
        self.assertTrue(
            any(r["relation"] == "Program Indicator → referenced Data Elements" for r in pi_rels)
        )

        os_obj = {
            "id": "OpTiOnSeT01",
            "options": [{"id": "OpTiOn00001", "name": "Yes"}, {"id": "OpTiOn00002", "name": "No"}],
        }
        os_rels = extract_relationships("optionSet", os_obj)
        self.assertEqual(len([r for r in os_rels if r["relation"] == "Option Set → Options"]), 2)

        dash = {
            "id": "DaShBoArD01",
            "dashboardItems": [
                {"type": "VISUALIZATION", "visualization": {"id": "ViSuAlIz001", "name": "Chart"}}
            ],
        }
        d_rels = extract_relationships("dashboard", dash)
        self.assertTrue(any(r["relation"] == "Dashboard → dashboard items" for r in d_rels))

    def test_extract_uids_from_text(self) -> None:
        uids = extract_uids_from_text("#{AbCdEfGhIj1} and AbCdEfGhIj2")
        self.assertEqual(uids, ["AbCdEfGhIj1", "AbCdEfGhIj2"])


class DuplicateAndConflictTests(unittest.TestCase):
    def test_duplicate_and_conflicting_uids(self) -> None:
        records = [
            {"uid": "AbCdEfGhIj1", "name": "Alpha", "code": "A", "object_type": "dataElement"},
            {"uid": "AbCdEfGhIj1", "name": "Alpha Renamed", "code": "A", "object_type": "dataElement"},
            {"uid": "AbCdEfGhIj2", "name": "Beta", "code": "B", "object_type": "dataElement"},
        ]
        offline = classify_index_records(records)
        self.assertEqual(len(offline["duplicate"]), 1)
        self.assertEqual(offline["duplicate"][0]["uid"], "AbCdEfGhIj1")
        self.assertEqual(len(offline["conflicting"]), 1)

    def test_merge_never_silently_overwrites_conflicts(self) -> None:
        existing = [
            {
                "uid": "AbCdEfGhIj1",
                "name": "Alpha",
                "code": "A",
                "object_type": "dataElement",
                "source_repository": "repo-a",
                "source_file": "a.csv",
                "source_environment": "live",
                "checksum": "x",
            }
        ]
        incoming = [
            NormalizedUidRecord.from_mapping(
                {
                    "uid": "AbCdEfGhIj1",
                    "name": "Different Name",
                    "code": "A2",
                    "object_type": "dataElement",
                    "source_repository": "repo-b",
                    "source_file": "b.csv",
                    "source_environment": "live",
                }
            )
        ]
        preview = merge_preview(existing, incoming)
        self.assertEqual(preview["counts"]["conflicting"], 1)
        self.assertEqual(preview["counts"]["added"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(root=Path(tmp))
            store.save({"ok": True, "records": existing, "record_count": 1})
            applied = apply_merge(store, preview, include_conflicts=False)
            # Original remains; conflict skipped
            self.assertEqual(applied["record_count"], 1)
            self.assertEqual(store.records()[0]["name"], "Alpha")
            self.assertEqual(applied["last_merge"]["skipped_conflicts"], 1)


class MissingMetadataTests(unittest.TestCase):
    def test_missing_in_dhis2_and_repository(self) -> None:
        client = MagicMock(spec=Dhis2Client)
        client.settings = Dhis2Settings(
            base_url="https://dhis2.example.org",
            username="u",
            password="p",
            timeout_seconds=5,
            allow_writes=False,
            enabled=True,
        )

        def fetch(_plural: str, _uid: str) -> dict:
            raise Dhis2Error("Not found", status_code=404)

        result = classify_against_dhis2(
            {"uid": "AbCdEfGhIj1", "name": "Alpha", "object_type": "dataElement"},
            client,
            fetch_object=fetch,
        )
        self.assertEqual(result["status"], Classification.MISSING_IN_DHIS2.value)

        matched = classify_against_dhis2(
            {
                "uid": "AbCdEfGhIj1",
                "name": "Alpha",
                "code": "A",
                "object_type": "dataElement",
                "value_type": "NUMBER",
            },
            client,
            fetch_object=lambda p, u: {
                "ok": True,
                "resource_type": "dataElements",
                "item": {"id": u, "name": "Alpha"},
                "raw": {"id": u, "name": "Alpha", "code": "A", "valueType": "NUMBER"},
            },
        )
        self.assertEqual(matched["status"], Classification.MATCHED.value)

        changed = classify_against_dhis2(
            {
                "uid": "AbCdEfGhIj1",
                "name": "Alpha Local",
                "code": "A",
                "object_type": "dataElement",
            },
            client,
            fetch_object=lambda p, u: {
                "ok": True,
                "resource_type": "dataElements",
                "raw": {"id": u, "name": "Alpha Remote", "code": "A"},
            },
        )
        self.assertEqual(changed["status"], Classification.CHANGED.value)
        self.assertIn("name", changed["diffs"])

        missing_repo = find_missing_in_repository(
            [{"id": "AbCdEfGhIj9", "name": "Only in DHIS2"}],
            {"AbCdEfGhIj1"},
        )
        self.assertEqual(len(missing_repo), 1)
        self.assertEqual(missing_repo[0]["status"], Classification.MISSING_IN_REPOSITORY.value)

    def test_resolve_plural_uses_catalog(self) -> None:
        self.assertEqual(resolve_plural("dataElement"), "dataElements")
        catalog = [{"id": "customThing", "singular": "customThing", "plural": "customThings"}]
        self.assertEqual(resolve_plural("customThing", catalog), "customThings")


class ParseAndChecksumTests(unittest.TestCase):
    def test_parse_csv_and_json(self) -> None:
        source = {
            "id": "lp",
            "repository_id": "live-processing",
            "environment": "live",
            "column_map": {
                "uid": "id",
                "name": "name",
                "code": "code",
                "object_type": "kind",
                "value_type": "valueType",
                "domain_type": "domainType",
                "program_uid": "program",
            },
        }
        rows = parse_csv_text(SAMPLE_CSV, source=source, source_file="AI_UID_INDEX.csv")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].uid, "AbCdEfGhIj1")
        self.assertEqual(rows[0].object_type, "dataElement")
        self.assertEqual(rows[0].source_repository, "live-processing")
        self.assertTrue(rows[0].checksum)

        json_rows = parse_json_text(
            '[{"uid":"AbCdEfGhIj1","name":"Alpha","object_type":"dataElement"}]',
            source={"id": "upload", "repository_id": "upload", "environment": "stage"},
            source_file="upload.json",
        )
        self.assertEqual(len(json_rows), 1)
        self.assertEqual(checksum_for(json_rows[0].to_dict()), json_rows[0].checksum)


if __name__ == "__main__":
    unittest.main()
