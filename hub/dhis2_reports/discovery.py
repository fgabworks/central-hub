"""Discover which parameters a standard report actually needs.

Does not trust only the boolean reportParams flags (often all false on HTML designs).
Inspects native report metadata, relative periods, and HTML design markers.
"""

from __future__ import annotations

import re
from typing import Any

_PERIOD_MARKERS = re.compile(
    r"(reporting\s*period|paramReportingMonth|relativePeriod|\{+\s*period\s*\}+|"
    r"data-period|pe\s*=|period\s*:|YYYYQn|YYYYMM)",
    re.IGNORECASE,
)
_QUARTER_MARKERS = re.compile(
    r"(quarter|YYYYQn|Q[1-4]\b|quarterly)",
    re.IGNORECASE,
)
_OU_MARKERS = re.compile(
    r"(organisation\s*unit|organization\s*unit|org\s*unit|orgUnit|"
    r"paramOrganisationUnit|\{+\s*ou\s*\}+|data-ou\b)",
    re.IGNORECASE,
)
_PARENT_OU_MARKERS = re.compile(
    r"(parent\s*organisation\s*unit|parentOrganisationUnit|grandParentOrganisationUnit)",
    re.IGNORECASE,
)


def discover_report_parameters(
    report: Any,
    *,
    design_html: str = "",
) -> dict[str, Any]:
    """Return UI guidance for period / OU controls.

    Sources (merged):
    - report.report_params boolean flags
    - relative_periods list
    - HTML design content heuristics
    - report type defaults for HTML designs (optional selectors + warning)
    """
    params = dict(getattr(report, "report_params", None) or {})
    relative = list(getattr(report, "relative_periods", None) or [])
    rtype = str(getattr(report, "report_type", "") or "").upper()
    design = design_html or ""

    flag_period = bool(params.get("param_reporting_month"))
    flag_ou = bool(params.get("param_organisation_unit"))
    flag_parent = bool(params.get("param_parent_organisation_unit"))
    has_relative = bool(relative)

    design_period = bool(design and _PERIOD_MARKERS.search(design))
    design_quarter = bool(design and _QUARTER_MARKERS.search(design))
    design_ou = bool(design and _OU_MARKERS.search(design))
    design_parent = bool(design and _PARENT_OU_MARKERS.search(design))

    needs_period = flag_period or has_relative or design_period
    needs_org_unit = flag_ou or design_ou
    needs_parent_ou = flag_parent or design_parent
    needs_quarter = design_quarter or (needs_period and design_quarter)

    sources: list[str] = []
    if flag_period or flag_ou or flag_parent:
        sources.append("reportParams")
    if has_relative:
        sources.append("relativePeriods")
    if design and (design_period or design_ou or design_parent or design_quarter):
        sources.append("designContent")

    incomplete = not sources
    # HTML reports with no declared params: show optional selectors + warning.
    show_period = needs_period or incomplete
    show_org_unit = needs_org_unit or needs_parent_ou or incomplete

    rel_l = [str(k).lower() for k in relative]
    preferred_period_type = "monthly"
    if needs_quarter or any("quarter" in k for k in rel_l):
        preferred_period_type = "quarterly"
    elif any(("year" in k and "month" not in k) for k in rel_l):
        preferred_period_type = "yearly"
    elif has_relative:
        preferred_period_type = "relative"
    elif incomplete:
        preferred_period_type = "quarterly"

    warning = ""
    if incomplete:
        warning = (
            "Parameter requirements could not be fully determined from DHIS2 metadata. "
            "Period and organisation unit are shown as optional — set them if the report is empty."
        )
    elif rtype == "HTML" and not (flag_period or flag_ou) and (design_period or design_ou):
        warning = "Requirements inferred from HTML design markers (reportParams flags were empty)."

    return {
        "needs_period": bool(needs_period),
        "needs_quarter": bool(needs_quarter),
        "needs_org_unit": bool(needs_org_unit),
        "needs_parent_organisation_unit": bool(needs_parent_ou),
        "needs_relative_period": bool(has_relative),
        "show_period": bool(show_period),
        "show_org_unit": bool(show_org_unit),
        "period_required": bool(needs_period and not incomplete),
        "org_unit_required": bool((needs_org_unit or needs_parent_ou) and not incomplete),
        "preferred_period_type": preferred_period_type,
        "relative_periods": relative,
        "sources": sources,
        "incomplete": incomplete,
        "warning": warning,
        "summary": _summary_line(
            needs_period=needs_period,
            needs_quarter=needs_quarter,
            needs_ou=needs_org_unit,
            needs_parent=needs_parent_ou,
            incomplete=incomplete,
        ),
    }


def _summary_line(
    *,
    needs_period: bool,
    needs_quarter: bool,
    needs_ou: bool,
    needs_parent: bool,
    incomplete: bool,
) -> str:
    if incomplete:
        return "Optional (discovery incomplete)"
    parts: list[str] = []
    if needs_quarter:
        parts.append("quarter")
    elif needs_period:
        parts.append("period")
    if needs_ou:
        parts.append("organisation unit")
    if needs_parent:
        parts.append("parent OU")
    return ", ".join(parts) if parts else "None declared"
