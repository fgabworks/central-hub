# ARCHITECTURE.md — Components and Boundaries

Agent rules: [AGENTS.md](AGENTS.md). Current state: [AI_REFERENCE.md](AI_REFERENCE.md).

## Core rule

> Central Hub **coordinates** connected repositories. Connected repositories
> **execute** and remain the source of truth for their own logic.

The hub owns: registry, adapters, **job engine (SQLite + worker)**, UI, health checks,
audit, a **read-only DHIS2 Web API client**, a **local metadata capability catalog**,
a **local UID mapping index**, a preview-only metadata builder, a **Repository Notebook** (local SQLite notes
linked to registry repos), and a **SQL Workspace** (read-only query library/runner).
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
                                  data/notebook.db
                                  data/sql_workspace.db
                                                                 data/{uploads,results,jobs}/
```

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

- **`hub/registry/`** — Load/validate `config/repositories.yaml`, optional `git_url`,
  raw YAML writer for Add/Edit/Enable/Disable, Git remote matching for existing
  checkouts (never clones). Demo samples live only under `tests/fixtures/`.

- **`hub/jobs/`** — SQLite job store, daemon worker, allowlisted command/API executors,
  upload/result helpers, optional owner token. Capabilities declared in
  `config/repositories.yaml` only.

- **`hub/dhis2/client.py`** — GET-only Session client: probe vs operation timeouts,
  bounded GET retries, HTTP pool, `iter_collection` with hard `max_pages` ceiling,
  request stats. No write methods. Reliability knobs via `.env` (see `.env.example`).
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
