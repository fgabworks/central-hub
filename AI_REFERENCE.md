# AI_REFERENCE.md — Verified Current State

Last verified: 2026-07-25 (Personal/Work workspaces + SQL Workspace + Notebook).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ Personal/Work workspace switcher + registry Add/Edit/Disable + SQL Workspace (read-only).**
Hub coordinates repos via registry/adapters; DHIS2 stays GET-only; jobs run
allowlisted capabilities only.

| Area | State |
|---|---|
| Registry + health | `config/repositories.yaml`, `${VAR:-default}` expansion, `hub/adapters/` |
| Registry management | Add / Edit / Enable / Disable via UI → YAML (`hub/registry/store.py`); no auto-clone |
| Health probes | Parallel checks; states: Healthy / Unreachable / Not Cloned / Disabled |
| Live Processing | `live-processing` (API GET-only) + `live-processing-local` (path + git_url) |
| Data-Script / Report Template | Registered with GitHub URLs; local path optional (`DATA_SCRIPT_PATH`, `REPORT_TEMPLATE_PATH`) |
| Workspaces | Personal / Work switcher (cookie + `hub_prefs`); System nav always visible |
| Personal Dashboard | `/personal` — personal tasks/notes + Quick Notepad (no repos/DHIS2) |
| Work Dashboard | `/work` (legacy `/` redirects here by remembered workspace) — repos, work queue, DHIS2 |
| Repository Notebook | Scoped notes (`personal` \| `work`); work keeps repo links; personal needs none |
| SQL Workspace | Read-only query library/runner (`/sql`); sqlglot allowlist; Live warning |
| DHIS2 | GET client, discovery, UID mapping, preview builder |
| UID index admin | LP-style controlled update: dry-run → preview → typed confirm → archive/versions/restore |
| Metadata enrichment | Read-only DHIS2 enrich → local SQLite relationships + audit statuses |
| Explorer | Prefer enrichment snapshot when present; tabs + filters; lazy raw metadata |
| Jobs (Phase 2) | SQLite `data/hub.db`, submit/list/get, worker, logs |
| Command exec (Phase 3) | YAML `command_template`, `shell=False`, cwd jail |
| API exec (Phase 4) | GET/HEAD only from YAML `http_path` |
| Files (Phase 5) | Uploads/results under `data/{uploads,results}/{job_id}/` |
| Safeguards (Phase 6) | Dry-run default, confirm for apply, max concurrent, owner token |
| Tests | `tests/` — registry store + fixtures for demo samples; active YAML has no samples |
| DHIS2 writes | **Disabled** |

## Connected repositories (active registry)

| id | Role |
|---|---|
| `live-processing` | API — GET health/history/preview |
| `live-processing-local` | Local checkout of same GitHub repo (`LIVE_PROCESSING_PATH`) |
| `data-script` | Git URL `PMNP-IS/Data-Script` — Not Cloned until path set |
| `report-template` | Git URL `PMNP-IS/REPORT_TEMPLATE` — Not Cloned until path set |

Demo `sample-*` entries removed from the active registry; job tests use
`tests/fixtures/repositories.yaml`.

## Repository Notebook + workspaces

| Route | Purpose |
|---|---|
| `/` | Redirects to remembered Personal or Work dashboard |
| `/workspace/<personal\|work>` | Switch workspace (cookie + `hub_prefs`) |
| `/personal` | Personal Dashboard + Quick Notepad |
| `/personal/notebook` | Personal notes/tasks (no repository required) |
| `/personal/tasks` | Personal open tasks list + Quick Notepad |
| `/work` | Work Dashboard (repos, work queue, DHIS2) |
| `/work/notebook` | Work notes with repository links |
| `/notebook` | Compat: GET redirects by note scope / workspace; POST handled in scope |
| `/notebook/<id>/export` | Download note JSON |
| `/api/notebook/preview` | Markdown → HTML preview |
| `/api/notebook/notepad*` | Quick Notepad (Personal) GET/PUT/clear/convert/restore |

Store: `data/notebook.db` (`hub/notebook/`) migrations include `pinned`, `quick_notepad`,
`scope` (`personal`\|`work`) + `hub_prefs`. Existing notes migrate to **work**.
Quick Notepad remains a **single** scratchpad under Personal (not a second pad).
Convert → creates a **personal** structured note. Work Dashboard queue shows work-scoped
notes only. No agent integration yet.

## SQL Workspace

| Route | Purpose |
|---|---|
| `/sql` | Query library + editor + results (Save / Format / Explain / Run) |
| `/api/sql/run` | Validate + execute one read-only statement |
| `/api/sql/queries` | Create/update saved queries (versions; never auto-run) |
| `/api/sql/connections/<id>/test` | Server-side connection probe |
| `/api/sql/runs/<id>/csv` | Export run results CSV |
| `/api/sql/runs/<id>/cancel` | Cooperative cancel |

Local store: `data/sql_workspace.db`. Connections: `config/sql_connections.yaml` + env secrets (`.env.example`).
Safety: sqlglot AST validation (not regex-only); SELECT / read-only WITH / EXPLAIN only; one statement; RO transaction + statement timeout + row cap; credentials never in UI/logs; Live connections show a strong warning.

## Connected Live Processing

Env (see `.env.example`): `LIVE_PROCESSING_BASE_URL`, `LIVE_PROCESSING_PATH`.

| Repo id | Type | Hub may do |
|---|---|---|
| `live-processing` | API | Health + GET `healthz`, `bulk_apply_history`, `bulk_preview` jobs |
| `live-processing-local` | command | Path presence health only (no domain commands) |

No LP apply/write proxies. No import of LP Python packages for business logic.

## Next

Optional: more GET-only LP capabilities via YAML; enrichment Phase A completeness.
Do **not** enable DHIS2 writes without [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).

Capability matrix: [SKILLS.md](SKILLS.md).
