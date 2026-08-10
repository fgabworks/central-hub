# ARCHITECTURE.md — Components and Boundaries

Agent rules: [AGENTS.md](AGENTS.md). Current state: [AI_REFERENCE.md](AI_REFERENCE.md).

## Core rule

> Central Hub **coordinates** connected repositories. Connected repositories
> **execute** and remain the source of truth for their own logic.

The hub owns: registry (optional `repository_group_id` UI grouping), adapters, **job engine (SQLite + worker)**, UI, health checks,
audit, a **read-only DHIS2 Web API client**, a **local metadata capability catalog**,
a **local UID mapping index**, a preview-only metadata builder, a **Repository Notebook** (local SQLite notes
linked to registry repos), a **SQL Workspace** (read-only query library/runner), an
**Email Center** (Gmail `gmail.readonly` OAuth; shared Personal/Work service), a
**Calendar Center** (Calendar readonly; reuses the same Google accounts), and a
**AI Assistant Center** (read-only multi-agent orchestration with isolated Personal and Work profiles).
Connected repos own: domain rules, data models, their own APIs and secrets handling.
The hub must never duplicate PMNP, DHIS2 *domain/business* logic, reporting,
convergence, immunization, DDS, tetanus, or scorecard rules — those stay in the
repositories that own them. Generic metadata GET/discovery and generic CSV/JSON
UID indexing are allowed; writes to DHIS2 are not. Mapping sources are configured
in YAML paths — the hub does not import connected-repo Python packages.

## Components (current)

```text
Browser ──HTTP──> Flask app (app.py, create_app)
                      │
        ┌─────────────┼──────────────┬──────────────────┬────────────┐
        ▼             ▼              ▼                  ▼            ▼
  hub/settings.py  hub/registry/  hub/adapters/   hub/dhis2/     hub/jobs/
  hub/audit/       config/*.yaml  hub/notebook/   (GET-only)     SQLite worker
                                  hub/sql_workspace/             data/hub.db
                                  hub/email/                     data/email.db
                                  hub/calendar/
                                  hub/agent_center/              data/agent_center.db
                                  data/notebook.db
                                  data/sql_workspace.db
                                                                 data/{uploads,results,jobs}/
```

- **`hub/agent_center/`** — shared AI Assistant Center orchestration engine for
  **Aira** (Personal & General) and **Okarun** (Work & Data). Histories,
  conversations, summaries, settings, context, and permissions are profile-isolated.
  Find/Ask/Plan/Review are read-only;
  provider-neutral connection and adapter registry for Hub Simulator / OpenAI Responses API /
  Grok Responses API / Claude Code / Cursor Agent / Codex / future agents. Models are loaded
  from provider-supported discovery surfaces rather than a configured model-name list.
  grouped selector, mode recommendations, reasoning effort, Pro background/timeouts,
  search-first context with secret exclusion + selected-repo AI instructions,
  cancellable/retryable runs, tool activity + usage, and redacted local history/audit.
  Email/Calendar/Notebook lookup is opt-in and forces the active workspace. Aira
  cannot select Work repositories, SQL, DHIS2, jobs, logs, or Audit. Edit/Test,
  execution, and writes are unavailable; agent output is untrusted.
- **`hub/calendar/`** — Shared Calendar Center: readonly Google Calendar API + FullCalendar
  grid (month/week/day) and list agenda/upcoming, JSON event feed, sanitized HTML
  descriptions, read-only detail drawer. Reuses `hub/email/` accounts, encrypted tokens,
  and incremental OAuth scopes. No writes/RSVP/drag/resize/push; assistant lookup
  is opt-in, read-only, and workspace-scoped.
- **`hub/email/`** — Shared Email Center: OAuth 2.0 web-server flow, multi-account
  Personal/Work assignment, Fernet-encrypted refresh tokens in `data/email.db`,
  Gmail REST readonly client, limited list/message cache + manual refresh.
  Convert-to-note/task and work repo linking use `hub/notebook/`. No send/modify;
  assistant search is opt-in, read-only, and workspace-scoped.
  Incremental Calendar scopes are requested via the same OAuth helpers.
- **`hub/sql_workspace/`** — Read-only SQL Workspace: query library (folders, tags,
  favorites, versions), run history in `data/sql_workspace.db`, connection profiles
  from `config/sql_connections.yaml` + env secrets, sqlglot AST safety, RO executor
  (timeout, cancel, row cap, CSV). Does not import connected-repo packages or copy
  LP domain SQL.
- **`hub/notebook/`** — Repository Notebook: local notes with statuses, filters,
  Markdown body, checklist, links, multi-repo relationship roles, pin flag, and
  activity history. SQLite + migrations under `data/notebook.db`. Dashboard Open
  Tasks / Work Queue (`hub/notebook/dashboard.py`) reads the same store (no
  duplicated note data). Uses the registry for repo pickers but keeps
  denormalized labels when a repo disappears. No agent integration or automatic
  scanning yet.

- **`hub/repository_workspace/`** — Repository Workspace Phases 1–2 for configured
  **local** checkouts only. Phase 1: browse / preview / search / safe text edit /
  Git inspect (path jail; no auto-clone; no commit / push / pull / merge / reset /
  checkout). Phase 2: approved **run profiles** — YAML templates in
  `config/run_profiles.yaml` plus repository-specific profiles in SQLite
  (`profile_store.py` / `data/repository_workspace.db`, DB overrides by id).
  Settings → Run Profiles builder manages CRUD/test/preview. Launch uses
  `shell=False` argv arrays, port modes (none/fixed/argument/env), process-group
  isolation, hub-tracked PIDs only, redacted logs, and optional health probes.
  **Repository Processes** (`process_detect.py`) inventories related local PIDs and
  stops only verified trees; Health → Local Process Monitor is read-only reuse.
  `hub_process_manager.py` extends these primitives for the Hub itself: atomic
  single-instance PID/identity lock, strict absolute-app/port verification,
  owner-confirmed lifecycle actions, detached clean restart, health/new-PID result,
  and shared AuditStore records. It is not a second generic process manager.
  Tabs: Overview / Files / Changes / Run / Logs / Settings. Connect Local Workspace
  (`connect_scan.py` / `connect.py`) scans a user-selected folder read-only, queues
  untrusted suggestions into the profile store (never overwrites approved), then
  saves the path only after confirm. Agents reuse workspace file search/read; agent
  file edits and command execution stay disabled.

- **`hub/workspace_console/`** — VS Code-style bottom console (Problems / Output /
  Debug / Terminal / Ports). Interactive Terminal uses `hub/workspace_console/terminal/`
  (PTY session manager + ConPTY/`pty` + `flask-sock` WebSocket + xterm.js). Approved
  run-profile launch remains available as a secondary action. Ports annotates
  terminal-owned PIDs and reuses verified stop paths. Console prefs persist height /
  visibility / selected session id (never command text). Active PTYs keep running when
  the console is collapsed; UI rendering pauses and shows a badge.
- **`hub/jobs/`** — SQLite job store, daemon worker, allowlisted command/API executors,
  upload/result helpers, optional owner token. Capabilities declared in
  `config/repositories.yaml` only.

- **`hub/dhis2_reports/`** — DHIS2 Reports (`/dhis2/reports`):
  - **Phase 1 Standard Report Manager:** sync accessible DHIS2 standard reports via
    GET `/api/reports` into a Stage/Live-separated metadata cache (SQLite). View via
    DHIS2 `/data.html` embed (prefer DHIS2 rendering) with Open-in-DHIS2 fallback;
    HTML design source / download through the GET-only client. No report replacement;
    no direct DB access. DHIS2 remains source of truth.
  - Catalog shortcuts (YAML) for `repository_html` / `static_html` / optional
    `dhis2_standard` URL helpers: `config/dhis2_reports.yaml`.
  - Presets, history, and sandboxed local HTML viewer remain for catalog runs.

- **`hub/dhis2/client.py`** — GET-only Session client: probe vs operation timeouts,
  bounded GET retries, HTTP pool, `iter_collection` with hard `max_pages` ceiling,
  `get_text` for HTML report bodies, request stats. No write methods. Reliability
  knobs via `.env` (see `.env.example`).
  Pattern source: [docs/LIVE_PROCESSING_PATTERNS.md](docs/LIVE_PROCESSING_PATTERNS.md).
- **`hub/dhis2/catalog.py`** — discovers system/me/authorities/schemas/openapi/api,
  persists a local capability catalog (schemas + endpoint definitions; not a full
  metadata dump). See [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
- **`hub/dhis2/uid_mapping/`** — scans configured repository mapping files into a
  normalized local UID index; explores relationships; compares to live DHIS2 via GET.
  Sources: `config/uid_mapping_sources.yaml`. Conflicts never overwrite silently.
  Controlled update UX: `/dhis2/uid-index/manage` (hub JSON archive only; never writes
  connected-repo CSV files).
- **`hub/dhis2/enrichment/`** — read-only DHIS2 metadata enrichment and relationship
  audit. Uses the repository UID index as the UID set and live GET metadata as the
  authoritative configuration source. Persists normalized objects, one-to-many
  relationships, option-set options, and versioned snapshots in
  `data/dhis2/enrichment.db`. Workflow: fetch → preview → typed confirm → local
  snapshot. Never creates/updates/deletes/imports metadata in DHIS2.
- **`hub/dhis2/workspace.py` + `hub/dhis2/builders/`** — Unified Metadata Builder
  (preview-only). Types come from the discovered catalog; specialized builders
  override three config types; `GenericSchemaBuilder` covers
  `generic_schema_builder` types; `read_only_explorer` is excluded. Drafts are
  local only; Create/Update/Delete/Import disabled.
- **`hub/dhis2/uid_index.py`** — read-only builder adapter over the existing mapping
  store. It supports dependency selectors and local duplicate checks; conflicting
  mappings deliberately resolve to no selectable dependency.
- **`hub/dhis2/drafts.py`** — local form/raw JSON snapshots under the gitignored
  data tree. Draft storage never calls DHIS2.
- **`hub/audit/`** — append-only JSONL audit store for operator actions.

## Adapter design

Repository Intelligence is a read-only extension of the existing registry and Agent Center
context pipeline, not a second assistant. `RepositoryIntelligenceService` resolves only
enabled local command repositories from `config/repositories.yaml`, stores compact profiles
and per-file summaries in Agent Center SQLite, and exposes bounded retrieval to routing,
planning, and execution context. Git uses fixed argv, `shell=False`, and a timeout. The
index is contextual only; live RO database and DHIS2 results remain authoritative.
Scan events are stored separately from profiles, and AiriX telemetry carries only a compact
diagnostic projection. Standard scanning has no provider seam; Deep AI Analysis remains a
disabled future capability.

`AgentConnectionRegistry` owns installation, authentication, account-label, capability,
health-check, disconnect, and model-refresh state. Routes and templates call this interface;
provider-specific commands and HTTP behavior remain in `hub/agent_center/adapters/`.
The same adapter selected for a prompt supplies its dynamic models and runner. Run context
records the assistant profile, provider, model, mode, repository scope, and context preview.

Assistant profiles are an orthogonal boundary: connection state is shared system configuration,
while conversations, summaries, source permissions, and histories remain keyed by `profile_id`.

| Adapter | Mechanism | Scope |
|---|---|---|
| API | Health probe + capability GET/HEAD from YAML `http_path` | No hub-owned write endpoints |
| Command | Health allowlist + capability `command_template` from YAML | `shell=False`, cwd jail, timeouts, cancel checks |

Controls: [SECURITY.md](SECURITY.md).

## Configuration-driven integration

Every connected repository is a YAML entry with `id`, `type`, connection info,
`health_check`, and declared `capabilities`. Adding an integration means editing
`config/repositories.yaml` — never adding hardcoded paths or clients to code.
Relative paths resolve from the hub root.

DHIS2 connection settings are env-driven (`DHIS2_*` in `.env`), separate from the
repository registry. No DHIS2 credentials belong in YAML or templates.

## Sensitive operation lifecycle

All future write-capable operations follow:
**Validate → Preview → Confirm → Execute → Verify → Audit.**
DHIS2-specific expansion: [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
