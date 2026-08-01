"""User-facing branding for Central Hub HCSC–RF (display only)."""

from __future__ import annotations

PAGE_TITLE = "Central Hub HCSC–RF"
PAGE_SUBTITLE = (
    "Indicators, Sources, and Validation — "
    "Household Convergence Scorecard and Results Framework."
)
PAGE_MEANING = "Household Convergence Scorecard and Results Framework"
NAV_LABEL = "HCSC–RF"
MODULE_SHORT = "HCSC–RF"

EXPORT_REPORT = "Central Hub HCSC–RF Report"
EXPORT_VALIDATION = "Central Hub HCSC–RF Validation"
EXPORT_EVIDENCE = "Central Hub HCSC–RF Evidence Package"

# Validation / Compare Sources terminology (display labels).
COMPARE_SOURCES = "Compare Sources"
REVIEW_DIFFERENCES = "Review Differences"
SOURCE_DHIS2_REPORT = "DHIS2 Report Result"
SOURCE_DHIS2_ANALYTICS = "DHIS2 Analytics Result"
SOURCE_APPROVED_DATA = "Approved Data Result"
SOURCE_PROCESSED_DATA = "Processed Data Result"
SOURCE_APPROVED_SQL = "Approved SQL Result"

COMPARISON_SOURCE_LABELS = {
    "analytics_num_den": SOURCE_DHIS2_ANALYTICS,
    "dhis2_analytics": SOURCE_DHIS2_ANALYTICS,
    "evidence_snapshot": SOURCE_APPROVED_DATA,
    "approved_data": SOURCE_APPROVED_DATA,
    "approved_sql": SOURCE_APPROVED_SQL,
    "connected_capability": SOURCE_PROCESSED_DATA,
    "processed_data": SOURCE_PROCESSED_DATA,
    "npmo_or_snapshot": SOURCE_DHIS2_REPORT,
    "dhis2_report": SOURCE_DHIS2_REPORT,
    "unresolved": "Unresolved",
}

CLASSIFICATIONS = frozenset({"HCSC", "RF", "HCSC + RF", "unresolved"})

DISPLAY_GROUPS = (
    "overview",
    "eligible_beneficiaries",
    "hcsc",
    "results_framework",
    "maternal_health",
    "child_nutrition_health",
    "household_wash_sbc",
    "food_security",
    "unresolved",
)

DISPLAY_GROUP_LABELS = {
    "overview": "Overview",
    "eligible_beneficiaries": "Eligible Beneficiaries",
    "hcsc": "HCSC",
    "results_framework": "Results Framework",
    "maternal_health": "Maternal Health",
    "child_nutrition_health": "Child Nutrition & Health",
    "household_wash_sbc": "Household / WASH / SBC",
    "food_security": "Food Security",
    "unresolved": "Unresolved",
}

# Domain groups nested under Results Framework in the Indicator Summary UI.
RF_DOMAIN_GROUPS = frozenset(
    {
        "maternal_health",
        "child_nutrition_health",
        "household_wash_sbc",
        "food_security",
    }
)


def comparison_source_label(source_id: str | None) -> str:
    key = (source_id or "").strip().lower()
    return COMPARISON_SOURCE_LABELS.get(key, source_id or "—")


def export_package_meta(
    *,
    kind: str,
    environment: str,
    period: str,
    org_unit: str,
    generated_at: str,
    source_versions: dict | None = None,
) -> dict:
    titles = {
        "report": EXPORT_REPORT,
        "validation": EXPORT_VALIDATION,
        "evidence": EXPORT_EVIDENCE,
    }
    return {
        "package_name": titles.get(kind, EXPORT_REPORT),
        "module": PAGE_TITLE,
        "module_meaning": PAGE_MEANING,
        "environment": environment,
        "period": period,
        "organisation_unit": org_unit,
        "generated_at": generated_at,
        "source_versions": source_versions or {},
    }
