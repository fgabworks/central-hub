# AI_REFERENCE.md — Verified Current State

Last verified: 2026-07-27 (Repository grouping + Run Profile Builder + Repository Processes).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ Personal/Work workspace switcher + registry Add/Edit/Disable + SQL Workspace (read-only)
+ Email Center (Gmail readonly) + Calendar Center (Calendar readonly, shared Google accounts)
+ Prompting & Agent Center (read-only multi-agent MVP, including OpenAI Responses API)
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
| SQL Workspace | Read-only query library/runner (`/sql`); sqlglot allowlist; Live warning |
| Prompting & Agent Center | Read-only Find/Ask/Plan/Review (`/agents`); Hub Simulator, OpenAI API, Claude/Cursor/Codex; Edit/Test not available |
| DHIS2 | GET client, discovery, UID mapping, preview builder |
| UID index admin | LP-style controlled update: dry-run → preview → typed confirm → archive/versions/restore |
| Metadata enrichment | Read-only DHIS2 enrich → local SQLite relationships + audit statuses |
| Explorer | Prefer enrichment snapshot when present; tabs + filters; lazy raw metadata |
| Jobs (Phase 2) | SQLite `data/hub.db`, submit/list/get, worker, logs |
| Command exec (Phase 3) | YAML `command_template`, `shell=False`, cwd jail |
| API exec (Phase 4) | GET/HEAD only from YAML `http_path` |
| Files (Phase 5) | Uploads/results under `data/{uploads,results}/{job_id}/` |
| Safeguards (Phase 6) | Dry-run default, confirm for apply, max concurrent, owner token |
| Tests | `tests/` — includes `test_openai_catalog.py`, `test_openai_agent.py`, `test_agent_center.py` |
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
Work Dashboard queue shows work-scoped notes only. Agent prompting is separate
(see Prompting & Agent Center); notebook content is not auto-fed to agents.

## Prompting & Agent Center (read-only MVP)

| Route | Purpose |
|---|---|
| `/agents` or `/prompting` | Work UI: repos, agent/model, prompt, preview, run, history |
| `/api/agents` | List adapters with availability / capability status |
| `/api/agents/<id>/models` | Models from adapter (managed fallback when undiscoverable) |
| `/api/agents/context/preview` | Instruction + file context preview (secrets excluded) |
| `/api/agents/runs` | Start run (POST) / history (GET) |
| `/api/agents/runs/<id>` | Status, logs, answer, referenced files, errors |
| `/api/agents/runs/<id>/cancel` | Cooperative cancel |
| `/api/agents/prompts` | Saved prompt library |

Implementation: `hub/agent_center/`, `config/agents.yaml`, SQLite `data/agent_center.db`.
Modes: Find / Ask / Plan / Review. Edit / Test labeled **Not yet available**.
Adapters: Hub Simulator (demo), **OpenAI API** (Responses + streaming; `OPENAI_ENABLED` /
`OPENAI_API_KEY` / `OPENAI_DEFAULT_MODEL`), Claude Code / Cursor Agent / Codex CLIs.
OpenAI models: curated Hub catalog ∩ `GET /v1/models` for the configured key
(never shows inaccessible models). Mode recommendations: Find=`gpt-5.6-luna`,
Ask/Plan=`gpt-5.6-terra`, Review=`gpt-5.6-sol`, with fallbacks. Optional
`OPENAI_ALLOWED_MODELS`, cache TTL, Pro longer timeout + background mode.
Reasoning-effort selector only for models that support it.
Read-only tools: `repo_search`, `read_file`, `uid_lookup`, `sql_lookup`, `notebook_lookup`
(search/read reuse Repository Workspace file services; edits and commands stay disabled).
Auto-includes repo AI instructions (`AGENTS.md`, `AI_START_HERE.md`, etc.).
Never packs `.env` / credentials / token paths / binaries. Output treated as untrusted.
Does not consume Email or Calendar content; does not execute SQL or shell.

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

## Connected Live Processing

Env (see `.env.example`): `LIVE_PROCESSING_BASE_URL`, `LIVE_PROCESSING_PATH`.

| Repo id | Type | Hub may do |
|---|---|---|
| `live-processing` | API | Health + GET `healthz`, `bulk_apply_history`, `bulk_preview` jobs |
| `live-processing-local` | command | Path presence health only (no domain commands) |

No LP apply/write proxies. No import of LP Python packages for business logic.

## Next

DHIS2 Standard Report Manager Phase 2+ (replacement / design write-back) is **not** started.
Optional: more GET-only LP capabilities via YAML; enrichment Phase A completeness.
Repository Workspace Phase 3+ (commit/push/pull UI, agent-driven edits) stays deferred.
Do **not** enable DHIS2 writes without [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
Do **not** expand Gmail or Calendar beyond readonly without an explicit safety design.
