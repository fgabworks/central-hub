# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Assistant / IDE phase — checkpoint (2026-08-02)**

Stabilization complete (no major new features):

- Aira/Okarun **full-height** right dock (`bottom: 0`); composer fixed at bottom; conversation scrolls
- Workspace Console **under main content only** (does not compress Okarun); **collapsed by default**; `Ctrl+J`
- Quick Notepad on **activity rail** (no floating pill); docks beside Okarun
- Compact Work Dashboard summary tiles; prefs persist width/height/visibility/tabs
- Personal↔Aira / Work↔Okarun isolation; read-only; lazy providers; paused polling when hidden

### Remaining Assistant limitations

- No Claude / Cursor Agent / Grok as first-class live providers beyond detection stubs
- No voice input/output; no autonomous write/execute actions
- Terminal remains allowlisted repository profiles only (no free-form shell)
- Codex live smoke depends on local `codex` install + login; unit/safety tests cover the adapter path
- Activity-rail SQL/Calendar/Audit icons are placeholders

### Next phase (do not start unless asked)

**Standard Reports** — DHIS2 report library/viewer work (credentialed HTML view may already be in the working tree; review there).

## Focused verification

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_assistant_dock tests.test_workspace_console tests.test_perf_navigation tests.test_dashboard_notebook tests.test_codex_cli -v
```

## Verify manually

1. Hard-refresh (`?v=shell-dock-3`)
2. Open Okarun — panel fills the right edge to the bottom; no blank gap under the composer
3. Open Console (`Ctrl+J`) — console sits under main content only; Okarun stays full height
4. Open Notepad from the rail — left of Okarun, not over the composer
5. Switch Personal / Work — Aira vs Okarun; histories stay isolated

## Do not implement unless asked

Free-form terminal · auto-kill ports · SPA rewrite · DHIS2 writeback · voice · new AI providers
