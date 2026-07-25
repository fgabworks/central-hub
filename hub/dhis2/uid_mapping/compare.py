"""Classify repository UID index records against live DHIS2 (GET-only)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error

_UID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")

# Map common singular/kind names → API plural collections.
_TYPE_TO_PLURAL: dict[str, str] = {
    "dataelement": "dataElements",
    "dataelements": "dataElements",
    "optionset": "optionSets",
    "optionsets": "optionSets",
    "option": "options",
    "options": "options",
    "program": "programs",
    "programs": "programs",
    "programindicator": "programIndicators",
    "programindicators": "programIndicators",
    "programrule": "programRules",
    "programstage": "programStages",
    "programstages": "programStages",
    "indicator": "indicators",
    "indicators": "indicators",
    "dataset": "dataSets",
    "datasets": "dataSets",
    "categorycombo": "categoryCombos",
    "categorycombos": "categoryCombos",
    "category": "categories",
    "categories": "categories",
    "trackedentityattribute": "trackedEntityAttributes",
    "trackedentityattributes": "trackedEntityAttributes",
    "dashboard": "dashboards",
    "dashboards": "dashboards",
    "organisationunit": "organisationUnits",
    "organisationunits": "organisationUnits",
}


class Classification(str, Enum):
    MATCHED = "matched"
    MISSING_IN_DHIS2 = "missing_in_dhis2"
    MISSING_IN_REPOSITORY = "missing_in_repository_index"
    CHANGED = "changed"
    DUPLICATE = "duplicate"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


def resolve_plural(object_type: str, catalog_types: list[dict[str, Any]] | None = None) -> str | None:
    key = (object_type or "").strip()
    if not key:
        return None
    mapped = _TYPE_TO_PLURAL.get(key.lower())
    if mapped:
        return mapped
    if catalog_types:
        for item in catalog_types:
            if key in {item.get("id"), item.get("singular"), item.get("plural"), item.get("schema_name")}:
                plural = item.get("plural")
                if plural:
                    return str(plural)
    # Heuristic: already plural-ish
    if key[0].islower() and key.endswith("s"):
        return key
    return None


def classify_index_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Offline classifications: duplicates and internal conflicts."""
    by_uid: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_uid.setdefault(str(rec.get("uid") or ""), []).append(rec)

    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for uid, group in by_uid.items():
        if not uid or len(group) < 2:
            continue
        duplicates.append({"uid": uid, "count": len(group), "records": group})
        # Conflicting if critical fields disagree across sources
        names = {str(r.get("name") or "") for r in group}
        codes = {str(r.get("code") or "") for r in group}
        types = {str(r.get("object_type") or "") for r in group}
        if len(names - {""}) > 1 or len(codes - {""}) > 1 or len(types - {""}) > 1:
            conflicts.append({"uid": uid, "records": group, "status": Classification.CONFLICTING.value})
    return {"duplicate": duplicates, "conflicting": conflicts}


def _compare_fields(index_rec: dict[str, Any], dhis2: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    pairs = [
        ("name", "name"),
        ("code", "code"),
        ("value_type", "valueType"),
        ("domain_type", "domainType"),
    ]
    for idx_key, dhis_key in pairs:
        left = str(index_rec.get(idx_key) or "").strip()
        right = str(dhis2.get(dhis_key) or dhis2.get(idx_key) or "").strip()
        if left and right and left != right:
            diffs.append(idx_key)
    return diffs


def classify_against_dhis2(
    record: dict[str, Any],
    client: Dhis2Client,
    *,
    catalog_types: list[dict[str, Any]] | None = None,
    fetch_object: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Classify one index record against live DHIS2 via GET.

    Returns status in {matched, missing_in_dhis2, changed, conflicting, unknown}.
    """
    uid = str(record.get("uid") or "").strip()
    if not _UID_RE.match(uid):
        return {
            "uid": uid,
            "status": Classification.UNKNOWN.value,
            "detail": "Invalid or missing UID",
            "diffs": [],
            "dhis2": None,
        }

    plural = resolve_plural(str(record.get("object_type") or ""), catalog_types)
    fetcher = fetch_object or (lambda p, u: client.get_metadata_object(p, u))

    try:
        if plural:
            dhis2_obj = fetcher(plural, uid)
        else:
            # Fall back to identifiableObjects lookup
            result = client.search(uid)
            rows = result.get("results") or []
            if not rows:
                return {
                    "uid": uid,
                    "status": Classification.MISSING_IN_DHIS2.value,
                    "detail": "Not found via identifiableObjects",
                    "diffs": [],
                    "dhis2": None,
                }
            # Minimal compare using search hit
            hit = rows[0]
            diffs = _compare_fields(record, hit)
            status = Classification.CHANGED.value if diffs else Classification.MATCHED.value
            return {
                "uid": uid,
                "status": status,
                "detail": "Compared via identifiableObjects search",
                "diffs": diffs,
                "dhis2": hit,
            }
    except Dhis2Error as exc:
        if exc.status_code == 404:
            return {
                "uid": uid,
                "status": Classification.MISSING_IN_DHIS2.value,
                "detail": exc.message,
                "diffs": [],
                "dhis2": None,
            }
        return {
            "uid": uid,
            "status": Classification.UNKNOWN.value,
            "detail": exc.message,
            "diffs": [],
            "dhis2": None,
        }

    item = dhis2_obj.get("item") if isinstance(dhis2_obj, dict) else None
    raw = (dhis2_obj.get("raw") if isinstance(dhis2_obj, dict) else None) or item or dhis2_obj
    if not isinstance(raw, dict) or not raw.get("id"):
        return {
            "uid": uid,
            "status": Classification.MISSING_IN_DHIS2.value,
            "detail": "Empty DHIS2 response",
            "diffs": [],
            "dhis2": None,
        }

    diffs = _compare_fields(record, raw)
    # Conflicting if object_type clearly disagrees with returned href/type
    returned_type = str(dhis2_obj.get("resource_type") or raw.get("type") or "")
    type_conflict = False
    if returned_type and record.get("object_type"):
        expected = resolve_plural(str(record.get("object_type")), catalog_types)
        if expected and returned_type not in {expected, record.get("object_type")}:
            # Allow singular/plural mismatch via resolve
            if resolve_plural(returned_type, catalog_types) != expected:
                type_conflict = True

    if type_conflict:
        status = Classification.CONFLICTING.value
        detail = "Index object_type conflicts with DHIS2 resource type"
    elif diffs:
        status = Classification.CHANGED.value
        detail = "Fields differ from live DHIS2 metadata"
    else:
        status = Classification.MATCHED.value
        detail = "Index fields match live DHIS2 metadata"

    return {
        "uid": uid,
        "status": status,
        "detail": detail,
        "diffs": diffs,
        "dhis2": raw,
        "resource_type": returned_type or plural,
    }


def find_missing_in_repository(
    dhis2_uids: list[dict[str, Any]],
    index_uids: set[str],
) -> list[dict[str, Any]]:
    """Mark DHIS2 objects that are absent from the local repository index."""
    missing = []
    for item in dhis2_uids:
        uid = str(item.get("id") or item.get("uid") or "")
        if uid and uid not in index_uids:
            missing.append(
                {
                    "uid": uid,
                    "status": Classification.MISSING_IN_REPOSITORY.value,
                    "dhis2": item,
                }
            )
    return missing
