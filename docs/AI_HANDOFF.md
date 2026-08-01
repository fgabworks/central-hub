# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Workspace Console → Terminal IDE UI (2026-08-02)**

Refine the bottom dock Terminal into a VS Code-style interactive PTY console matching the target layout.

### Layout

1. **Title row** — Workspace Console + Ctrl+J + minimize / maximize / close  
2. **Tabs row** — Problems | Output | Debug Console | Terminal | Ports  
3. **Terminal toolbar** — Repository · Shell · `+ New Terminal` · session tabs · Split / Restart / Kill  
4. **xterm.js stage** under the toolbar (empty state when no session)

### Behavior (implemented)

- Interactive ConPTY/`pty` sessions via WebSocket; path-jailed to connected repo local paths  
- Session tabs named like `PowerShell 1 — <repo>` (rename on double-click)  
- Split / Restart / Kill disabled until a session is active; Kill is destructive + confirm when processes are alive  
- Console collapse (`Ctrl+J`) pauses xterm rendering; PTY keeps running  
- Prefs persist height, tab, `terminal_session_id`, split  
- Ports annotates terminal-owned PIDs with session name + Open URL  
- Aira/Okarun **Insert into Terminal** fills text only (no Enter / no auto-exec)

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_wc_terminal tests.test_workspace_console -v
```

1. Ctrl+J → Terminal → pick repo/shell → **+ New Terminal**  
2. Confirm tab label, ANSI colors, Ctrl+C, resize, second tab, Kill confirm  
3. Collapse console while a process runs → reopen → session still live  
4. Ports shows terminal association / Open URL when a server listens

### Do not implement unless asked

Free-form shell · AI auto-exec · remote (non-localhost) PTY · restoring sessions after hub restart
