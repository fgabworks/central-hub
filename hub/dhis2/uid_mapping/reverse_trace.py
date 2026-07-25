"""Live reverse metadata traces via DHIS2 GET filters (no writes, no DB).

Data Elements do not embed program/stage; those links are found by filtering
programStages / programs. Physical Postgres/analytics table names are NOT
available from the metadata API — see logical_storage_hint().
"""

from __future__ import annotations

from typing import Any, Protocol

from hub.dhis2.uid_mapping.audit_profile import parse_program_label


class _ClientProto(Protocol):
    def find_by_filter(
        self,
        plural: str,
        filter_expr: str,
        *,
        fields: str = ...,
        page_size: int = ...,
        max_pages: int = ...,
    ) -> list[dict[str, Any]]: ...

    def get_metadata_object(
        self, plural: str, uid: str, *, fields: str | None = None
    ) -> dict[str, Any]: ...


def _uid_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("uid") or "").strip()
    return str(value or "").strip()


def _name_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("displayName") or "").strip()
    return ""


def logical_storage_hint(
    *,
    object_type: str,
    domain_type: str = "",
    stage_count: int = 0,
    program_count: int = 0,
    has_dataset: bool = False,
) -> dict[str, str]:
    """Human-readable logical location — not a physical table map."""
    kind = (object_type or "").lower()
    domain = (domain_type or "").upper()
    if "trackedentityattribute" in kind or kind in {"tea", "trackedentityattributes"}:
        return {
            "layer": "Tracker attribute",
            "summary": (
                "Stored as a tracked entity attribute value on the TEI. "
                "DHIS2 metadata API does not expose the physical DB table/column."
            ),
            "typical_store": "trackedentityattributevalue (instance DB — not via metadata API)",
        }
    if "programindicator" in kind:
        return {
            "layer": "Computed indicator",
            "summary": (
                "Program indicator — calculated from expression/filter; "
                "not a stored data-element column."
            ),
            "typical_store": "Computed at query/analytics time",
        }
    if "indicator" in kind and "program" not in kind:
        return {
            "layer": "Aggregate indicator",
            "summary": "Indicator formula over aggregate data; not a direct DE store.",
            "typical_store": "Computed / analytics",
        }
    if "optionset" in kind:
        return {
            "layer": "Metadata dictionary",
            "summary": "Option set defines allowed codes; values live on DE/TEA that reference it.",
            "typical_store": "optionset / option tables (metadata)",
        }
    if "dataelement" in kind:
        if stage_count or domain == "TRACKER":
            return {
                "layer": "Tracker event value",
                "summary": (
                    f"Tracker data element"
                    f"{f' on {stage_count} program stage(s)' if stage_count else ''}"
                    f"{f' / {program_count} program(s)' if program_count else ''}. "
                    "Values are event data values keyed by DE UID. "
                    "Physical analytics/linelist table names are not in the metadata API "
                    "(resolve in Live Processing if needed)."
                ),
                "typical_store": "programstageinstance.eventdatavalues (instance DB — not via metadata API)",
            }
        if has_dataset or domain == "AGGREGATE":
            return {
                "layer": "Aggregate data value",
                "summary": (
                    "Aggregate data element (data set path). "
                    "Physical datavalue table is not exposed by metadata API."
                ),
                "typical_store": "datavalue (instance DB — not via metadata API)",
            }
        return {
            "layer": "Data element",
            "summary": (
                "Data element metadata found. Connect DHIS2 to reverse-trace "
                "program stages / data sets. Physical DB tables are not in metadata GET."
            ),
            "typical_store": "Unknown until reverse links are loaded",
        }
    return {
        "layer": "Metadata object",
        "summary": "No physical table mapping available from DHIS2 metadata REST.",
        "typical_store": "n/a",
    }


def reverse_trace_links(
    client: _ClientProto,
    *,
    object_type: str,
    uid: str,
    dhis2_obj: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Issue capped reverse GETs to find program / stage / option-set consumers.

    Returns edges + queries used for the audit UI. Never writes.
    """
    kind = (object_type or "").lower()
    uid = (uid or "").strip()
    edges: list[dict[str, Any]] = []
    queries: list[str] = []
    errors: list[str] = []
    obj = dhis2_obj if isinstance(dhis2_obj, dict) else {}

    def _add(
        relation: str,
        related_uid: str,
        related_name: str,
        related_type: str,
        *,
        via: str,
    ) -> None:
        if not related_uid:
            return
        if any(e.get("related_uid") == related_uid and e.get("relation") == relation for e in edges):
            return
        edges.append(
            {
                "relation": relation,
                "related_uid": related_uid,
                "related_name": related_name,
                "related_type": related_type,
                "source": "dhis2_reverse_filter",
                "via": via,
            }
        )

    # Nested forward refs when already on the object
    if isinstance(obj.get("optionSet"), dict):
        _add(
            "→ Option Set",
            _uid_of(obj["optionSet"]),
            _name_of(obj["optionSet"]),
            "optionSet",
            via="object.optionSet",
        )
    if isinstance(obj.get("categoryCombo"), dict):
        _add(
            "→ Category Combination",
            _uid_of(obj["categoryCombo"]),
            _name_of(obj["categoryCombo"]),
            "categoryCombo",
            via="object.categoryCombo",
        )
    if isinstance(obj.get("program"), dict):
        _add(
            "→ Program",
            _uid_of(obj["program"]),
            _name_of(obj["program"]),
            "program",
            via="object.program",
        )

    try:
        if "dataelement" in kind and "programstage" not in kind:
            filt = f"programStageDataElements.dataElement.id:eq:{uid}"
            queries.append(f"GET /api/programStages?filter={filt}")
            stages = client.find_by_filter(
                "programStages",
                filt,
                fields="id,name,program[id,name,programType]",
                page_size=50,
                max_pages=2,
            )
            for stage in stages:
                _add(
                    "Data Element → Program Stage",
                    _uid_of(stage),
                    _name_of(stage),
                    "programStage",
                    via=filt,
                )
                program = stage.get("program") if isinstance(stage, dict) else None
                if isinstance(program, dict):
                    _add(
                        "Data Element → Program",
                        _uid_of(program),
                        _name_of(program),
                        "program",
                        via=filt,
                    )
            # Aggregate path
            ds_filter = f"dataSetElements.dataElement.id:eq:{uid}"
            queries.append(f"GET /api/dataSets?filter={ds_filter}")
            datasets = client.find_by_filter(
                "dataSets",
                ds_filter,
                fields="id,name,periodType",
                page_size=50,
                max_pages=1,
            )
            for ds in datasets:
                _add(
                    "Data Element → Data Set",
                    _uid_of(ds),
                    _name_of(ds),
                    "dataSet",
                    via=ds_filter,
                )

        elif "trackedentityattribute" in kind:
            filt = f"programTrackedEntityAttributes.trackedEntityAttribute.id:eq:{uid}"
            queries.append(f"GET /api/programs?filter={filt}")
            programs = client.find_by_filter(
                "programs",
                filt,
                fields="id,name,programType",
                page_size=50,
                max_pages=2,
            )
            for program in programs:
                _add(
                    "TEA → Program",
                    _uid_of(program),
                    _name_of(program),
                    "program",
                    via=filt,
                )

        elif "optionset" in kind:
            de_filt = f"optionSet.id:eq:{uid}"
            queries.append(f"GET /api/dataElements?filter={de_filt}")
            for de in client.find_by_filter(
                "dataElements",
                de_filt,
                fields="id,name,domainType,valueType",
                page_size=50,
                max_pages=2,
            ):
                _add(
                    "Option Set → Data Element",
                    _uid_of(de),
                    _name_of(de),
                    "dataElement",
                    via=de_filt,
                )
            tea_filt = f"optionSet.id:eq:{uid}"
            queries.append(f"GET /api/trackedEntityAttributes?filter={tea_filt}")
            for tea in client.find_by_filter(
                "trackedEntityAttributes",
                tea_filt,
                fields="id,name,valueType",
                page_size=50,
                max_pages=1,
            ):
                _add(
                    "Option Set → TEA",
                    _uid_of(tea),
                    _name_of(tea),
                    "trackedEntityAttribute",
                    via=tea_filt,
                )

        elif "programindicator" in kind:
            # Resolve stage names for expression refs (capped)
            from hub.dhis2.uid_mapping.audit_profile import extract_stage_data_element_refs

            refs = extract_stage_data_element_refs(
                str(obj.get("expression") or ""),
                str(obj.get("filter") or ""),
            )
            for ref in refs[:10]:
                stage_uid = ref["program_stage_uid"]
                queries.append(f"GET /api/programStages/{stage_uid}")
                try:
                    payload = client.get_metadata_object(
                        "programStages",
                        stage_uid,
                        fields="id,name,program[id,name]",
                    )
                    stage = payload.get("raw") or payload.get("item") or {}
                    _add(
                        "Program Indicator → Program Stage",
                        _uid_of(stage) or stage_uid,
                        _name_of(stage),
                        "programStage",
                        via="expression/filter #{stage.de}",
                    )
                    program = stage.get("program") if isinstance(stage, dict) else None
                    if isinstance(program, dict):
                        _add(
                            "Program Indicator → Program",
                            _uid_of(program),
                            _name_of(program),
                            "program",
                            via="programStages/{id}.program",
                        )
                except Exception as exc:  # noqa: BLE001 — keep other traces
                    errors.append(f"programStages/{stage_uid}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    stage_count = sum(1 for e in edges if e.get("related_type") == "programStage")
    program_count = sum(1 for e in edges if e.get("related_type") == "program")
    has_dataset = any(e.get("related_type") == "dataSet" for e in edges)
    domain_type = str(obj.get("domainType") or "")

    # Prefer nested program name when PI has "UID - Name" only offline
    if not program_count and obj.get("program"):
        p_uid, p_name = parse_program_label(
            obj["program"] if isinstance(obj["program"], str) else _uid_of(obj["program"])
        )
        if isinstance(obj.get("program"), dict):
            p_uid = _uid_of(obj["program"]) or p_uid
            p_name = _name_of(obj["program"]) or p_name
        if p_uid:
            _add("→ Program", p_uid, p_name, "program", via="object.program")
            program_count = 1

    storage = logical_storage_hint(
        object_type=object_type,
        domain_type=domain_type,
        stage_count=stage_count,
        program_count=program_count,
        has_dataset=has_dataset,
    )

    return {
        "ok": not errors or bool(edges),
        "edges": edges,
        "queries": queries,
        "errors": errors,
        "counts": {
            "edges": len(edges),
            "programs": program_count,
            "program_stages": stage_count,
            "option_sets": sum(1 for e in edges if e.get("related_type") == "optionSet"),
            "data_sets": sum(1 for e in edges if e.get("related_type") == "dataSet"),
        },
        "storage": storage,
    }
