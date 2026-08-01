# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**HCSC Phase 3 Validation + UI visibility fix (2026-08-02)**

### Root cause (invisible / non-interactive HCSC page)

1. Template used `{% block head %}` but `base.html` only defines `{% block head_extra %}` — **JS never loaded**.
2. DHIS2 Overview tools grid was **hardcoded** and omitted HCSC Indicators (and other `_DHIS2_TOOLS` entries).
3. Bootstrap JSON lived in a single-quoted HTML attribute (fragile); moved to `<script type="application/json" id="hcsc-bootstrap">`.
4. Page endpoint renamed to `dhis2_hcsc_indicators` so Work → DHIS2 nav `active_prefix` highlights correctly.

### Phase 3 Validation

Read-only Validation workspace comparing report results to:
- same-batch analytics N/D
- local evidence snapshots
- approved SQL / capabilities marked **Comparison Source Unavailable** (no auto-execute)

API: `GET /api/dhis2/hcsc-indicators/validation`, snapshot + investigation notes POSTs. Evidence DB under `data/hcsc_validation_evidence.db` (gitignored).

### Unresolved (unchanged)

Convergent Units · Pct_Convergence_Mun · Overview IP/non-IP · balanced-food frequency rate · SQL lineage SoT

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_hcsc_indicators -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open Work → DHIS2 → HCSC Indicators, or `/dhis2/hcsc-indicators` (refresh / back-forward).
