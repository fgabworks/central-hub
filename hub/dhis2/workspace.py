"""Build the metadata workspace from the discovered DHIS2 catalog."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from hub.dhis2.type_config import FieldSpec, MetadataBuilderConfig, MetadataTypeSpec

_SKIP_GENERIC_FIELDS = {
    "href",
    "access",
    "favorites",
    "sharing",
    "translations",
    "attributeValues",
    "created",
    "lastUpdated",
    "lastUpdatedBy",
    "user",
    "createdBy",
}

# Max fields synthesized for a generic form (keeps UI usable).
_MAX_GENERIC_FIELDS = 60


def workspace_types(catalog: dict[str, Any] | None, config: MetadataBuilderConfig) -> list[MetadataTypeSpec]:
    """
    Return builder types from discovery.

    - specialized_builder: config override when catalog type matches
    - generic_schema_builder: schema-driven GenericSchemaBuilder
    - read_only_explorer: excluded from the builder workspace
    """
    if not catalog:
        return []
    catalog_types = [item for item in (catalog.get("types") or []) if isinstance(item, dict)]
    specialized: dict[str, MetadataTypeSpec] = {}
    for spec in config.metadata_types:
        for key in (spec.id, spec.plural, spec.schema_klass):
            specialized[_norm(key)] = spec

    output: list[MetadataTypeSpec] = []
    for entry in catalog_types:
        singular = str(entry.get("singular") or entry.get("id") or "").strip()
        plural = str(entry.get("plural") or entry.get("collection") or "").strip()
        if not singular or not plural:
            continue

        mode = str(entry.get("builder_mode") or "").strip() or "generic_schema_builder"
        match = specialized.get(_norm(singular)) or specialized.get(_norm(plural))

        if match:
            output.append(
                replace(
                    match,
                    label=str(entry.get("schema_name") or match.label),
                    plural=plural,
                    schema_klass=str(entry.get("klass") or match.schema_klass),
                    builder_mode="specialized_builder",
                    catalog_entry=entry,
                )
            )
            continue

        if mode == "read_only_explorer":
            continue

        # identifiable / nameable types get the generic schema builder
        if mode == "generic_schema_builder" or entry.get("identifiable") or entry.get("nameable"):
            output.append(_generic_spec(entry, catalog_types))

    return sorted(output, key=lambda item: item.label.lower())


def workspace_stats(types: list[MetadataTypeSpec]) -> dict[str, int]:
    counts = {"specialized_builder": 0, "generic_schema_builder": 0, "total": len(types)}
    for item in types:
        mode = item.builder_mode if item.builder_mode in counts else "generic_schema_builder"
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _generic_spec(entry: dict[str, Any], catalog_types: list[dict[str, Any]]) -> MetadataTypeSpec:
    singular = str(entry.get("singular") or entry.get("id"))
    fields: list[FieldSpec] = [FieldSpec("id", "UID", "text", help="Required for update preview")]
    seen = {"id"}
    enums = entry.get("enums") or {}
    ref_names = {
        str(item.get("name"))
        for item in (entry.get("reference_properties") or [])
        if isinstance(item, dict) and item.get("name")
    }
    coll_names = {
        str(item.get("name"))
        for item in (entry.get("collection_properties") or [])
        if isinstance(item, dict) and item.get("name")
    }

    properties: list[dict[str, Any]] = []
    for group in (
        "required_properties",
        "reference_properties",
        "collection_properties",
        "optional_properties",
    ):
        properties.extend(item for item in (entry.get(group) or []) if isinstance(item, dict))

    dependency_resources: list[str] = []
    for prop in properties:
        if len(fields) >= _MAX_GENERIC_FIELDS:
            break
        name = str(prop.get("name") or "").strip()
        if not name or name in seen or name in _SKIP_GENERIC_FIELDS:
            continue
        seen.add(name)
        required = bool(prop.get("required"))
        ptype = str(prop.get("propertyType") or "").upper()
        options = [str(value) for value in (enums.get(name) or [])]
        resource = None
        input_type = "text"
        if name in ref_names or ptype == "REFERENCE":
            input_type = "dependency"
            resource = _reference_resource(prop, catalog_types)
            if resource and resource not in dependency_resources:
                dependency_resources.append(resource)
        elif name in coll_names or ptype == "COLLECTION":
            input_type = "json"
        elif options:
            input_type = "select"
        elif ptype in {"BOOLEAN", "BOOL"}:
            input_type = "checkbox"
        elif ptype in {"INTEGER", "NUMBER", "DOUBLE", "FLOAT", "DECIMAL"}:
            input_type = "number"
        elif name.lower() in {
            "description",
            "expression",
            "filter",
            "numerator",
            "denominator",
        }:
            input_type = "textarea"
        fields.append(
            FieldSpec(
                id=name,
                label=_humanize(name),
                input=input_type,
                required=required,
                options=options,
                resource=resource,
                help="JSON array/object" if input_type == "json" else None,
            )
        )

    return MetadataTypeSpec(
        id=singular,
        label=str(entry.get("schema_name") or _humanize(singular)),
        plural=str(entry.get("plural") or entry.get("collection")),
        schema_klass=str(entry.get("klass") or singular),
        builder="generic_schema",
        dependency_resources=dependency_resources,
        fields=fields,
        builder_mode="generic_schema_builder",
        catalog_entry=entry,
    )


def catalog_schema_summary(spec: MetadataTypeSpec) -> dict[str, Any]:
    entry = spec.catalog_entry or {}
    if not entry:
        return {}
    required = [
        {"name": field.id, "propertyType": _field_property_type(field), "required": True}
        for field in spec.fields
        if field.required
    ]
    return {
        "ok": True,
        "source": "discovered_catalog",
        "klass": entry.get("klass") or spec.schema_klass,
        "singular": entry.get("singular") or spec.id,
        "plural": entry.get("plural") or spec.plural,
        "required": required,
        "optional_count": int(entry.get("optional_property_count") or 0),
        "raw_property_count": len(spec.fields),
        "builder_mode": spec.builder_mode,
    }


def _reference_resource(prop: dict[str, Any], catalog_types: list[dict[str, Any]]) -> str:
    target = str(
        prop.get("referencedType") or prop.get("relativeApiEndpoint") or prop.get("name") or ""
    )
    target = target.strip("/").split("/")[-1]
    target_norm = _norm(target.split(".")[-1])
    for item in catalog_types:
        keys = (item.get("id"), item.get("singular"), item.get("plural"), item.get("klass"))
        if any(target_norm == _norm(str(key).split(".")[-1]) for key in keys if key):
            return str(item.get("plural") or item.get("collection") or item.get("singular"))
    if target.endswith("s"):
        return target
    if not target:
        return str(prop.get("name") or "")
    return f"{target[:1].lower()}{target[1:]}s"


def _field_property_type(field: FieldSpec) -> str:
    return {
        "checkbox": "BOOLEAN",
        "number": "NUMBER",
        "dependency": "REFERENCE",
        "json": "COLLECTION",
    }.get(field.input, "TEXT")


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").strip().capitalize()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
