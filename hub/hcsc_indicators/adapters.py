"""Phase 2 source adapters — call DHIS2 / SQL Workspace / capabilities without copying HCSC formulas."""

from __future__ import annotations

from typing import Any, Protocol

from hub.dhis2.client import Dhis2Client
from hub.hcsc_indicators.analytics import fetch_analytics_batch, map_indicator_values


ADAPTER_DHIS2 = "dhis2_analytics"
ADAPTER_SQL = "approved_sql"
ADAPTER_CAPABILITY = "connected_capability"


class SourceAdapter(Protocol):
    name: str

    def supports(self, indicator: dict[str, Any]) -> bool: ...

    def retrieve(
        self,
        indicators: list[dict[str, Any]],
        *,
        environment: str,
        period: str,
        org_unit: str,
        client: Dhis2Client | None = None,
    ) -> dict[str, Any]: ...


class Dhis2AnalyticsAdapter:
    """Batched GET /api/analytics.json — DHIS2 owns the formulas."""

    name = ADAPTER_DHIS2

    def supports(self, indicator: dict[str, Any]) -> bool:
        if indicator.get("unresolved"):
            return False
        adapter = (indicator.get("adapter") or ADAPTER_DHIS2).strip().lower()
        if adapter not in {ADAPTER_DHIS2, "dhis2", ""}:
            return False
        return bool((indicator.get("dhis2_uids") or {}).get("value"))

    def retrieve(
        self,
        indicators: list[dict[str, Any]],
        *,
        environment: str,
        period: str,
        org_unit: str,
        client: Dhis2Client | None = None,
        ou_level: int | None = None,
    ) -> dict[str, Any]:
        if client is None:
            raise ValueError("Dhis2AnalyticsAdapter requires a read-only DHIS2 client.")
        from hub.hcsc_indicators.registry import collect_analytics_uids

        dx = collect_analytics_uids(indicators)
        batch = fetch_analytics_batch(
            client,
            dx_uids=dx,
            period=period,
            org_unit=org_unit,
            include_num_den=True,
            ou_level=ou_level,
        )
        values = batch.get("values") or {}
        num_den = batch.get("num_den") or {}
        rows: list[dict[str, Any]] = []
        for ind in indicators:
            mapped = map_indicator_values(ind, values, num_den=num_den)
            rows.append(
                {
                    "indicator_key": ind["key"],
                    "mapped": mapped,
                    "adapter": self.name,
                    "retrieval_method": "DHIS2 Analytics",
                }
            )
        return {
            "ok": True,
            "adapter": self.name,
            "retrieval_method": "DHIS2 Analytics",
            "rows": rows,
            "batch": batch,
            "invented": False,
            "dhis2_writes": 0,
        }


class ApprovedSqlAdapter:
    """Reference approved SQL Workspace queries — never invent SQL text."""

    name = ADAPTER_SQL

    def supports(self, indicator: dict[str, Any]) -> bool:
        adapter = (indicator.get("adapter") or "").strip().lower()
        return adapter in {ADAPTER_SQL, "sql", "approved_sql"} or bool(
            indicator.get("approved_sql_query_id") or indicator.get("approved_sql_reference")
        )

    def retrieve(
        self,
        indicators: list[dict[str, Any]],
        *,
        environment: str,
        period: str,
        org_unit: str,
        client: Dhis2Client | None = None,
    ) -> dict[str, Any]:
        # Phase 2: expose references + open-in-SQL-workspace hooks only.
        # Do not execute embedded HCSC CTE math as hub SoT (may diverge from DHIS2 PIs).
        rows = []
        refs = []
        for ind in indicators:
            refs.append(
                {
                    "indicator_key": ind["key"],
                    "approved_sql_query_id": ind.get("approved_sql_query_id"),
                    "approved_sql_reference": ind.get("approved_sql_reference"),
                    "open_sql_workspace": True,
                    "note": (
                        "Approved SQL is referenced for lineage/validation exploration. "
                        "Hub does not invent SQL or reimplement HCSC rate formulas."
                    ),
                }
            )
            rows.append(
                {
                    "indicator_key": ind["key"],
                    "mapped": {
                        "count": None,
                        "numerator": None,
                        "denominator": None,
                        "percentage": None,
                        "source_uid": (ind.get("dhis2_uids") or {}).get("value"),
                    },
                    "adapter": self.name,
                    "retrieval_method": "Approved SQL",
                    "deferred": True,
                    "reason": ind.get("notes")
                    or "SQL-backed HCSC parity deferred — use DHIS2 analytics UID when registered, or open approved query.",
                }
            )
        return {
            "ok": True,
            "adapter": self.name,
            "retrieval_method": "Approved SQL",
            "rows": rows,
            "sql_references": refs,
            "invented": False,
            "executed": False,
            "dhis2_writes": 0,
        }


class ConnectedCapabilityAdapter:
    """Connected-repo capability references (LP/data_scripts) — no local formula engine."""

    name = ADAPTER_CAPABILITY

    def supports(self, indicator: dict[str, Any]) -> bool:
        adapter = (indicator.get("adapter") or "").strip().lower()
        return adapter in {ADAPTER_CAPABILITY, "capability", "live_processing"} or bool(
            indicator.get("capability_reference")
        )

    def retrieve(
        self,
        indicators: list[dict[str, Any]],
        *,
        environment: str,
        period: str,
        org_unit: str,
        client: Dhis2Client | None = None,
    ) -> dict[str, Any]:
        rows = []
        for ind in indicators:
            rows.append(
                {
                    "indicator_key": ind["key"],
                    "mapped": {
                        "count": None,
                        "numerator": None,
                        "denominator": None,
                        "percentage": None,
                        "source_uid": None,
                    },
                    "adapter": self.name,
                    "retrieval_method": "Connected Repository Capability",
                    "deferred": True,
                    "capability_reference": ind.get("capability_reference"),
                    "reason": (
                        ind.get("notes")
                        or "No allowlisted HCSC aggregate capability on connected repos yet."
                    ),
                }
            )
        return {
            "ok": True,
            "adapter": self.name,
            "retrieval_method": "Connected Repository Capability",
            "rows": rows,
            "invented": False,
            "executed": False,
            "dhis2_writes": 0,
        }


def select_adapter(indicator: dict[str, Any]) -> str:
    adapter = (indicator.get("adapter") or "").strip().lower()
    if adapter in {ADAPTER_SQL, "sql", "approved_sql"}:
        return ADAPTER_SQL
    if adapter in {ADAPTER_CAPABILITY, "capability", "live_processing"}:
        return ADAPTER_CAPABILITY
    if adapter in {ADAPTER_DHIS2, "dhis2"} and (indicator.get("dhis2_uids") or {}).get("value"):
        return ADAPTER_DHIS2
    if (indicator.get("dhis2_uids") or {}).get("value"):
        return ADAPTER_DHIS2
    if indicator.get("approved_sql_query_id") or indicator.get("approved_sql_reference"):
        return ADAPTER_SQL
    if indicator.get("capability_reference"):
        return ADAPTER_CAPABILITY
    return "unresolved"


def get_adapters() -> dict[str, Any]:
    return {
        ADAPTER_DHIS2: Dhis2AnalyticsAdapter(),
        ADAPTER_SQL: ApprovedSqlAdapter(),
        ADAPTER_CAPABILITY: ConnectedCapabilityAdapter(),
    }
