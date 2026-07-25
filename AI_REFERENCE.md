# AI_REFERENCE.md — Verified Current State

Last verified: 2026-07-25 (Repository registry management + connected GitHub repos).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ registry Add/Edit/Disable.**
Hub coordinates repos via registry/adapters; DHIS2 stays GET-only; jobs run
allowlisted capabilities only.

| Area | State |
|---|---|
| Registry + health | `config/repositories.yaml`, `${VAR:-default}` expansion, `hub/adapters/` |
| Registry management | Add / Edit / Enable / Disable via UI → YAML (`hub/registry/store.py`); no auto-clone |
| Health probes | Parallel checks; states: Healthy / Unreachable / Not Cloned / Disabled |
| Live Processing | `live-processing` (API GET-only) + `live-processing-local` (path + git_url) |
| Data-Script / Report Template | Registered with GitHub URLs; local path optional (`DATA_SCRIPT_PATH`, `REPORT_TEMPLATE_PATH`) |
| Dashboard | Live health cards; Open Tasks from Notebook; Notebook Work Queue + Recent Activity |
| Repository Notebook | Local SQLite notes; pin + work queue; no agent scan |
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

## Repository Notebook

| Route | Purpose |
|---|---|
| `/notebook` | Status rail + filters + list + editor (New / Save / Archive / Restore; pin) |
| `/notebook/<id>/export` | Download note JSON |
| `/api/notebook/preview` | Markdown → HTML preview |
| `/` (dashboard) | Open Tasks card + Notebook Work Queue (Open / Pinned / Overdue / Due Today / Upcoming / Blocked); excludes Done & Archived |

Store: `data/notebook.db` (`hub/notebook/`) with schema migrations (`pinned` column).
Notes keep denormalized repository labels when a registry repo becomes unavailable.
Dashboard reuses `NotebookStore` / `hub/notebook/dashboard.py` — no duplicated note data.
No browser-only storage; no agent integration yet.

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
