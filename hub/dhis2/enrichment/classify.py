"""Audit classifications comparing repository index vs enriched DHIS2 metadata."""

from __future__ import annotations

from typing import Any

from hub.dhis2.enrichment.models import (
    AUDIT_CHANGED_SINCE_SCAN,
    AUDIT_DOMAIN_TYPE_MISMATCH,
    AUDIT_DUPLICATE_MAPPING,
    AUDIT_MATCHED,
    AUDIT_MISSING_DHIS2,
    AUDIT_MISSING_REPO,
    AUDIT_NAME_MISMATCH,
    AUDIT_OBJECT_TYPE_MISMATCH,
    AUDIT_OPTION_SET_MISMATCH,
    AUDIT_PROGRAM_STAGE_MISMATCH,
    AUDIT_UNKNOWN,
    AUDIT_VALUE_TYPE_MISMATCH,
)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _object_type_compat(repo_type: str, dhis2_type: str) -> bool:
    a, b = _norm(repo_type), _norm(dhis2_type)
    if not a or not b:
        return True
    if a == b:
        return True
    return a.rstrip("s") == b.rstrip("s") or a + "s" == b or b + "s" == a


def classify_uid(
    *,
    repo_rows: list[dict[str, Any]],
    dhis2_obj: dict[str, Any] | None,
    previous_checksum: str | None = None,
    current_checksum: str | None = None,
    stage_uids_live: list[str] | None = None,
) -> list[str]:
    """Return one or more audit status labels for a UID."""
    statuses: list[str] = []
    if len(repo_rows) > 1:
        statuses.append(AUDIT_DUPLICATE_MAPPING)

    if not repo_rows and dhis2_obj:
        statuses.append(AUDIT_MISSING_REPO)
        return statuses or [AUDIT_UNKNOWN]

    if repo_rows and not dhis2_obj:
        statuses.append(AUDIT_MISSING_DHIS2)
        return statuses

    if not repo_rows and not dhis2_obj:
        return [AUDIT_UNKNOWN]

    primary = repo_rows[0]
    live = dhis2_obj or {}

    if primary.get("name") and live.get("name") and _norm(primary.get("name")) != _norm(live.get("name")):
        statuses.append(AUDIT_NAME_MISMATCH)

    repo_type = str(primary.get("object_type") or "")
    live_type = str(live.get("object_type") or live.get("kind") or "")
    if repo_type and live_type and not _object_type_compat(repo_type, live_type):
        statuses.append(AUDIT_OBJECT_TYPE_MISMATCH)

    repo_vt = str(primary.get("value_type") or (primary.get("extras") or {}).get("valueType") or "")
    live_vt = str(live.get("value_type") or live.get("valueType") or "")
    if repo_vt and live_vt and _norm(repo_vt) != _norm(live_vt):
        statuses.append(AUDIT_VALUE_TYPE_MISMATCH)

    repo_domain = str(primary.get("domain_type") or (primary.get("extras") or {}).get("domainType") or "")
    live_domain = str(live.get("domain_type") or live.get("domainType") or "")
    if repo_domain and live_domain and _norm(repo_domain) != _norm(live_domain):
        statuses.append(AUDIT_DOMAIN_TYPE_MISMATCH)

    repo_os = str(primary.get("option_set_uid") or (primary.get("extras") or {}).get("optionSet") or "")
    live_os = str(live.get("option_set_uid") or "")
    if isinstance(live.get("optionSet"), dict):
        live_os = live_os or str(live["optionSet"].get("id") or "")
    if repo_os and live_os and repo_os != live_os:
        statuses.append(AUDIT_OPTION_SET_MISMATCH)

    repo_stage = str(primary.get("program_stage_uid") or "")
    live_stages = [s for s in (stage_uids_live or []) if s]
    if repo_stage and live_stages and repo_stage not in live_stages:
        statuses.append(AUDIT_PROGRAM_STAGE_MISMATCH)

    if previous_checksum and current_checksum and previous_checksum != current_checksum:
        statuses.append(AUDIT_CHANGED_SINCE_SCAN)

    if not statuses:
        statuses.append(AUDIT_MATCHED)
    return statuses
