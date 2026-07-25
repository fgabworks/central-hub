"""Mocked tests for DHIS2 instance discovery and catalog building."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.dhis2.catalog import (
    CatalogStore,
    categorize_schema,
    extract_type_entry,
    filter_types,
    run_discovery,
)
from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.settings import Dhis2Settings


def _settings() -> Dhis2Settings:
    return Dhis2Settings(
        base_url="https://dhis2.example.org",
        username="stage_user",
        password="secret-password",
        timeout_seconds=5,
        allow_writes=False,
        enabled=True,
    )


class CatalogHelpersTests(unittest.TestCase):
    def test_categorize_and_extract(self) -> None:
        self.assertEqual(categorize_schema("dataElement", "dataElement"), "data")
        self.assertEqual(categorize_schema("weirdThing", "weirdThing"), "other")
        schema = {
            "name": "dataElement",
            "singular": "dataElement",
            "plural": "dataElements",
            "klass": "org.hisp.dhis.dataelement.DataElement",
            "relativeApiEndpoint": "/dataElements",
            "identifiableObject": True,
            "properties": {
                "name": {"propertyType": "TEXT", "required": True},
                "code": {"propertyType": "TEXT", "required": False},
                "categoryCombo": {
                    "propertyType": "REFERENCE",
                    "required": False,
                    "referencedType": "categoryCombo",
                },
                "valueType": {
                    "propertyType": "CONSTANT",
                    "required": True,
                    "constants": ["TEXT", "NUMBER"],
                },
                "href": {"propertyType": "TEXT", "required": False},
            },
        }
        entry = extract_type_entry(schema, specialized={"dataElement", "dataElements"})
        self.assertEqual(entry["builder_mode"], "specialized_builder")
        self.assertEqual(entry["api_endpoint"], "/api/dataElements")
        self.assertTrue(any(p["name"] == "name" for p in entry["required_properties"]))
        self.assertTrue(any(p["name"] == "categoryCombo" for p in entry["reference_properties"]))
        self.assertIn("valueType", entry["enums"])
        self.assertFalse(entry["operations"]["hub_apply_enabled"])

    def test_filter_types(self) -> None:
        types = [
            {"schema_name": "Data Element", "singular": "dataElement", "plural": "dataElements", "category": "data", "builder_mode": "specialized_builder", "api_endpoint": "/api/dataElements"},
            {"schema_name": "Program", "singular": "program", "plural": "programs", "category": "tracker", "builder_mode": "generic_schema_builder", "api_endpoint": "/api/programs"},
        ]
        self.assertEqual(len(filter_types(types, query="program")), 1)
        self.assertEqual(len(filter_types(types, category="data")), 1)
        self.assertEqual(len(filter_types(types, builder_mode="specialized_builder")), 1)


class DiscoveryTests(unittest.TestCase):
    def test_run_discovery_builds_catalog_without_secrets(self) -> None:
        client = MagicMock(spec=Dhis2Client)
        client.settings = _settings()
        client.check_status.return_value = {
            "ok": True,
            "system": {"version": "2.40.3", "systemName": "Stage", "serverDate": "2026-07-25"},
            "detail": "Connected",
        }
        client.get_me.return_value = {
            "id": "user123",
            "username": "stage_user",
            "displayName": "Stage User",
            "email": "stage@example.org",
            "organisationUnits": [{"id": "ou1", "name": "Country"}],
            "userRoles": [{"id": "role1", "name": "Data Entry"}],
        }
        client.get_authorities.return_value = ["F_DATAELEMENT_PUBLIC_ADD", "M_dhis-web-maintenance"]
        client.get_schemas_document.return_value = [
            {
                "name": "dataElement",
                "singular": "dataElement",
                "plural": "dataElements",
                "klass": "DataElement",
                "relativeApiEndpoint": "/dataElements",
                "identifiableObject": True,
                "properties": {
                    "name": {"propertyType": "TEXT", "required": True},
                    "shortName": {"propertyType": "TEXT", "required": True},
                },
            },
            {
                "name": "program",
                "singular": "program",
                "plural": "programs",
                "klass": "Program",
                "relativeApiEndpoint": "/programs",
                "identifiableObject": True,
                "properties": {"name": {"propertyType": "TEXT", "required": True}},
            },
        ]
        client.get_openapi_summary.return_value = {
            "available": True,
            "detail": "ok",
            "path_count": 12,
            "tags": ["metadata"],
            "sample_paths": ["/dataElements"],
        }
        client.get_api_entry.return_value = {
            "available": True,
            "resource_count": 1,
            "resources": [{"name": "dataElements", "href": "https://dhis2.example.org/api/dataElements"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            store = CatalogStore(Path(tmp))
            catalog = run_discovery(client, store=store, enrich_samples=False)
            self.assertTrue(catalog["ok"])
            self.assertEqual(catalog["dhis2_version"], "2.40.3")
            self.assertEqual(catalog["type_count"], 2)
            self.assertEqual(catalog["authority_count"], 2)
            self.assertNotIn("secret-password", json.dumps(catalog))
            self.assertNotIn("Authorization", json.dumps(catalog))
            loaded = store.load_latest()
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["type_count"], 2)
            data_element = store.get_type("dataElement")
            self.assertIsNotNone(data_element)
            assert data_element is not None
            self.assertEqual(data_element["builder_mode"], "specialized_builder")

    def test_discovery_fails_when_status_offline(self) -> None:
        client = MagicMock(spec=Dhis2Client)
        client.settings = _settings()
        client.check_status.return_value = {
            "ok": False,
            "detail": "DHIS2 is not configured.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Dhis2Error):
                run_discovery(client, store=CatalogStore(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
