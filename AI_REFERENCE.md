# AI_REFERENCE.md — Verified Current State

Last verified: 2026-08-02 (HCSC–RF geographic breakdown: Population Filter + batched multi-OU analytics; parent summary preserved).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ Personal/Work workspace switcher + registry Add/Edit/Disable + SQL Workspace (read-only)
+ Email Center (Gmail readonly) + Calendar Center (Calendar readonly, shared Google accounts)
+ AI Assistant Center (read-only Aira/Okarun profiles, including OpenAI Responses API)
+ persistent VS Code-style assistant dock across pages
+ navigation performance (async secondary panels; cached AI connection status)
+ Repository Workspace Phases 1–2 + Connect Local Workspace
+ DHIS2 Reports — Standard Report Manager Phase 1 (sync/view) + catalog shortcuts.**
Hub coordinates repos via registry/adapters; DHIS2 stays GET-only; jobs run
allowlisted capabilities only; Gmail is `gmail.readonly`; Calendar is
`calendar.calendarlist.readonly` + `calendar.events.readonly` only.
Agent Center invokes external CLIs with allowlisted argv only (`shell=False`),
or the OpenAI Responses API with read-only function tools when enabled.

| Area | State |
|---|---|
| Registry + health | `config/repositories.yaml`, `${VAR:-default}` expansion, `hub/adapters/` |
| Registry grouping | Optional `repository_group_id` merges adapters into one UI row (`hub/registry/grouping.py`); Workspace / Application / API statuses independent |
| Registry management | Add / Edit / Enable / Disable via UI → YAML (`hub/registry/store.py`); no auto-clone |
| Repository Workspace | Phases 1–2 + Connect + Run Profile Builder + Active Application status card: Overview / Files / Changes / Run / Logs / Settings; process vs HTTP health reconciled (`hub/repository_workspace/run_status.py`); YAML templates + SQLite repo profiles |
| DHIS2 Reports | `/dhis2/reports` — Phase 1 Standard Report Manager: sync Stage/Live `/api/reports` metadata cache, filters, View / Open in DHIS2 / HTML source / Download / Refresh; period+OU controls; iframe embed with Open-in-DHIS2 fallback. Catalog shortcuts remain for repository/static HTML (`hub/dhis2_reports/`) |
| Central Hub HCSC–RF | `/dhis2/hcsc-indicators` — Phase 0–3 registry + batched Overview/report/category + Compare Sources (`hub/hcsc_indicators/`); quarters **2025Q3–2026Q4**; OU cascade via env-isolated SQLite cache + DHIS2 GET refresh (`hub/dhis2_reports/org_unit_store.py`); optional **Geographic Breakdown** (batched multi-OU analytics below selected level; parent Selected Area Summary retained); Population Filter = All Households; client generation state machine (fresh/cached/stale/slow/timeout; animate only while request ID active); no formula engine |
| Health probes | Parallel checks; states: Healthy / Unreachable / Not Cloned / Disabled |
| Live Processing | `live-processing` (API GET-only) + `live-processing-local` (path + git_url) |
| Data-Script / Report Template | Registered with GitHub URLs; local path optional (`DATA_SCRIPT_PATH`, `REPORT_TEMPLATE_PATH`) |
| Workspaces | Personal / Work switcher (cookie + `hub_prefs`); System nav always visible |
| Personal Dashboard | `/personal` — personal tasks/notes + upcoming calendar + floating Quick Notepad |
| Work Dashboard | `/work` (legacy `/` redirects here by remembered workspace) — repos, work queue, DHIS2 |
| Repository Notebook | Scoped notes (`personal` \| `work`); work keeps repo links; personal needs none |
| Email Center | Shared Gmail service; accounts assigned Personal/Work; readonly OAuth |
| Calendar Center | Shared Calendar service + FullCalendar grid (month/week/day) + agenda/upcoming |
| Google Connections | System page to connect/assign/enable Gmail+Calendar scopes |
| SQL Workspace | Read-only query library/runner (`/sql`); sqlglot allowlist; Live warning; layout `minmax(260px,320px) | 1fr` under shell |
| Live Data Export | `/live-data-export` — Phase 1 allowlisted CSV/XLSX/csv.gz from approved sources (`hub/live_data_export/`, `config/live_data_exports.yaml`); preview+jobs; RO connections; unverified prod sources disabled |
| Data Explorer | `/data-explorer` — Phase 1 RO schema/browse/lineage/inventory (`hub/data_explorer/`, `config/data_explorer.yaml`); no ad-hoc SQL; Live inventory requires configured `live-ro` |
| AI Assistant Center | Aira at `/personal/aira`; Okarun at `/work/okarun` (history/management); full-height right dock + fixed composer via topbar + activity rail (`hub/agent_center/dock.py`); Find/Ask/Plan/Review; Codex CLI (Okarun MVP), Claude Code, Cursor, Grok, OpenAI |
| Workspace Console | Bottom panel under main content only (`left: var(--sidebar-w)`); bounded height; Ctrl+J; collapsed by default |
| Activity Rail | Far-right icons for AI Assistant, Quick Notepad, Workspace Console (future utilities placeholders); reduces main width only |
| App shell | Fixed sidebar 210–216px + `padding-left` on `.app-shell`; `.main-column` / `.content` `flex:1; min-width:0`; `.sidebar-scroll` for nav |
| AI Connections | `/system/ai-connections`; Installed/Authenticated/Version/Last Checked + connect/test/capabilities/disconnect; Codex never stores tokens |
| DHIS2 | GET client, discovery, UID mapping, preview builder |
| UID index admin | LP-style controlled update: dry-run → preview → typed confirm → archive/versions/restore |
| Metadata enrichment | Read-only DHIS2 enrich → local SQLite relationships + audit statuses |
| Explorer | Prefer enrichment snapshot when present; tabs + filters; lazy raw metadata |
| Jobs (Phase 2) | SQLite `data/hub.db`, submit/list/get, worker, logs |
| Command exec (Phase 3) | YAML `command_template`, `shell=False`, cwd jail |
| API exec (Phase 4) | GET/HEAD only from YAML `http_path` |
| Files (Phase 5) | Uploads/results under `data/{uploads,results}/{job_id}/` |
| Safeguards (Phase 6) | Dry-run default, confirm for apply, max concurrent, owner token |
| Tests | `tests/` — includes `test_perf_navigation.py`, `test_ai_assistant_center.py`, `test_openai_catalog.py`, `test_openai_agent.py`, `test_agent_center.py` |
| DHIS2 writes | **Disabled** |
| Gmail writes | **Disabled** (no send/reply/delete/label/mark-read) |
| Calendar writes | **Disabled** (no create/update/delete/RSVP) |

## Connected repositories (active registry)

| id | Role |
|---|---|
| `live-processing` | API — GET health/history/preview · group `pmnp-live-processing` |
| `live-processing-local` | Local checkout of same GitHub repo (`LIVE_PROCESSING_PATH`) · same group |
| `data-script` | Git URL `PMNP-IS/Data-Script` — Not Cloned until path set |
| `report-template` | Git URL `PMNP-IS/REPORT_TEMPLATE` — Not Cloned until path set |

Demo `sample-*` entries removed from the active registry; job tests use
`tests/fixtures/repositories.yaml`.

## Repository Notebook + workspaces

| Route | Purpose |
|---|---|
| `/` | Redirects to remembered Personal or Work dashboard |
| `/workspace/<personal\|work>` | Switch workspace (cookie + `hub_prefs`) |
| `/personal` | Personal Dashboard + floating Quick Notepad (no sidebar entry) |
| `/personal/notebook` | Personal notes/tasks (no repository required) |
| `/personal/tasks` | Personal open tasks list + floating Quick Notepad |
| `/work` | Work Dashboard (repos, work queue, DHIS2) |
| `/work/notebook` | Work notes with repository links |
| `/notebook` | Compat: GET redirects by note scope / workspace; POST handled in scope |
| `/notebook/<id>/export` | Download note JSON |
| `/api/notebook/preview` | Markdown → HTML preview |
| `/api/notebook/notepad*` | Quick Notepad GET/PUT/clear/convert/restore (`?scope=`) |

Store: `data/notebook.db` (`hub/notebook/`) migrations include `pinned`, `quick_notepad`,
`scope` (`personal`\|`work`) + `hub_prefs`, separate Quick Notepads (`personal` / `work`),
and `panel_size` (`normal`\|`expanded`\|`maximized`) for the shared floating drawer.
Existing notes migrate to **work**. Existing Quick Notepad content migrates to the
**personal** pad; work starts empty. Convert → note uses the same scope as the pad.
Work Dashboard queue shows work-scoped notes only. Assistant context never preloads
Notebook content; selected lookup tools search the active profile scope.

## AI Assistant Center (read-only MVP)

| Route | Purpose |
|---|---|
| Persistent dock | Aira/Okarun full-height panel on all pages; prefs `/api/assistant-dock/prefs`; lazy agents; composer fixed at bottom |
| `/personal/aira` | Personal UI; no repository/SQL/DHIS2/jobs/logs/Audit access |
| `/work/okarun` | Work UI; selected repositories and Work read-only services |
| `/api/assistants/<profile>/agents` | Profile-bound adapter availability |
| `/api/assistants/<profile>/agents/<id>/models` | Dynamic adapter model list |
| `/api/assistants/<profile>/context/preview` | Included/excluded sources and secret-safe context |
| `/api/assistants/<profile>/runs` | Start run / isolated history |
| `/api/assistants/<profile>/runs/<id>` | Profile-bound status, stream, files, tools, usage |
| `/api/assistants/<profile>/runs/<id>/cancel` | Cooperative cancel |
| `/api/assistants/<profile>/runs/<id>/retry` | Retry in the same scoped conversation |
| `/api/assistants/<profile>/prompts` | Isolated saved prompt library |

Implementation: `hub/agent_center/` (incl. `dock.py`), `config/agents.yaml`, SQLite `data/agent_center.db`,
`templates/partials/assistant_dock_panel.html`, `static/js/assistant_dock.js`.
Modes: Find / Ask / Plan / Review. Edit / Test labeled **Not yet available**.
Adapters: Hub Simulator (demo), **OpenAI API** and **Grok/xAI** Responses APIs,
plus Claude Code / Cursor Agent / Codex CLIs. Provider accounts are managed at
`/system/ai-connections`; Aira and Okarun are profiles, never providers.
OpenAI and Grok models come from the provider model-list endpoint; Codex MVP uses the
authenticated Codex default model only (`__provider_default__`, no discovery yet) via
`codex exec -C <repo> --sandbox read-only --ephemeral --json` for Okarun. Cursor uses
`agent models`. Claude Code currently exposes only its provider default because its
supported non-interactive CLI has no model-list command.
Inaccessible models are never shown. Optional `OPENAI_ALLOWED_MODELS` can further restrict
the live list; cache TTL and Pro timeout settings remain available.

Connection persistence stores only Hub disconnect/check metadata. CLI credentials stay in
provider-managed storage; API keys stay in server environment variables. Audit events contain
provider ID, operation, and outcome only.
Reasoning-effort selector only for models that support it.
Read-only tools: repository search/read, scoped Notebook/Quick Notepad, SQL-library
lookup, DHIS2 UID metadata, scoped Email/Calendar search, jobs, and redacted Audit.
Schemas are filtered by profile and user selection. Repository instructions load only
for Okarun's selected repositories. No repositories, emails, documents, or old messages
are bulk-loaded. Never packs `.env`, credentials, token paths, binaries, or oversized
files. Output is untrusted. No SQL/shell/repository execution or external writes.

## Repository Workspace runs

YAML templates live in `config/run_profiles.yaml` (`REPO_WS_RUN_PROFILES`).
Repository-specific profiles (Settings → Run Profiles) are stored in SQLite
(`REPO_WS_PROFILE_DATABASE` / `data/repository_workspace.db`) and override templates
by profile id without rewriting YAML. Connect suggestions are saved untrusted/
disabled until approved in the builder.

Executable + argv arrays only; placeholders `{port}`, `{repository_path}`, `{environment}`.
Port modes: `none` | `fixed` | `argument` | `environment_variable`. Fixed ports block
startup when occupied (never auto-kill). Env values stay server-side (UI shows names).
**Repository Processes** (Run tab): detects hub-tracked and related local PIDs (cwd /
command path / entry point / profile port — never name-only). Stop Gracefully / Force
Stop only a verified PID tree; Medium external requires typed `STOP PROCESS <PID>`;
Low is view-only. Start blocks on conflicts / occupied fixed ports and points users
to Repository Processes (no silent fixed-port switching). Health → Local Process
Monitor is a read-only cross-repo summary.
UI: `/repositories/<id>/settings#run-profiles`, `/run`, `/logs`. State/logs under
`data/repository_runs/`. Live / write-capable live profiles require
`REPO_WS_ALLOW_LIVE_RUNS` + confirm. No unrestricted terminal; stop/restart only
hub-tracked process groups.

## Email Center (Gmail readonly)

Shared implementation in `hub/email/` (one service for Personal and Work). Accounts are
assigned to a workspace; UI routes are `/personal/email` and `/work/email`.

| Route | Purpose |
|---|---|
| `/personal/email` · `/work/email` | Mailbox list (inbox/unread/starred/sent), search, labels, pagination |
| `/email` | Redirect by remembered workspace |
| `/email/oauth/start` · `/email/oauth/callback` | OAuth 2.0 web-server flow (`gmail.readonly`) |
| `/email/accounts/<id>/assign` | Assign account to Personal or Work |
| `/email/accounts/<id>/disconnect` | Local token wipe + Google revoke |
| `/email/accounts/<id>/refresh` | Invalidate limited local cache; reload from Gmail |
| `/email/accounts/<id>/messages/<id>` | Message detail |
| `/email/accounts/<id>/threads/<id>` | Thread detail |
| `/email/.../attachments/<id>` | Attachment download (validated against message metadata) |
| POST convert-note / convert-task / link-repo | Create Notebook note/task (work can link a registry repo) |

Store: `data/email.db`. Refresh tokens encrypted at rest (Fernet derived from
`CENTRAL_HUB_SECRET_KEY`). Client id/secret via `GMAIL_*` in `.env` (see `.env.example`).
List rows use Gmail’s `UNREAD` label for unread styling (bold sender/subject/timestamp,
dot + badge, left accent) vs muted read rows; hover / selected / focus-visible stay
distinct. Opening a message does **not** mark it read (`gmail.readonly` only).
No push notifications; limited TTL cache + manual refresh. **No automatic agent access
to email content.** Passwords never stored; tokens never rendered in UI/logs.
OAuth supports **incremental scopes** (`include_granted_scopes=true`) so Calendar can be
added to an existing Gmail account without dropping mail access.

## Calendar Center (Google Calendar readonly)

Shared `hub/calendar/` service reuses the same Google accounts / encrypted tokens /
Personal|Work assignment from Email Center. Scopes (incremental):
`calendar.calendarlist.readonly` + `calendar.events.readonly`.

| Route | Purpose |
|---|---|
| `/personal/calendar` | Personal Calendar — FullCalendar month/week/day grid + agenda/upcoming |
| `/work/calendar` | Work Calendar (same shared service + grid) |
| `/calendar` | Redirect by remembered workspace |
| `/api/calendar/accounts/<id>/events` | JSON feed for FullCalendar (reuses `CalendarService` cache) |
| `/api/calendar/.../events/<id>` | JSON event detail (sanitized description) for read-only drawer |
| `/system/google-connections` | Connect, assign workspace, enable Gmail/Calendar scopes, disconnect |
| `/email/oauth/calendar/start` | Incremental Calendar OAuth start |
| `/calendar/.../events/<id>` | HTML event detail (attendees, location, sanitized description, Meet) |
| POST convert-note / convert-task / link-repo | Notebook actions (repo link Work-only) |

UI: Today / Prev / Next + date-range title; all-day band; today highlight; colors by
source calendar; click event → **right-side read-only drawer** (sticky header/footer,
scrollable sections, sanitized description); search + calendar + timezone filters.
Default timezone is the browser/account zone (selector preserved). Small screens default
to Agenda; drawer goes full-width. Create/edit/delete/drag/resize/RSVP remain disabled.

Personal Dashboard shows **Upcoming Personal Events**. Limited local cache + manual
refresh; no push; no create/update/delete/RSVP; **no automatic agent access**.

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

## Live Data Export

| Route | Purpose |
|---|---|
| `/live-data-export` | New Export / Presets / Jobs / History / Settings |
| `/api/live-data-export/preview` | Count + sample rows (same filter scope as export) |
| `/api/live-data-export/export` | Sync or background CSV/XLSX/csv.gz |
| `/api/live-data-export/jobs/<id>/download` | Token + TTL protected download |

Registry: `config/live_data_exports.yaml`. Store: `data/live_data_export.db` + `data/live_exports/`.
Uses SQL Workspace RO profiles (`live-ro` / `stage-ro` / `local-demo`). No unrestricted SQL editor.

## Data Explorer

| Route | Purpose |
|---|---|
| `/data-explorer` | Three-panel RO explorer (tree / grid / details) |
| `/api/data-explorer/tree` | Cached schema tree |
| `/api/data-explorer/browse` | Paginated SELECT from discovered objects only |
| `/api/data-explorer/inventory` | Grouped source inventory |
| `/api/data-explorer/export` | CSV/XLSX/csv.gz with sensitivity policy |

Config: `config/data_explorer.yaml`. Store: `data/data_explorer.db`. SQL Workspace remains the place for approved ad hoc queries.

## Connected Live Processing

Env (see `.env.example`): `LIVE_PROCESSING_BASE_URL`, `LIVE_PROCESSING_PATH`.

| Repo id | Type | Hub may do |
|---|---|---|
| `live-processing` | API | Health + GET `healthz`, `bulk_apply_history`, `bulk_preview` jobs |
| `live-processing-local` | command | Path presence health only (no domain commands) |

No LP apply/write proxies. No import of LP Python packages for business logic.

## Next

**Interactive repository terminal is implemented** (PTY + WebSocket + xterm.js).
See [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md) and [SECURITY.md](SECURITY.md).

Next development target: **DHIS2 Standard Reports** (credentialed HTML viewer / library polish).
DHIS2 Standard Report Manager Phase 2+ (replacement / design write-back) is **not** started.
Optional: more GET-only LP capabilities via YAML; enrichment Phase A completeness.
Repository Workspace Phase 3+ (commit/push/pull UI, agent-driven edits) stays deferred.
Do **not** enable DHIS2 writes without [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
Do **not** expand Gmail or Calendar beyond readonly without an explicit safety design.
