"""Builder registry — maps config builder keys to implementations."""

from __future__ import annotations

from hub.dhis2.builders.base import BaseMetadataBuilder
from hub.dhis2.builders.data_element import DataElementBuilder
from hub.dhis2.builders.generic import GenericSchemaBuilder
from hub.dhis2.builders.option_set import OptionSetBuilder
from hub.dhis2.builders.program_indicator import ProgramIndicatorBuilder
from hub.dhis2.client import Dhis2Client
from hub.dhis2.type_config import MetadataTypeSpec
from hub.dhis2.uid_index import UidIndex

_BUILDERS = {
    "data_element": DataElementBuilder,
    "program_indicator": ProgramIndicatorBuilder,
    "option_set": OptionSetBuilder,
    "generic_schema": GenericSchemaBuilder,
}


def get_builder(
    type_spec: MetadataTypeSpec,
    client: Dhis2Client,
    uid_index: UidIndex | None = None,
) -> BaseMetadataBuilder:
    cls = _BUILDERS.get(type_spec.builder)
    if cls is None:
        raise KeyError(
            f"No builder registered for '{type_spec.builder}'. "
            "Add it under hub/dhis2/builders/ and config/dhis2_metadata_types.yaml."
        )
    return cls(client, type_spec, uid_index=uid_index)


def registered_builder_keys() -> list[str]:
    return sorted(_BUILDERS.keys())
