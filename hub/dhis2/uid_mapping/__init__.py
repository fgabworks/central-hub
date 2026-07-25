"""Normalized UID mapping index (repository sources ↔ DHIS2, read-only)."""

from __future__ import annotations

from hub.dhis2.uid_mapping.compare import Classification, classify_against_dhis2, classify_index_records
from hub.dhis2.uid_mapping.models import INDEX_FIELDS, NormalizedUidRecord, checksum_for
from hub.dhis2.uid_mapping.relationships import extract_relationships
from hub.dhis2.uid_mapping.scan import load_sources_config, scan_all_sources, scan_source
from hub.dhis2.uid_mapping.store import MappingIndexStore, merge_preview, apply_merge

__all__ = [
    "INDEX_FIELDS",
    "NormalizedUidRecord",
    "checksum_for",
    "Classification",
    "classify_against_dhis2",
    "classify_index_records",
    "extract_relationships",
    "load_sources_config",
    "scan_all_sources",
    "scan_source",
    "MappingIndexStore",
    "merge_preview",
    "apply_merge",
]
