"""Models for synced DHIS2 standard reports (Phase 1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Common DHIS2 Report.type values. Others are stored but may be unsupported for HTML view.
KNOWN_REPORT_TYPES = frozenset(
    {
        "HTML",
        "JASPER_REPORT_TABLE",
        "JASPER_JDBC",
        "JASPERREPORT",  # legacy spelling seen on older instances
    }
)

# Types where /data.html is usually meaningful; Jasper JDBC may still fail without JDBC.
HTML_RENDER_TYPES = frozenset({"HTML", "JASPER_REPORT_TABLE", "JASPERREPORT"})

REPORT_LIST_FIELDS = (
    "id,name,displayName,type,reportParams,relativePeriods,"
    "reportTable[id,name],visualization[id,name],designContent,cacheStrategy,"
    "created,lastUpdated"
)

REPORT_DETAIL_FIELDS = REPORT_LIST_FIELDS


def favorite_key(environment: str, uid: str) -> str:
    return f"std:{environment}:{uid}"


def parse_favorite_key(key: str) -> tuple[str, str] | None:
    text = (key or "").strip()
    if not text.startswith("std:"):
        return None
    parts = text.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _as_bool_map(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, bool] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(value, bool):
            out[name] = value
        elif value in (1, "1", "true", "True", "yes"):
            out[name] = True
        elif value in (0, "0", "false", "False", "no", None, ""):
            out[name] = False
        else:
            out[name] = bool(value)
    return out


def _relative_period_labels(raw: Any) -> list[str]:
    flags = _as_bool_map(raw)
    return sorted(name for name, on in flags.items() if on)


def _report_params_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "param_reporting_month": False,
            "param_organisation_unit": False,
            "param_parent_organisation_unit": False,
            "raw_keys": [],
        }
    # DHIS2 uses both camelCase and legacy keys across versions.
    period = bool(
        raw.get("paramReportingMonth")
        or raw.get("reportingMonth")
        or raw.get("paramPeriod")
        or raw.get("reportingPeriod")
    )
    ou = bool(
        raw.get("paramOrganisationUnit")
        or raw.get("organisationUnit")
        or raw.get("paramOrgUnit")
    )
    parent = bool(
        raw.get("paramParentOrganisationUnit")
        or raw.get("parentOrganisationUnit")
        or raw.get("grandParentOrganisationUnit")
    )
    return {
        "param_reporting_month": period,
        "param_organisation_unit": ou,
        "param_parent_organisation_unit": parent,
        "raw_keys": sorted(str(k) for k in raw.keys()),
    }


def _data_source(raw: dict[str, Any]) -> dict[str, str]:
    for key in ("reportTable", "visualization", "reportTables"):
        value = raw.get(key)
        if isinstance(value, dict) and (value.get("id") or value.get("name")):
            return {
                "kind": key,
                "id": str(value.get("id") or ""),
                "name": str(value.get("displayName") or value.get("name") or ""),
            }
        if isinstance(value, list) and value and isinstance(value[0], dict):
            first = value[0]
            return {
                "kind": key,
                "id": str(first.get("id") or ""),
                "name": str(first.get("displayName") or first.get("name") or ""),
            }
    return {"kind": "", "id": "", "name": ""}


@dataclass
class SyncedStandardReport:
    environment: str
    uid: str
    name: str
    report_type: str = ""
    report_params: dict[str, Any] = field(default_factory=dict)
    relative_periods: list[str] = field(default_factory=list)
    relative_periods_raw: dict[str, bool] = field(default_factory=dict)
    data_source_kind: str = ""
    data_source_id: str = ""
    data_source_name: str = ""
    html_design_available: bool = False
    design_content_cached: bool = False
    cache_strategy: str = ""
    dhis2_version: str = ""
    last_synced_at: str = ""
    last_updated: str = ""
    created: str = ""
    unsupported_reason: str = ""
    favorite: bool = False

    @property
    def id(self) -> str:
        return favorite_key(self.environment, self.uid)

    @property
    def needs_period(self) -> bool:
        return bool(self.report_params.get("param_reporting_month")) or bool(
            self.relative_periods
        )

    @property
    def needs_org_unit(self) -> bool:
        return bool(
            self.report_params.get("param_organisation_unit")
            or self.report_params.get("param_parent_organisation_unit")
        )

    @property
    def render_supported(self) -> bool:
        if self.unsupported_reason:
            return False
        if not self.report_type:
            return True  # unknown — try
        return self.report_type.upper() in HTML_RENDER_TYPES or self.html_design_available

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "type": self.report_type or "UNKNOWN",
            "environment": self.environment,
            "report_parameters": dict(self.report_params),
            "relative_periods": list(self.relative_periods),
            "data_source": {
                "kind": self.data_source_kind,
                "id": self.data_source_id,
                "name": self.data_source_name,
            },
            "html_design_available": self.html_design_available,
            "design_content_cached": self.design_content_cached,
            "cache_strategy": self.cache_strategy,
            "dhis2_version": self.dhis2_version,
            "last_synced_at": self.last_synced_at,
            "last_updated": self.last_updated,
            "created": self.created,
            "needs_period": self.needs_period,
            "needs_org_unit": self.needs_org_unit,
            "render_supported": self.render_supported,
            "unsupported_reason": self.unsupported_reason,
            "favorite": self.favorite,
            "source_of_truth": "dhis2",
        }


def normalize_report_payload(
    raw: dict[str, Any],
    *,
    environment: str,
    dhis2_version: str = "",
    last_synced_at: str = "",
    cache_design: bool = False,
) -> SyncedStandardReport:
    uid = str(raw.get("id") or raw.get("uid") or "").strip()
    name = str(raw.get("displayName") or raw.get("name") or uid).strip()
    rtype = str(raw.get("type") or raw.get("reportType") or "").strip().upper()
    design = raw.get("designContent")
    has_design = isinstance(design, str) and bool(design.strip())
    params = _report_params_summary(raw.get("reportParams") or raw.get("reportParameters"))
    rel_raw = _as_bool_map(raw.get("relativePeriods"))
    source = _data_source(raw)
    unsupported = ""
    if rtype and rtype not in KNOWN_REPORT_TYPES and not has_design:
        unsupported = f"Unrecognized report type {rtype}; Open in DHIS2 may still work."
    elif rtype == "JASPER_JDBC":
        unsupported = (
            "Jasper JDBC reports often require server-side JDBC context; "
            "prefer Open in DHIS2."
        )

    return SyncedStandardReport(
        environment=environment,
        uid=uid,
        name=name,
        report_type=rtype,
        report_params=params,
        relative_periods=_relative_period_labels(rel_raw),
        relative_periods_raw=rel_raw,
        data_source_kind=source["kind"],
        data_source_id=source["id"],
        data_source_name=source["name"],
        html_design_available=has_design or rtype == "HTML",
        design_content_cached=bool(cache_design and has_design),
        cache_strategy=str(raw.get("cacheStrategy") or ""),
        dhis2_version=dhis2_version,
        last_synced_at=last_synced_at,
        last_updated=str(raw.get("lastUpdated") or ""),
        created=str(raw.get("created") or ""),
        unsupported_reason=unsupported,
    )


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_json(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text or "")
    except json.JSONDecodeError:
        return default if default is not None else {}
