"""Find DHIS2 metadata objects missing from the local UID index (GET-only).

Scans configured metadata collections via ``Dhis2Client.iter_collection`` and
diffs against local index UIDs. No BOOLEAN / name filters — discovery is
type-driven and generic.
"""

from __future__ import annotations

from typing import Any

from hub.dhis2.uid_mapping.compare import Classification, find_missing_in_repository
from hub.dhis2.uid_mapping.models import (
    SOURCE_DHIS2_IMPORT,
    SOURCE_LABELS,
    NormalizedUidRecord,
)

# Configured scan targets: plural API path, singular object_type, fields.
# Keep in sync with enrichment + index needs; never name/valueType filters.
SCANNABLE_COLLECTIONS: tuple[dict[str, str], ...] = (
    {
        "plural": "dataElements",
        "object_type": "dataElement",
        "fields": "id,name,code,valueType,domainType,optionSet[id],categoryCombo[id]",
    },
    {
        "plural": "indicators",
        "object_type": "indicator",
        "fields": "id,name,code",
    },
    {
        "plural": "programIndicators",
        "object_type": "programIndicator",
        "fields": "id,name,code,program[id]",
    },
    {
        "plural": "programs",
        "object_type": "program",
        "fields": "id,name,code",
    },
    {
        "plural": "programStages",
        "object_type": "programStage",
        "fields": "id,name,code,program[id]",
    },
    {
        "plural": "dataSets",
        "object_type": "dataSet",
        "fields": "id,name,code",
    },
    {
        "plural": "optionSets",
        "object_type": "optionSet",
        "fields": "id,name,code,valueType",
    },
    {
        "plural": "trackedEntityAttributes",
        "object_type": "trackedEntityAttribute",
        "fields": "id,name,code,valueType,optionSet[id]",
    },
    {
        "plural": "trackedEntityTypes",
        "object_type": "trackedEntityType",
        "fields": "id,name,code",
    },
    {
        "plural": "categoryCombos",
        "object_type": "categoryCombo",
        "fields": "id,name,code",
    },
)

CONFIRM_ADD_MISSING = "ADD MISSING UIDS TO INDEX"

# Display labels only — never used as discovery filters.
OBJECT_TYPE_LABELS: dict[str, str] = {
    "dataElement": "Data Element",
    "indicator": "Indicator",
    "programIndicator": "Program Indicator",
    "program": "Program",
    "programStage": "Program Stage",
    "dataSet": "Dataset",
    "optionSet": "Option Set",
    "trackedEntityAttribute": "Tracked Entity Attribute",
    "trackedEntityType": "Tracked Entity Type",
    "categoryCombo": "Category Combo",
}


def confirm_phrase_for_add_missing() -> str:
    return CONFIRM_ADD_MISSING


def object_type_label(object_type: str) -> str:
    key = str(object_type or "").strip()
    if key in OBJECT_TYPE_LABELS:
        return OBJECT_TYPE_LABELS[key]
    spaced = "".join(f" {c}" if c.isupper() else c for c in key).strip()
    return spaced[:1].upper() + spaced[1:] if spaced else key


def scannable_type_options() -> list[dict[str, str]]:
    return [
        {
            "id": c["object_type"],
            "plural": c["plural"],
            "label": object_type_label(c["object_type"]),
        }
        for c in SCANNABLE_COLLECTIONS
    ]


def _label(object_type: str) -> str:
    return object_type_label(object_type)


def paginate_rows(
    rows: list[dict[str, Any]],
    *,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Slice filtered rows for the results table (UI-only; does not change scan data)."""
    total = len(rows)
    size = max(1, min(int(per_page or 50), 200))
    pages = max(1, (total + size - 1) // size) if total else 1
    current = max(1, min(int(page or 1), pages))
    offset = (current - 1) * size
    page_rows = rows[offset : offset + size]
    return {
        "page": current,
        "per_page": size,
        "total": total,
        "total_pages": pages,
        "offset": offset,
        "rows": page_rows,
        "uids": [str(r.get("uid") or "") for r in rows if r.get("uid")],
    }


def _nested_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("uid") or "").strip()
    return str(value or "").strip()


def dhis2_item_to_index_record(
    item: dict[str, Any],
    *,
    object_type: str,
    environment: str,
) -> NormalizedUidRecord:
    """Normalize a DHIS2 metadata object into a hub index row (local only)."""
    uid = str(item.get("id") or item.get("uid") or "").strip()
    program_uid = _nested_id(item.get("program"))
    option_set_uid = _nested_id(item.get("optionSet"))
    category_combo_uid = _nested_id(item.get("categoryCombo"))
    # programStage items carry program; stages themselves may be referenced later
    payload = {
        "uid": uid,
        "name": str(item.get("name") or item.get("displayName") or ""),
        "code": str(item.get("code") or ""),
        "object_type": object_type,
        "value_type": str(item.get("valueType") or ""),
        "domain_type": str(item.get("domainType") or ""),
        "source_repository": "dhis2-import",
        "source_file": f"dhis2/{environment or 'unknown'}/{object_type}",
        "source_environment": environment or "",
        "program_uid": program_uid,
        "program_stage_uid": "",
        "option_set_uid": option_set_uid,
        "category_combo_uid": category_combo_uid,
        "source_origin": SOURCE_DHIS2_IMPORT,
        "csv_synced": False,
        "extras": {
            "imported_from": "dhis2",
            "dhis2_plural_hint": object_type,
        },
    }
    if object_type == "programStage" and program_uid:
        payload["program_stage_uid"] = uid
    record = NormalizedUidRecord.from_mapping(payload)
    return record


def index_uids(records: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("uid") or "").strip() for r in records if str(r.get("uid") or "").strip()}


def scan_dhis2_collection(
    client: Any,
    *,
    plural: str,
    fields: str,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, Any]:
    """GET-only page through one collection. Never writes."""
    result = client.iter_collection(
        plural,
        fields=fields,
        page_size=page_size,
        max_pages=max_pages,
    )
    items = list(result.get("items") or [])
    return {
        "plural": plural,
        "items": items,
        "count": len(items),
        "total": result.get("total"),
        "pages_fetched": result.get("pages_fetched"),
        "truncated": bool(result.get("truncated")),
        "dhis2_writes": 0,
    }


def discover_missing_uids(
    client: Any,
    index_records: list[dict[str, Any]],
    *,
    environment: str = "",
    object_types: list[str] | None = None,
    page_size: int = 100,
    max_pages: int = 50,
    collections: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    """Scan DHIS2 collections and return objects absent from the local index."""
    wanted = {t.strip() for t in (object_types or []) if t and t.strip()}
    targets = [
        c
        for c in (collections or SCANNABLE_COLLECTIONS)
        if not wanted or c["object_type"] in wanted
    ]
    local = index_uids(index_records)
    missing_rows: list[dict[str, Any]] = []
    per_type: dict[str, Any] = {}
    truncated = False
    errors: list[str] = []

    for target in targets:
        try:
            scanned = scan_dhis2_collection(
                client,
                plural=target["plural"],
                fields=target["fields"],
                page_size=page_size,
                max_pages=max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — surface per-type failures
            errors.append(f"{target['plural']}: {exc}")
            per_type[target["object_type"]] = {
                "scanned": 0,
                "missing": 0,
                "error": str(exc),
            }
            continue
        if scanned.get("truncated"):
            truncated = True
        gaps = find_missing_in_repository(scanned["items"], local)
        typed = []
        for gap in gaps:
            item = gap.get("dhis2") or {}
            typed.append(
                {
                    "uid": gap["uid"],
                    "status": gap["status"],
                    "object_type": target["object_type"],
                    "name": item.get("name") or item.get("displayName") or "",
                    "code": item.get("code") or "",
                    "value_type": item.get("valueType") or "",
                    "domain_type": item.get("domainType") or "",
                    "program_uid": _nested_id(item.get("program")),
                    "option_set_uid": _nested_id(item.get("optionSet")),
                    "category_combo_uid": _nested_id(item.get("categoryCombo")),
                    "source_environment": environment,
                    "dhis2": item,
                }
            )
        missing_rows.extend(typed)
        per_type[target["object_type"]] = {
            "scanned": scanned.get("count") or 0,
            "missing": len(typed),
            "truncated": scanned.get("truncated"),
            "total": scanned.get("total"),
        }

    return {
        "ok": not errors,
        "environment": environment,
        "index_uid_count": len(local),
        "missing_count": len(missing_rows),
        "missing": missing_rows,
        "per_type": per_type,
        "truncated": truncated,
        "errors": errors,
        "dhis2_writes": 0,
        "classification": Classification.MISSING_IN_REPOSITORY.value,
    }


def filter_missing_rows(
    rows: list[dict[str, Any]],
    *,
    object_type: str = "",
    program_uid: str = "",
    program_stage_uid: str = "",
    dataset_uid: str = "",
    environment: str = "",
    q: str = "",
) -> list[dict[str, Any]]:
    """Client-side filters for the Find Missing UIDs UI (no BOOLEAN/name hardcoding)."""
    query = (q or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        ot = str(row.get("object_type") or "")
        if object_type and ot != object_type:
            continue
        if environment and str(row.get("source_environment") or "") != environment:
            continue
        if program_uid:
            if ot == "program":
                if str(row.get("uid") or "") != program_uid:
                    continue
            elif str(row.get("program_uid") or "") != program_uid:
                continue
        if program_stage_uid:
            if ot == "programStage":
                if str(row.get("uid") or "") != program_stage_uid:
                    continue
            elif str(row.get("program_stage_uid") or "") != program_stage_uid:
                continue
        if dataset_uid:
            if ot == "dataSet":
                if str(row.get("uid") or "") != dataset_uid:
                    continue
            else:
                # DE↔dataset graph is not part of this scan; only dataSet rows match.
                continue
        if query:
            blob = " ".join(
                str(row.get(k) or "")
                for k in ("uid", "name", "code", "object_type", "value_type")
            ).lower()
            if query not in blob:
                continue
        out.append(row)
    return out


def selected_rows_to_records(
    selected: list[dict[str, Any]],
    *,
    environment: str,
) -> list[NormalizedUidRecord]:
    records: list[NormalizedUidRecord] = []
    for row in selected:
        item = row.get("dhis2") if isinstance(row.get("dhis2"), dict) else row
        object_type = str(row.get("object_type") or item.get("object_type") or "dataElement")
        env = str(row.get("source_environment") or environment or "")
        rec = dhis2_item_to_index_record(item, object_type=object_type, environment=env)
        if rec.uid:
            records.append(rec)
    return records


def source_badge(record: dict[str, Any]) -> dict[str, Any]:
    origin = str(record.get("source_origin") or "").strip()
    if not origin:
        # Legacy rows from CSV scan
        if str(record.get("source_repository") or "") in {"live-processing", "upload"} or str(
            record.get("source_file") or ""
        ).endswith(".csv"):
            origin = "csv"
        elif str(record.get("source_repository") or "") == "dhis2-import":
            origin = SOURCE_DHIS2_IMPORT
        else:
            origin = "manual" if record.get("source_file") else "csv"
    csv_synced = record.get("csv_synced")
    if csv_synced is None:
        csv_synced = origin == "csv"
    return {
        "source_origin": origin,
        "label": SOURCE_LABELS.get(origin, origin or "Unknown"),
        "csv_synced": bool(csv_synced),
        "needs_csv_export": origin == SOURCE_DHIS2_IMPORT and not bool(csv_synced),
    }


def export_source_update_csv_rows(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Rows for a source-update CSV (DHIS2 imports not yet in canonical CSV)."""
    rows: list[dict[str, str]] = []
    for record in records:
        badge = source_badge(record)
        if not badge["needs_csv_export"]:
            continue
        rows.append(
            {
                "id": str(record.get("uid") or ""),
                "name": str(record.get("name") or ""),
                "code": str(record.get("code") or ""),
                "kind": str(record.get("object_type") or ""),
                "valueType": str(record.get("value_type") or ""),
                "domainType": str(record.get("domain_type") or ""),
                "program": str(record.get("program_uid") or ""),
                "programStage": str(record.get("program_stage_uid") or ""),
                "optionSet": str(record.get("option_set_uid") or ""),
                "categoryCombo": str(record.get("category_combo_uid") or ""),
                "environment": str(record.get("source_environment") or ""),
                "source_origin": SOURCE_DHIS2_IMPORT,
                "csv_synced": "false",
            }
        )
    return rows
