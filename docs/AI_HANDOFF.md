# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Personal / Work workspace organization** — single Notebook model with `scope`,
sidebar switcher, **separate** Quick Notepads per workspace.

- Nav groups: Personal · Work · System; last workspace remembered (cookie + `hub_prefs`)
- Existing notes categorized as **work**; personal notes need no repositories
- Existing Quick Notepad content kept on the **personal** pad; work pad is separate
- `/` and `/notebook` remain via redirects / POST compat
- Prior: SQL Workspace, registry, enrichment, GET-only DHIS2

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_workspace_scope tests.test_quick_notepad tests.test_dashboard_notebook tests.test_notebook tests.test_sql_workspace -v
python -m unittest discover -s tests -v
python app.py
```

1. Switcher Personal ↔ Work remembers selection after refresh
2. Work Dashboard queue shows only work notes; Personal Dashboard shows Quick Notepad
3. `/notebook?note=<id>` opens the correct scoped notebook
4. `/sql`, repositories, jobs, health, audit, DHIS2 still load

## Next task

Optional: set `DATA_SCRIPT_PATH` / `REPORT_TEMPLATE_PATH` after manual clone.
Optional: enrichment Phase A completeness; Notebook agent assist (deferred).
Keep DHIS2 writes off.

## Research (2026-07-25)

[docs/LIVE_PROCESSING_DHIS2_DATA_SOURCES.md](LIVE_PROCESSING_DHIS2_DATA_SOURCES.md) —
API + repository mappings (provenance-aware); no hub SQL/SSH for metadata.
