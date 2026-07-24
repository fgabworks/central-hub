# SKILLS.md — Capability Status

Status categories: **Available** (works now), **Partial** (works with limits),
**Placeholder** (UI exists, no behavior), **Planned** (not started).
Verified state details: [AI_REFERENCE.md](AI_REFERENCE.md).

## Available

| Capability | Where |
|---|---|
| Load/validate repository registry from YAML | `hub/registry/loader.py` |
| List repositories (UI + JSON) | `/repositories`, `/api/repositories` |
| HTTP health probe for API repos | `hub/adapters/api_adapter.py` |
| Path/executable existence check for command repos | `hub/adapters/command_adapter.py` |
| Allowlisted command health probe (`python -c "print('ok')"` only) | `hub/adapters/command_adapter.py` |
| Hub process health endpoint | `/api/healthz` |
| Read-only settings view | `/settings` |

## Partial

| Capability | Limitation |
|---|---|
| Dashboard | Layout complete but summary cards, repo rows, jobs, activity, and DHIS2 panel are hard-coded demo fixtures in `app.py` |
| Capabilities in registry | Parsed and displayed, but not executable (no job engine) |

## Placeholder (UI only, no behavior)

- `/jobs` — job history page
- `/audit` — audit log page
- `/dhis2` — DHIS2 maintenance panel and tool buttons (no DHIS2 client exists)
- Topbar theme/notification/profile icons

## Planned (per roadmap in [AI_REFERENCE.md](AI_REFERENCE.md#next-milestone--phase-2))

- Phase 2: SQLite job + audit storage, job submit/list/get APIs, real dashboard data
- Phase 3: safe command adapter execution (allowlisted templates, dry-run, cancel, cwd jail)
- Phase 4: API adapter capability calls mapped from YAML
- Phase 5: file uploads, result collection, artifact downloads
- Phase 6: confirm gates, concurrency limits, config-change audit
- DHIS2 read-only status probe via a configured API adapter (writes stay disabled; see [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md))
