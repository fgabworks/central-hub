# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Central Hub HCSC–RF rename + classification grouping (2026-08-02)**

User-facing module name is **HCSC–RF** (Household Convergence Scorecard and Results Framework).
Page title: **Central Hub HCSC–RF**. Subtitle: **Indicators, Sources, and Validation**.
One page/route preserved: `/dhis2/hcsc-indicators` (`dhis2_hcsc_indicators`).

### Prior milestone (still in place)

**Phase 3 Validation + UI visibility fix**

1. Template used `{% block head %}` but `base.html` only defines `{% block head_extra %}` — **JS never loaded**.
2. DHIS2 Overview tools grid was **hardcoded** and omitted HCSC (and other `_DHIS2_TOOLS` entries).
3. Bootstrap JSON lived in a single-quoted HTML attribute (fragile); moved to `<script type="application/json" id="hcsc-bootstrap">`.
4. Page endpoint renamed to `dhis2_hcsc_indicators` so Work → DHIS2 nav `active_prefix` highlights correctly.

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
.\.venv\Scripts\python.exe -m unittest tests.test_hcsc_indicators -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open Work → DHIS2 → HCSC–RF, or `/dhis2/hcsc-indicators` (refresh / back-forward).
