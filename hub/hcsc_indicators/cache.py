"""TTL caches for HCSC Indicator Summary (env-isolated)."""

from __future__ import annotations

import os

from hub.dhis2_reports.cache import TtlCache


def _ttl(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(30, int(raw))
    except ValueError:
        return default


REGISTRY_CACHE = TtlCache(ttl_seconds=300, max_entries=8)
DESIGN_CACHE = TtlCache(ttl_seconds=600, max_entries=16)
OVERVIEW_CACHE = TtlCache(ttl_seconds=90, max_entries=64)
CATEGORY_CACHE = TtlCache(ttl_seconds=90, max_entries=128)
# Regional + National reports: longer TTL so National roll-up can reuse region hits.
REPORT_CACHE = TtlCache(
    ttl_seconds=_ttl("HCSC_REPORT_CACHE_TTL_SECONDS", 600),
    max_entries=max(64, _ttl("HCSC_REPORT_CACHE_MAX_ENTRIES", 256)),
)
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
    geographic_breakdown: str = "none",
    indicator_version: str = "",
) -> str:
    return "|".join(
        [
            "hcsc-report",
            (environment or "").strip().lower(),
            (period or "").strip(),
            (org_unit or "").strip(),
            (disaggregation or "none").strip().lower(),
            (geographic_breakdown or "none").strip().lower(),
            (indicator_version or "v0").strip(),
        ]
    )
