"""HCSC Indicator Summary service — read-only orchestration (Phase 0–2)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.maintenance import environment_availability
from hub.dhis2_reports.org_unit_store import OrgUnitStore
from hub.dhis2_reports.security import ReportSecurityError, validate_environment, validate_org_unit
from hub.hcsc_indicators.adapters import (
    ADAPTER_CAPABILITY,
    ADAPTER_DHIS2,
    ADAPTER_SQL,
    get_adapters,
    select_adapter,
)
from hub.hcsc_indicators.analytics import fetch_analytics_batch, map_indicator_values
from hub.hcsc_indicators.branding import (
    COMPARE_SOURCES,
    NAV_LABEL,
    PAGE_MEANING,
    PAGE_SUBTITLE,
    PAGE_TITLE,
    REVIEW_DIFFERENCES,
    export_package_meta,
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
from hub.hcsc_indicators.geographic_breakdown import (
    BREAKDOWN_LABELS,
    BREAKDOWN_NONE,
    bootstrap_breakdown_meta,
    breakdown_thresholds,
    format_estimate,
    options_for_parent_level,
    target_level_for_breakdown,
    validate_breakdown_for_parent,
)
from hub.hcsc_indicators.presentation import enrich_result_row
from hub.hcsc_indicators.query_display import build_retrieval_panel
from hub.hcsc_indicators.quarters import (
    allowed_quarter_ids,
    assert_allowed_quarter,
    cycle_periods_payload,
)
from hub.hcsc_indicators.registry import (
    SECTION_LABELS,
    SECTIONS,
    collect_analytics_uids,
    load_registry,
)
from hub.hcsc_indicators.validation import validate_row


class HcscIndicatorService:
    def __init__(
        self,
        *,
        client_factory: Callable[[str], Dhis2Client],
        registry_path: Path | None = None,
        reports_db_path: Path | None = None,
        ou_store: OrgUnitStore | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._registry_path = registry_path
        self._reports_db_path = reports_db_path
        self._ou_store = ou_store or OrgUnitStore()
        self._lock = threading.RLock()
        self._adapters = get_adapters()

    def registry(self, *, force: bool = False) -> dict[str, Any]:
        return load_registry(self._registry_path, force=force)

    def design_bindings(self, *, force: bool = False) -> dict[str, Any]:
        return decode_npmo_design(db_path=self._reports_db_path, force=force)

    def bootstrap(self) -> dict[str, Any]:
        reg = self.registry()
        design = self.design_bindings()
        periods = cycle_periods_payload(reg)
        return {
            "ok": True,
            "page_title": PAGE_TITLE,
            "page_subtitle": PAGE_SUBTITLE,
            "page_meaning": PAGE_MEANING,
            "nav_label": NAV_LABEL,
            "phase": "0-3",
            "npmo_report_uid": reg.get("npmo_report_uid"),
            "npmo_report_name": reg.get("npmo_report_name"),
            "indicators": reg.get("indicators") or [],
            "overview_keys": [r["key"] for r in reg.get("overview_indicators") or []],
            "unresolved_keys": reg.get("unresolved_keys") or [],
            "unresolved_classifications": reg.get("unresolved_classifications") or [],
            "sections": reg.get("sections") or [],
            "phase2_keys": reg.get("phase2_keys") or [],
            "compare_sources_label": COMPARE_SOURCES,
            "review_differences_label": REVIEW_DIFFERENCES,
            "design": {
                "ok": design.get("ok"),
                "dx_to_element": design.get("dx_to_element") or {},
                "unresolved_elements": design.get("unresolved_elements") or [],
                "notes": design.get("notes"),
                "environment": design.get("environment"),
            },
            "periods": periods,
            "reporting_cycle": (periods.get("cycle") or {}),
            "disaggregations": [
                {"id": "none", "label": "All Households"},
                {
                    "id": "ip",
                    "label": "IP / non-IP (planned)",
                    "disabled": True,
                    "note": "No Overview IP/non-IP dual PIs registered yet — do not guess.",
                },
            ],
            "population_filters": [
                {"id": "none", "label": "All Households"},
                {
                    "id": "ip",
                    "label": "IP / non-IP (planned)",
                    "disabled": True,
                    "note": "No Overview IP/non-IP dual PIs registered yet — do not guess.",
                },
            ],
            "geographic_breakdown": bootstrap_breakdown_meta(),
            "environments": [
                {
                    "id": "stage",
                    "label": "Stage",
                    **{
                        k: environment_availability("stage")[k]
                        for k in ("status", "message", "maintenance")
                    },
                },
                {
                    "id": "live",
                    "label": "Live",
                    **{
                        k: environment_availability("live")[k]
                        for k in ("status", "message", "maintenance")
                    },
                },
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
                "no_dhis2_writes": True,
                "org_unit_source": "hub_dhis2_reports_org_units",
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
        if sid == "convergence":
            sid = "hcsc"
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
        geographic_breakdown: str = "none",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Full Phase 0–2 report: all registry sections in one batched analytics call."""
        return self._cached_fetch(
            scope="report",
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            geographic_breakdown=geographic_breakdown,
            force_refresh=force_refresh,
            section=None,
        )

    def breakdown_estimate(
        self,
        *,
        environment: str,
        org_unit: str,
        geographic_breakdown: str = "none",
    ) -> dict[str, Any]:
        """Estimate child OU count for a geographic breakdown (SQLite cache only)."""
        env = validate_environment(environment)
        ou = validate_org_unit(org_unit, required=True)
        parent = self._ou_store.get(env, ou) or {}
        parent_level = parent.get("level")
        bid = validate_breakdown_for_parent(
            parent_level=parent_level,
            geographic_breakdown=geographic_breakdown,
        )
        thresholds = breakdown_thresholds()
        options = options_for_parent_level(parent_level)
        parent_info = {
            "uid": parent.get("uid") or ou,
            "name": parent.get("name") or ou,
            "level": parent_level,
            "path": parent.get("path") or "",
        }
        if bid == BREAKDOWN_NONE:
            return {
                "ok": True,
                "mode": BREAKDOWN_NONE,
                "label": BREAKDOWN_LABELS[BREAKDOWN_NONE],
                "count": 0,
                "child_count": 0,
                "estimate_label": BREAKDOWN_LABELS[BREAKDOWN_NONE],
                "requires_confirmation": False,
                "thresholds": thresholds,
                "options": options,
                "parent": parent_info,
                "target_level": None,
            }
        target = target_level_for_breakdown(bid)
        count = (
            self._ou_store.count_descendants_at_level(env, ou, int(target))
            if target is not None
            else 0
        )
        return {
            "ok": True,
            "mode": bid,
            "label": BREAKDOWN_LABELS.get(bid, bid),
            "count": count,
            "child_count": count,
            "estimate_label": format_estimate(count, bid),
            "requires_confirmation": count >= int(thresholds.get("confirm_at") or 200),
            "thresholds": thresholds,
            "options": options,
            "parent": parent_info,
            "target_level": target,
        }

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
        geographic_breakdown: str = "none",
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        pe = assert_allowed_quarter(period, allowed_quarter_ids(self.registry()))
        ou = validate_org_unit(org_unit, required=True)
        disagg = (disaggregation or "none").strip().lower() or "none"
        if disagg not in {"none"}:
            raise ReportSecurityError(
                "Only disaggregation=none is supported until IP/non-IP definitions are verified.",
                code="invalid_disaggregation",
            )

        # Overview / category stay aggregate-only; geo applies to full report scope.
        if scope in {"overview", "category"}:
            geo = BREAKDOWN_NONE
        else:
            parent_meta = self._ou_store.get(env, ou)
            parent_level = parent_meta.get("level") if parent_meta else None
            geo = validate_breakdown_for_parent(
                parent_level=parent_level,
                geographic_breakdown=geographic_breakdown,
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
                environment=env,
                period=pe,
                org_unit=ou,
                disaggregation=disagg,
                geographic_breakdown=geo,
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
                geographic_breakdown=geo,
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
            "display_group": ind.get("display_group"),
            "display_group_label": ind.get("display_group_label"),
            "classification": ind.get("classification"),
            "classification_unresolved": ind.get("classification_unresolved"),
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
        geographic_breakdown: str = "none",
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
                http_requests = int(batch.get("http_requests") or 0)
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

        by_section: dict[str, list[dict[str, Any]]] = {}
        by_group: dict[str, list[dict[str, Any]]] = {}
        for row in results:
            by_section.setdefault(row.get("section") or "eligible_beneficiaries", []).append(row)
            by_group.setdefault(row.get("display_group") or row.get("section") or "unresolved", []).append(row)
        from hub.hcsc_indicators.branding import DISPLAY_GROUP_LABELS, DISPLAY_GROUPS, RF_DOMAIN_GROUPS

        sections_out = []
        for gid in DISPLAY_GROUPS:
            rows = by_group.get(gid) or []
            if not rows and not (
                gid == "results_framework" and any(by_group.get(d) for d in RF_DOMAIN_GROUPS)
            ):
                continue
            sections_out.append(
                {
                    "id": gid,
                    "label": DISPLAY_GROUP_LABELS.get(gid, gid),
                    "results": rows,
                    "count": len(rows),
                    "rf_domain": gid in RF_DOMAIN_GROUPS,
                }
            )

        # Prefer batch http_requests; fall back to one call when dx present.
        dx_list = (
            ((adapter_payloads.get(ADAPTER_DHIS2) or {}).get("batch") or {})
            .get("request", {})
            .get("parameters", {})
            .get("dx")
            or []
        )
        if not http_requests and dx_list:
            http_requests = 1

        geo_payload: dict[str, Any] = {"mode": BREAKDOWN_NONE, "children": []}
        if geographic_breakdown and geographic_breakdown != BREAKDOWN_NONE:
            overview_inds = [
                r
                for r in (reg.get("overview_indicators") or [])
                if select_adapter(r) == ADAPTER_DHIS2
            ]
            if not overview_inds:
                overview_inds = [
                    r
                    for r in (reg.get("indicators") or [])
                    if select_adapter(r) == ADAPTER_DHIS2
                ]
            geo_payload = self._build_geographic_breakdown(
                env=env,
                pe=pe,
                ou=ou,
                breakdown_id=geographic_breakdown,
                indicators=overview_inds,
                freshness=freshness,
            )
            geo_timings = geo_payload.get("timings") or {}
            http_requests += int(geo_timings.get("http_requests") or 0)
            analytics_ms += int(geo_timings.get("analytics_ms") or 0)

        total_ms = int((time.perf_counter() - started) * 1000)
        package = export_package_meta(
            kind="report",
            environment=env,
            period=pe,
            org_unit=ou,
            generated_at=freshness,
            source_versions={
                "registry": "config/hcsc_indicators.yaml",
                "npmo_report_uid": reg.get("npmo_report_uid"),
                "adapters": sorted(adapter_payloads.keys()),
                "dx_count": len(dx_list),
                "geographic_breakdown": geographic_breakdown or BREAKDOWN_NONE,
            },
        )
        return {
            "ok": True,
            "scope": scope,
            "section": section,
            "environment": env,
            "period": pe,
            "org_unit": ou,
            "disaggregation": disagg,
            "geographic_breakdown": geo_payload,
            "freshness": freshness,
            "package": package,
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

    def _build_geographic_breakdown(
        self,
        *,
        env: str,
        pe: str,
        ou: str,
        breakdown_id: str,
        indicators: list[dict[str, Any]],
        freshness: str,
    ) -> dict[str, Any]:
        """Batched multi-OU analytics for descendants at a target level (DHIS2 only)."""
        thresholds = breakdown_thresholds()
        target = target_level_for_breakdown(breakdown_id)
        parent = self._ou_store.get(env, ou) or {}
        parent_info = {
            "uid": parent.get("uid") or ou,
            "name": parent.get("name") or ou,
            "level": parent.get("level"),
            "path": parent.get("path") or "",
        }
        label = BREAKDOWN_LABELS.get(breakdown_id, breakdown_id)

        def _fail(message: str, *, child_count: int = 0) -> dict[str, Any]:
            return {
                "ok": False,
                "mode": breakdown_id,
                "label": label,
                "target_level": target,
                "parent": parent_info,
                "child_count": child_count,
                "estimate_label": format_estimate(child_count, breakdown_id),
                "requires_confirmation": child_count
                >= int(thresholds.get("confirm_at") or 200),
                "thresholds": thresholds,
                "children": [],
                "rows_flat": [],
                "timings": {"http_requests": 0, "analytics_ms": 0, "child_count": child_count},
                "dhis2_writes": 0,
                "error": message,
            }

        if target is None:
            return _fail("Invalid geographic breakdown target.")
        if not indicators:
            return _fail("No DHIS2 overview indicators available for geographic breakdown.")

        try:
            child_count = self._ou_store.count_descendants_at_level(env, ou, int(target))
            children_meta = self._ou_store.list_descendants_at_level(
                env,
                ou,
                int(target),
                limit=int(thresholds.get("max_children") or 2500),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(f"Organisation unit lookup failed: {exc}")

        estimate_label = format_estimate(child_count, breakdown_id)
        requires_confirmation = child_count >= int(thresholds.get("confirm_at") or 200)

        if not children_meta:
            return {
                "ok": True,
                "mode": breakdown_id,
                "label": label,
                "target_level": target,
                "parent": parent_info,
                "child_count": child_count,
                "estimate_label": estimate_label,
                "requires_confirmation": requires_confirmation,
                "thresholds": thresholds,
                "children": [],
                "rows_flat": [],
                "timings": {"http_requests": 0, "analytics_ms": 0, "child_count": child_count},
                "dhis2_writes": 0,
                "error": None
                if child_count == 0
                else "No organisation units found in hub OU cache for this breakdown.",
            }

        client: Dhis2Client | None = None
        try:
            client = self._client_factory(env)
            if not getattr(client.settings, "is_configured", False):
                return _fail(f"DHIS2 {env} is not configured.", child_count=child_count)
            dx = collect_analytics_uids(indicators)
            child_uids = [c.get("uid") or c.get("id") for c in children_meta if c.get("uid") or c.get("id")]
            try:
                batch = fetch_analytics_batch(
                    client,
                    dx_uids=dx,
                    period=pe,
                    org_unit=child_uids,
                    include_num_den=True,
                )
            except Dhis2Error as exc:
                return _fail(str(exc), child_count=child_count)

            by_ou = batch.get("by_ou") or {}
            children_out: list[dict[str, Any]] = []
            rows_flat: list[dict[str, Any]] = []
            for child in children_meta:
                uid = child.get("uid") or child.get("id") or ""
                bucket = by_ou.get(uid) or {"values": {}, "num_den": {}}
                results: list[dict[str, Any]] = []
                for ind in indicators:
                    mapped = map_indicator_values(
                        ind,
                        bucket.get("values") or {},
                        num_den=bucket.get("num_den") or {},
                    )
                    enriched = self._build_result_row(
                        ind,
                        mapped=mapped,
                        freshness=freshness,
                        adapter_name=ADAPTER_DHIS2,
                        retrieval_method="DHIS2 Analytics",
                    )
                    results.append(enriched)
                    rows_flat.append(
                        {
                            "org_unit": uid,
                            "org_unit_name": child.get("name") or uid,
                            "hierarchy_path": child.get("path_label")
                            or child.get("path")
                            or "",
                            "level": child.get("level"),
                            "indicator_key": enriched.get("indicator_key"),
                            "display_name": enriched.get("display_name"),
                            "value_text": enriched.get("value_text"),
                            "count": enriched.get("count"),
                            "numerator": enriched.get("numerator"),
                            "denominator": enriched.get("denominator"),
                            "percentage": enriched.get("percentage"),
                            "source_badge": enriched.get("source_badge"),
                            "validation_status": enriched.get("validation_status"),
                            "freshness": enriched.get("freshness"),
                        }
                    )
                children_out.append(
                    {
                        "org_unit": uid,
                        "org_unit_name": child.get("name") or uid,
                        "hierarchy_path": child.get("path_label")
                        or child.get("path")
                        or "",
                        "level": child.get("level"),
                        "results": results,
                    }
                )

            return {
                "ok": True,
                "mode": breakdown_id,
                "label": label,
                "target_level": target,
                "parent": parent_info,
                "child_count": child_count,
                "estimate_label": estimate_label,
                "requires_confirmation": requires_confirmation,
                "thresholds": thresholds,
                "truncated": len(children_meta) < child_count,
                "children": children_out,
                "rows_flat": rows_flat,
                "timings": {
                    "http_requests": int(batch.get("http_requests") or 0),
                    "analytics_ms": int(batch.get("latency_ms") or 0),
                    "child_count": child_count,
                    "listed_children": len(children_meta),
                    "dx_count": len(dx),
                },
                "dhis2_writes": 0,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            return _fail(str(exc), child_count=child_count)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

    def validation_workspace(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str = "none",
        force_refresh: bool = False,
        evidence_path=None,
    ) -> dict[str, Any]:
        """Phase 3 read-only validation against compatible authoritative sources."""
        from hub.hcsc_indicators.compare import build_comparison_row, summarize_comparisons
        from hub.hcsc_indicators.evidence import latest_snapshot_comparisons

        started = time.perf_counter()
        report = self.report(
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            force_refresh=force_refresh,
        )
        scope = {
            "environment": report["environment"],
            "period": report["period"],
            "org_unit": report["org_unit"],
            "disaggregation": report["disaggregation"],
        }
        snap_map = latest_snapshot_comparisons(
            environment=scope["environment"],
            period=scope["period"],
            org_unit=scope["org_unit"],
            path=evidence_path,
        )
        comparisons: list[dict[str, Any]] = []
        for row in report.get("results") or []:
            comparison_source = "analytics_num_den"
            comparison_payload: dict[str, Any] | None = {
                "period": scope["period"],
                "org_unit": scope["org_unit"],
                "numerator": row.get("numerator"),
                "denominator": row.get("denominator"),
                "numerator_label": row.get("numerator_label"),
                "denominator_label": row.get("denominator_label"),
                "population_definition_reference": row.get("population_definition_reference"),
                "age_range": row.get("age_range"),
                "ip_non_ip_rule": row.get("ip_non_ip_rule"),
                "reference": "same-batch includeNumDen / companion UIDs",
                "freshness": row.get("freshness"),
            }
            # Prefer prior evidence snapshot values when present (same env/period/OU).
            if row.get("indicator_key") in snap_map:
                comparison_source = "evidence_snapshot"
                comparison_payload = snap_map[row["indicator_key"]]
            elif row.get("adapter") == "approved_sql" or row.get("approved_sql_reference"):
                comparison_source = "approved_sql"
                comparison_payload = {
                    "unavailable": True,
                    "reason": "Approved SQL is lineage-only — not auto-executed for validation.",
                    "reference": row.get("approved_sql_reference") or row.get("approved_sql_query_id"),
                    "period": scope["period"],
                    "org_unit": scope["org_unit"],
                }
            elif row.get("adapter") == "connected_capability" or row.get("capability_reference"):
                comparison_source = "connected_capability"
                comparison_payload = {
                    "unavailable": True,
                    "reason": row.get("capability_reference")
                    or "No allowlisted connected capability result available.",
                    "reference": row.get("capability_reference"),
                    "period": scope["period"],
                    "org_unit": scope["org_unit"],
                }
            elif row.get("unresolved") or not row.get("source_uid"):
                comparison_source = "unresolved"
                comparison_payload = {
                    "unavailable": True,
                    "reason": row.get("notes") or "Unresolved — no comparable UID/source.",
                    "period": scope["period"],
                    "org_unit": scope["org_unit"],
                }
            elif row.get("result_type") == "count":
                # Counts: compare only when a snapshot exists; otherwise mark unavailable
                # rather than inventing a second source.
                if row.get("indicator_key") not in snap_map:
                    comparison_source = "npmo_or_snapshot"
                    comparison_payload = {
                        "unavailable": True,
                        "reason": (
                            "No structured NPMO value or saved evidence snapshot for this count yet. "
                            "HTML scrape is not used."
                        ),
                        "period": scope["period"],
                        "org_unit": scope["org_unit"],
                    }

            comparisons.append(
                build_comparison_row(
                    primary_row=row,
                    comparison_source=comparison_source,
                    comparison_payload=comparison_payload,
                    scope=scope,
                )
            )

        summary = summarize_comparisons(comparisons)
        total_ms = int((time.perf_counter() - started) * 1000)
        package = export_package_meta(
            kind="validation",
            environment=scope["environment"],
            period=scope["period"],
            org_unit=scope["org_unit"],
            generated_at=datetime.now(timezone.utc).isoformat(),
            source_versions={
                "registry": "config/hcsc_indicators.yaml",
                "npmo_report_uid": self.registry().get("npmo_report_uid"),
                "adapters": report.get("adapters_used") or [],
            },
        )
        return {
            "ok": True,
            "scope": scope,
            "package": package,
            "compare_sources_label": COMPARE_SOURCES,
            "review_differences_label": REVIEW_DIFFERENCES,
            "summary": summary,
            "comparisons": comparisons,
            "filters": {
                "categories": sorted(
                    {
                        c.get("display_group_label") or c.get("section_label") or c.get("section")
                        for c in comparisons
                        if c.get("display_group_label") or c.get("section")
                    }
                ),
                "statuses": sorted({c.get("validation_status") for c in comparisons if c.get("validation_status")}),
                "sources": sorted(
                    {
                        c.get("comparison_source_label") or c.get("comparison_source")
                        for c in comparisons
                        if c.get("comparison_source_label") or c.get("comparison_source")
                    }
                ),
            },
            "timings": {
                "total_ms": total_ms,
                "report_ms": (report.get("timings") or {}).get("total_ms"),
                "http_requests": (report.get("timings") or {}).get("http_requests"),
                "report_cache_hit": (report.get("cache") or {}).get("hit"),
            },
            "retrieval": report.get("retrieval"),
            "dhis2_writes": 0,
            "sql_executed": False,
            "boundaries": {
                "readonly": True,
                "no_formula_engine": True,
                "no_html_scrape": True,
                "no_sql_auto_execute": True,
            },
        }

    def save_validation_snapshot(
        self,
        *,
        environment: str,
        period: str,
        org_unit: str,
        disaggregation: str = "none",
        note: str | None = None,
        evidence_path=None,
    ) -> dict[str, Any]:
        from hub.hcsc_indicators.evidence import save_snapshot

        workspace = self.validation_workspace(
            environment=environment,
            period=period,
            org_unit=org_unit,
            disaggregation=disaggregation,
            evidence_path=evidence_path,
        )
        saved = save_snapshot(
            environment=workspace["scope"]["environment"],
            period=workspace["scope"]["period"],
            org_unit=workspace["scope"]["org_unit"],
            disaggregation=disaggregation or "none",
            comparisons=workspace.get("comparisons") or [],
            report_meta={
                "timings": workspace.get("timings"),
                "summary": workspace.get("summary"),
                "package": workspace.get("package"),
            },
            note=note,
            path=evidence_path,
        )
        return {
            "ok": True,
            "snapshot": saved,
            "summary": workspace.get("summary"),
            "package": workspace.get("package"),
        }

    def add_validation_note(
        self,
        *,
        note: str,
        indicator_key: str | None = None,
        environment: str | None = None,
        period: str | None = None,
        org_unit: str | None = None,
        evidence_path=None,
    ) -> dict[str, Any]:
        from hub.hcsc_indicators.evidence import add_investigation_note

        return add_investigation_note(
            note=note,
            indicator_key=indicator_key,
            environment=environment,
            period=period,
            org_unit=org_unit,
            path=evidence_path,
        )
