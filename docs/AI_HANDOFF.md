# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**HCSC Indicator Summary Phase 2 — broader indicators (2026-08-02)**

### What changed

Registry-backed maternal / child / WASH·SBC / food-security indicators via **batched DHIS2 analytics** (no formula copy). Overview (Phase 0–1) preserved. Category + full report APIs with env/period/OU caches.

| Area | Detail |
|---|---|
| Adapters | `dhis2_analytics` (batched GET), `approved_sql` (reference/deferred), `connected_capability` (deferred) |
| Sections | Eligible Beneficiaries · Maternal · Child Nutrition & Health · Household/WASH/SBC · Food Security · Convergence · Data Mapping · Validation |
| APIs | `/overview` (unchanged set), `/report` (full), `/category/<section>` |
| UI | Sectioned Indicator Summary; Open in SQL Workspace when referenced; cache shown immediately |
| Result model | count / % / ratio / status / disaggregation + N/D labels, lineage, validation, unresolved notes |

### Unresolved (marked, not invented)

- Convergent Units — no dx UID (NPMO JS)
- Pct_Convergence_Mun — client-computed; may ≠ `qzjKcfO9J2w`
- Overview IP/non-IP disaggregation — planned
- Nutritious/balanced food frequency % — bucket PIs exist; no verified rate IND / capability
- HCSC / RF SQL Workspace query — lineage only, not SoT

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_hcsc_indicators -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open `/dhis2/hcsc-indicators` → Generate Report (loads `/api/dhis2/hcsc-indicators/report`).

---

## Previous: HCSC UI refine + Phase 0–1

Mockup table/tabs/drawer; registry YAML + batched analytics Overview — see `config/hcsc_indicators.yaml`, `hub/hcsc_indicators/`.
