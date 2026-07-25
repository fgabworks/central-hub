"""Focused DHIS2-3 unified metadata builder regression tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app import create_app
from hub.dhis2.builders import get_builder
from hub.dhis2.builders.data_element import DataElementBuilder
from hub.dhis2.builders.generic import GenericSchemaBuilder
from hub.dhis2.builders.option_set import OptionSetBuilder
from hub.dhis2.builders.program_indicator import ProgramIndicatorBuilder
from hub.dhis2.catalog import CatalogStore
from hub.dhis2.client import Dhis2Client
from hub.dhis2.drafts import DraftStore
from hub.dhis2.type_config import load_metadata_builder_config
from hub.dhis2.uid_index import UidIndex
from hub.dhis2.uid_mapping.store import MappingIndexStore
from hub.dhis2.workspace import workspace_types


def _type_entry(singular: str, plural: str, *, required: list[dict] | None = None, references: list[dict] | None = None, enums: dict | None = None) -> dict:
    return {
        "id": singular,
        "schema_name": singular[:1].upper() + singular[1:],
        "klass": singular,
        "singular": singular,
        "plural": plural,
        "collection": plural,
        "builder_mode": "generic_schema_builder",
        "required_properties": required or [],
        "optional_properties": [],
        "optional_property_count": 0,
        "reference_properties": references or [],
        "collection_properties": [],
        "enums": enums or {},
    }


def _catalog() -> dict:
    common = [
        {"name": "name", "propertyType": "TEXT", "required": True},
    ]
    return {
        "ok": True,
        "types": [
            _type_entry("dataElement", "dataElements", required=common),
            _type_entry("programIndicator", "programIndicators", required=common),
            _type_entry("optionSet", "optionSets", required=common),
            _type_entry(
                "legend",
                "legends",
                required=common + [{"name": "color", "propertyType": "TEXT", "required": True}],
                references=[{"name": "program", "propertyType": "REFERENCE", "required": False, "referencedType": "program"}],
            ),
            _type_entry("program", "programs", required=common),
        ],
    }


def _client(*, configured: bool = False, duplicate_total: int = 0) -> MagicMock:
    client = MagicMock(spec=Dhis2Client)
    client.public_config.return_value = {"configured": configured}
    client.find_duplicates.return_value = {
        "has_duplicates": duplicate_total > 0,
        "matches": {"name": [{"id": "Existing001"}] if duplicate_total else [], "code": [], "id": []},
        "detail": f"Found {duplicate_total} duplicate candidate(s).",
    }
    return client


class MetadataBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.mapping_store = MappingIndexStore(root / "uid-index")
        self.mapping_store.save({
            "records": [
                {"uid": "Abcdefghijk", "name": "Nutrition Program", "code": "NUT", "object_type": "program", "source_repository": "fixture"},
                {"uid": "Defghijklmn", "name": "Default", "code": "DEFAULT", "object_type": "categoryCombo", "source_repository": "fixture"},
                {"uid": "Eabcdefghij", "name": "Weight", "code": "WEIGHT", "object_type": "dataElement", "source_repository": "fixture"},
            ]
        })
        self.uid_index = UidIndex(mapping_store=self.mapping_store)
        self.types = workspace_types(_catalog(), load_metadata_builder_config())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def type(self, type_id: str):
        return next(item for item in self.types if item.id == type_id)

    def test_dynamic_metadata_types_come_from_catalog(self) -> None:
        ids = [item.id for item in self.types]
        self.assertIn("legend", ids)
        self.assertIn("program", ids)
        self.assertNotIn("notDiscovered", ids)

    def test_specialized_and_generic_builder_loading(self) -> None:
        self.assertIsInstance(get_builder(self.type("dataElement"), _client(), self.uid_index), DataElementBuilder)
        self.assertIsInstance(get_builder(self.type("programIndicator"), _client(), self.uid_index), ProgramIndicatorBuilder)
        self.assertIsInstance(get_builder(self.type("optionSet"), _client(), self.uid_index), OptionSetBuilder)
        generic = get_builder(self.type("legend"), _client(), self.uid_index)
        self.assertIsInstance(generic, GenericSchemaBuilder)
        fields = {field.id: field for field in self.type("legend").fields}
        self.assertTrue(fields["name"].required)
        self.assertEqual(fields["program"].input, "dependency")

    def test_required_fields_and_dependency_validation(self) -> None:
        builder = get_builder(self.type("programIndicator"), _client(), self.uid_index)
        missing = builder.preview({}, operation="create", check_remote=False)
        self.assertFalse(missing["ok"])
        self.assertTrue(any("name" in item for item in missing["errors"]))
        unresolved = builder.preview({
            "name": "PI", "shortName": "PI", "program": "Zabcdefghij",
            "expression": "1", "analyticsType": "EVENT", "aggregationType": "SUM",
        }, operation="create", check_remote=False)
        self.assertFalse(unresolved["ok"])
        self.assertTrue(any("Unresolved dependency" in item for item in unresolved["errors"]))

    def test_duplicate_detection_blocks_create(self) -> None:
        builder = get_builder(self.type("optionSet"), _client(configured=True, duplicate_total=1), self.uid_index)
        preview = builder.preview({"name": "New", "valueType": "TEXT"}, operation="create", check_remote=True)
        self.assertFalse(preview["ok"])
        self.assertTrue(preview["duplicates"]["has_duplicates"])

    def test_payload_generation_exact_envelope(self) -> None:
        builder = get_builder(self.type("dataElement"), _client(), self.uid_index)
        preview = builder.preview({
            "name": "Body Mass", "shortName": "Body Mass", "domainType": "AGGREGATE",
            "valueType": "NUMBER", "aggregationType": "SUM", "categoryCombo": "Defghijklmn",
        }, operation="create", check_remote=False)
        self.assertTrue(preview["ok"], preview["errors"])
        payload = json.loads(preview["payload_json"])
        self.assertEqual(payload["dataElements"][0]["categoryCombo"], {"id": "Defghijklmn"})
        self.assertFalse(preview["apply_enabled"])

    def test_draft_save_and_load_preserves_raw_json(self) -> None:
        store = DraftStore(Path(self.temp.name) / "drafts")
        saved = store.save({"metadata_type": "optionSet", "operation": "create", "form": {"name": "Choices"}, "raw_json": "{\"optionSets\": []}"})
        loaded = store.load(saved["id"])
        self.assertEqual(loaded["raw_json"], "{\"optionSets\": []}")
        self.assertEqual(loaded["mode"], "preview_only")

    def test_invalid_raw_json_is_rejected(self) -> None:
        builder = get_builder(self.type("optionSet"), _client(), self.uid_index)
        preview = builder.preview_raw('{"optionSets": [}', operation="create", check_remote=False)
        self.assertFalse(preview["ok"])
        self.assertIn("Invalid raw JSON", preview["errors"][0])

    def test_option_set_order_follows_row_order_and_has_csv(self) -> None:
        builder = get_builder(self.type("optionSet"), _client(), self.uid_index)
        preview = builder.preview({
            "name": "Choices", "valueType": "TEXT",
            "options_json": json.dumps([
                {"code": "B", "name": "Beta", "sortOrder": 99},
                {"code": "A", "name": "Alpha", "sortOrder": 1},
            ]),
        }, operation="create", check_remote=False)
        options = preview["payload_object"]["options"]
        self.assertEqual([item["code"] for item in options], ["B", "A"])
        self.assertEqual([item["sortOrder"] for item in options], [1, 2])
        self.assertIn("1,B,Beta", preview["options_csv"])

    def test_program_indicator_uid_insertion_controls_render(self) -> None:
        root = Path(self.temp.name)
        catalog_store = CatalogStore(root / "catalog")
        catalog_store.save(_catalog())
        app = create_app()
        app.testing = True
        app.config["DHIS2_CATALOG"] = catalog_store
        app.config["DHIS2_MAPPING_INDEX"] = self.mapping_store
        app.config["DHIS2_UID_INDEX"] = self.uid_index
        app.config["DHIS2_DRAFTS"] = DraftStore(root / "drafts-route")
        response = app.test_client().get("/dhis2/metadata-builder?type=programIndicator")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="uid-search"', text)
        self.assertIn('id="uid-insert"', text)
        self.assertIn('id="expression-preview"', text)
        self.assertIn('data-builder="program_indicator"', text)
        self.assertIn("setRangeText(uid", text)


if __name__ == "__main__":
    unittest.main()
