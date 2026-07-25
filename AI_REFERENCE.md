# AI_REFERENCE.md — Verified Current State

Last verified: 2026-07-25 (Calendar Center + Email Center + Personal/Work).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ Personal/Work workspace switcher + registry Add/Edit/Disable + SQL Workspace (read-only)
+ Email Center (Gmail readonly) + Calendar Center (Calendar readonly, shared Google accounts).**
Hub coordinates repos via registry/adapters; DHIS2 stays GET-only; jobs run
allowlisted capabilities only; Gmail is `gmail.readonly`; Calendar is
`calendar.calendarlist.readonly` + `calendar.events.readonly` only.

| Area | State |
|---|---|
| Registry + health | `config/repositories.yaml`, `${VAR:-default}` expansion, `hub/adapters/` |
| Registry management | Add / Edit / Enable / Disable via UI → YAML (`hub/registry/store.py`); no auto-clone |
| Health probes | Parallel checks; states: Healthy / Unreachable / Not Cloned / Disabled |
| Live Processing | `live-processing` (API GET-only) + `live-processing-local` (path + git_url) |
| Data-Script / Report Template | Registered with GitHub URLs; local path optional (`DATA_SCRIPT_PATH`, `REPORT_TEMPLATE_PATH`) |
| Workspaces | Personal / Work switcher (cookie + `hub_prefs`); System nav always visible |
| Personal Dashboard | `/personal` — personal tasks/notes + upcoming calendar + Quick Notepad |
| Work Dashboard | `/work` (legacy `/` redirects here by remembered workspace) — repos, work queue, DHIS2 |
| Repository Notebook | Scoped notes (`personal` \| `work`); work keeps repo links; personal needs none |
| Email Center | Shared Gmail service; accounts assigned Personal/Work; readonly OAuth |
| Calendar Center | Shared Calendar service reusing Google accounts; month/week/day/agenda/upcoming |
| Google Connections | System page to connect/assign/enable Gmail+Calendar scopes |
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
| Tests | `tests/` — includes `test_email_center.py`, `test_calendar_center.py` |
| DHIS2 writes | **Disabled** |
| Gmail writes | **Disabled** (no send/reply/delete/label/mark-read) |
| Calendar writes | **Disabled** (no create/update/delete/RSVP) |

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
| `/api/notebook/notepad*` | Quick Notepad GET/PUT/clear/convert/restore (`?scope=`) |

Store: `data/notebook.db` (`hub/notebook/`) migrations include `pinned`, `quick_notepad`,
`scope` (`personal`\|`work`) + `hub_prefs`, and separate Quick Notepads (`personal` / `work`).
Existing notes migrate to **work**. Existing Quick Notepad content migrates to the
**personal** pad; work starts empty. Convert → note uses the same scope as the pad.
Work Dashboard queue shows work-scoped notes only. No agent integration yet.

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
| `/personal/calendar` | Personal Calendar (month / week / day / agenda / upcoming) |
| `/work/calendar` | Work Calendar (same shared service) |
| `/calendar` | Redirect by remembered workspace |
| `/system/google-connections` | Connect, assign workspace, enable Gmail/Calendar scopes, disconnect |
| `/email/oauth/calendar/start` | Incremental Calendar OAuth start |
| `/calendar/.../events/<id>` | Event detail (attendees, location, description, Meet, source calendar) |
| POST convert-note / convert-task / link-repo | Notebook actions (repo link Work-only) |

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

Optional: more GET-only LP capabilities via YAML; enrichment Phase A completeness.
Do **not** enable DHIS2 writes without [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
Do **not** expand Gmail or Calendar beyond readonly without an explicit safety design.
