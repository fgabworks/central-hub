"""Derive human-readable answer types from DHIS2 valueType / optionSetValue."""

from __future__ import annotations

from typing import Any


def derive_answer_type(
    value_type: str | None,
    *,
    option_set_value: bool | None = None,
    option_set_uid: str | None = None,
) -> str:
    """
    Map DHIS2 valueType (+ option set flag) to an audit-friendly answer label.

    BOOLEAN → Yes / No
    TRUE_ONLY → Yes only
    optionSetValue / option set present → Option Set
    NUMBER / INTEGER* → Numeric
    DATE / DATETIME → Date or Date-Time
    TEXT / LONG_TEXT → Free Text
    else preserve unknown value types
    """
    if option_set_value or (option_set_uid and str(option_set_uid).strip()):
        return "Option Set"

    vt = (value_type or "").strip().upper()
    if not vt:
        return "Unknown"
    if vt == "BOOLEAN":
        return "Yes / No"
    if vt == "TRUE_ONLY":
        return "Yes only"
    if vt in {
        "NUMBER",
        "INTEGER",
        "INTEGER_POSITIVE",
        "INTEGER_NEGATIVE",
        "INTEGER_ZERO_OR_POSITIVE",
        "PERCENTAGE",
        "UNIT_INTERVAL",
    }:
        return "Numeric"
    if vt == "DATE":
        return "Date"
    if vt == "DATETIME":
        return "Date-Time"
    if vt in {"TEXT", "LONG_TEXT"}:
        return "Free Text"
    return vt.replace("_", " ").title()


def as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None
