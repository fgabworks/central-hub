# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Dashboard Quick Notepad** (same scratchpad as Notebook) plus SQL Workspace and
registry management.

- Dashboard right-side Quick Notepad reuses `QuickNotepadStore` / `/api/notebook/notepad*`
  (no second pad); collapsible + drawer; Saving… / Saved / Save failed
- `/sql` read-only SQL Workspace unchanged in scope
- Prior work: Notebook, registry, enrichment, GET-only DHIS2

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_quick_notepad tests.test_dashboard_notebook tests.test_sql_workspace -v
python app.py
```

1. Dashboard → Quick Notepad shows same text as Notebook; edit autosaves
2. Collapse on Dashboard → still collapsed on Notebook refresh
3. Clear / Convert to Note still work from Dashboard
4. `/sql`, repositories, jobs, health, audit, DHIS2 still load

## Next task

Optional: set `DATA_SCRIPT_PATH` / `REPORT_TEMPLATE_PATH` after manual clone.
Optional: enrichment Phase A completeness; Notebook agent assist (deferred).
Keep DHIS2 writes off.

## Research (2026-07-25)

[docs/LIVE_PROCESSING_DHIS2_DATA_SOURCES.md](LIVE_PROCESSING_DHIS2_DATA_SOURCES.md) —
API + repository mappings (provenance-aware); no hub SQL/SSH for metadata.
