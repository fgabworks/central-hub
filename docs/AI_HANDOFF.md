# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**VS Code / Cursor-style assistant right rail (2026-08-02)**

- Persistent far-right rail toggle for Aira (Personal) / Okarun (Work)
- Click rail → panel opens and main content grid-shrinks; click again or × → hides
- Desktop docks as a grid column (not overlay/modal); mobile uses right overlay drawer
- Pin / minimize / drag-resize; prefs per workspace
- Panel: header+status, Conversation/Output, suggestions, context preview button,
  agent/model selector, fixed prompt
- Full Assistant Center pages keep advanced settings and also show the dock
- Lazy provider load; read-only; no voice/TTS

Focused verification: `python -m unittest tests.test_assistant_dock tests.test_perf_navigation -v`

## Prior milestones (retained)

**Navigation performance — defer probes + Server-Timing**

- Shell routes avoid sync health/calendar/process/AI probes
- Secondary panels hydrate via async APIs

**AI Connections** — shared provider registry for isolated Aira/Okarun profiles.

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_perf_navigation tests.test_assistant_dock -v
python app.py
```

1. Navigate Work ↔ Personal ↔ Repositories — shell should feel instant
2. DevTools Network: HTML responses include `Server-Timing`; health/calendar fetch after paint
3. AI Connections: statuses appear immediately, then refresh in background
4. Health → process rows fill after shell; Re-check uses `?fresh=1`

## Next task

Do **not** implement yet unless asked:

- Writing DHIS2-imported UIDs back into Live Processing’s `AI_UID_INDEX.csv` automatically
- Auto-killing processes that occupy fixed ports
- Free-form terminal / unrestricted shell
- SPA rewrite

Keep DHIS2 writes off. Never preload mail/calendar on navigation; assistant lookup stays
explicit, read-only, minimal, and workspace-scoped.
