"""Load configuration-driven DHIS2 metadata builder type registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hub.settings import ROOT_DIR


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    input: str
    required: bool = False
    help: str | None = None
    options: list[str] = field(default_factory=list)
    resource: str | None = None


@dataclass(frozen=True)
class MetadataTypeSpec:
    id: str
    label: str
    plural: str
    schema_klass: str
    builder: str
    dependency_resources: list[str]
    fields: list[FieldSpec]
    builder_mode: str = "specialized_builder"
    catalog_entry: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class OperationSpec:
    id: str
    label: str
    enabled: bool
    apply_enabled: bool


@dataclass(frozen=True)
class InstanceSpec:
    id: str
    label: str
    source: str


@dataclass(frozen=True)
class MetadataBuilderConfig:
    instances: list[InstanceSpec]
    operations: list[OperationSpec]
    metadata_types: list[MetadataTypeSpec]

    def get_type(self, type_id: str) -> MetadataTypeSpec | None:
        for item in self.metadata_types:
            if item.id == type_id:
                return item
        return None

    def get_operation(self, op_id: str) -> OperationSpec | None:
        for item in self.operations:
            if item.id == op_id:
                return item
        return None


def load_metadata_builder_config(
    path: Path | None = None,
) -> MetadataBuilderConfig:
    config_path = path or (ROOT_DIR / "config" / "dhis2_metadata_types.yaml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    instances = [
        InstanceSpec(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            source=str(item.get("source") or "env"),
        )
        for item in (raw.get("instances") or [])
    ]
    operations = [
        OperationSpec(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            enabled=bool(item.get("enabled", True)),
            apply_enabled=bool(item.get("apply_enabled", False)),
        )
        for item in (raw.get("operations") or [])
        if str(item.get("id")) != "delete"
    ]
    types: list[MetadataTypeSpec] = []
    for item in raw.get("metadata_types") or []:
        fields = [
            FieldSpec(
                id=str(field["id"]),
                label=str(field.get("label") or field["id"]),
                input=str(field.get("input") or "text"),
                required=bool(field.get("required", False)),
                help=str(field["help"]) if field.get("help") else None,
                options=[str(opt) for opt in (field.get("options") or [])],
                resource=str(field["resource"]) if field.get("resource") else None,
            )
            for field in (item.get("fields") or [])
        ]
        types.append(
            MetadataTypeSpec(
                id=str(item["id"]),
                label=str(item.get("label") or item["id"]),
                plural=str(item.get("plural") or item["id"]),
                schema_klass=str(item.get("schema_klass") or item["id"]),
                builder=str(item.get("builder") or item["id"]),
                dependency_resources=[str(r) for r in (item.get("dependency_resources") or [])],
                fields=fields,
                builder_mode="specialized_builder",
            )
        )
    return MetadataBuilderConfig(
        instances=instances or [InstanceSpec("default", "Configured DHIS2 (.env)", "env")],
        operations=operations,
        metadata_types=types,
    )


def type_to_dict(spec: MetadataTypeSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "label": spec.label,
        "plural": spec.plural,
        "schema_klass": spec.schema_klass,
        "builder": spec.builder,
        "builder_mode": spec.builder_mode,
        "dependency_resources": spec.dependency_resources,
        "fields": [
            {
                "id": f.id,
                "label": f.label,
                "input": f.input,
                "required": f.required,
                "help": f.help,
                "options": f.options,
                "resource": f.resource,
            }
            for f in spec.fields
        ],
    }
