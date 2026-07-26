# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**DHIS2 Standard Report Manager — Phase 1** under `Work → DHIS2 → Reports` (`/dhis2/reports`)

- Sync accessible standard reports from Stage/Live via GET `/api/reports` (paginated)
- Local SQLite metadata/cache only; DHIS2 remains source of truth
- Library: Stage and Live lists separated; search/filters (type, env, HTML, favorite)
- Actions: View Report, Open in DHIS2, View HTML Source, Download HTML, Refresh Metadata
- Period + organisation-unit controls before render
- Prefer DHIS2 `/data.html` embed; fallback Open in DHIS2 when iframe/CSP/auth blocks
- No report replacement; no DHIS2 writes; no direct DB access; no credentials in UI/URLs

Package: `hub/dhis2_reports/` (+ `standard_sync.py`, `standard_models.py`). Catalog YAML remains for repository/static shortcuts only.

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_dhis2_standard_reports tests.test_dhis2_reports -v
python -m unittest discover -s tests -v
python app.py
```

1. Open `/dhis2/reports` → Sync Stage (and Live with confirm)
2. Open a report → set period/OU → View Report / Open in DHIS2
3. View HTML Source / Download HTML
4. Refresh Metadata on one report
5. Confirm Stage and Live lists stay separate; no passwords in page/network URLs

## Next task

Do **not** implement yet unless asked:

- Report replacement / design upload
- Writing report metadata back to DHIS2
- Direct DHIS2 database access
- Copying PMNP report calculation into the hub

Keep DHIS2 writes off. Do not auto-feed mail/calendar to agents.
