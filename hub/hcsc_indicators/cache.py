"""TTL caches for HCSC Indicator Summary (env-isolated)."""

from __future__ import annotations

from hub.dhis2_reports.cache import TtlCache

REGISTRY_CACHE = TtlCache(ttl_seconds=300, max_entries=8)
DESIGN_CACHE = TtlCache(ttl_seconds=600, max_entries=16)
OVERVIEW_CACHE = TtlCache(ttl_seconds=90, max_entries=64)
CATEGORY_CACHE = TtlCache(ttl_seconds=90, max_entries=128)
REPORT_CACHE = TtlCache(ttl_seconds=90, max_entries=64)
INFLIGHT: dict[str, object] = {}


def overview_cache_key(
    *,
    environment: str,
    period: str,
    org_unit: str,
    disaggregation: str,
) -> str:
    return "|".join(
        [
            "hcsc-overview",
            (environment or "").strip().lower(),
            (period or "").strip(),
            (org_unit or "").strip(),
            (disaggregation or "none").strip().lower(),
        ]
    )


def category_cache_key(
    *,
    environment: str,
    period: str,
    org_unit: str,
    disaggregation: str,
    section: str,
) -> str:
    return "|".join(
        [
            "hcsc-category",
            (environment or "").strip().lower(),
            (period or "").strip(),
            (org_unit or "").strip(),
            (disaggregation or "none").strip().lower(),
            (section or "").strip().lower(),
        ]
    )


def report_cache_key(
    *,
    environment: str,
    period: str,
    org_unit: str,
    disaggregation: str,
) -> str:
    return "|".join(
        [
            "hcsc-report",
            (environment or "").strip().lower(),
            (period or "").strip(),
            (org_unit or "").strip(),
            (disaggregation or "none").strip().lower(),
        ]
    )
