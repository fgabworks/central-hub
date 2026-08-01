# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**HCSC–RF OU SQLite cache + quarter cap (2026-07-30)**

Reporting cycle capped to **2025Q3–2026Q4** (config-driven; invalid remembered quarters reset).
Organisation units served cache-first from env-isolated SQLite (`data/dhis2_org_units.db`) with
background DHIS2 refresh, manual **Refresh Organisation Units**, Stage/Live isolation, and Stage
maintenance messaging. Cascade/search/UID/APIs/registry/read-only unchanged.

Prior: **HCSC–RF preview UI refinement** + OU cascade + Stage maintenance handling.

### Prior milestone

**DHIS2 Run Report parameter pickers (2026-08-02)**

Run Report Period + Organisation Unit are searchable dropdown/combobox controls (no typed free-text submit). Reuses `/api/dhis2/reports/periods` and `/api/dhis2/reports/org-units`.

Prior: **Central Hub HCSC–RF rename + classification grouping** — see below.

### Compare Sources (Phase 3)

Read-only Compare Sources workspace comparing report results to:
- same-batch analytics N/D
- local evidence snapshots
- approved SQL / capabilities marked **Comparison Source Unavailable** (no auto-execute)

API: `GET /api/dhis2/hcsc-indicators/validation`, snapshot + investigation notes POSTs. Evidence DB under `data/hcsc_validation_evidence.db` (gitignored).

### Classifications (verified; no guessing)

- **HCSC** — scorecard / eligible beneficiary counts
- **RF** — maternal / child / WASH–SBC / food-security rates
- **Unresolved** — convergent units, Pct_Convergence_Mun, Overview IP/non-IP, nutritious-food frequency, SQL lineage SoT
- No **HCSC + RF** duals invented

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dhis2_report_params tests.test_dhis2_reports_bridge -v
.\.venv\Scripts\python.exe -m unittest tests.test_hcsc_indicators -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open Work → DHIS2 → Reports → Run Report, or `/dhis2/reports/run` (hard refresh for JS).
Open Work → DHIS2 → HCSC–RF, or `/dhis2/hcsc-indicators`.
