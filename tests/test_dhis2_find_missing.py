"""Find Missing UIDs — DHIS2 → local index discovery (GET-only)."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.dhis2.uid_mapping.admin import apply_with_confirmation, enrich_controlled_preview
from hub.dhis2.uid_mapping.compare import Classification
from hub.dhis2.uid_mapping.missing import (
    CONFIRM_ADD_MISSING,
    dhis2_item_to_index_record,
    discover_missing_uids,
    export_source_update_csv_rows,
    filter_missing_rows,
    scannable_type_options,
    selected_rows_to_records,
    source_badge,
)
from hub.dhis2.uid_mapping.models import (
    SOURCE_CSV,
    SOURCE_DHIS2_IMPORT,
    SOURCE_MANUAL,
    NormalizedUidRecord,
)
from hub.dhis2.uid_mapping.scan import normalize_row, parse_csv_text
from hub.dhis2.uid_mapping.store import MappingIndexStore, merge_preview


class FakeClient:
    def __init__(self, collections: dict[str, list[dict]]) -> None:
        self.collections = collections
        self.write_calls = 0

    def iter_collection(self, plural, fields="", page_size=100, max_pages=50, **kwargs):
        items = list(self.collections.get(plural) or [])
        return {
            "items": items,
            "count": len(items),
            "total": len(items),
            "pages_fetched": 1,
            "truncated": False,
        }


class MissingDiscoveryTests(unittest.TestCase):
    def test_generic_missing_object_detection(self) -> None:
        client = FakeClient(
            {
                "dataElements": [
                    {"id": "AAAAAAAAAAA", "name": "Known", "valueType": "TEXT"},
                    {"id": "JV4XSWHKnaU", "name": "ANC Compliance", "valueType": "BOOLEAN"},
                    {"id": "iPA4CCa6tFd", "name": "PNC Compliance", "valueType": "BOOLEAN"},
                    {"id": "BBBBBBBBBBB", "name": "Other", "valueType": "NUMBER"},
                ],
                "indicators": [
                    {"id": "CCCCCCCCCCC", "name": "Ind", "code": "I1"},
                ],
                "programs": [],
                "programStages": [],
                "dataSets": [],
                "optionSets": [],
                "trackedEntityAttributes": [],
                "trackedEntityTypes": [],
                "programIndicators": [],
                "categoryCombos": [],
            }
        )
        index = [{"uid": "AAAAAAAAAAA", "object_type": "dataElement"}]
        result = discover_missing_uids(
            client,
            index,
            environment="live",
            object_types=["dataElement", "indicator"],
        )
        self.assertEqual(result["dhis2_writes"], 0)
        missing_uids = {m["uid"] for m in result["missing"]}
        self.assertIn("JV4XSWHKnaU", missing_uids)
        self.assertIn("iPA4CCa6tFd", missing_uids)
        self.assertIn("BBBBBBBBBBB", missing_uids)
        self.assertIn("CCCCCCCCCCC", missing_uids)
        self.assertNotIn("AAAAAAAAAAA", missing_uids)
        self.assertEqual(result["classification"], Classification.MISSING_IN_REPOSITORY.value)

    def test_no_filtering_by_boolean_or_names(self) -> None:
        client = FakeClient(
            {
                "dataElements": [
                    {"id": "BoolOnlyUID1", "name": "ZZZ Completely Unrelated", "valueType": "BOOLEAN"},
                    {"id": "TextOnlyUID2", "name": "Compliance Something", "valueType": "TEXT"},
                ],
            }
        )
        # Limit scan targets via collections override for speed
        from hub.dhis2.uid_mapping.missing import SCANNABLE_COLLECTIONS

        only_de = tuple(c for c in SCANNABLE_COLLECTIONS if c["object_type"] == "dataElement")
        result = discover_missing_uids(
            client,
            [],
            environment="stage",
            collections=only_de,
        )
        uids = {m["uid"] for m in result["missing"]}
        self.assertEqual(uids, {"BoolOnlyUID1", "TextOnlyUID2"})
        # Filters are explicit params only — BOOLEAN and "Compliance" are not special-cased
        filtered = filter_missing_rows(result["missing"], q="unrelated")
        self.assertEqual([r["uid"] for r in filtered], ["BoolOnlyUID1"])

    def test_selected_import_updates_local_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(root=Path(tmp))
            store.save({"ok": True, "records": [], "record_count": 0, "updated_at": "t"})
            selected = [
                {
                    "uid": "JV4XSWHKnaU",
                    "object_type": "dataElement",
                    "source_environment": "live",
                    "dhis2": {
                        "id": "JV4XSWHKnaU",
                        "name": "ANC Compliance",
                        "valueType": "BOOLEAN",
                    },
                }
            ]
            incoming = selected_rows_to_records(selected, environment="live")
            self.assertEqual(incoming[0].source_origin, SOURCE_DHIS2_IMPORT)
            self.assertFalse(incoming[0].csv_synced)
            preview = enrich_controlled_preview(
                merge_preview(store.records(), incoming),
                existing=store.records(),
                incoming=incoming,
                store=store,
            )
            result = apply_with_confirmation(
                store,
                preview,
                CONFIRM_ADD_MISSING,
                confirm_phrase=CONFIRM_ADD_MISSING,
            )
            self.assertTrue(result["ok"])
            rows = store.records()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["uid"], "JV4XSWHKnaU")
            self.assertEqual(rows[0]["source_origin"], SOURCE_DHIS2_IMPORT)
            self.assertFalse(rows[0]["csv_synced"])

    def test_csv_reload_remains_separate(self) -> None:
        # CSV normalize defaults to Source CSV / synced — different from DHIS2 import
        source = {
            "id": "lp",
            "repository_id": "live-processing",
            "environment": "live",
            "column_map": {"uid": "id", "object_type": "kind", "value_type": "valueType"},
        }
        rec = normalize_row(
            {"id": "AAAAAAAAAAA", "name": "From CSV", "kind": "dataElement", "valueType": "TEXT"},
            source=source,
            source_file="AI_UID_INDEX.csv",
            column_map=source["column_map"],
        )
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.source_origin, SOURCE_CSV)
        self.assertTrue(rec.csv_synced)
        # Manual upload path
        manual = normalize_row(
            {"id": "BBBBBBBBBBB", "name": "Upload", "kind": "dataElement"},
            source={**source, "source_origin": SOURCE_MANUAL, "repository_id": "upload"},
            source_file="upload.csv",
            column_map=source["column_map"],
        )
        assert manual is not None
        self.assertEqual(manual.source_origin, SOURCE_MANUAL)

    def test_source_badges_and_export(self) -> None:
        records = [
            {
                "uid": "AAAAAAAAAAA",
                "name": "CSV",
                "object_type": "dataElement",
                "source_origin": SOURCE_CSV,
                "csv_synced": True,
            },
            {
                "uid": "JV4XSWHKnaU",
                "name": "Imported",
                "object_type": "dataElement",
                "value_type": "BOOLEAN",
                "source_origin": SOURCE_DHIS2_IMPORT,
                "csv_synced": False,
                "source_environment": "live",
            },
            {
                "uid": "MANUAL00001",
                "name": "Manual",
                "object_type": "indicator",
                "source_origin": SOURCE_MANUAL,
                "csv_synced": False,
            },
        ]
        self.assertEqual(source_badge(records[0])["label"], "Source CSV")
        self.assertEqual(source_badge(records[1])["label"], "DHIS2 Import")
        self.assertTrue(source_badge(records[1])["needs_csv_export"])
        self.assertEqual(source_badge(records[2])["label"], "Manual")
        export_rows = export_source_update_csv_rows(records)
        self.assertEqual(len(export_rows), 1)
        self.assertEqual(export_rows[0]["id"], "JV4XSWHKnaU")
        # Round-trip CSV header shape
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(export_rows[0].keys()))
        writer.writeheader()
        writer.writerows(export_rows)
        self.assertIn("JV4XSWHKnaU", buf.getvalue())

    def test_stage_live_separation(self) -> None:
        client = FakeClient(
            {
                "dataElements": [
                    {"id": "StageOnlyUID1", "name": "Stage DE", "valueType": "TEXT"},
                ],
            }
        )
        from hub.dhis2.uid_mapping.missing import SCANNABLE_COLLECTIONS

        only_de = tuple(c for c in SCANNABLE_COLLECTIONS if c["object_type"] == "dataElement")
        stage = discover_missing_uids(
            client, [], environment="stage", collections=only_de
        )
        live = discover_missing_uids(
            client, [], environment="live", collections=only_de
        )
        self.assertEqual(stage["environment"], "stage")
        self.assertEqual(live["environment"], "live")
        self.assertEqual(stage["missing"][0]["source_environment"], "stage")
        rec = dhis2_item_to_index_record(
            stage["missing"][0]["dhis2"],
            object_type="dataElement",
            environment="stage",
        )
        self.assertEqual(rec.source_environment, "stage")
        self.assertIn("stage", rec.source_file)

    def test_no_dhis2_writes(self) -> None:
        client = FakeClient({"dataElements": [{"id": "XXXXXXXXXXX", "name": "X"}]})
        from hub.dhis2.uid_mapping.missing import SCANNABLE_COLLECTIONS

        only_de = tuple(c for c in SCANNABLE_COLLECTIONS if c["object_type"] == "dataElement")
        result = discover_missing_uids(client, [], collections=only_de)
        self.assertEqual(result["dhis2_writes"], 0)
        self.assertEqual(client.write_calls, 0)
        types = {t["id"] for t in scannable_type_options()}
        self.assertIn("dataElement", types)
        self.assertIn("indicator", types)
        self.assertIn("program", types)
        self.assertIn("trackedEntityType", types)

    def test_readable_type_labels_and_pagination(self) -> None:
        from hub.dhis2.uid_mapping.missing import object_type_label, paginate_rows

        self.assertEqual(object_type_label("dataElement"), "Data Element")
        self.assertEqual(object_type_label("programIndicator"), "Program Indicator")
        self.assertEqual(object_type_label("programStage"), "Program Stage")
        labels = {t["id"]: t["label"] for t in scannable_type_options()}
        self.assertEqual(labels["dataElement"], "Data Element")
        rows = [{"uid": f"UID{i:08d}X"} for i in range(5)]
        page1 = paginate_rows(rows, page=1, per_page=2)
        page2 = paginate_rows(rows, page=2, per_page=2)
        self.assertEqual([r["uid"] for r in page1["rows"]], ["UID00000000X", "UID00000001X"])
        self.assertEqual([r["uid"] for r in page2["rows"]], ["UID00000002X", "UID00000003X"])
        self.assertEqual(page1["total"], 5)
        self.assertEqual(page1["total_pages"], 3)
        self.assertEqual(len(page1["uids"]), 5)


if __name__ == "__main__":
    unittest.main()
