"""Bulk read-only DHIS2 metadata fetch for enrichment (session reuse, batching)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2.enrichment.derive import as_bool, derive_answer_type
from hub.dhis2.enrichment.models import (
    AUDIT_BROKEN_REFERENCE,
    REL_DE_IN_DATA_SET,
    REL_DE_IN_GROUP,
    REL_DE_IN_PROGRAM_STAGE,
    REL_DE_USES_CATEGORY_COMBO,
    REL_DE_USES_OPTION_SET,
    REL_OPTION_SET_USED_BY_DE,
    REL_OPTION_SET_USED_BY_TEA,
    REL_PI_BELONGS_TO_PROGRAM,
    REL_PI_REFERENCES_ATTR,
    REL_PI_REFERENCES_CONSTANT,
    REL_PI_REFERENCES_DE,
    REL_PI_REFERENCES_STAGE,
    REL_TEA_IN_PROGRAM,
    relationship,
)
from hub.dhis2.enrichment.refs import extract_pi_references
from hub.dhis2.redact import redact_mapping

ProgressCb = Callable[[str, float, str], None]
CancelCb = Callable[[], bool]

_DE_FIELDS = (
    "id,name,shortName,code,description,formName,domainType,valueType,aggregationType,"
    "zeroIsSignificant,optionSetValue,optionSet[id,name],categoryCombo[id,name],"
    "dataElementGroups[id,name],dataSetElements[dataSet[id,name]],lastUpdated"
)
_TEA_FIELDS = (
    "id,name,shortName,code,description,formName,valueType,unique,optionSetValue,"
    "optionSet[id,name],lastUpdated"
)
_PI_FIELDS = (
    "id,name,shortName,code,description,expression,filter,analyticsType,aggregationType,"
    "decimals,program[id,name],lastUpdated"
)
_OS_FIELDS = (
    "id,name,code,valueType,options[id,name,code,sortOrder,style[color,icon]]"
)


def _checksum(payload: dict[str, Any]) -> str:
    material = {k: payload.get(k) for k in sorted(payload) if k != "raw"}
    blob = json.dumps(material, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _chunk(items: list[str], size: int = 60) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _uid(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("uid") or "").strip()
    return str(value or "").strip()


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("displayName") or "").strip()
    return ""


class EnrichmentFetcher:
    def __init__(self, client: Dhis2Client) -> None:
        self.client = client
        self._option_sets: dict[str, dict[str, Any]] = {}
        self._programs: dict[str, dict[str, Any]] = {}
        self._stages: dict[str, dict[str, Any]] = {}

    def fetch_all(
        self,
        repo_records: list[dict[str, Any]],
        *,
        environment: str = "",
        include_raw: bool = False,
        previous_checksums: dict[str, str] | None = None,
        on_progress: ProgressCb | None = None,
        should_cancel: CancelCb | None = None,
    ) -> dict[str, Any]:
        """
        Build enriched objects + relationships from repository UID list + live DHIS2.

        Uses bulk id:in fetches and a reverse graph from programStages/dataSets
        (not one request per UID).
        """
        prev_checksums = previous_checksums or {}
        def progress(phase: str, pct: float, message: str) -> None:
            if on_progress:
                on_progress(phase, pct, message)

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        by_uid: dict[str, list[dict[str, Any]]] = {}
        for row in repo_records:
            uid = str(row.get("uid") or "").strip()
            if uid:
                by_uid.setdefault(uid, []).append(row)

        target_uids = sorted(by_uid)
        progress("prepare", 2, f"Prepared {len(target_uids)} repository UID(s)")

        if cancelled():
            return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}

        # Reverse graphs (one-to-many)
        progress("reverse_graph", 8, "Building program-stage → data-element graph")
        de_to_stages, de_to_programs = self._build_stage_graph(should_cancel=should_cancel)
        if cancelled():
            return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}

        progress("reverse_graph", 18, "Building data-set → data-element graph")
        de_to_datasets = self._build_dataset_graph(should_cancel=should_cancel)
        if cancelled():
            return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}

        progress("reverse_graph", 25, "Building program → TEA graph")
        tea_to_programs = self._build_tea_program_graph(should_cancel=should_cancel)

        # Bulk metadata for UIDs present in repo index
        live_by_uid: dict[str, dict[str, Any]] = {}
        kind_groups: dict[str, list[str]] = {
            "dataElement": [],
            "trackedEntityAttribute": [],
            "programIndicator": [],
            "optionSet": [],
        }
        for uid, rows in by_uid.items():
            kind = str(rows[0].get("object_type") or "").lower()
            if "programindicator" in kind:
                kind_groups["programIndicator"].append(uid)
            elif "trackedentityattribute" in kind or kind in {"tea"}:
                kind_groups["trackedEntityAttribute"].append(uid)
            elif "optionset" in kind:
                kind_groups["optionSet"].append(uid)
            else:
                kind_groups["dataElement"].append(uid)

        progress("fetch_de", 30, f"Fetching {len(kind_groups['dataElement'])} data element(s)")
        live_by_uid.update(
            self._fetch_collection(
                "dataElements",
                kind_groups["dataElement"],
                _DE_FIELDS,
                object_type="dataElement",
                should_cancel=should_cancel,
            )
        )
        if cancelled():
            return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}

        progress("fetch_tea", 45, f"Fetching {len(kind_groups['trackedEntityAttribute'])} attribute(s)")
        live_by_uid.update(
            self._fetch_collection(
                "trackedEntityAttributes",
                kind_groups["trackedEntityAttribute"],
                _TEA_FIELDS,
                object_type="trackedEntityAttribute",
                should_cancel=should_cancel,
            )
        )
        progress("fetch_pi", 55, f"Fetching {len(kind_groups['programIndicator'])} program indicator(s)")
        live_by_uid.update(
            self._fetch_collection(
                "programIndicators",
                kind_groups["programIndicator"],
                _PI_FIELDS,
                object_type="programIndicator",
                should_cancel=should_cancel,
            )
        )

        # Option sets referenced by live objects + repo option-set UIDs
        os_uids = set(kind_groups["optionSet"])
        for obj in live_by_uid.values():
            os_id = _uid(obj.get("optionSet"))
            if os_id:
                os_uids.add(os_id)
        progress("fetch_os", 65, f"Fetching {len(os_uids)} option set(s)")
        self._option_sets.update(
            self._fetch_collection(
                "optionSets",
                sorted(os_uids),
                _OS_FIELDS,
                object_type="optionSet",
                should_cancel=should_cancel,
            )
        )
        live_by_uid.update({uid: self._option_sets[uid] for uid in self._option_sets if uid in os_uids})

        if cancelled():
            return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}

        progress("assemble", 75, "Assembling objects and relationships")
        from hub.dhis2.enrichment.classify import classify_uid
        from hub.dhis2.enrichment.db import utcnow

        fetched_at = utcnow()
        objects: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        options_out: list[dict[str, Any]] = []
        unresolved_refs = 0
        broken = 0

        # Emit option-set option rows + objects for cached sets
        for os_uid, os_obj in self._option_sets.items():
            raw_options = os_obj.get("options") if isinstance(os_obj.get("options"), list) else []
            for idx, opt in enumerate(raw_options):
                if not isinstance(opt, dict):
                    continue
                opt_uid = _uid(opt)
                if not opt_uid:
                    continue
                style = opt.get("style") if isinstance(opt.get("style"), dict) else {}
                options_out.append(
                    {
                        "option_set_uid": os_uid,
                        "option_uid": opt_uid,
                        "name": opt.get("name"),
                        "code": opt.get("code"),
                        "sort_order": opt.get("sortOrder", idx),
                        "color": style.get("color"),
                        "icon": style.get("icon"),
                    }
                )

        for uid in target_uids:
            if cancelled():
                return {"ok": False, "cancelled": True, "objects": [], "relationships": [], "options": []}
            repo_rows = by_uid[uid]
            live = live_by_uid.get(uid)
            object_type = (
                (live or {}).get("object_type")
                or str(repo_rows[0].get("object_type") or "dataElement")
            )
            stage_links = de_to_stages.get(uid) or []
            stage_uids = [s["uid"] for s in stage_links]

            if live:
                obj = self._normalize_live(
                    live,
                    object_type=object_type,
                    environment=environment,
                    fetched_at=fetched_at,
                    include_raw=include_raw,
                )
            else:
                obj = {
                    "uid": uid,
                    "object_type": object_type,
                    "name": repo_rows[0].get("name"),
                    "code": repo_rows[0].get("code"),
                    "value_type": repo_rows[0].get("value_type"),
                    "domain_type": repo_rows[0].get("domain_type"),
                    "answer_type": derive_answer_type(
                        str(repo_rows[0].get("value_type") or ""),
                        option_set_uid=str(repo_rows[0].get("option_set_uid") or ""),
                    ),
                    "fetched_at": fetched_at,
                    "checksum": "",
                    "summary": {"source": "repository_only"},
                }

            statuses = classify_uid(
                repo_rows=repo_rows,
                dhis2_obj=live,
                stage_uids_live=stage_uids,
                previous_checksum=prev_checksums.get(uid),
                current_checksum=str(obj.get("checksum") or "") or None,
            )
            obj["audit_statuses"] = statuses
            objects.append(obj)

            # Relationships from reverse graphs / nested refs
            if "dataelement" in object_type.lower():
                for stage in stage_links:
                    relationships.append(
                        relationship(
                            rel_type=REL_DE_IN_PROGRAM_STAGE,
                            from_uid=uid,
                            from_type="dataElement",
                            to_uid=stage["uid"],
                            to_type="programStage",
                            to_name=stage.get("name") or "",
                            detail={"program_uid": stage.get("program_uid"), "program_name": stage.get("program_name")},
                        )
                    )
                    if stage.get("program_uid"):
                        relationships.append(
                            relationship(
                                rel_type=REL_DE_IN_PROGRAM_STAGE,
                                from_uid=uid,
                                from_type="dataElement",
                                to_uid=stage["program_uid"],
                                to_type="program",
                                to_name=stage.get("program_name") or "",
                                detail={"via_program_stage": stage["uid"]},
                            )
                        )
                for ds in de_to_datasets.get(uid) or []:
                    relationships.append(
                        relationship(
                            rel_type=REL_DE_IN_DATA_SET,
                            from_uid=uid,
                            from_type="dataElement",
                            to_uid=ds["uid"],
                            to_type="dataSet",
                            to_name=ds.get("name") or "",
                        )
                    )
                if live:
                    for group in live.get("dataElementGroups") or []:
                        g_uid = _uid(group)
                        if g_uid:
                            relationships.append(
                                relationship(
                                    rel_type=REL_DE_IN_GROUP,
                                    from_uid=uid,
                                    from_type="dataElement",
                                    to_uid=g_uid,
                                    to_type="dataElementGroup",
                                    to_name=_name(group),
                                )
                            )
                    os_uid = _uid(live.get("optionSet"))
                    if os_uid:
                        relationships.append(
                            relationship(
                                rel_type=REL_DE_USES_OPTION_SET,
                                from_uid=uid,
                                from_type="dataElement",
                                to_uid=os_uid,
                                to_type="optionSet",
                                to_name=_name(live.get("optionSet")),
                            )
                        )
                        relationships.append(
                            relationship(
                                rel_type=REL_OPTION_SET_USED_BY_DE,
                                from_uid=os_uid,
                                from_type="optionSet",
                                to_uid=uid,
                                to_type="dataElement",
                                to_name=obj.get("name") or "",
                            )
                        )
                    cc = live.get("categoryCombo")
                    if isinstance(cc, dict) and _uid(cc):
                        relationships.append(
                            relationship(
                                rel_type=REL_DE_USES_CATEGORY_COMBO,
                                from_uid=uid,
                                from_type="dataElement",
                                to_uid=_uid(cc),
                                to_type="categoryCombo",
                                to_name=_name(cc),
                            )
                        )

            if "trackedentityattribute" in object_type.lower() and live:
                os_uid = _uid(live.get("optionSet"))
                if os_uid:
                    relationships.append(
                        relationship(
                            rel_type=REL_OPTION_SET_USED_BY_TEA,
                            from_uid=os_uid,
                            from_type="optionSet",
                            to_uid=uid,
                            to_type="trackedEntityAttribute",
                            to_name=obj.get("name") or "",
                        )
                    )
                for program in tea_to_programs.get(uid) or []:
                    relationships.append(
                        relationship(
                            rel_type=REL_TEA_IN_PROGRAM,
                            from_uid=uid,
                            from_type="trackedEntityAttribute",
                            to_uid=program["uid"],
                            to_type="program",
                            to_name=program.get("name") or "",
                        )
                    )

            if "programindicator" in object_type.lower() and live:
                program = live.get("program") if isinstance(live.get("program"), dict) else {}
                if _uid(program):
                    relationships.append(
                        relationship(
                            rel_type=REL_PI_BELONGS_TO_PROGRAM,
                            from_uid=uid,
                            from_type="programIndicator",
                            to_uid=_uid(program),
                            to_type="program",
                            to_name=_name(program),
                        )
                    )
                    obj["program_uid"] = _uid(program)
                    obj["program_name"] = _name(program)
                refs = extract_pi_references(
                    str(live.get("expression") or ""),
                    str(live.get("filter") or ""),
                )
                obj["summary"] = {
                    **(obj.get("summary") or {}),
                    "pi_references": refs,
                }
                for de_uid in refs["data_elements"]:
                    relationships.append(
                        relationship(
                            rel_type=REL_PI_REFERENCES_DE,
                            from_uid=uid,
                            from_type="programIndicator",
                            to_uid=de_uid,
                            to_type="dataElement",
                        )
                    )
                    if de_uid not in live_by_uid and de_uid not in by_uid:
                        unresolved_refs += 1
                for attr in refs["attributes"]:
                    relationships.append(
                        relationship(
                            rel_type=REL_PI_REFERENCES_ATTR,
                            from_uid=uid,
                            from_type="programIndicator",
                            to_uid=attr,
                            to_type="trackedEntityAttribute",
                        )
                    )
                for const in refs["constants"]:
                    relationships.append(
                        relationship(
                            rel_type=REL_PI_REFERENCES_CONSTANT,
                            from_uid=uid,
                            from_type="programIndicator",
                            to_uid=const,
                            to_type="constant",
                        )
                    )
                for stage_uid in refs["program_stages"]:
                    relationships.append(
                        relationship(
                            rel_type=REL_PI_REFERENCES_STAGE,
                            from_uid=uid,
                            from_type="programIndicator",
                            to_uid=stage_uid,
                            to_type="programStage",
                            to_name=(self._stages.get(stage_uid) or {}).get("name") or "",
                        )
                    )
                if refs["unresolved"]:
                    unresolved_refs += len(refs["unresolved"])
                    broken += 1
                    statuses = list(obj.get("audit_statuses") or [])
                    if AUDIT_BROKEN_REFERENCE not in statuses:
                        statuses = [s for s in statuses if s != "Matched"]
                        statuses.append(AUDIT_BROKEN_REFERENCE)
                        obj["audit_statuses"] = statuses

        # Deduplicate relationships
        seen_rel: set[tuple[str, str, str, str]] = set()
        unique_rels: list[dict[str, Any]] = []
        for rel in relationships:
            key = (rel["rel_type"], rel["from_uid"], rel["to_uid"], rel["to_type"])
            if key in seen_rel:
                continue
            seen_rel.add(key)
            unique_rels.append(rel)

        progress("done", 100, f"Enriched {len(objects)} object(s), {len(unique_rels)} relationship(s)")
        return {
            "ok": True,
            "cancelled": False,
            "environment": environment,
            "objects": objects,
            "relationships": unique_rels,
            "options": options_out,
            "stats": {
                "repository_uids": len(target_uids),
                "objects": len(objects),
                "relationships": len(unique_rels),
                "option_rows": len(options_out),
                "missing_in_dhis2": sum(
                    1 for o in objects if "Missing in DHIS2" in (o.get("audit_statuses") or [])
                ),
                "unresolved_references": unresolved_refs,
                "broken_reference_objects": broken,
            },
        }

    def _normalize_live(
        self,
        live: dict[str, Any],
        *,
        object_type: str,
        environment: str,
        fetched_at: str,
        include_raw: bool,
    ) -> dict[str, Any]:
        os_obj = live.get("optionSet") if isinstance(live.get("optionSet"), dict) else {}
        cc = live.get("categoryCombo") if isinstance(live.get("categoryCombo"), dict) else {}
        option_set_value = as_bool(live.get("optionSetValue"))
        if option_set_value is None:
            option_set_value = bool(_uid(os_obj))
        value_type = str(live.get("valueType") or "")
        summary = {
            "groups": [
                {"uid": _uid(g), "name": _name(g)}
                for g in (live.get("dataElementGroups") or [])
                if isinstance(g, dict)
            ],
            "data_sets": [
                {
                    "uid": _uid((d.get("dataSet") if isinstance(d, dict) else d)),
                    "name": _name((d.get("dataSet") if isinstance(d, dict) else d)),
                }
                for d in (live.get("dataSetElements") or [])
            ],
        }
        payload = {
            "uid": _uid(live),
            "object_type": object_type,
            "name": live.get("name"),
            "short_name": live.get("shortName"),
            "code": live.get("code"),
            "description": live.get("description"),
            "form_name": live.get("formName"),
            "domain_type": live.get("domainType"),
            "value_type": value_type,
            "aggregation_type": live.get("aggregationType"),
            "zero_is_significant": as_bool(live.get("zeroIsSignificant")),
            "option_set_value": option_set_value,
            "option_set_uid": _uid(os_obj),
            "option_set_name": _name(os_obj),
            "category_combo_uid": _uid(cc),
            "category_combo_name": _name(cc),
            "analytics_type": live.get("analyticsType"),
            "decimals": live.get("decimals"),
            "expression": live.get("expression"),
            "filter": live.get("filter"),
            "answer_type": derive_answer_type(
                value_type,
                option_set_value=option_set_value,
                option_set_uid=_uid(os_obj),
            ),
            "fetched_at": fetched_at,
            "summary": summary,
        }
        if isinstance(live.get("program"), dict):
            payload["program_uid"] = _uid(live["program"])
            payload["program_name"] = _name(live["program"])
        payload["checksum"] = _checksum(payload)
        if include_raw:
            payload["raw_json"] = json.dumps(redact_mapping(live), ensure_ascii=True)
        else:
            payload["raw_json"] = None
        return payload

    def _fetch_collection(
        self,
        plural: str,
        uids: list[str],
        fields: str,
        *,
        object_type: str,
        should_cancel: CancelCb | None,
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not uids:
            return out
        for batch in _chunk(sorted(set(uids)), 60):
            if should_cancel and should_cancel():
                break
            filt = "id:in:[" + ",".join(batch) + "]"
            try:
                items = self.client.find_by_filter(
                    plural,
                    filt,
                    fields=fields,
                    page_size=min(60, len(batch)),
                    max_pages=2,
                    normalize=False,
                )
            except Dhis2Error:
                continue
            for item in items:
                uid = _uid(item)
                if not uid:
                    continue
                merged = dict(item)
                merged["object_type"] = object_type
                out[uid] = merged
        return out

    def _build_stage_graph(
        self, *, should_cancel: CancelCb | None
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
        de_to_stages: dict[str, list[dict[str, str]]] = {}
        de_to_programs: dict[str, list[dict[str, str]]] = {}
        try:
            result = self.client.iter_collection(
                "programStages",
                fields="id,name,program[id,name],programStageDataElements[dataElement[id]]",
                page_size=100,
                max_pages=20,
                normalize=False,
            )
        except Dhis2Error:
            return de_to_stages, de_to_programs

        for stage in result.get("items") or []:
            if should_cancel and should_cancel():
                break
            stage_uid = _uid(stage)
            stage_name = _name(stage)
            program = stage.get("program") if isinstance(stage.get("program"), dict) else {}
            program_uid = _uid(program)
            program_name = _name(program)
            if stage_uid:
                self._stages[stage_uid] = {"id": stage_uid, "name": stage_name, "program": program}
            if program_uid:
                self._programs[program_uid] = {"id": program_uid, "name": program_name}
            psdes = stage.get("programStageDataElements") or []
            if not isinstance(psdes, list):
                continue
            for row in psdes:
                de = row.get("dataElement") if isinstance(row, dict) else None
                de_uid = _uid(de)
                if not de_uid or not stage_uid:
                    continue
                de_to_stages.setdefault(de_uid, []).append(
                    {
                        "uid": stage_uid,
                        "name": stage_name,
                        "program_uid": program_uid,
                        "program_name": program_name,
                    }
                )
                if program_uid:
                    de_to_programs.setdefault(de_uid, []).append(
                        {"uid": program_uid, "name": program_name}
                    )
        return de_to_stages, de_to_programs

    def _build_dataset_graph(
        self, *, should_cancel: CancelCb | None
    ) -> dict[str, list[dict[str, str]]]:
        de_to_datasets: dict[str, list[dict[str, str]]] = {}
        try:
            result = self.client.iter_collection(
                "dataSets",
                fields="id,name,dataSetElements[dataElement[id]]",
                page_size=100,
                max_pages=10,
                normalize=False,
            )
        except Dhis2Error:
            return de_to_datasets
        for ds in result.get("items") or []:
            if should_cancel and should_cancel():
                break
            ds_uid = _uid(ds)
            ds_name = _name(ds)
            elements = ds.get("dataSetElements") or []
            if not isinstance(elements, list):
                continue
            for row in elements:
                de = row.get("dataElement") if isinstance(row, dict) else None
                de_uid = _uid(de)
                if de_uid and ds_uid:
                    de_to_datasets.setdefault(de_uid, []).append({"uid": ds_uid, "name": ds_name})
        return de_to_datasets

    def _build_tea_program_graph(
        self, *, should_cancel: CancelCb | None
    ) -> dict[str, list[dict[str, str]]]:
        tea_to_programs: dict[str, list[dict[str, str]]] = {}
        try:
            result = self.client.iter_collection(
                "programs",
                fields="id,name,programTrackedEntityAttributes[trackedEntityAttribute[id]]",
                page_size=100,
                max_pages=10,
                normalize=False,
            )
        except Dhis2Error:
            return tea_to_programs
        for program in result.get("items") or []:
            if should_cancel and should_cancel():
                break
            p_uid = _uid(program)
            p_name = _name(program)
            attrs = program.get("programTrackedEntityAttributes") or []
            if not isinstance(attrs, list):
                continue
            for row in attrs:
                tea = row.get("trackedEntityAttribute") if isinstance(row, dict) else None
                tea_uid = _uid(tea)
                if tea_uid and p_uid:
                    tea_to_programs.setdefault(tea_uid, []).append({"uid": p_uid, "name": p_name})
        return tea_to_programs
