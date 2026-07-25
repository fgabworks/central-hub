"""Constants and lightweight models for DHIS2 metadata enrichment (read-only)."""

from __future__ import annotations

from typing import Any

# Normalized relationship types (one-to-many friendly).
REL_DE_IN_PROGRAM_STAGE = "DATA_ELEMENT_IN_PROGRAM_STAGE"
REL_DE_IN_DATA_SET = "DATA_ELEMENT_IN_DATA_SET"
REL_DE_IN_GROUP = "DATA_ELEMENT_IN_GROUP"
REL_DE_USES_OPTION_SET = "DATA_ELEMENT_USES_OPTION_SET"
REL_DE_USES_CATEGORY_COMBO = "DATA_ELEMENT_USES_CATEGORY_COMBO"
REL_PI_BELONGS_TO_PROGRAM = "PROGRAM_INDICATOR_BELONGS_TO_PROGRAM"
REL_PI_REFERENCES_DE = "PROGRAM_INDICATOR_REFERENCES_DATA_ELEMENT"
REL_PI_REFERENCES_ATTR = "PROGRAM_INDICATOR_REFERENCES_ATTRIBUTE"
REL_PI_REFERENCES_CONSTANT = "PROGRAM_INDICATOR_REFERENCES_CONSTANT"
REL_PI_REFERENCES_STAGE = "PROGRAM_INDICATOR_REFERENCES_PROGRAM_STAGE"
REL_OPTION_SET_USED_BY_DE = "OPTION_SET_USED_BY_DATA_ELEMENT"
REL_OPTION_SET_USED_BY_TEA = "OPTION_SET_USED_BY_ATTRIBUTE"
REL_TEA_IN_PROGRAM = "ATTRIBUTE_IN_PROGRAM"

RELATION_TYPES: tuple[str, ...] = (
    REL_DE_IN_PROGRAM_STAGE,
    REL_DE_IN_DATA_SET,
    REL_DE_IN_GROUP,
    REL_DE_USES_OPTION_SET,
    REL_DE_USES_CATEGORY_COMBO,
    REL_PI_BELONGS_TO_PROGRAM,
    REL_PI_REFERENCES_DE,
    REL_PI_REFERENCES_ATTR,
    REL_PI_REFERENCES_CONSTANT,
    REL_PI_REFERENCES_STAGE,
    REL_OPTION_SET_USED_BY_DE,
    REL_OPTION_SET_USED_BY_TEA,
    REL_TEA_IN_PROGRAM,
)

# Audit classifications
AUDIT_MATCHED = "Matched"
AUDIT_MISSING_DHIS2 = "Missing in DHIS2"
AUDIT_MISSING_REPO = "Missing in Repository"
AUDIT_NAME_MISMATCH = "Name Mismatch"
AUDIT_OBJECT_TYPE_MISMATCH = "Object Type Mismatch"
AUDIT_VALUE_TYPE_MISMATCH = "Value Type Mismatch"
AUDIT_DOMAIN_TYPE_MISMATCH = "Domain Type Mismatch"
AUDIT_OPTION_SET_MISMATCH = "Option Set Mismatch"
AUDIT_PROGRAM_STAGE_MISMATCH = "Program Stage Mismatch"
AUDIT_BROKEN_REFERENCE = "Broken Reference"
AUDIT_DUPLICATE_MAPPING = "Duplicate Mapping"
AUDIT_CHANGED_SINCE_SCAN = "Changed Since Last Scan"
AUDIT_UNKNOWN = "Unknown"

AUDIT_STATUSES: tuple[str, ...] = (
    AUDIT_MATCHED,
    AUDIT_MISSING_DHIS2,
    AUDIT_MISSING_REPO,
    AUDIT_NAME_MISMATCH,
    AUDIT_OBJECT_TYPE_MISMATCH,
    AUDIT_VALUE_TYPE_MISMATCH,
    AUDIT_DOMAIN_TYPE_MISMATCH,
    AUDIT_OPTION_SET_MISMATCH,
    AUDIT_PROGRAM_STAGE_MISMATCH,
    AUDIT_BROKEN_REFERENCE,
    AUDIT_DUPLICATE_MAPPING,
    AUDIT_CHANGED_SINCE_SCAN,
    AUDIT_UNKNOWN,
)

CONFIRM_APPLY = "APPLY DHIS2 ENRICHMENT"

OBJECT_TYPES = (
    "dataElement",
    "trackedEntityAttribute",
    "programIndicator",
    "optionSet",
    "program",
    "programStage",
    "dataSet",
    "categoryCombo",
    "constant",
)


def relationship(
    *,
    rel_type: str,
    from_uid: str,
    from_type: str,
    to_uid: str,
    to_type: str,
    to_name: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rel_type": rel_type,
        "from_uid": from_uid,
        "from_type": from_type,
        "to_uid": to_uid,
        "to_type": to_type,
        "to_name": to_name or "",
        "detail": detail or {},
    }
