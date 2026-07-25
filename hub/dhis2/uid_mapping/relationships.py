"""Extract metadata relationships from live DHIS2 objects (read-only)."""

from __future__ import annotations

import re
from typing import Any

_UID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{10}\b")


def _ref_uid(value: Any) -> str | None:
    if isinstance(value, str) and _UID_RE.fullmatch(value):
        return value
    if isinstance(value, dict):
        uid = value.get("id") or value.get("uid")
        if isinstance(uid, str) and _UID_RE.fullmatch(uid):
            return uid
    return None


def _ref_entry(relation: str, value: Any, *, related_type: str = "") -> dict[str, Any] | None:
    uid = _ref_uid(value)
    if not uid:
        return None
    name = ""
    if isinstance(value, dict):
        name = str(value.get("name") or value.get("displayName") or "")
    return {
        "relation": relation,
        "related_uid": uid,
        "related_name": name,
        "related_type": related_type,
    }


def extract_uids_from_text(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _UID_RE.findall(text):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out


def extract_relationships(
    object_type: str,
    obj: dict[str, Any],
    *,
    catalog_type: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Build relationship edges for common DHIS2 metadata shapes.

    Uses object fields when present; falls back to catalog reference_properties
    for generic REFERENCE fields. No PMNP-specific rules.
    """
    if not isinstance(obj, dict):
        return []

    relations: list[dict[str, Any]] = []
    kind = (object_type or "").lower()

    # Data Element → Option Set / Category Combo / Program Stage (via index extras) / Data Set
    if "dataelement" in kind:
        for rel, key, rtype in (
            ("Data Element → Option Set", "optionSet", "optionSet"),
            ("Data Element → Category Combination", "categoryCombo", "categoryCombo"),
        ):
            entry = _ref_entry(rel, obj.get(key), related_type=rtype)
            if entry:
                relations.append(entry)
        # dataSetElements / dataSets collections
        for key, rel in (
            ("dataSetElements", "Data Element → Data Set"),
            ("dataSets", "Data Element → Data Set"),
        ):
            coll = obj.get(key)
            if isinstance(coll, list):
                for item in coll:
                    target = item.get("dataSet") if isinstance(item, dict) and "dataSet" in item else item
                    entry = _ref_entry(rel, target, related_type="dataSet")
                    if entry:
                        relations.append(entry)

    # Program Indicator → Program / referenced Data Elements (from expression/filter text)
    if "programindicator" in kind:
        entry = _ref_entry("Program Indicator → Program", obj.get("program"), related_type="program")
        if entry:
            relations.append(entry)
        for field in ("expression", "filter", "numerator", "denominator"):
            for uid in extract_uids_from_text(str(obj.get(field) or "")):
                # Skip self
                if uid == obj.get("id"):
                    continue
                relations.append(
                    {
                        "relation": "Program Indicator → referenced Data Elements",
                        "related_uid": uid,
                        "related_name": "",
                        "related_type": "dataElement",
                        "source_field": field,
                    }
                )

    # Option Set → Options
    if "optionset" in kind:
        options = obj.get("options")
        if isinstance(options, list):
            for opt in options:
                entry = _ref_entry("Option Set → Options", opt, related_type="option")
                if entry:
                    relations.append(entry)

    # Dashboard → dashboard items
    if "dashboard" in kind:
        items = obj.get("dashboardItems") or obj.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Prefer nested visualization/map/report refs
                nested = None
                for key in ("visualization", "map", "eventReport", "eventChart", "report", "resources"):
                    if item.get(key):
                        nested = item.get(key)
                        break
                entry = _ref_entry(
                    "Dashboard → dashboard items",
                    nested or item,
                    related_type=str(item.get("type") or "dashboardItem"),
                )
                if entry:
                    relations.append(entry)

    # Program Stage data elements (when object is a program stage)
    if "programstage" in kind and "programindicator" not in kind:
        pdes = obj.get("programStageDataElements") or []
        if isinstance(pdes, list):
            for row in pdes:
                de = row.get("dataElement") if isinstance(row, dict) else None
                entry = _ref_entry("Program Stage → Data Element", de, related_type="dataElement")
                if entry:
                    relations.append(entry)
        entry = _ref_entry("Program Stage → Program", obj.get("program"), related_type="program")
        if entry:
            relations.append(entry)

    # Generic catalog reference properties (fill gaps)
    if catalog_type:
        known_uids = {r["related_uid"] for r in relations}
        for ref in catalog_type.get("reference_properties") or []:
            prop = ref.get("name")
            if not prop or prop not in obj:
                continue
            entry = _ref_entry(
                f"{object_type} → {prop}",
                obj.get(prop),
                related_type=str(ref.get("referencedType") or prop),
            )
            if entry and entry["related_uid"] not in known_uids:
                relations.append(entry)

    # De-dupe
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for rel in relations:
        key = (rel.get("relation") or "", rel.get("related_uid") or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(rel)
    return unique
