# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Repository registry management** (Add / Edit / Enable / Disable) plus connected
GitHub repos (Data-Script, PMNP Live Processing, Report Template). Builds on
Notebook Work Queue, DHIS2 enrichment, Phases 1–6.

- Active `config/repositories.yaml`: LP API + LP local checkout + Data-Script +
  Report Template (`git_url` + optional local path; no auto-clone)
- Demo `sample-*` removed from active registry → `tests/fixtures/repositories.yaml`
- UI: `/repositories/new`, edit, enable/disable; statuses Healthy / Unreachable /
  Not Cloned / Disabled
- Reuses matching local checkout when remote URL matches (depth-1 scan)
- Prior work unchanged: Notebook, enrichment, GET-only DHIS2

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_registry_store tests.test_registry_loader tests.test_jobs -v
python app.py
```

1. Repositories → see Data-Script / PMNP Live Processing / Report Template (no sample demos)
2. Add Repository form validates duplicates; Enable/Disable works; YAML updates
3. Missing local path with git_url → Not Cloned (hub does not clone)
4. LP API health still GET-only; Jobs page still works with fixture samples in tests
5. DHIS2 write methods still absent

## Next task

Optional: set `DATA_SCRIPT_PATH` / `REPORT_TEMPLATE_PATH` after manual clone.
Optional: enrichment Phase A completeness; Notebook agent assist (deferred).
Keep DHIS2 writes off.

## Research (2026-07-25)

[docs/LIVE_PROCESSING_DHIS2_DATA_SOURCES.md](LIVE_PROCESSING_DHIS2_DATA_SOURCES.md) —
API + repository mappings (provenance-aware); no hub SQL/SSH for metadata.
