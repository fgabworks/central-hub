# README.md — Central Hub

Personal multi-repository control center. Coordinates connected repositories through
a config-driven registry and adapters. Connected repos remain the source of truth
for their own logic.

**Current status: Phases 1–6 MVP** — registry, health, DHIS2 read-only tools,
job engine (SQLite), safe command/API capabilities, uploads/results, owner role.

## What is included

- Repository registry (`config/repositories.yaml`) + health checks
- Repository Workspace: local browse/edit + Run Profile Builder (YAML templates + SQLite repo profiles)
- DHIS2 Reports: Standard Report Manager Phase 1 (sync/view Stage+Live `/api/reports`) + catalog shortcuts (`config/dhis2_reports.yaml`)
- DHIS2 GET client, discovery/catalog, UID mapping explorer, preview metadata builder
- Job engine: submit / list / poll / cancel / pause / resume
- Allowlisted command templates + GET-only API capabilities
- Uploads under `data/uploads/{job_id}/`, results under `data/results/{job_id}/`
- Confirm gates for non-dry-run; optional `CENTRAL_HUB_OWNER_TOKEN`
- Audit JSONL + SQLite job history
- AI Assistant Center: Aira under Personal and Okarun under Work, using one
  read-only engine with isolated histories, summaries, context, and tools

## What is *not* included

- DHIS2 create/update/delete/import (writes stay off)
- PMNP / Live Processing / report calculation domain logic inside the hub
- Free-form / unrestricted shell outside connected repository paths
- Arbitrary working directories, symlink/junction escapes, or user-supplied shell binaries
- AI auto-execution of terminal commands (Aira/Okarun may suggest; user must insert + Enter)
- Multi-user auth beyond a single local owner token
- Assistant file edits, SQL execution, email/calendar actions, DHIS2
  writes, voice input, and text-to-speech

Interactive **repository terminals** (Workspace Console → Terminal) use a real PTY
(Windows ConPTY via pywinpty; Unix native PTY) over an authenticated localhost
WebSocket with xterm.js. Sessions start only inside enabled connected repo paths.

## AI Assistant Center

- **Aira**: /personal/aira; Personal Notebook/tasks, Quick Notepad, and explicitly
  selected Personal Email/Calendar search.
- **Okarun**: /work/okarun; selected repositories and instructions, Work Notebook,
  SQL library, DHIS2 UID metadata, Work Email/Calendar, jobs, and Audit.
- Adapters include Codex, Claude Code, Cursor Agent, Grok, OpenAI API, Hub Simulator,
  and future config-driven adapters. Models refresh for the selected adapter.
- System -> AI Connections provides Connect/Reconnect, Test Connection, Refresh Models,
  capability inspection, and Disconnect. Credentials remain in supported CLI storage or
  server environment variables; Aira/Okarun data remains isolated.
- Modes are Ask, Find, Plan, and Review. Context preview lists included and
  excluded sources. Runs support streaming, cancel, retry, files, tools, and usage.
- Context is search-first; repositories, mail, and documents are never bulk-loaded.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Open `http://127.0.0.1:8080`.

Direct `app.py` startup is single-instance. Prefer the launcher for terminal cleanup:

```powershell
python scripts/run_central_hub.py
```

Its token-matched PID/identity lock and owned-process registry live under
`data/central_hub_process/`; invalid or dead locks are cleaned on startup.
Health → Central Hub Process Manager lists owned hub processes (including `app.py`
labeled **Central Hub Server**) and other Python processes (view-only). Owner-gated
controls: per-process Stop/Restart, Stop Stale, typed **Stop Central Hub** (full owned
tree via detached supervisor), Stop All, and Restart Cleanly. The Werkzeug code
reloader is disabled to preserve one server process.

Optional sample API for healthy `sample-api` checks:

```powershell
python samples/sample-api/app.py
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

Focused terminal suite:

```powershell
python -m unittest tests.test_wc_terminal tests.test_workspace_console -v
```

On Windows with Python 3.14+, install `pywinpty` with:
`$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY='1'; pip install pywinpty`


## Docs for agents

Start at [AI_START_HERE.md](AI_START_HERE.md) → [AGENTS.md](AGENTS.md).
