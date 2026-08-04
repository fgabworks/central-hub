"""National HCSC–RF regional roll-up (aggregate regions; never average %).

National totals are built from regional reports using the same registry mapping:
  - sum counts
  - sum numerators / denominators
  - recompute percentage from aggregated N/D (never average regional %)
"""

from __future__ import annotations

import threading
import time
from typing import Any


STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CACHED = "cached"


def indicator_version(registry: dict[str, Any] | None) -> str:
    """Stable-ish version token for regional/national cache keys."""
    reg = registry or {}
    uid = str(reg.get("npmo_report_uid") or "registry").strip() or "registry"
    n = len(reg.get("indicators") or [])
    return f"{uid}:{n}"


def _as_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _sum_optional(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return float(sum(present))


def aggregate_indicator_mapped(
    *,
    result_type: str | None,
    regional_mapped: list[dict[str, Any]],
    source_uid: str | None = None,
) -> dict[str, Any]:
    """Roll one indicator across regions into a national mapped shape."""
    rt = (result_type or "").strip().lower()
    counts = [_as_float(m.get("count")) for m in regional_mapped]
    nums = [_as_float(m.get("numerator")) for m in regional_mapped]
    dens = [_as_float(m.get("denominator")) for m in regional_mapped]

    count = _sum_optional(counts)
    numerator = _sum_optional(nums)
    denominator = _sum_optional(dens)
    percentage = None

    if rt in {"percentage", "numerator_denominator_percentage", "ratio"}:
        if numerator is not None and denominator not in (None, 0.0):
            percentage = (numerator / denominator) * 100.0
        # Never average regional percentages when N/D companions are missing.
    elif rt in {"count", "status", "derived_status", "disaggregation", ""}:
        # Counts / status-like values: sum regional counts when present.
        pass
    else:
        pass

    return {
        "count": count,
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
        "source_uid": source_uid,
        "rollup": {
            "regions_contributed": len(regional_mapped),
            "aggregation": "sum_nd_recompute_pct"
            if rt in {"percentage", "numerator_denominator_percentage", "ratio"}
            else "sum_count",
        },
    }


def aggregate_result_rows(
    *,
    indicators: list[dict[str, Any]],
    regional_results_by_key: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Produce national mapped rows (pre-_build_result_row) keyed like adapter output."""
    out: list[dict[str, Any]] = []
    for ind in indicators:
        key = ind["key"]
        region_rows = regional_results_by_key.get(key) or []
        mapped_parts = [
            {
                "count": r.get("count"),
                "numerator": r.get("numerator"),
                "denominator": r.get("denominator"),
                "percentage": r.get("percentage"),
                "source_uid": r.get("source_uid"),
            }
            for r in region_rows
        ]
        source_uid = (ind.get("dhis2_uids") or {}).get("value")
        if region_rows and region_rows[0].get("source_uid"):
            source_uid = region_rows[0].get("source_uid") or source_uid
        mapped = aggregate_indicator_mapped(
            result_type=ind.get("result_type"),
            regional_mapped=mapped_parts,
            source_uid=source_uid,
        )
        out.append(
            {
                "indicator_key": key,
                "mapped": mapped,
                "adapter": "dhis2_analytics",
                "retrieval_method": "Regional roll-up (sum N/D; recompute %)",
            }
        )
    return out


def verify_national_equals_region_sums(
    national_results: list[dict[str, Any]],
    regional_result_lists: list[list[dict[str, Any]]],
    *,
    tolerance: float = 1e-6,
) -> list[dict[str, Any]]:
    """Return mismatch rows (empty if National == sum of regions for count/N/D)."""
    by_key_regions: dict[str, list[dict[str, Any]]] = {}
    for rows in regional_result_lists:
        for row in rows:
            by_key_regions.setdefault(row.get("indicator_key") or "", []).append(row)

    mismatches: list[dict[str, Any]] = []
    for nat in national_results:
        key = nat.get("indicator_key") or ""
        parts = by_key_regions.get(key) or []
        exp_count = _sum_optional([_as_float(r.get("count")) for r in parts])
        exp_num = _sum_optional([_as_float(r.get("numerator")) for r in parts])
        exp_den = _sum_optional([_as_float(r.get("denominator")) for r in parts])
        got_count = _as_float(nat.get("count"))
        got_num = _as_float(nat.get("numerator"))
        got_den = _as_float(nat.get("denominator"))

        def _close(a: float | None, b: float | None) -> bool:
            if a is None and b is None:
                return True
            if a is None or b is None:
                return False
            return abs(a - b) <= tolerance

        if not (
            _close(got_count, exp_count)
            and _close(got_num, exp_num)
            and _close(got_den, exp_den)
        ):
            mismatches.append(
                {
                    "indicator_key": key,
                    "expected": {"count": exp_count, "numerator": exp_num, "denominator": exp_den},
                    "got": {"count": got_count, "numerator": got_num, "denominator": got_den},
                }
            )
    return mismatches


class NationalRollupProgress:
    """In-memory per-scope progress for National regional generation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def job_key(*, environment: str, period: str, org_unit: str, version: str) -> str:
        return "|".join(
            [
                "national-rollup",
                (environment or "").strip().lower(),
                (period or "").strip(),
                (org_unit or "").strip(),
                (version or "").strip(),
            ]
        )

    def begin(
        self,
        key: str,
        *,
        regions: list[dict[str, Any]],
        environment: str,
        period: str,
        org_unit: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = {
                "key": key,
                "environment": environment,
                "period": period,
                "org_unit": org_unit,
                "status": "running",
                "started_at": time.time(),
                "updated_at": time.time(),
                "completed_at": None,
                "regions": [
                    {
                        "uid": r.get("uid") or r.get("id"),
                        "name": r.get("name") or r.get("uid"),
                        "status": STATUS_PENDING,
                        "error": None,
                        "cache_hit": False,
                        "latency_ms": None,
                    }
                    for r in regions
                    if r.get("uid") or r.get("id")
                ],
                "error": None,
                "region_payloads": {},
            }
            self._jobs[key] = job
            return self.snapshot(key) or job

    def store_region_payload(self, key: str, region_uid: str, payload: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return
            payloads = job.setdefault("region_payloads", {})
            payloads[region_uid] = payload
            job["updated_at"] = time.time()

    def region_payloads(self, key: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return {}
            return dict(job.get("region_payloads") or {})

    def set_region(
        self,
        key: str,
        region_uid: str,
        *,
        status: str,
        error: str | None = None,
        cache_hit: bool = False,
        latency_ms: int | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return
            for row in job.get("regions") or []:
                if row.get("uid") == region_uid:
                    row["status"] = status
                    row["error"] = error
                    row["cache_hit"] = bool(cache_hit)
                    if latency_ms is not None:
                        row["latency_ms"] = latency_ms
                    break
            job["updated_at"] = time.time()

    def finish(self, key: str, *, status: str = "completed", error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return
            job["status"] = status
            job["error"] = error
            job["completed_at"] = time.time()
            job["updated_at"] = job["completed_at"]

    def snapshot(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(key)
            if not job:
                return None
            regions = list(job.get("regions") or [])
            counts = {
                STATUS_PENDING: 0,
                STATUS_PROCESSING: 0,
                STATUS_COMPLETED: 0,
                STATUS_FAILED: 0,
                STATUS_CACHED: 0,
            }
            for r in regions:
                st = r.get("status") or STATUS_PENDING
                if st == STATUS_CACHED:
                    counts[STATUS_COMPLETED] += 1
                    counts[STATUS_CACHED] += 1
                elif st in counts:
                    counts[st] += 1
                else:
                    counts[STATUS_PENDING] += 1
            out = dict(job)
            out["regions"] = [dict(r) for r in regions]
            out["counts"] = counts
            out["total_regions"] = len(regions)
            out["done_regions"] = counts[STATUS_COMPLETED] + counts[STATUS_FAILED]
            return out


# Process-wide progress (env-isolated via job key).
ROLLUP_PROGRESS = NationalRollupProgress()
