"""HCSC Indicator Summary service — read-only orchestration."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.periods import default_completed_quarter, periods_payload
from hub.dhis2_reports.security import ReportSecurityError, validate_environment, validate_org_unit, validate_period
from hub.hcsc_indicators.analytics import fetch_analytics_batch, map_indicator_values
from hub.hcsc_indicators.cache import INFLIGHT, OVERVIEW_CACHE, overview_cache_key
from hub.hcsc_indicators.design_decode import decode_npmo_design
from hub.hcsc_indicators.query_display import build_query_panel
from hub.hcsc_indicators.registry import collect_analytics_uids, load_registry
from hub.hcsc_indicators.validation import validate_row


class HcscIndicatorService:
    def __init__(
        self,
        *,
        client_factory: Callable[[str], Dhis2Client],
        registry_path: Path | None = None,
        reports_db_path: Path | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._registry_path = registry_path
        self._reports_db_path = reports_db_path
        self._lock = threading.RLock()

    def registry(self, *, force: bool = False) -> dict[str, Any]:
        return load_registry(self._registry_path, force=force)

    def design_bindings(self, *, force: bool = False) -> dict[str, Any]:
        return decode_npmo_design(db_path=self._reports_db_path, force=force)

    def bootstrap(self) -> dict[str, Any]:
        reg = self.registry()
        design = self.design_bindings()
        periods = periods_payload(remembered=default_completed_quarter())
        return {
            "ok": True,
            "page_title": "HCSC Indicator Summary & Data Lineage — NPMO",
            "phase": "0-1",
            "npmo_report_uid": reg.get("npmo_report_uid"),
            "npmo_report_name": reg.get("npmo_report_name"),
            "indicators": reg.get("indicators") or [],
            "overview_keys": [r["key"] for r in reg.get("overview_indicators") or []],
            "unresolved_keys": reg.get("unresolved_keys") or [],
            "design": {
                "ok": design.get("ok"),
                "dx_to_element": design.get("dx_to_element") or {},
                "unresolved_elements": design.get("unresolved_elements") or [],
                "notes": design.get("notes"),
                "environment": design.get("environment"),
            },
            "periods": periods,
            "disaggregations": [
                {"id": "none", "label": "None (aggregate)"},
                {
                    "id": "ip",
                    "label": "IP / non-IP (planned)",
                    "disabled": True,
                    "note": "No Overview IP/non-IP dual PIs registered yet — do not guess.",
                },
            ],
            "environments": [
                {"id": "stage", "label": "Stage"},
                {"id": "live", "label": "Live"},
            ],
            "retrieval_methods": [
                "DHIS2 Analytics",
                "Approved SQL",
                "Connected Repository Capability",
            ],
            "boundaries": {
                "readonly": True,
                "no_formula_engine": True,
                "no_html_scrape": True,
                "dhis2_writes": False,
            },
        }

    def indicator_detail(self, key: str) -> dict[str, Any]:
        reg = self.registry()
        row = next((r for r in reg.get("indicators") or [] if r["key"] == key), None)
        if not row:
            raise ReportSecurityError("Indicator not found in registry.", code="not_found")
        design = self.design_bindings()
        value_uid = (row.get("dhis2_uids") or {}).get("value")
        element = (design.get("dx_to_element") or {}).get(value_uid or "") if value_uid else None
        return {
            "ok": True,
            "indicator": row,
            "design_element_id": element,
            "last_refreshed": None,
            "uid_explorer_hint": f"/dhis2/uid-explorer/{value_uid}" if value_uid else None,
        }

    def overview(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str = "none",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        pe = validate_period(period, required=True)
        ou = validate_org_unit(org_unit, required=True)
        disagg = (disaggregation or "none").strip().lower() or "none"
        if disagg not in {"none"}:
            raise ReportSecurityError(
                "Only disaggregation=none is supported in Phase 0–1.",
                code="invalid_disaggregation",
            )

        cache_key = overview_cache_key(
            environment=env, period=pe, org_unit=ou, disaggregation=disagg
        )
        if not force_refresh:
            cached = OVERVIEW_CACHE.get(cache_key)
            if cached is not None:
                out = dict(cached)
                out["cache"] = {"hit": True, "key": cache_key}
                return out

        with self._lock:
            # Prevent duplicate in-flight requests for the same scope.
            existing = INFLIGHT.get(cache_key)
            if existing is not None:
                event, holder = existing  # type: ignore[misc]
                event.wait(timeout=60)
                if holder:
                    result = holder[0] if holder else None
                    if result is not None:
                        out = dict(result)
                        out["cache"] = {"hit": True, "deduped": True, "key": cache_key}
                        return out

            event = threading.Event()
            holder: list[Any] = []
            INFLIGHT[cache_key] = (event, holder)

        try:
            payload = self._fetch_overview(env=env, pe=pe, ou=ou, disagg=disagg, cache_key=cache_key)
            holder.append(payload)
            OVERVIEW_CACHE.set(cache_key, payload)
            return payload
        finally:
            event.set()
            with self._lock:
                INFLIGHT.pop(cache_key, None)

    def _fetch_overview(
        self,
        *,
        env: str,
        pe: str,
        ou: str,
        disagg: str,
        cache_key: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        reg = self.registry()
        design = self.design_bindings()
        overview_rows = [r for r in (reg.get("overview_indicators") or [])]
        dx_uids = collect_analytics_uids(overview_rows)

        client = self._client_factory(env)
        try:
            if not getattr(client.settings, "is_configured", False):
                raise ReportSecurityError(
                    f"DHIS2 {env} is not configured.",
                    code="dhis2_unconfigured",
                )
            batch = fetch_analytics_batch(client, dx_uids=dx_uids, period=pe, org_unit=ou)
        except Dhis2Error as exc:
            raise ReportSecurityError(str(exc), code="dhis2_error") from exc
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        values = batch.get("values") or {}
        freshness = datetime.now(timezone.utc).isoformat()
        results: list[dict[str, Any]] = []
        for ind in overview_rows:
            mapped = map_indicator_values(ind, values)
            row = {
                "indicator_key": ind["key"],
                "display_name": ind["display_name"],
                "category": ind["category"],
                "result_type": ind["result_type"],
                "count": mapped["count"],
                "numerator": mapped["numerator"],
                "denominator": mapped["denominator"],
                "percentage": mapped["percentage"],
                "numerator_label": ind.get("numerator_label"),
                "denominator_label": ind.get("denominator_label"),
                "percentage_formula_reference": ind.get("percentage_formula_reference"),
                "source_uid": mapped["source_uid"],
                "dhis2_uids": ind.get("dhis2_uids") or {},
                "source_owner": ind["source_owner"],
                "source_type": ind.get("source_type"),
                "source_table_view_reference": ind.get("source_table_view_reference"),
                "repository_file_reference": ind.get("repository_file_reference"),
                "population_definition_reference": ind.get("population_definition_reference"),
                "age_range": ind.get("age_range"),
                "quarter_rule_reference": ind.get("quarter_rule_reference"),
                "organisation_unit_rule": ind.get("organisation_unit_rule"),
                "ip_non_ip_rule": ind.get("ip_non_ip_rule"),
                "confidence": ind.get("confidence"),
                "notes": ind.get("notes"),
                "unresolved": bool(ind.get("unresolved")),
                "freshness": freshness,
            }
            results.append(validate_row(row))

        query_panel = build_query_panel(
            retrieval_method="DHIS2 Analytics",
            request_meta=batch.get("request"),
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "environment": env,
            "period": pe,
            "org_unit": ou,
            "disaggregation": disagg,
            "freshness": freshness,
            "results": results,
            "query": query_panel,
            "design_unresolved_elements": design.get("unresolved_elements") or [],
            "registry_unresolved_keys": reg.get("unresolved_keys") or [],
            "timings": {
                "total_ms": total_ms,
                "analytics_ms": batch.get("latency_ms"),
                "dx_count": len(dx_uids),
                "http_requests": 1 if dx_uids else 0,
            },
            "cache": {"hit": False, "key": cache_key},
            "dhis2_writes": 0,
            "boundaries": {
                "readonly": True,
                "no_formula_engine": True,
                "no_html_scrape": True,
            },
        }
