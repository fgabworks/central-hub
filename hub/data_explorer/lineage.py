"""Report/indicator lineage from hub registries only — never invent mappings."""

from __future__ import annotations

from typing import Any

from hub.data_explorer.discovery import ObjectMeta
from hub.hcsc_indicators.registry import load_registry
from hub.live_data_export.registry import LiveExportRegistry, get_registry as get_export_registry


def build_lineage_index(
    objects: list[ObjectMeta],
    *,
    export_registry: LiveExportRegistry | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Map object full_name/name → lineage payload.

    Sources:
    - HCSC registry: only when source_table_view_reference clearly names a schema.object
      or bare object that matches a discovered name; otherwise listed under unresolved_hub.
    - Live Data Export registry: verified sources with object_name.
    - DHIS2 Standard Reports: no DB table mappings in hub config (noted as unresolved).
    """
    by_name = {o.name.lower(): o for o in objects}
    by_full = {o.full_name.lower(): o for o in objects}
    index: dict[str, dict[str, Any]] = {
        o.full_name.lower(): {
            "indicators": [],
            "reports": [],
            "exports": [],
            "unresolved_refs": [],
        }
        for o in objects
    }

    # Live Data Export verified objects
    try:
        ereg = export_registry or get_export_registry()
        for src in ereg.list_sources():
            if not src.available or not src.object_name:
                continue
            full = (
                f"{src.schema}.{src.object_name}".lower()
                if src.schema and src.schema != "main"
                else src.object_name.lower()
            )
            target = None
            if full in by_full:
                target = by_full[full].full_name.lower()
            elif src.object_name.lower() in by_name:
                target = by_name[src.object_name.lower()].full_name.lower()
            entry = {
                "name": src.display_name,
                "source_key": src.source_key,
                "source_type": "live_data_export",
                "schema": src.schema,
                "object": src.object_name,
                "fields_used": list(src.allowed_columns),
                "source_owner": src.source_owner,
                "confidence": "verified",
                "export_formats": list(src.supported_formats),
            }
            if target and target in index:
                index[target]["exports"].append(entry)
    except Exception:  # noqa: BLE001
        pass

    # HCSC indicators — attach only when reference matches a discovered object
    try:
        reg = load_registry()
        for ind in reg.get("indicators") or []:
            if not isinstance(ind, dict):
                continue
            ref = str(ind.get("source_table_view_reference") or "").strip()
            matched = _match_object_from_ref(ref, by_name, by_full)
            payload = {
                "name": ind.get("display_name") or ind.get("name") or ind.get("key"),
                "key": ind.get("key"),
                "uid": (ind.get("dhis2_uids") or {}).get("value")
                or (ind.get("dhis2_uids") or {}).get("result"),
                "numerator_uid": (ind.get("dhis2_uids") or {}).get("numerator"),
                "denominator_uid": (ind.get("dhis2_uids") or {}).get("denominator"),
                "source_type": ind.get("adapter") or ind.get("source_type") or "",
                "source_schema_table_view": ref,
                "fields_used": [],
                "joins": [],
                "saved_sql_or_repo_ref": ind.get("approved_sql_reference")
                or ind.get("approved_sql_query_id")
                or "",
                "numerator_source": (ind.get("dhis2_uids") or {}).get("numerator"),
                "denominator_source": (ind.get("dhis2_uids") or {}).get("denominator"),
                "source_owner": ind.get("source_owner") or "",
                "confidence": "verified" if matched else "unresolved",
                "section": ind.get("section") or ind.get("display_group") or "",
                "unresolved_mapping": matched is None,
            }
            if matched:
                index[matched]["indicators"].append(payload)
            else:
                # Keep a global unresolved list keyed under special entry
                index.setdefault(
                    "__unresolved__",
                    {"indicators": [], "reports": [], "exports": [], "unresolved_refs": []},
                )
                index["__unresolved__"]["indicators"].append(payload)
    except Exception:  # noqa: BLE001
        pass

    # DHIS2 reports have no DB object mappings in hub
    index.setdefault(
        "__unresolved__",
        {"indicators": [], "reports": [], "exports": [], "unresolved_refs": []},
    )
    index["__unresolved__"]["reports"].append(
        {
            "name": "DHIS2 Standard Reports (catalog)",
            "note": "Hub report catalog has no physical database table mappings.",
            "confidence": "unresolved",
            "unresolved_mapping": True,
        }
    )

    return index


def lineage_for_object(
    obj: ObjectMeta, index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    data = index.get(obj.full_name.lower()) or index.get(obj.name.lower()) or {
        "indicators": [],
        "reports": [],
        "exports": [],
        "unresolved_refs": [],
    }
    return {
        "used_by_indicators": data.get("indicators") or [],
        "used_by_reports": data.get("reports") or [],
        "used_by_exports": data.get("exports") or [],
        "unresolved_refs": data.get("unresolved_refs") or [],
    }


def _match_object_from_ref(
    ref: str,
    by_name: dict[str, ObjectMeta],
    by_full: dict[str, ObjectMeta],
) -> str | None:
    """Only match when ref looks like schema.object or bare identifier present in catalog."""
    text = (ref or "").strip()
    if not text:
        return None
    # Reject DHIS2 analytics prose
    if "dhis2" in text.lower() or "analytics dx:" in text.lower() or "programindicator" in text.lower().replace(" ", ""):
        return None
    # schema.object
    if "." in text and " " not in text.split(".")[0]:
        parts = text.split(".")
        if len(parts) == 2 and parts[0] and parts[1]:
            full = text.lower()
            if full in by_full:
                return by_full[full].full_name.lower()
            if parts[1].lower() in by_name:
                return by_name[parts[1].lower()].full_name.lower()
    # bare object token
    token = text.split()[0].strip(",;")
    if token.lower() in by_name and "." not in token:
        # Only if entire ref is essentially the object name
        if token.lower() == text.lower() or text.lower().startswith(token.lower()):
            if " " not in text:
                return by_name[token.lower()].full_name.lower()
    return None
