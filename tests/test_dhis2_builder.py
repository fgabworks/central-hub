"""Mocked tests for DHIS2-3 Unified Metadata Builder (preview-only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.dhis2.builders import get_builder, registered_builder_keys
from hub.dhis2.builders.generic import GenericSchemaBuilder
from hub.dhis2.client import Dhis2Client
from hub.dhis2.drafts import DraftStore
from hub.dhis2.type_config import FieldSpec, MetadataBuilderConfig, MetadataTypeSpec, OperationSpec, InstanceSpec
from hub.dhis2.uid_index import UidIndex
from hub.dhis2.uid_mapping.models import NormalizedUidRecord
from hub.dhis2.uid_mapping.store import MappingIndexStore, apply_merge, merge_preview
from hub.dhis2.workspace import catalog_schema_summary, workspace_stats, workspace_types
from hub.settings import Dhis2Settings


def _client() -> Dhis2Client:
    return Dhis2Client(
        Dhis2Settings(
            base_url="https://dhis2.example.org",
            username="u",
            password="p",
            timeout_seconds=5,
            allow_writes=False,
            enabled=True,
            retry_max=0,
            retry_backoff_seconds=0.0,
        )
    )


def _sample_catalog() -> dict:
    return {
        "ok": True,
        "types": [
            {
                "id": "dataElement",
                "singular": "dataElement",
                "plural": "dataElements",
                "schema_name": "Data Element",
                "klass": "org.hisp.dhis.dataelement.DataElement",
                "builder_mode": "specialized_builder",
                "identifiable": True,
                "nameable": True,
                "required_properties": [{"name": "name", "propertyType": "TEXT", "required": True}],
                "reference_properties": [
                    {"name": "categoryCombo", "propertyType": "REFERENCE", "referencedType": "categoryCombo"}
                ],
                "collection_properties": [],
                "optional_properties": [{"name": "code", "propertyType": "TEXT"}],
                "enums": {"valueType": ["TEXT", "NUMBER"]},
            },
            {
                "id": "indicator",
                "singular": "indicator",
                "plural": "indicators",
                "schema_name": "Indicator",
                "klass": "org.hisp.dhis.indicator.Indicator",
                "builder_mode": "generic_schema_builder",
                "identifiable": True,
                "nameable": True,
                "required_properties": [
                    {"name": "name", "propertyType": "TEXT", "required": True},
                    {"name": "shortName", "propertyType": "TEXT", "required": True},
                ],
                "reference_properties": [
                    {"name": "indicatorType", "propertyType": "REFERENCE", "referencedType": "indicatorType"}
                ],
                "collection_properties": [],
                "optional_properties": [{"name": "description", "propertyType": "TEXT"}],
                "enums": {},
                "optional_property_count": 1,
            },
            {
                "id": "jobConfiguration",
                "singular": "jobConfiguration",
                "plural": "jobConfigurations",
                "schema_name": "Job Configuration",
                "klass": "org.hisp.dhis.scheduling.JobConfiguration",
                "builder_mode": "read_only_explorer",
                "identifiable": True,
                "required_properties": [],
                "reference_properties": [],
                "collection_properties": [],
                "optional_properties": [],
                "enums": {},
            },
        ],
    }


def _config() -> MetadataBuilderConfig:
    return MetadataBuilderConfig(
        instances=[InstanceSpec("default", "Stage", "env")],
        operations=[
            OperationSpec("create", "Create", True, False),
            OperationSpec("update", "Update", True, False),
        ],
        metadata_types=[
            MetadataTypeSpec(
                id="dataElement",
                label="Data Element",
                plural="dataElements",
                schema_klass="dataElement",
                builder="data_element",
                dependency_resources=["categoryCombos", "optionSets"],
                fields=[
                    FieldSpec("name", "Name", "text", required=True),
                    FieldSpec("shortName", "Short name", "text", required=True),
                    FieldSpec("valueType", "Value type", "select", required=True, options=["TEXT", "NUMBER"]),
                    FieldSpec("domainType", "Domain", "select", required=True, options=["AGGREGATE", "TRACKER"]),
                    FieldSpec("aggregationType", "Aggregation", "select", required=True, options=["SUM", "NONE"]),
                    FieldSpec("categoryCombo", "Category combo", "dependency", resource="categoryCombos"),
                ],
            )
        ],
    )


class WorkspaceTests(unittest.TestCase):
    def test_workspace_skips_read_only_and_uses_specialized_or_generic(self) -> None:
        types = workspace_types(_sample_catalog(), _config())
        ids = {item.id for item in types}
        self.assertIn("dataElement", ids)
        self.assertIn("indicator", ids)
        self.assertNotIn("jobConfiguration", ids)
        de = next(item for item in types if item.id == "dataElement")
        ind = next(item for item in types if item.id == "indicator")
        self.assertEqual(de.builder_mode, "specialized_builder")
        self.assertEqual(de.builder, "data_element")
        self.assertEqual(ind.builder_mode, "generic_schema_builder")
        self.assertEqual(ind.builder, "generic_schema")
        self.assertTrue(any(f.id == "indicatorType" and f.input == "dependency" for f in ind.fields))
        stats = workspace_stats(types)
        self.assertEqual(stats["specialized_builder"], 1)
        self.assertEqual(stats["generic_schema_builder"], 1)

    def test_empty_catalog_yields_no_types(self) -> None:
        self.assertEqual(workspace_types(None, _config()), [])
        self.assertEqual(workspace_types({}, _config()), [])


class GenericBuilderTests(unittest.TestCase):
    def test_generic_preview_payload_and_no_apply(self) -> None:
        types = workspace_types(_sample_catalog(), _config())
        ind = next(item for item in types if item.id == "indicator")
        builder = get_builder(ind, _client(), uid_index=UidIndex())
        self.assertIsInstance(builder, GenericSchemaBuilder)
        preview = builder.preview(
            {
                "name": "Coverage",
                "shortName": "Cov",
                "description": "Demo",
                "indicatorType": "InDiCaToR01",
            },
            operation="create",
            check_remote=False,
        )
        self.assertFalse(preview["apply_enabled"])
        self.assertEqual(preview["builder_mode"], "generic_schema_builder")
        self.assertIn("indicators", preview["payload"])
        obj = preview["payload_object"]
        self.assertEqual(obj["name"], "Coverage")
        self.assertEqual(obj["indicatorType"], {"id": "InDiCaToR01"})
        # Unresolved dependency → validation error (index empty)
        self.assertFalse(preview["ok"])
        self.assertTrue(any("Unresolved dependency" in e for e in preview["errors"]))

    def test_specialized_data_element_preview_ok(self) -> None:
        types = workspace_types(_sample_catalog(), _config())
        de = next(item for item in types if item.id == "dataElement")
        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(root=Path(tmp))
            incoming = [
                NormalizedUidRecord.from_mapping(
                    {
                        "uid": "CaTcOmBo001",
                        "name": "default",
                        "object_type": "categoryCombo",
                        "source_repository": "test",
                        "source_file": "t.csv",
                        "source_environment": "stage",
                    }
                )
            ]
            apply_merge(store, merge_preview([], incoming))
            uid_index = UidIndex(mapping_store=store)
            builder = get_builder(de, _client(), uid_index=uid_index)
            preview = builder.preview(
                {
                    "name": "Alpha",
                    "shortName": "Alpha",
                    "valueType": "NUMBER",
                    "domainType": "AGGREGATE",
                    "aggregationType": "SUM",
                    "categoryCombo": "CaTcOmBo001",
                },
                operation="create",
                check_remote=False,
            )
        self.assertTrue(preview["ok"])
        self.assertFalse(preview["apply_enabled"])
        self.assertEqual(preview["payload_object"]["categoryCombo"], {"id": "CaTcOmBo001"})

    def test_update_requires_uid(self) -> None:
        types = workspace_types(_sample_catalog(), _config())
        de = next(item for item in types if item.id == "dataElement")
        builder = get_builder(de, _client())
        preview = builder.preview(
            {
                "name": "Alpha",
                "shortName": "Alpha",
                "valueType": "TEXT",
                "domainType": "AGGREGATE",
                "aggregationType": "SUM",
            },
            operation="update",
            check_remote=False,
        )
        self.assertFalse(preview["ok"])
        self.assertTrue(any("UID" in e or "id" in e for e in preview["errors"]))

    def test_registered_builders_include_generic(self) -> None:
        keys = registered_builder_keys()
        self.assertIn("generic_schema", keys)
        self.assertIn("data_element", keys)


class DraftAndSummaryTests(unittest.TestCase):
    def test_draft_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drafts = DraftStore(root=Path(tmp))
            saved = drafts.save(
                {
                    "metadata_type": "indicator",
                    "operation": "create",
                    "form": {"name": "X"},
                    "payload": {"indicators": [{"name": "X"}]},
                    "validation_ok": True,
                }
            )
            loaded = drafts.load(saved["id"])
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["form"]["name"], "X")
            self.assertEqual(len(drafts.list_recent()), 1)

    def test_catalog_schema_summary(self) -> None:
        types = workspace_types(_sample_catalog(), _config())
        ind = next(item for item in types if item.id == "indicator")
        summary = catalog_schema_summary(ind)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["source"], "discovered_catalog")
        self.assertEqual(summary["plural"], "indicators")


class UidIndexMatchTests(unittest.TestCase):
    def test_matches_plural_and_singular_object_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(root=Path(tmp))
            incoming = [
                NormalizedUidRecord.from_mapping(
                    {
                        "uid": "PrOgRaM0001",
                        "name": "Household",
                        "object_type": "program",
                        "source_repository": "test",
                        "source_file": "t.csv",
                        "source_environment": "live",
                    }
                )
            ]
            apply_merge(store, merge_preview([], incoming))
            idx = UidIndex(mapping_store=store)
            hits = idx.search("programs", "Household")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["id"], "PrOgRaM0001")


class RouteSmokeTests(unittest.TestCase):
    def test_metadata_builder_page_loads_without_catalog(self) -> None:
        from app import create_app

        app = create_app()
        client = app.test_client()
        resp = client.get("/dhis2/metadata-builder")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Unified Metadata Builder", resp.data)
        self.assertIn(b"Create", resp.data)
        self.assertIn(b"disabled", resp.data.lower())


if __name__ == "__main__":
    unittest.main()
