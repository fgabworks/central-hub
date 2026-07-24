# AI_REFERENCE.md — Verified Current State

Last verified: 2026-07-24 (Phase 1, hub version 0.1.0, branch `main`, no commits yet).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff details: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status: Phase 1 — skeleton + registry + health

Implemented and verified against code:

| Area | State |
|---|---|
| App entry | `app.py` — Flask `create_app()`, runs on `127.0.0.1:8080` by default |
| Settings | `hub/settings.py` — `CENTRAL_HUB_*` env vars via `.env` / `python-dotenv` |
| Registry | `hub/registry/` — loads and validates `config/repositories.yaml` (4 demo repos) |
| Adapters | `hub/adapters/` — health checks only; API (HTTP) + command (path/allowlisted probe) |
| UI | Dark sidebar dashboard; Jinja templates + `static/css/style.css`; no JS frameworks |
| Database | None. `db/` is an empty placeholder for Phase 2 SQLite |
| Tests | None yet (manual checklist in `README.md`) |

## Real vs demo data — important

- **Real:** `/repositories`, `/repositories/<id>`, `/health` and all `/api/*` routes
  read the live registry and run real health probes.
- **Demo/UI-only:** the Dashboard (`/`) renders hard-coded fixtures in `app.py`
  (`_demo_summary_cards`, `_demo_repositories`, `_demo_jobs`, `_demo_activity`,
  `_DHIS2_TOOLS`). The repositories, jobs, activity rows, and DHIS2 panel shown
  there are **not** live data.
- **Placeholders:** `/jobs`, `/dhis2`, `/audit` are static pages; `/settings` is a
  read-only view of runtime settings.
- `ALLOW_DHIS2_WRITES` appears in `.env.example` and UI banners only —
  **no Python code reads it**, because no DHIS2 client exists.

## Routes

| Route | Behavior |
|---|---|
| `/` | Dashboard (demo fixtures) |
| `/repositories`, `/repositories/<id>` | Live registry list/detail |
| `/health` | Live health checks for all registered repos |
| `/jobs`, `/dhis2`, `/audit` | Placeholders ("coming in next phase") |
| `/settings` | Read-only runtime settings |
| `/api/healthz` | Hub process health JSON |
| `/api/repositories` | Registry JSON |
| `/api/repositories/<id>/health` | Single-repo health JSON |
| `/api/health` | All-repo health JSON |

## Environment variables (read by code)

`CENTRAL_HUB_APP_NAME`, `CENTRAL_HUB_ENV`, `CENTRAL_HUB_HOST`, `CENTRAL_HUB_PORT`,
`CENTRAL_HUB_DEBUG`, `CENTRAL_HUB_REPOSITORIES_CONFIG`, `CENTRAL_HUB_REQUEST_TIMEOUT`.
See `.env.example`.

## Next milestone — Phase 2

Jobs + dashboard + audit (from `CENTRAL_HUB_REFERENCE.md` §12):

1. SQLite tables for jobs and audit events (under `db/` / `data/`)
2. Submit / list / get job APIs (queued only; no real execution yet)
3. Job dashboard replacing demo fixtures with real data
4. Per-job log file scaffolding
5. Audit events: `SUBMIT_JOB`, `VIEW_LOGS`, `HEALTH_CHECK`

Capability status matrix: [SKILLS.md](SKILLS.md).
