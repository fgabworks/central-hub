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
- Free-form shell / unrestricted terminal execution
- Multi-user auth beyond a single local owner token
- Assistant file edits, commands, SQL execution, email/calendar actions, DHIS2
  writes, repository runs, voice input, and text-to-speech

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

Optional sample API for healthy `sample-api` checks:

```powershell
python samples/sample-api/app.py
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

Focused assistant suite: `python -m pytest tests/test_ai_connections.py
tests/test_agent_center.py tests/test_openai_agent.py tests/test_ai_assistant_center.py -q`

## Docs for agents

Start at [AI_START_HERE.md](AI_START_HERE.md) → [AGENTS.md](AGENTS.md).
