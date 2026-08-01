# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**VS Code-style Workspace Console (bottom dock) — 2026-08-02**

- Bottom panel with Problems / Output / Debug Console / Terminal / Ports
- Toggle via topbar **Console** button and `Ctrl+J`
- Drag resize, minimize / maximize / restore / close
- Height + active tab persisted per workspace
- Main content uses `padding-bottom`; right Aira/Okarun dock remains independent
- Lazy tab loads; polling pauses when hidden/minimized/closed
- Terminal = approved repository run profiles only (no free shell)
- Ports reuses process monitor; stops require confirm + identity token

Focused verification:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_workspace_console tests.test_assistant_dock tests.test_perf_navigation -v
```

## Prior milestones (retained)

**Codex CLI first real provider — Okarun MVP**

**VS Code / Cursor-style assistant right rail**

**Navigation performance** — defer probes + Server-Timing

**AI Connections** — shared provider registry

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_workspace_console tests.test_assistant_dock -v
python app.py
```

1. Hard-refresh; click **Console** or press `Ctrl+J`
2. Open Okarun on the right and Console at the bottom together — both should resize content
3. Switch tabs; leave Console and confirm network polling stops when closed/hidden
4. Terminal lists approved profiles only; Ports Stop asks for confirmation

## Next task

Do **not** implement yet unless asked:

- Free-form terminal / unrestricted shell
- Auto-killing processes on fixed ports
- SPA rewrite
- DHIS2 writeback into Live Processing UID index

Keep DHIS2 writes off. Assistant stays read-only / workspace-scoped.
