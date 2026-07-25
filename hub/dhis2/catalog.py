"""Build and persist a local DHIS2 metadata capability catalog (read-only)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2.redact import redact_mapping, redact_url
from hub.dhis2.type_config import load_metadata_builder_config
from hub.settings import ROOT_DIR

_SKIP_PROPS = {
    "href",
    "access",
    "favorites",
    "sharing",
    "translations",
    "attributeValues",
    "user",
    "lastUpdatedBy",
    "createdBy",
}

# Heuristic category buckets — unknown types stay in "other".
_CATEGORY_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("data", ("dataElement", "dataSet", "indicator", "category", "option", "legend")),
    ("tracker", ("program", "trackedEntity", "enrollment", "event", "relationship")),
    ("org", ("organisationUnit", "user", "userRole", "userGroup")),
    ("system", ("constant", "jobConfiguration", "sqlView", "predictor", "pushAnalysis")),
    ("visualization", ("visualization", "map", "dashboard", "report", "eventReport", "eventChart")),
]


def categorize_schema(singular: str, name: str) -> str:
    blob = f"{singular} {name}".lower()
    for category, hints in _CATEGORY_HINTS:
        if any(hint.lower() in blob for hint in hints):
            return category
    return "other"


def builder_mode_for(singular: str, plural: str, specialized: set[str]) -> str:
    """Classify how Central Hub may use this type later."""
    if singular in specialized or plural in specialized:
        return "specialized_builder"
    # Identifiable metadata-ish types get a generic schema builder path later.
    if plural and singular and singular[0].islower():
        return "generic_schema_builder"
    return "read_only_explorer"


def extract_type_entry(
    schema: dict[str, Any],
    *,
    specialized: set[str],
) -> dict[str, Any]:
    singular = str(schema.get("singular") or schema.get("name") or schema.get("collectionName") or "")
    plural = str(schema.get("plural") or schema.get("collectionName") or "")
    name = str(schema.get("name") or singular or plural)
    klass = str(schema.get("klass") or name)
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}

    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []
    enums: dict[str, list[str]] = {}

    for prop_name, prop in properties.items():
        if not isinstance(prop, dict) or prop_name in _SKIP_PROPS:
            continue
        property_type = str(prop.get("propertyType") or prop.get("type") or "")
        entry = {
            "name": prop_name,
            "propertyType": property_type,
            "required": bool(prop.get("required")),
            "simple": bool(prop.get("simple", True)),
        }
        constants = prop.get("constants")
        if isinstance(constants, list) and constants:
            enums[prop_name] = [str(item) for item in constants]

        if property_type in {"REFERENCE", "reference"} or prop.get("reference"):
            references.append(
                {
                    **entry,
                    "referencedType": prop.get("referencedType") or prop.get("relativeApiEndpoint"),
                }
            )
        elif property_type in {"COLLECTION", "collection"} or prop.get("collection"):
            collections.append(
                {
                    **entry,
                    "itemPropertyType": prop.get("itemPropertyType"),
                }
            )
        elif prop.get("required"):
            required.append(entry)
        else:
            optional.append(entry)

    relative = schema.get("relativeApiEndpoint") or (f"/{plural}" if plural else None)
    api_endpoint = f"/api{relative}" if relative and not str(relative).startswith("/api") else relative

    operations = {
        "read": True,
        "create": bool(schema.get("shareable") or schema.get("dataShareable") or True)
        and not bool(schema.get("embeddedObject")),
        "update": bool(schema.get("identifiableObject") or schema.get("nameableObject") or True),
        "delete": bool(schema.get("identifiableObject") or False),
        # Hub never enables apply — recorded for awareness only.
        "hub_apply_enabled": False,
    }

    return {
        "id": singular or plural or name,
        "schema_name": name,
        "klass": klass,
        "singular": singular,
        "plural": plural,
        "collection": plural,
        "api_endpoint": api_endpoint or None,
        "category": categorize_schema(singular, name),
        "required_properties": required,
        "optional_properties": optional[:80],  # keep catalog lean
        "optional_property_count": len(optional),
        "reference_properties": references,
        "collection_properties": collections,
        "enums": enums,
        "operations": operations,
        "builder_mode": builder_mode_for(singular, plural, specialized),
        "identifiable": bool(schema.get("identifiableObject")),
        "nameable": bool(schema.get("nameableObject")),
        "metadata": bool(schema.get("metadata")),
        "count": None,
        "sample": [],
    }


class CatalogStore:
    """Load/save discovered catalog JSON under data/dhis2/catalog/."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (ROOT_DIR / "data" / "dhis2" / "catalog")
        self.latest_path = self.root / "latest.json"

    def save(self, catalog: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        stamped = self.root / f"catalog_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        text = json.dumps(catalog, indent=2, ensure_ascii=True)
        stamped.write_text(text, encoding="utf-8")
        self.latest_path.write_text(text, encoding="utf-8")
        return stamped

    def load_latest(self) -> dict[str, Any] | None:
        if not self.latest_path.is_file():
            return None
        try:
            data = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def get_type(self, type_id: str) -> dict[str, Any] | None:
        catalog = self.load_latest()
        if not catalog:
            return None
        for item in catalog.get("types") or []:
            if item.get("id") == type_id or item.get("singular") == type_id or item.get("plural") == type_id:
                return item
        return None


def filter_types(
    types: list[dict[str, Any]],
    *,
    query: str = "",
    category: str = "",
    builder_mode: str = "",
) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    cat = (category or "").strip().lower()
    mode = (builder_mode or "").strip().lower()
    results = types
    if cat and cat != "all":
        results = [item for item in results if str(item.get("category", "")).lower() == cat]
    if mode and mode != "all":
        results = [item for item in results if str(item.get("builder_mode", "")).lower() == mode]
    if q:
        results = [
            item
            for item in results
            if q in str(item.get("schema_name", "")).lower()
            or q in str(item.get("singular", "")).lower()
            or q in str(item.get("plural", "")).lower()
            or q in str(item.get("api_endpoint", "")).lower()
        ]
    return results


def run_discovery(
    client: Dhis2Client,
    *,
    store: CatalogStore | None = None,
    enrich_samples: bool = False,
    sample_limit: int = 12,
) -> dict[str, Any]:
    """
    Discover instance capabilities via GET-only endpoints and persist a local catalog.

    Never exports all metadata. Optional tiny samples/counts for a few identifiable types.
    """
    store = store or CatalogStore()
    secrets = [s for s in [client.settings.password, client.settings.username] if s]
    started = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    status = client.check_status()
    if not status.get("ok"):
        raise Dhis2Error(status.get("detail") or "DHIS2 status check failed.")

    try:
        me = client.get_me()
    except Dhis2Error as exc:
        me = {}
        errors.append(f"/api/me: {exc.message}")

    try:
        authorities = client.get_authorities()
    except Dhis2Error as exc:
        authorities = []
        errors.append(f"/api/me/authorization: {exc.message}")

    try:
        schemas = client.get_schemas_document()
    except Dhis2Error as exc:
        schemas = []
        errors.append(f"/api/schemas.json: {exc.message}")

    try:
        openapi = client.get_openapi_summary()
    except Dhis2Error as exc:
        openapi = {
            "available": False,
            "detail": exc.message,
            "path_count": 0,
            "tags": [],
            "sample_paths": [],
        }
        errors.append(f"/api/openapi.json: {exc.message}")

    try:
        api_entry = client.get_api_entry()
    except Dhis2Error as exc:
        api_entry = {"available": False, "resource_count": 0, "resources": [], "detail": exc.message}
        errors.append(f"/api: {exc.message}")

    specialized: set[str] = set()
    try:
        builder_cfg = load_metadata_builder_config()
        for item in builder_cfg.metadata_types:
            specialized.add(item.id)
            specialized.add(item.plural)
            specialized.add(item.schema_klass)
    except Exception:  # noqa: BLE001
        specialized = {"dataElement", "dataElements", "programIndicator", "programIndicators", "optionSet", "optionSets"}

    types = [extract_type_entry(schema, specialized=specialized) for schema in schemas]
    types.sort(key=lambda item: (item.get("category") or "", item.get("singular") or ""))

    if enrich_samples and types:
        sampled = 0
        for item in types:
            if sampled >= sample_limit:
                break
            if not item.get("identifiable") or not item.get("plural"):
                continue
            if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", item["plural"]):
                continue
            try:
                stats = client.get_resource_count_and_sample(item["plural"], sample_size=3)
                item["count"] = stats.get("count")
                item["sample"] = stats.get("sample") or []
                sampled += 1
            except Dhis2Error as exc:
                item["sample_error"] = exc.message

    user_public = {
        "id": me.get("id"),
        "username": me.get("username"),
        "displayName": me.get("displayName") or me.get("name"),
        "email": me.get("email"),
        "organisationUnits": [
            {"id": ou.get("id"), "name": ou.get("name")}
            for ou in (me.get("organisationUnits") or [])
            if isinstance(ou, dict)
        ][:20],
        "userRoles": [
            {"id": role.get("id"), "name": role.get("name")}
            for role in (me.get("userRoles") or [])
            if isinstance(role, dict)
        ][:20],
    }

    categories: dict[str, int] = {}
    modes: dict[str, int] = {}
    for item in types:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
        modes[item["builder_mode"]] = modes.get(item["builder_mode"], 0) + 1

    catalog = {
        "ok": True,
        "discovered_at": started,
        "dhis2_version": (status.get("system") or {}).get("version"),
        "base_url": redact_url(client.settings.base_url),
        "system": status.get("system"),
        "user": user_public,
        "authorities": authorities,
        "authority_count": len(authorities),
        "api_entry": api_entry,
        "openapi": openapi,
        "types": types,
        "type_count": len(types),
        "categories": categories,
        "builder_modes": modes,
        "errors": errors,
        "notes": [
            "Read-only discovery. No metadata was created, updated, deleted, or imported.",
            "Catalog stores schemas/endpoint definitions; not a full metadata export.",
            "hub_apply_enabled is always false while ALLOW_DHIS2_WRITES=false.",
        ],
    }
    catalog = redact_mapping(catalog, secrets)
    path = store.save(catalog)
    catalog["catalog_path"] = str(path)
    return catalog
