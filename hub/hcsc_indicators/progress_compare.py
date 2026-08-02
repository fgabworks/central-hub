"""Progress NPMO report comparison — structured analytics only (no HTML scrape/OCR)."""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.cache import TtlCache
from hub.dhis2_reports.security import (
    ReportSecurityError,
    validate_environment,
    validate_org_unit,
    validate_period,
)
from hub.dhis2_reports.store import ReportsStore
from hub.hcsc_indicators.analytics import fetch_analytics_batch
from hub.hcsc_indicators.quarters import assert_allowed_quarter, allowed_quarter_ids
from hub.hcsc_indicators.registry import load_registry
from hub.live_data_export.formats import write_xlsx
from hub.settings import ROOT_DIR


def sanitize_report_snapshot(raw: str | None) -> str:
    """Allowlist tables/structure for report snapshot; strip scripts and handlers."""
    text = raw or ""
    # Drop script/style blocks first
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", "", text)
    # Strip on* handlers and javascript: URLs
    text = re.sub(r"(?i)\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text)
    text = re.sub(r"(?i)(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r"\1=\"#\"", text)
    # Remove iframe/object/embed
    text = re.sub(r"(?is)</?(iframe|object|embed|link|meta)[^>]*>", "", text)
    return text

COMPARE_CACHE = TtlCache(ttl_seconds=120, max_entries=64)
INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()

STATUSES = (
    "Exact Match",
    "Rounding Difference",
    "Expected Logic Difference",
    "Unexplained Difference",
    "Incompatible Definitions",
    "Mapping Unresolved",
    "Source Unavailable",
    "Not Comparable",
)

_IDENT_UID = re.compile(r"^[A-Za-z0-9]{11}$")


@dataclass(frozen=True)
class ProgressReportMeta:
    key: str
    display_name: str
    dhis2_report_uid: str
    dhis2_report_name: str
    report_version: str
    extraction_method: str
    repository_evidence: list[str]
    indicators: list[dict[str, Any]]
    not_comparable: list[str]
    raw: dict[str, Any]


@lru_cache(maxsize=1)
def load_progress_comparison_config(path: str | None = None) -> ProgressReportMeta:
    cfg_path = Path(path) if path else (ROOT_DIR / "config" / "hcsc_progress_comparison.yaml")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    report = raw.get("report") or {}
    uid = str(report.get("dhis2_report_uid") or "").strip()
    if not _IDENT_UID.match(uid):
        raise ReportSecurityError("Invalid progress report UID in registry.", code="forbidden")
    return ProgressReportMeta(
        key=str(report.get("key") or "progress_npmo"),
        display_name=str(report.get("display_name") or ""),
        dhis2_report_uid=uid,
        dhis2_report_name=str(report.get("dhis2_report_name") or ""),
        report_version=str(report.get("report_version") or uid),
        extraction_method=str(report.get("extraction_method") or "structured_analytics"),
        repository_evidence=[str(x) for x in (report.get("repository_evidence") or [])],
        indicators=[i for i in (raw.get("indicators") or []) if isinstance(i, dict)],
        not_comparable=[str(x) for x in (raw.get("not_comparable") or [])],
        raw=raw,
    )


def clear_progress_config_cache() -> None:
    load_progress_comparison_config.cache_clear()


def quarter_to_months(period: str) -> str:
    """Convert YYYYQn → YYYYMM;YYYYMM;YYYYMM (Progress report period style)."""
    pe = validate_period(period, required=True)
    if len(pe) == 6 and pe[4] == "Q":
        year = int(pe[:4])
        q = int(pe[5])
        start = (q - 1) * 3 + 1
        months = [f"{year}{m:02d}" for m in range(start, start + 3)]
        return ";".join(months)
    # Already month-joined
    if ";" in pe:
        return pe
    return pe


def previous_quarter(period: str) -> str:
    pe = validate_period(period, required=True)
    if len(pe) != 6 or pe[4] != "Q":
        raise ReportSecurityError("previous_quarter requires YYYYQn.", code="invalid_period")
    year = int(pe[:4])
    q = int(pe[5])
    if q == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{q - 1}"


def is_q3(period: str) -> bool:
    pe = validate_period(period, required=True)
    return len(pe) == 6 and pe[4] == "Q" and pe[5] == "3"


def cache_key(*, environment: str, report_uid: str, period: str, org_unit: str, report_version: str) -> str:
    return "|".join(
        [
            "progress-compare",
            (environment or "").strip().lower(),
            report_uid,
            (period or "").strip(),
            (org_unit or "").strip(),
            report_version,
        ]
    )


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def compare_values(
    *,
    result_type: str,
    report_value: float | None,
    hcsc_value: float | None,
    mapping_status: str,
    report_num: float | None = None,
    report_den: float | None = None,
    hcsc_num: float | None = None,
    hcsc_den: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if mapping_status == "Not Comparable":
        return {
            "status": "Not Comparable",
            "explanation": notes or "Not comparable by registry.",
            "result_diff": None,
            "num_diff": None,
            "den_diff": None,
        }
    if mapping_status == "Unresolved":
        return {
            "status": "Mapping Unresolved",
            "explanation": notes or "No verified HCSC–RF mapping.",
            "result_diff": None,
            "num_diff": None,
            "den_diff": None,
        }
    if hcsc_value is None and mapping_status != "Verified":
        # Partial with missing HCSC still unresolved mapping path
        pass
    if hcsc_value is None:
        return {
            "status": "Source Unavailable",
            "explanation": notes or "HCSC–RF value unavailable.",
            "result_diff": None,
            "num_diff": None,
            "den_diff": None,
        }
    if report_value is None:
        return {
            "status": "Source Unavailable",
            "explanation": "DHIS2 report-side value unavailable.",
            "result_diff": None,
            "num_diff": None,
            "den_diff": None,
        }

    result_diff = float(report_value) - float(hcsc_value)
    num_diff = None
    den_diff = None
    if report_num is not None and hcsc_num is not None:
        num_diff = float(report_num) - float(hcsc_num)
    if report_den is not None and hcsc_den is not None:
        den_diff = float(report_den) - float(hcsc_den)

    abs_diff = abs(result_diff)
    if result_type == "percentage":
        if abs_diff <= 1e-9:
            status = "Exact Match"
        elif abs_diff <= 0.15:
            status = "Rounding Difference"
        elif mapping_status == "Partial":
            status = "Expected Logic Difference"
        else:
            status = "Unexplained Difference"
        explanation = (
            f"Report {report_value:.2f}% vs HCSC {hcsc_value:.2f}% (Δ {result_diff:+.2f} pp). "
            + (notes or "")
        ).strip()
    else:
        if abs_diff <= 1e-9:
            status = "Exact Match"
        elif abs_diff < 1:
            status = "Rounding Difference"
        elif mapping_status == "Partial":
            status = "Expected Logic Difference"
        else:
            status = "Unexplained Difference"
        explanation = (
            f"Report {report_value} vs HCSC {hcsc_value} (Δ {result_diff:+.4g}). "
            + (notes or "")
        ).strip()

    return {
        "status": status,
        "explanation": explanation,
        "result_diff": result_diff if result_type != "percentage" else result_diff,
        "result_diff_pp": result_diff if result_type == "percentage" else None,
        "num_diff": num_diff,
        "den_diff": den_diff,
    }


class ProgressCompareService:
    def __init__(
        self,
        *,
        client_factory: Callable[[str], Dhis2Client],
        reports_store: ReportsStore | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._store = reports_store or ReportsStore()
        self._config_path = str(config_path) if config_path else None
        self._lock = threading.RLock()

    def meta(self) -> ProgressReportMeta:
        return load_progress_comparison_config(self._config_path)

    def bootstrap(self) -> dict[str, Any]:
        meta = self.meta()
        reg = load_registry()
        return {
            "ok": True,
            "page_title": meta.display_name,
            "page_subtitle": "Compare DHIS2 report output with Central Hub HCSC–RF results",
            "breadcrumb": ["Reports & Comparisons", "Compare with DHIS2 Report"],
            "report": {
                "uid": meta.dhis2_report_uid,
                "name": meta.dhis2_report_name,
                "version": meta.report_version,
                "extraction_method": meta.extraction_method,
                "evidence": meta.repository_evidence,
            },
            "periods": [
                {"id": q, "label": q} for q in allowed_quarter_ids(reg)
            ],
            "indicator_count": len(meta.indicators),
            "mapping_summary": self._mapping_summary(meta),
            "central_hub_report_id": "hcsr-progress-npmo-v1",
        }

    def compare(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        force_refresh: bool = False,
        request_id: str = "",
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        pe = assert_allowed_quarter(period, allowed_quarter_ids(load_registry()))
        ou = validate_org_unit(org_unit, required=True)
        meta = self.meta()
        key = cache_key(
            environment=env,
            report_uid=meta.dhis2_report_uid,
            period=pe,
            org_unit=ou,
            report_version=meta.report_version,
        )

        if not force_refresh:
            cached = COMPARE_CACHE.get(key)
            if cached is not None:
                out = dict(cached)
                out["cache"] = {"hit": True, "key": key}
                out["request_id"] = request_id or out.get("request_id")
                return out

        with _INFLIGHT_LOCK:
            if key in INFLIGHT and not force_refresh:
                raise ReportSecurityError(
                    "Duplicate comparison already in progress for this scope.",
                    code="duplicate_request",
                )
            event = threading.Event()
            INFLIGHT[key] = event

        started = time.perf_counter()
        try:
            payload = self._run_compare(env=env, period=pe, org_unit=ou, meta=meta, request_id=request_id)
            payload["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            payload["cache"] = {"hit": False, "key": key}
            COMPARE_CACHE.set(key, payload)
            return payload
        finally:
            with _INFLIGHT_LOCK:
                INFLIGHT.pop(key, None)
                event.set()

    def snapshot_html(self, *, environment: str) -> dict[str, Any]:
        env = validate_environment(environment)
        meta = self.meta()
        raw = self._store.get_synced_design_content(env, meta.dhis2_report_uid)
        if not raw:
            # Stage may hold design when Live sync missing
            raw = self._store.get_synced_design_content("stage", meta.dhis2_report_uid)
        if not raw:
            return {
                "ok": False,
                "error": "Synced design HTML not available. Sync Stage/Live standard reports first.",
                "uid": meta.dhis2_report_uid,
            }
        # Sanitize: strip scripts/handlers; never execute report JS in hub
        safe = sanitize_report_snapshot(raw)
        return {
            "ok": True,
            "uid": meta.dhis2_report_uid,
            "bytes": len(raw),
            "sanitized_html": safe,
            "note": "Scripts and event handlers removed. Snapshot is display-only evidence.",
        }

    def export(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        format: str,
        force_refresh: bool = False,
    ) -> tuple[bytes, str, str]:
        data = self.compare(
            environment=environment,
            period=period,
            org_unit=org_unit,
            force_refresh=force_refresh,
        )
        fmt = (format or "json").lower()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"progress_npmo_compare_{environment}_{period}_{org_unit}_{stamp}"
        if fmt == "json":
            body = json.dumps(data, indent=2, ensure_ascii=True).encode("utf-8")
            return body, f"{base}.json", "application/json"
        rows = data.get("indicators") or []
        columns = [
            "key",
            "report_label",
            "mapping_status",
            "comparison_status",
            "result_type",
            "dhis2_source_type",
            "dhis2_uid",
            "report_result",
            "report_numerator",
            "report_denominator",
            "hcsc_key",
            "hcsc_uid",
            "hcsc_result",
            "hcsc_numerator",
            "hcsc_denominator",
            "result_diff",
            "num_diff",
            "den_diff",
            "explanation",
        ]
        table = []
        for r in rows:
            table.append(
                [
                    r.get("key"),
                    r.get("report_label"),
                    r.get("mapping_status"),
                    r.get("comparison_status"),
                    r.get("result_type"),
                    r.get("dhis2_source_type"),
                    r.get("dhis2_uid"),
                    r.get("report", {}).get("result"),
                    r.get("report", {}).get("numerator"),
                    r.get("report", {}).get("denominator"),
                    r.get("hcsc_indicator_key"),
                    r.get("hcsc_source_uid"),
                    r.get("hcsc", {}).get("result"),
                    r.get("hcsc", {}).get("numerator"),
                    r.get("hcsc", {}).get("denominator"),
                    r.get("diffs", {}).get("result_diff"),
                    r.get("diffs", {}).get("num_diff"),
                    r.get("diffs", {}).get("den_diff"),
                    r.get("explanation"),
                ]
            )
        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(columns)
            w.writerows(table)
            return buf.getvalue().encode("utf-8"), f"{base}.csv", "text/csv"
        if fmt == "xlsx":
            path = ROOT_DIR / "data" / "tmp_progress_compare.xlsx"
            path.parent.mkdir(parents=True, exist_ok=True)
            write_xlsx(path, columns, table)
            return path.read_bytes(), f"{base}.xlsx", (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        raise ReportSecurityError(f"Unsupported export format: {format}", code="forbidden")

    def _run_compare(
        self,
        *,
        env: str,
        period: str,
        org_unit: str,
        meta: ProgressReportMeta,
        request_id: str,
    ) -> dict[str, Any]:
        client = self._client_factory(env)
        pe_months = quarter_to_months(period)
        prev_pe = previous_quarter(period)
        prev_months = quarter_to_months(prev_pe)

        dx_current = sorted(
            {
                str(i.get("dhis2_uid"))
                for i in meta.indicators
                if i.get("dhis2_uid") and _IDENT_UID.match(str(i.get("dhis2_uid")))
            }
        )
        # Always include N/D companions for CLIENT percentages
        for uid in ("BSqDSIpHhoT", "fxmvSiKfEpn", "mRQ1mcOrUER"):
            if uid not in dx_current:
                dx_current.append(uid)

        try:
            current_batch = fetch_analytics_batch(
                client,
                dx_uids=dx_current,
                period=period,  # YYYYQn accepted by analytics; report uses months equivalently
                org_unit=org_unit,
                include_num_den=True,
            )
            prev_batch = fetch_analytics_batch(
                client,
                dx_uids=["fxmvSiKfEpn"],
                period=prev_pe,
                org_unit=org_unit,
                include_num_den=False,
            )
        except Dhis2Error as exc:
            raise ReportSecurityError(str(exc), code="dhis2_error") from exc

        values = current_batch.get("values") or {}
        num_den = current_batch.get("num_den") or {}
        prev_values = prev_batch.get("values") or {}

        # HCSC side: analytics for mapped UIDs / IND
        hcsc_uids = sorted(
            {
                str(i.get("hcsc_source_uid"))
                for i in meta.indicators
                if i.get("hcsc_source_uid") and _IDENT_UID.match(str(i.get("hcsc_source_uid")))
            }
        )
        # Include N/D for completion IND
        for uid in ("BSqDSIpHhoT", "fxmvSiKfEpn", "StDJxe7tIiS"):
            if uid not in hcsc_uids:
                hcsc_uids.append(uid)
        try:
            hcsc_batch = fetch_analytics_batch(
                client,
                dx_uids=hcsc_uids,
                period=period,
                org_unit=org_unit,
                include_num_den=True,
            )
        except Dhis2Error as exc:
            raise ReportSecurityError(str(exc), code="dhis2_error") from exc
        hcsc_values = hcsc_batch.get("values") or {}
        hcsc_nd = hcsc_batch.get("num_den") or {}

        eligible = _as_float(values.get("fxmvSiKfEpn"))
        approved = _as_float(values.get("BSqDSIpHhoT"))
        prev_eligible = _as_float(prev_values.get("fxmvSiKfEpn"))
        pct_validated = (
            _round_pct((approved / eligible) * 100.0) if eligible not in (None, 0) and approved is not None else None
        )
        if is_q3(period):
            pct_coverage = pct_validated
        else:
            pct_coverage = (
                _round_pct((approved / prev_eligible) * 100.0)
                if prev_eligible not in (None, 0) and approved is not None
                else None
            )

        rows: list[dict[str, Any]] = []
        for ind in meta.indicators:
            row = self._build_indicator_row(
                ind,
                values=values,
                prev_eligible=prev_eligible,
                pct_validated=pct_validated,
                pct_coverage=pct_coverage,
                eligible=eligible,
                approved=approved,
                hcsc_values=hcsc_values,
                hcsc_nd=hcsc_nd,
            )
            rows.append(row)

        status_counts = {s: 0 for s in STATUSES}
        for r in rows:
            status_counts[r["comparison_status"]] = status_counts.get(r["comparison_status"], 0) + 1

        compared = [
            r
            for r in rows
            if r["comparison_status"]
            not in {"Mapping Unresolved", "Not Comparable", "Source Unavailable"}
        ]
        exact = sum(1 for r in compared if r["comparison_status"] == "Exact Match")
        overall = "Exact Match" if compared and exact == len(compared) else (
            "Differences Found" if compared else "Incomplete Mapping"
        )

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return {
            "ok": True,
            "request_id": request_id,
            "generated_at": now,
            "environment": env,
            "period": period,
            "period_months": pe_months,
            "previous_period": prev_pe,
            "previous_period_months": prev_months,
            "org_unit": org_unit,
            "report": {
                "uid": meta.dhis2_report_uid,
                "name": meta.dhis2_report_name,
                "version": meta.report_version,
                "extraction_method": meta.extraction_method,
                "central_hub_report_id": "hcsr-progress-npmo-v1",
            },
            "overall": {
                "status": overall,
                "compared": len(compared),
                "total": len(rows),
                "exact_match": exact,
                "status_counts": status_counts,
            },
            "highlights": {
                "dhis2": {
                    "total_households": values.get("mRQ1mcOrUER"),
                    "approved_households": approved,
                    "completion_rate": pct_validated,
                    "eligible_households": eligible,
                    "validation_rate": pct_validated,
                },
                "hcsc": {
                    "eligible_households": hcsc_values.get("fxmvSiKfEpn"),
                    "approved_households": hcsc_values.get("BSqDSIpHhoT"),
                    "completion_rate": _round_pct(_as_float(hcsc_values.get("StDJxe7tIiS"))),
                },
            },
            "population_compatibility": {
                "status": "Compatible",
                "note": "Verified mappings share pe+ou analytics scope; CLIENT vs IND noted as Partial.",
            },
            "timestamps": {
                "compared_at": now,
                "analytics_request": (current_batch.get("request") or {}),
                "hcsc_analytics_request": (hcsc_batch.get("request") or {}),
            },
            "indicators": rows,
            "not_comparable": meta.not_comparable,
            "evidence": {
                "repository": meta.repository_evidence,
                "extraction_method": meta.extraction_method,
                "html_scrape": False,
                "ocr": False,
            },
            "diagnostics": {
                "dx_current": dx_current,
                "dx_hcsc": hcsc_uids,
                "q3_coverage_uses_validated": is_q3(period),
            },
        }

    def _build_indicator_row(
        self,
        ind: dict[str, Any],
        *,
        values: dict[str, Any],
        prev_eligible: float | None,
        pct_validated: float | None,
        pct_coverage: float | None,
        eligible: float | None,
        approved: float | None,
        hcsc_values: dict[str, Any],
        hcsc_nd: dict[str, Any],
    ) -> dict[str, Any]:
        key = str(ind.get("key") or "")
        result_type = str(ind.get("result_type") or "count")
        mapping_status = str(ind.get("mapping_status") or "Unresolved")
        source_type = str(ind.get("dhis2_source_type") or "")
        uid = ind.get("dhis2_uid")

        report_result = None
        report_num = None
        report_den = None
        if key == "percentage_data_validated":
            report_result = pct_validated
            report_num = approved
            report_den = eligible
        elif key == "percentage_coverage":
            report_result = pct_coverage
            report_num = approved
            report_den = eligible if pct_coverage == pct_validated else prev_eligible
        elif key == "prev_eligible_households":
            report_result = prev_eligible
        elif key == "estimated_households":
            report_result = None
        elif uid:
            report_result = _as_float(values.get(str(uid)))

        hcsc_result = None
        hcsc_num = None
        hcsc_den = None
        hcsc_uid = ind.get("hcsc_source_uid")
        if hcsc_uid:
            hcsc_result = _as_float(hcsc_values.get(str(hcsc_uid)))
            nd = hcsc_nd.get(str(hcsc_uid)) or {}
            hcsc_num = _as_float(nd.get("numerator"))
            hcsc_den = _as_float(nd.get("denominator"))
            # For IND completion, prefer includeNumDen companions
            if str(hcsc_uid) == "StDJxe7tIiS":
                if hcsc_num is None:
                    hcsc_num = _as_float(hcsc_values.get("BSqDSIpHhoT"))
                if hcsc_den is None:
                    hcsc_den = _as_float(hcsc_values.get("fxmvSiKfEpn"))
                hcsc_result = _round_pct(hcsc_result)

        if mapping_status == "Not Comparable":
            cmp = compare_values(
                result_type=result_type,
                report_value=report_result,
                hcsc_value=None,
                mapping_status=mapping_status,
                notes=str(ind.get("notes") or ""),
            )
        else:
            cmp = compare_values(
                result_type=result_type,
                report_value=report_result,
                hcsc_value=hcsc_result,
                mapping_status=mapping_status,
                report_num=report_num,
                report_den=report_den,
                hcsc_num=hcsc_num,
                hcsc_den=hcsc_den,
                notes=str(ind.get("notes") or ""),
            )

        num_meta = ind.get("numerator") if isinstance(ind.get("numerator"), dict) else None
        den_meta = ind.get("denominator") if isinstance(ind.get("denominator"), dict) else None

        return {
            "key": key,
            "report_label": ind.get("report_label"),
            "report_section": ind.get("report_section"),
            "result_type": result_type,
            "dhis2_source_type": source_type,
            "dhis2_uid": uid,
            "mapping_status": mapping_status,
            "comparison_status": cmp["status"],
            "explanation": cmp["explanation"],
            "evidence": ind.get("evidence"),
            "hcsc_indicator_key": ind.get("hcsc_indicator_key"),
            "hcsc_source_uid": hcsc_uid,
            "report": {
                "result": report_result,
                "numerator": report_num if result_type == "percentage" else None,
                "denominator": report_den if result_type == "percentage" else None,
                "numerator_meta": num_meta,
                "denominator_meta": den_meta,
                "source_badge": source_type or "PI",
            },
            "hcsc": {
                "result": hcsc_result,
                "numerator": hcsc_num if result_type == "percentage" else None,
                "denominator": hcsc_den if result_type == "percentage" else None,
                "source_badge": "IND" if hcsc_uid == "StDJxe7tIiS" else ("PI" if hcsc_uid else None),
            },
            "diffs": {
                "result_diff": cmp.get("result_diff"),
                "result_diff_pp": cmp.get("result_diff_pp"),
                "num_diff": cmp.get("num_diff"),
                "den_diff": cmp.get("den_diff"),
            },
            "population": ind.get("population"),
            "filters": ind.get("filters"),
            "rounding": ind.get("rounding"),
        }

    @staticmethod
    def _mapping_summary(meta: ProgressReportMeta) -> dict[str, int]:
        counts = {"Verified": 0, "Partial": 0, "Unresolved": 0, "Not Comparable": 0}
        for i in meta.indicators:
            st = str(i.get("mapping_status") or "Unresolved")
            counts[st] = counts.get(st, 0) + 1
        return counts
