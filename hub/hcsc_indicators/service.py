"""HCSC Indicator Summary service — read-only orchestration (Phase 0–2)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.periods import default_completed_quarter, periods_payload
from hub.dhis2_reports.security import ReportSecurityError, validate_environment, validate_org_unit, validate_period
from hub.hcsc_indicators.adapters import (
    ADAPTER_CAPABILITY,
    ADAPTER_DHIS2,
    ADAPTER_SQL,
    get_adapters,
    select_adapter,
)
from hub.hcsc_indicators.cache import (
    CATEGORY_CACHE,
    INFLIGHT,
    OVERVIEW_CACHE,
    REPORT_CACHE,
    category_cache_key,
    overview_cache_key,
    report_cache_key,
)
from hub.hcsc_indicators.design_decode import decode_npmo_design
from hub.hcsc_indicators.presentation import enrich_result_row
from hub.hcsc_indicators.query_display import build_retrieval_panel
from hub.hcsc_indicators.registry import SECTION_LABELS, SECTIONS, load_registry
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
        self._adapters = get_adapters()

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
            "phase": "0-2",
            "npmo_report_uid": reg.get("npmo_report_uid"),
            "npmo_report_name": reg.get("npmo_report_name"),
            "indicators": reg.get("indicators") or [],
            "overview_keys": [r["key"] for r in reg.get("overview_indicators") or []],
            "unresolved_keys": reg.get("unresolved_keys") or [],
            "sections": reg.get("sections") or [],
            "phase2_keys": reg.get("phase2_keys") or [],
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
            "adapters": list(self._adapters.keys()),
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
        from hub.hcsc_indicators.presentation import source_badge

        badge = source_badge(row.get("source_type"), source_owner=row.get("source_owner"))
        display = enrich_result_row(
            {
                **row,
                "indicator_key": row["key"],
                "source_uid": value_uid,
                "count": None,
                "numerator": None,
                "denominator": None,
                "percentage": None,
                "unresolved": bool(row.get("unresolved")),
            }
        )
        sql_id = row.get("approved_sql_query_id")
        return {
            "ok": True,
            "indicator": {
                **row,
                "display_result_type": display.get("display_result_type"),
                "source_badge": badge["code"],
                "source_badge_label": badge["label"],
            },
            "design_element_id": element,
            "last_refreshed": None,
            "uid_explorer_hint": f"/dhis2/uid-explorer/{value_uid}" if value_uid else None,
            "open_mapping_url": f"/dhis2/uid-explorer/{value_uid}" if value_uid else "/dhis2/uid-explorer",
            "open_sql_workspace_url": f"/sql?query={sql_id}" if sql_id else ("/sql" if row.get("approved_sql_reference") else None),
            "actions": {
                "copy_uid": value_uid,
                "open_mapping": True,
                "view_in_dhis2": bool(value_uid),
                "open_sql_workspace": bool(sql_id or row.get("approved_sql_reference")),
            },
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
        """Phase 0–1 Overview set — preserved batched analytics path."""
        return self._cached_fetch(
            scope="overview",
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            force_refresh=force_refresh,
            section=None,
        )

    def category(
        self,
        *,
        section: str,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str = "none",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        sid = (section or "").strip().lower()
        if sid not in SECTION_LABELS:
            raise ReportSecurityError(f"Unknown section: {section}", code="invalid_section")
        return self._cached_fetch(
            scope="category",
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            force_refresh=force_refresh,
            section=sid,
        )

    def report(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str = "none",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Full Phase 0–2 report: all registry sections in one batched analytics call."""
        return self._cached_fetch(
            scope="report",
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            force_refresh=force_refresh,
            section=None,
        )

    def _cached_fetch(
        self,
        *,
        scope: str,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str,
        force_refresh: bool,
        section: str | None,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        pe = validate_period(period, required=True)
        ou = validate_org_unit(org_unit, required=True)
        disagg = (disaggregation or "none").strip().lower() or "none"
        if disagg not in {"none"}:
            raise ReportSecurityError(
                "Only disaggregation=none is supported until IP/non-IP definitions are verified.",
                code="invalid_disaggregation",
            )

        if scope == "overview":
            cache = OVERVIEW_CACHE
            cache_key = overview_cache_key(
                environment=env, period=pe, org_unit=ou, disaggregation=disagg
            )
        elif scope == "category":
            cache = CATEGORY_CACHE
            cache_key = category_cache_key(
                environment=env,
                period=pe,
                org_unit=ou,
                disaggregation=disagg,
                section=section or "",
            )
        else:
            cache = REPORT_CACHE
            cache_key = report_cache_key(
                environment=env, period=pe, org_unit=ou, disaggregation=disagg
            )

        if not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                out = dict(cached)
                out["cache"] = {"hit": True, "key": cache_key}
                return out

        with self._lock:
            if not force_refresh:
                cached = cache.get(cache_key)
                if cached is not None:
                    out = dict(cached)
                    out["cache"] = {"hit": True, "deduped": True, "key": cache_key}
                    return out
            if cache_key in INFLIGHT:
                waiter = INFLIGHT[cache_key]
            else:
                waiter = threading.Event()
                INFLIGHT[cache_key] = waiter
                waiter = None

        if waiter is not None:
            waiter.wait(timeout=60)
            cached = cache.get(cache_key)
            if cached is not None:
                out = dict(cached)
                out["cache"] = {"hit": True, "deduped": True, "key": cache_key}
                return out

        try:
            payload = self._fetch_indicators(
                scope=scope,
                env=env,
                pe=pe,
                ou=ou,
                disagg=disagg,
                section=section,
                cache_key=cache_key,
            )
            cache.set(cache_key, payload)
            return payload
        finally:
            with self._lock:
                ev = INFLIGHT.pop(cache_key, None)
                if isinstance(ev, threading.Event):
                    ev.set()

    def _select_indicators(
        self, reg: dict[str, Any], *, scope: str, section: str | None
    ) -> list[dict[str, Any]]:
        all_rows = list(reg.get("indicators") or [])
        if scope == "overview":
            return [r for r in all_rows if r.get("overview")]
        if scope == "category":
            return [r for r in all_rows if r.get("section") == section]
        # Full report: all sections including unresolved markers.
        return all_rows

    def _build_result_row(
        self,
        ind: dict[str, Any],
        *,
        mapped: dict[str, Any],
        freshness: str,
        adapter_name: str,
        retrieval_method: str,
        deferred: bool = False,
        deferred_reason: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "indicator_key": ind["key"],
            "display_name": ind["display_name"],
            "category": ind["category"],
            "section": ind.get("section"),
            "section_label": ind.get("section_label") or SECTION_LABELS.get(ind.get("section") or "", ""),
            "phase": ind.get("phase"),
            "adapter": adapter_name,
            "retrieval_method": retrieval_method,
            "result_type": ind["result_type"],
            "definition": ind.get("definition"),
            "count": mapped.get("count"),
            "numerator": mapped.get("numerator"),
            "denominator": mapped.get("denominator"),
            "percentage": mapped.get("percentage"),
            "numerator_label": ind.get("numerator_label"),
            "denominator_label": ind.get("denominator_label"),
            "percentage_formula_reference": ind.get("percentage_formula_reference"),
            "source_uid": mapped.get("source_uid"),
            "dhis2_uids": ind.get("dhis2_uids") or {},
            "source_owner": ind["source_owner"],
            "source_type": ind.get("source_type"),
            "source_table_view_reference": ind.get("source_table_view_reference"),
            "source_columns_reference": ind.get("source_columns_reference"),
            "source_fields": ind.get("source_columns_reference"),
            "repository_file_reference": ind.get("repository_file_reference"),
            "population_definition_reference": ind.get("population_definition_reference"),
            "age_range": ind.get("age_range"),
            "quarter_rule_reference": ind.get("quarter_rule_reference"),
            "organisation_unit_rule": ind.get("organisation_unit_rule"),
            "status_filters_reference": ind.get("status_filters_reference"),
            "ip_non_ip_rule": ind.get("ip_non_ip_rule"),
            "lineage_reference": ind.get("lineage_reference"),
            "approved_sql_reference": ind.get("approved_sql_reference"),
            "approved_sql_query_id": ind.get("approved_sql_query_id"),
            "capability_reference": ind.get("capability_reference"),
            "hcsc_excel_key": ind.get("hcsc_excel_key"),
            "confidence": ind.get("confidence"),
            "notes": ind.get("notes"),
            "unresolved_notes": ind.get("notes") if ind.get("unresolved") else None,
            "validation_parity_note": ind.get("validation_parity_note"),
            "unresolved": bool(ind.get("unresolved")),
            "deferred": deferred,
            "deferred_reason": deferred_reason,
            "freshness": freshness,
            "last_updated": freshness,
            "filters": {
                "quarter": ind.get("quarter_rule_reference"),
                "organisation_unit": ind.get("organisation_unit_rule"),
            },
        }
        return enrich_result_row(validate_row(row))

    def _fetch_indicators(
        self,
        *,
        scope: str,
        env: str,
        pe: str,
        ou: str,
        disagg: str,
        section: str | None,
        cache_key: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        reg = self.registry()
        design = self.design_bindings()
        indicators = self._select_indicators(reg, scope=scope, section=section)
        freshness = datetime.now(timezone.utc).isoformat()

        by_adapter: dict[str, list[dict[str, Any]]] = {
            ADAPTER_DHIS2: [],
            ADAPTER_SQL: [],
            ADAPTER_CAPABILITY: [],
            "unresolved": [],
        }
        for ind in indicators:
            by_adapter.setdefault(select_adapter(ind), []).append(ind)

        client: Dhis2Client | None = None
        adapter_payloads: dict[str, Any] = {}
        http_requests = 0
        analytics_ms = 0
        dhis2_writes = 0
        try:
            dhis2_inds = by_adapter.get(ADAPTER_DHIS2) or []
            if dhis2_inds:
                client = self._client_factory(env)
                if not getattr(client.settings, "is_configured", False):
                    raise ReportSecurityError(
                        f"DHIS2 {env} is not configured.",
                        code="dhis2_unconfigured",
                    )
                try:
                    adapter_payloads[ADAPTER_DHIS2] = self._adapters[ADAPTER_DHIS2].retrieve(
                        dhis2_inds,
                        environment=env,
                        period=pe,
                        org_unit=ou,
                        client=client,
                    )
                except Dhis2Error as exc:
                    raise ReportSecurityError(str(exc), code="dhis2_error") from exc
                batch = adapter_payloads[ADAPTER_DHIS2].get("batch") or {}
                analytics_ms = int(batch.get("latency_ms") or 0)
                dhis2_writes += int(adapter_payloads[ADAPTER_DHIS2].get("dhis2_writes") or 0)

            if by_adapter.get(ADAPTER_SQL):
                adapter_payloads[ADAPTER_SQL] = self._adapters[ADAPTER_SQL].retrieve(
                    by_adapter[ADAPTER_SQL],
                    environment=env,
                    period=pe,
                    org_unit=ou,
                    client=client,
                )
            if by_adapter.get(ADAPTER_CAPABILITY):
                adapter_payloads[ADAPTER_CAPABILITY] = self._adapters[ADAPTER_CAPABILITY].retrieve(
                    by_adapter[ADAPTER_CAPABILITY],
                    environment=env,
                    period=pe,
                    org_unit=ou,
                    client=client,
                )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

        mapped_by_key: dict[str, dict[str, Any]] = {}
        for name, payload in adapter_payloads.items():
            for row in payload.get("rows") or []:
                mapped_by_key[row["indicator_key"]] = row

        results: list[dict[str, Any]] = []
        calc_refs: list[dict[str, Any]] = []
        mapping_rows: list[dict[str, Any]] = []
        for ind in indicators:
            adapter_row = mapped_by_key.get(ind["key"])
            if adapter_row:
                mapped = adapter_row.get("mapped") or {}
                enriched = self._build_result_row(
                    ind,
                    mapped=mapped,
                    freshness=freshness,
                    adapter_name=adapter_row.get("adapter") or select_adapter(ind),
                    retrieval_method=adapter_row.get("retrieval_method") or "DHIS2 Analytics",
                    deferred=bool(adapter_row.get("deferred")),
                    deferred_reason=adapter_row.get("reason"),
                )
            else:
                # Unresolved / no adapter payload
                enriched = self._build_result_row(
                    ind,
                    mapped={
                        "count": None,
                        "numerator": None,
                        "denominator": None,
                        "percentage": None,
                        "source_uid": (ind.get("dhis2_uids") or {}).get("value"),
                    },
                    freshness=freshness,
                    adapter_name="unresolved",
                    retrieval_method="Unresolved",
                    deferred=True,
                    deferred_reason=ind.get("notes") or "Unresolved — no adapter retrieval.",
                )
            results.append(enriched)
            if ind.get("percentage_formula_reference") or ind.get("lineage_reference"):
                calc_refs.append(
                    {
                        "indicator_key": ind["key"],
                        "display_name": ind["display_name"],
                        "formula_reference": ind.get("percentage_formula_reference"),
                        "lineage_reference": ind.get("lineage_reference"),
                        "invented": False,
                    }
                )
            mapping_rows.append(
                {
                    "indicator_key": ind["key"],
                    "display_name": ind["display_name"],
                    "section": ind.get("section"),
                    "source_badge": enriched.get("source_badge"),
                    "source_type": ind.get("source_type"),
                    "source_owner": ind.get("source_owner"),
                    "uid": enriched.get("source_uid"),
                    "source_table_view_reference": ind.get("source_table_view_reference"),
                    "approved_sql_query_id": ind.get("approved_sql_query_id"),
                    "capability_reference": ind.get("capability_reference"),
                    "adapter": enriched.get("adapter"),
                    "unresolved": bool(ind.get("unresolved")),
                }
            )

        # Prefer DHIS2 retrieval panel when analytics ran; else SQL/capability refs.
        if ADAPTER_DHIS2 in adapter_payloads:
            retrieval = build_retrieval_panel(
                retrieval_method="DHIS2 Analytics",
                request_meta=(adapter_payloads[ADAPTER_DHIS2].get("batch") or {}).get("request"),
                calculation_refs=calc_refs,
                source_mapping_rows=mapping_rows,
                pi_expressions={},
            )
        elif ADAPTER_SQL in adapter_payloads:
            sql_refs = adapter_payloads[ADAPTER_SQL].get("sql_references") or []
            note = "; ".join(
                f"{r.get('indicator_key')}: {r.get('approved_sql_reference') or r.get('approved_sql_query_id')}"
                for r in sql_refs
            )
            retrieval = build_retrieval_panel(
                retrieval_method="Approved SQL",
                sql_text=note or None,
                calculation_refs=calc_refs,
                source_mapping_rows=mapping_rows,
            )
            retrieval["sql_references"] = sql_refs
            retrieval["open_sql_workspace"] = True
            retrieval["open_sql_workspace_url"] = "/sql"
        else:
            caps = [
                r.get("capability_reference")
                for r in (adapter_payloads.get(ADAPTER_CAPABILITY) or {}).get("rows") or []
                if r.get("capability_reference")
            ]
            retrieval = build_retrieval_panel(
                retrieval_method="Connected Repository Capability",
                capability_ref="; ".join(caps) if caps else None,
                calculation_refs=calc_refs,
                source_mapping_rows=mapping_rows,
            )

        by_section: dict[str, list[dict[str, Any]]] = {s: [] for s in SECTIONS}
        for row in results:
            by_section.setdefault(row.get("section") or "eligible_beneficiaries", []).append(row)
        sections_out = [
            {
                "id": sid,
                "label": SECTION_LABELS[sid],
                "results": by_section.get(sid) or [],
                "count": len(by_section.get(sid) or []),
            }
            for sid in SECTIONS
            if by_section.get(sid)
        ]

        # Fix http_requests: one batched analytics call when dx present.
        dx_list = (
            ((adapter_payloads.get(ADAPTER_DHIS2) or {}).get("batch") or {})
            .get("request", {})
            .get("parameters", {})
            .get("dx")
            or []
        )
        http_requests = 1 if dx_list else 0

        total_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "scope": scope,
            "section": section,
            "environment": env,
            "period": pe,
            "org_unit": ou,
            "disaggregation": disagg,
            "freshness": freshness,
            "results": results,
            "sections": sections_out,
            "adapters_used": sorted(adapter_payloads.keys()),
            "query": retrieval,
            "retrieval": retrieval,
            "design_unresolved_elements": design.get("unresolved_elements") or [],
            "registry_unresolved_keys": [
                r["indicator_key"] for r in results if r.get("unresolved")
            ],
            "timings": {
                "total_ms": total_ms,
                "analytics_ms": analytics_ms,
                "dx_count": len(dx_list),
                "http_requests": http_requests,
                "indicator_count": len(results),
            },
            "cache": {"hit": False, "key": cache_key},
            "dhis2_writes": dhis2_writes,
            "boundaries": {
                "readonly": True,
                "no_formula_engine": True,
                "no_html_scrape": True,
            },
        }
