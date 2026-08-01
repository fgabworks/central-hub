# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**HCSC–RF Work sidebar visibility (2026-08-02)**

Root cause: HCSC–RF lived only in `_DHIS2_TOOLS` (DHIS2 Overview tools grid), not in Work sidebar `work_nav`. Added sidebar entry below DHIS2 Reports → `/dhis2/hcsc-indicators` (`dhis2_hcsc_indicators`).

Prior: **DHIS2 Run Report parameter pickers** — searchable Period + OU.

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
