"""Audit-oriented mapping profile for UID index rows (read-only helpers).

Derives human-readable answer kinds, program/stage links, and option-set
summaries from repository index fields + optional live DHIS2 GET payloads.
No PMNP domain rules — generic DHIS2 metadata shapes only.
"""

from __future__ import annotations

import re
from typing import Any

from hub.dhis2.uid_mapping.models import NormalizedUidRecord

_UID = r"[A-Za-z][A-Za-z0-9]{10}"
_PROGRAM_LABEL_RE = re.compile(rf"^({_UID})\s*[-–—]\s*(.+)$")
_STAGE_DE_RE = re.compile(rf"#\{{({_UID})\.({_UID})\}}")
_UID_ONLY_RE = re.compile(rf"^({_UID})$")

_ANSWER_KINDS: dict[str, dict[str, str]] = {
    "BOOLEAN": {
        "label": "Yes / No",
        "summary": "Boolean answer (true = Yes, false = No).",
    },
    "TRUE_ONLY": {
        "label": "True only",
        "summary": "Checked means true; unchecked is empty (not false).",
    },
    "TEXT": {"label": "Text", "summary": "Free-text string."},
    "LONG_TEXT": {"label": "Long text", "summary": "Multi-line text."},
    "NUMBER": {"label": "Number", "summary": "Numeric value."},
    "INTEGER": {"label": "Integer", "summary": "Whole number."},
    "INTEGER_POSITIVE": {
        "label": "Positive integer",
        "summary": "Integer greater than zero.",
    },
    "INTEGER_ZERO_OR_POSITIVE": {
        "label": "Zero or positive integer",
        "summary": "Integer ≥ 0.",
    },
    "INTEGER_NEGATIVE": {
        "label": "Negative integer",
        "summary": "Integer less than zero.",
    },
    "DATE": {"label": "Date", "summary": "Calendar date."},
    "DATETIME": {"label": "Date & time", "summary": "Timestamp."},
    "TIME": {"label": "Time", "summary": "Time of day."},
    "PERCENTAGE": {"label": "Percentage", "summary": "Percentage number."},
    "UNIT_INTERVAL": {
        "label": "Unit interval",
        "summary": "Number between 0 and 1.",
    },
    "PHONE_NUMBER": {"label": "Phone number", "summary": "Phone string."},
    "EMAIL": {"label": "Email", "summary": "Email address."},
    "URL": {"label": "URL", "summary": "Web address."},
    "FILE_RESOURCE": {"label": "File", "summary": "Uploaded file resource."},
    "IMAGE": {"label": "Image", "summary": "Image file resource."},
    "COORDINATE": {"label": "Coordinate", "summary": "Geographic coordinate."},
    "ORGANISATION_UNIT": {
        "label": "Organisation unit",
        "summary": "References an org unit.",
    },
    "AGE": {"label": "Age", "summary": "Age value."},
    "USERNAME": {"label": "Username", "summary": "User account name."},
}


def parse_program_label(value: str) -> tuple[str, str]:
    """Split ``UID - Name`` (LP program column) into uid + display name."""
    text = (value or "").strip()
    if not text:
        return "", ""
    match = _PROGRAM_LABEL_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    if _UID_ONLY_RE.match(text):
        return text, ""
    return "", text


def extract_stage_data_element_refs(*texts: str) -> list[dict[str, str]]:
    """Extract ``#{programStageUid.dataElementUid}`` refs from PI expression/filter."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for text in texts:
        if not text:
            continue
        for stage_uid, de_uid in _STAGE_DE_RE.findall(str(text)):
            key = (stage_uid, de_uid)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "program_stage_uid": stage_uid,
                    "data_element_uid": de_uid,
                }
            )
    return out


def answer_kind(value_type: str, *, has_option_set: bool = False) -> dict[str, str]:
    vt = (value_type or "").strip().upper()
    if has_option_set:
        return {
            "code": vt or "OPTION_SET",
            "label": "Option set choice",
            "summary": "Answer is selected from a DHIS2 option set (e.g. Yes/No codes).",
        }
    if not vt:
        return {
            "code": "",
            "label": "Unknown",
            "summary": "Value type not present in the repository index.",
        }
    known = _ANSWER_KINDS.get(vt)
    if known:
        return {"code": vt, "label": known["label"], "summary": known["summary"]}
    return {
        "code": vt,
        "label": vt.replace("_", " ").title(),
        "summary": f"DHIS2 value type {vt}.",
    }


def _extras(record: dict[str, Any]) -> dict[str, Any]:
    extras = record.get("extras")
    return extras if isinstance(extras, dict) else {}


def _first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def summarize_option_set(option_set: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(option_set, dict):
        return None
    options_raw = option_set.get("options") or []
    options: list[dict[str, str]] = []
    if isinstance(options_raw, list):
        for item in options_raw:
            if not isinstance(item, dict):
                continue
            options.append(
                {
                    "uid": str(item.get("id") or item.get("uid") or ""),
                    "name": str(item.get("name") or item.get("displayName") or ""),
                    "code": str(item.get("code") or ""),
                }
            )
    names = [o["name"] for o in options if o.get("name")]
    codes = [o["code"] for o in options if o.get("code")]
    yes_no_like = False
    joined = " ".join(n.lower() for n in names + codes)
    if {"yes", "no"} <= set(joined.split()) or (
        any(c.lower() in {"y", "yes", "1", "true"} for c in codes)
        and any(c.lower() in {"n", "no", "0", "false"} for c in codes)
    ):
        yes_no_like = True
    return {
        "uid": str(option_set.get("id") or option_set.get("uid") or ""),
        "name": str(option_set.get("name") or option_set.get("displayName") or ""),
        "code": str(option_set.get("code") or ""),
        "option_count": len(options),
        "options": options[:50],
        "yes_no_like": yes_no_like,
        "choice_summary": (
            "Yes / No style option set"
            if yes_no_like
            else (", ".join(names[:8]) + ("…" if len(names) > 8 else "") if names else "—")
        ),
    }


def build_audit_profile(
    record: dict[str, Any],
    *,
    dhis2: dict[str, Any] | None = None,
    option_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an audit panel payload for a UID index row (+ optional live DHIS2)."""
    extras = _extras(record)
    dhis2 = dhis2 if isinstance(dhis2, dict) else {}

    program_raw = _first(
        record.get("program_uid"),
        extras.get("program"),
        (dhis2.get("program") or {}).get("id")
        if isinstance(dhis2.get("program"), dict)
        else dhis2.get("program"),
    )
    program_uid, program_name = parse_program_label(program_raw)
    if not program_name and isinstance(dhis2.get("program"), dict):
        program_name = str(
            dhis2["program"].get("name") or dhis2["program"].get("displayName") or ""
        )
    if not program_uid and isinstance(dhis2.get("program"), dict):
        program_uid = str(dhis2["program"].get("id") or "")

    expression = _first(extras.get("expression"), dhis2.get("expression"))
    filter_text = _first(extras.get("filter"), dhis2.get("filter"))
    stage_refs = extract_stage_data_element_refs(expression, filter_text)
    stage_uids = sorted({ref["program_stage_uid"] for ref in stage_refs})
    index_stage = _first(record.get("program_stage_uid"), extras.get("programStage"))
    if index_stage and index_stage not in stage_uids:
        stage_uids.insert(0, index_stage)

    option_set_uid = _first(
        record.get("option_set_uid"),
        extras.get("optionSet"),
        (dhis2.get("optionSet") or {}).get("id")
        if isinstance(dhis2.get("optionSet"), dict)
        else dhis2.get("optionSet"),
    )
    option_summary = summarize_option_set(
        option_set
        or (dhis2.get("optionSet") if isinstance(dhis2.get("optionSet"), dict) else None)
    )
    if option_summary and not option_set_uid:
        option_set_uid = option_summary.get("uid") or ""

    value_type = _first(
        dhis2.get("valueType"),
        record.get("value_type"),
        extras.get("valueType"),
    )
    domain_type = _first(
        dhis2.get("domainType"),
        record.get("domain_type"),
        extras.get("domainType"),
    )
    aggregation_type = _first(dhis2.get("aggregationType"), extras.get("aggregationType"))
    form_name = _first(dhis2.get("formName"), extras.get("formName"))
    short_name = _first(dhis2.get("shortName"), extras.get("shortName"))
    kind = answer_kind(value_type, has_option_set=bool(option_set_uid or option_summary))

    connections: list[dict[str, str]] = []
    if program_uid or program_name:
        connections.append(
            {
                "role": "Program",
                "uid": program_uid,
                "name": program_name,
                "detail": "From index program column / live DHIS2 program ref",
            }
        )
    for stage_uid in stage_uids:
        connections.append(
            {
                "role": "Program stage",
                "uid": stage_uid,
                "name": "",
                "detail": "Referenced in expression/filter as #{stage.dataElement}",
            }
        )
    if option_set_uid:
        connections.append(
            {
                "role": "Option set",
                "uid": option_set_uid,
                "name": (option_summary or {}).get("name") or "",
                "detail": (option_summary or {}).get("choice_summary") or "Option set UID",
            }
        )

    return {
        "object_type": _first(record.get("object_type"), extras.get("kind"), dhis2.get("href")),
        "value_type": value_type,
        "domain_type": domain_type,
        "aggregation_type": aggregation_type,
        "form_name": form_name,
        "short_name": short_name,
        "answer": kind,
        "program_uid": program_uid,
        "program_name": program_name,
        "program_stage_uids": stage_uids,
        "stage_data_element_refs": stage_refs[:40],
        "option_set_uid": option_set_uid,
        "option_set": option_summary,
        "expression": expression,
        "filter": filter_text,
        "connections": connections,
        "source_endpoint": _first(extras.get("source_endpoint")),
        "dhis2_environment": _first(
            record.get("source_environment"), extras.get("dhis2_environment")
        ),
    }


def enrich_record_mapping_fields(record: NormalizedUidRecord) -> NormalizedUidRecord:
    """Normalize program label + stage hints onto a record during scan/import."""
    program_uid, program_name = parse_program_label(record.program_uid)
    if program_uid:
        record.program_uid = program_uid
    if program_name:
        record.extras.setdefault("program_name", program_name)
        record.extras.setdefault("program", f"{program_uid} - {program_name}" if program_uid else program_name)

    expression = str(record.extras.get("expression") or "")
    filter_text = str(record.extras.get("filter") or "")
    refs = extract_stage_data_element_refs(expression, filter_text)
    if refs and not record.program_stage_uid:
        # Prefer stage that references this DE when possible.
        for ref in refs:
            if ref["data_element_uid"] == record.uid:
                record.program_stage_uid = ref["program_stage_uid"]
                break
        if not record.program_stage_uid:
            # For program indicators, keep all stages in extras; first as hint.
            record.program_stage_uid = refs[0]["program_stage_uid"]
        record.extras["stage_data_element_refs"] = refs[:40]

    kind = answer_kind(record.value_type, has_option_set=bool(record.option_set_uid))
    record.extras["answer_label"] = kind["label"]
    record.extras["answer_summary"] = kind["summary"]
    return record
