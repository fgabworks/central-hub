# ARCHITECTURE.md — Components and Boundaries

Agent rules: [AGENTS.md](AGENTS.md). Current state: [AI_REFERENCE.md](AI_REFERENCE.md).

## Core rule

> Central Hub **coordinates** connected repositories. Connected repositories
> **execute** and remain the source of truth for their own logic.

The hub owns: registry, adapters, (future) job queue, UI, health checks, audit.
Connected repos own: domain rules, data models, their own APIs and secrets handling.
The hub must never duplicate PMNP, DHIS2, reporting, convergence, immunization,
DDS, tetanus, or scorecard logic — those stay in the repositories that own them.

## Components (Phase 1)

```text
Browser ──HTTP──> Flask app (app.py, create_app)
                      │
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
  hub/settings.py  hub/registry/  hub/adapters/
  (env config)     (YAML loader,  (AdapterManager
                    typed models)  ├─ ApiAdapter: HTTP health probe
                                   └─ CommandAdapter: path/allowlisted probe)
                      │
                      ▼
        config/repositories.yaml (demo entries only)
```

- **`app.py`** — single Flask entry; UI routes + JSON APIs; also holds the
  Dashboard's demo fixtures (UI-only, see [AI_REFERENCE.md](AI_REFERENCE.md#real-vs-demo-data--important)).
- **`hub/settings.py`** — frozen `Settings` dataclass from `CENTRAL_HUB_*` env vars.
- **`hub/registry/`** — `loader.py` parses/validates YAML into frozen dataclasses
  (`Repository`, `Capability`, `HealthCheckConfig`, `RegistryDefaults`); rejects
  duplicate IDs, bad types, malformed health checks.
- **`hub/adapters/`** — `AdapterManager` resolves adapter by repo `type`
  (`api` | `command`); disabled repos are skipped, not probed. Phase 1 adapters
  implement `health_check()` only; the job-oriented protocol in
  `hub/adapters/base.py` is the future interface.
- **`templates/` + `static/css/style.css`** — server-rendered dark dashboard;
  system fonts; no JS frameworks.
- **`db/`** — empty; reserved for Phase 2 SQLite.
- **`samples/sample-cli/`** — fake local repo so path health checks can pass.

## Adapter design

| Adapter | Mechanism | Phase 1 scope |
|---|---|---|
| API | `requests` to `base_url` + configured health path, timeout per config | Health probe only |
| Command | Path/executable existence; optional subprocess probe restricted to an exact-match allowlist, `shell=False`, timeout, cwd resolved under the repo path | Health probe only |

Future capability execution (Phases 3–4) must keep these properties: allowlisted
argv templates from YAML, restricted working directories, timeouts, cooperative
cancellation, and no shell by default. Controls: [SECURITY.md](SECURITY.md).

## Configuration-driven integration

Every connected repository is a YAML entry with `id`, `type`, connection info,
`health_check`, and declared `capabilities`. Adding an integration means editing
`config/repositories.yaml` — never adding hardcoded paths or clients to code.
Relative paths resolve from the hub root.

## Sensitive operation lifecycle

All future write-capable operations follow:
**Validate → Preview → Confirm → Execute → Verify → Audit.**
DHIS2-specific expansion: [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
