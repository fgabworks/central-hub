"""HCSC Indicator Summary & Data Lineage — NPMO (Phase 0–1).

Read-only registry + batched DHIS2 analytics Overview.
Does not reimplement HCSC / convergence / scorecard formulas.
"""

from hub.hcsc_indicators.service import HcscIndicatorService

__all__ = ["HcscIndicatorService"]
