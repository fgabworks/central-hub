# Central Hub Reference from Live Processing

Reference guide for building **central-hub** — a personal multi-repository control center.  
Source of patterns: `pmnp-live-processing` (Lookup / Live Processing app).  
**Use this document for infrastructure ideas only.** Do not copy PMNP domain logic into central-hub.

---

## 1. Purpose of Central Hub

**central-hub** is a generic personal control center for connected repositories.

It should:

- Register and connect to different repositories (local paths and/or HTTP APIs)
- Route jobs to the right repository via adapters
- Launch scripts, CLIs, or API workflows
- Monitor progress, logs, and results
- Check repository / connection health
- Keep an audit history of operator actions

It is **not** a PMNP app, not a DHIS2 client, and not a second copy of any connected repo’s business rules. Connected repositories remain the source of truth for their own logic.

---

## 2. Core Design Rule

> **Central Hub coordinates repositories only. It must not duplicate the internal processing logic of connected repositories.**

| Hub owns | Connected repo owns |
|----------|---------------------|
| Registry, adapters, job queue, UI | Domain rules, data models, product APIs |
| Progress polling, cancel/resume chrome | What a “job” actually computes |
| Audit of who submitted/cancelled what | Correctness of outputs |
| Config (paths, endpoints, capabilities) | Secrets handling for *its* systems (hub only stores connection config) |

If a capability needs convergence scoring, immunization rules, DHIS2 scorecard writes, DDS, tetanus, or PMNP indicators — call or run the **Live Processing (or other) repository**. Do not reimplement those rules inside central-hub.

---

## 3. Reusable Patterns from Live Processing

Patterns observed in this repo that are worth **studying conceptually** for central-hub.

### App startup structure

- Single Flask entry (`lookup/app_lookup.py`) with CLI flags (`--environment`, `--connection-mode`, `--reload`).
- Eager connection build only in the serving child process (reload parent skips heavy init).
- Managed Postgres + SSH tunnel via `_support/` with timeouts and `atexit` cleanup.
- Explicit reconnect APIs that dispose and rebuild engines/clients.

**Extract conceptually:** one process entry, env-driven connections, reconnect without restarting the whole app.

### Route / API organization

- Flat route catalog on one app (no blueprints today): job start / progress / pause / cancel / resume families.
- Parallel families share the same lifecycle shape (bulk apply, CSV batch, listed apply).
- Connection mode + health endpoints sit beside job APIs.

**Extract conceptually:** group routes by lifecycle (`/jobs`, `/repos`, `/health`, `/audit`) even if Live Processing keeps them flat.

### Long-running process handling

- Background worker thread + durable job JSON on disk (`lookup/logs/bulk_apply_jobs/{run_id}.json`).
- Phases: queued → loading → deriving → importing → terminal (completed / failed / canceled / paused).
- Percent capped below 100 until terminal success.

**Extract conceptually:** disk-backed job state so UI and process restarts can resume observation.

### Bulk processing pattern

- Preview / dry-run first; apply only with explicit confirm.
- Chunked work units with per-chunk progress updates.
- Scoped payloads (OU, quarter, CSV rows) stored on the job record.

**Extract conceptually:** dry-run → confirm → chunked apply; never imply silent writes.

### Progress tracking

- Stable poll payload: `percent`, `phase`, `message`, `resumable`, cancel/pause flags.
- Shared ETA helpers (`listed_apply_progress.py`).
- UI progress bar + localStorage of `run_id` to reconnect after refresh.

**Extract conceptually:** one progress DTO for all job types; poll, don’t push (MVP).

### Cancellation handling

- Cooperative cancel/pause flags on disk; workers check between units and raise cancel/pause exceptions.
- API: request cancel / pause / resume; UI confirms destructive actions.

**Extract conceptually:** cooperative cancellation only — never kill the OS process as the primary cancel path for MVP.

### Resume / reconnect safeguards

- Classify connection failures vs hard logic failures; mark jobs `resumable`.
- `_reset_engine()` / reconnect endpoint after SSH/DB drop.
- After process restart, orphaned in-memory threads → failed + resume/restart flags on disk jobs.
- Connection attempt budgets and timeouts.

**Extract conceptually:** treat network loss as recoverable; persist enough state to resume.

### Logging pattern

- Console logging with timestamps (`setup_logging` in `_support`).
- Job artifacts under a logs directory: live job JSON, full run JSON, append-only history **jsonl**.
- Operator scripts often tee stdout to a file (outside the app); hub should write its own job log files.

**Extract conceptually:** separate **job execution logs** from **audit event logs**.

### Environment handling

- `ENVIRONMENT=live|stage` with prefixed secrets (`LIVE_*` / `STAGE_*`) in `.env`.
- Resolution order documented; CLI can override `.env`.
- `.env.example` without secrets; never commit real credentials.

**Extract conceptually:** profile-based env + example file; hub profiles might be `dev` / `personal` / `prod` instead of live/stage.

### File upload / CSV handling

- Browser reads file → POSTs parsed rows (or hub stores upload then processes).
- Pure validation/normalize module separate from apply (`csv_canonical.py` idea).
- Preview endpoints before apply.

**Extract conceptually:** validate → preview → store input artifact → job references artifact path.

### Job history pattern

- Append-only `bulk_apply_history.jsonl` + optional full run JSON.
- Optional external tracker (Sheets / Excel) for coverage — **adapt** as optional plugin, not required for hub MVP.
- History APIs for UI lists and filters.

**Extract conceptually:** SQLite (or jsonl) history table for hub; full payloads on disk.

### UI / dashboard layout

- Single-page ops surface: select scope → preview → apply → progress → history.
- Mode / environment banners; connection status dot.
- Collapsible workspace columns; shared run lock while a job is active.

**Extract conceptually:** simple dashboard: Repositories | Jobs | Logs | Audit | Health.

### Health / status checks

- `/api/healthz`: dependency probes (DB `SELECT 1`, client configured).
- Connection mode GET/POST + reconnect.
- Smoke-test CLI for offline package check vs full connectivity.

**Extract conceptually:** hub health = hub process + each registered repo’s `health_check`.

### Branch / commit / version display

- Live Processing shows **runtime environment / connection mode**, not primarily app git SHA.
- Separate “artifact version” panels exist for domain indexes — don’t copy those.

**Extract conceptually:** show hub git SHA + env profile; show each repo’s configured revision/path.

### Tests / config structure

- Co-located `test_*.py` under `lookup/` with `unittest`.
- Infra tests for reconnect, resume, CSV validation, UI string contracts.
- `requirements.txt` + `.env.example`.

**Extract conceptually:** test adapter fakes and job state machine early; keep domain out of hub tests.

---

## 4. Copy / Adapt / Avoid Recommendations

| Pattern or Module | Recommendation | Reason |
|-------------------|----------------|--------|
| Disk-backed job JSON + poll progress | **ADAPT** | Excellent lifecycle; rewrite as hub-generic (SQLite + files), not PMNP phases |
| Cooperative cancel / pause / resume | **ADAPT** | Keep cooperative flags; drop DHIS2-specific resume semantics |
| Connection failure classification → resumable | **ADAPT** | Reuse idea for API/SSH/subprocess failures |
| Prefixed multi-environment `.env` | **ADAPT** | Keep profile prefixes; rename away from live/stage/DHIS2 |
| Append-only history jsonl / run archive | **ADAPT** | Good audit spine; prefer SQLite for hub queries |
| `/healthz` + reconnect endpoint | **ADAPT** | Generic health + per-repo health |
| Preview / dry-run before apply | **ADAPT** | Hub should support dry-run capabilities declared by repos |
| CSV validate-then-apply split | **ADAPT** | Generic file validation layer; not PMNP schemas |
| Correction Workspace UI chrome (progress, lock, banners) | **ADAPT** | Layout ideas only; rebuild thin dashboard |
| `_support` SSH + managed Postgres | **ADAPT** (optional) | Only if hub itself needs DB; don’t require PMNP DB |
| Flask monolith route catalog | **ADAPT** | Prefer FastAPI routers or Flask blueprints by domain |
| Google Sheets / Excel OU trackers | **AVOID** (MVP) | PMNP ops coupling; optional later plugin |
| `lookup/convergence/**` | **AVOID** | PMNP scoring / indicators |
| Immunization / DDS / tetanus / FIC/CIC modules | **AVOID** | Domain rules belong in Live Processing |
| DHIS2 write clients & UID catalogs | **AVOID** | Product-specific; expose via Live Processing API/commands |
| Linelist loaders / SQL scorecard parity | **AVOID** | Data-model specific |
| Simulators (`*_simulator*.py`) | **AVOID** | Domain simulators, not hub infrastructure |
| `uid_mapping_registry.py` / AI UID index | **AVOID** | PMNP metadata |
| Hardcoded credentials or TEIs in scripts | **AVOID** | Security anti-pattern |

**COPY** in the narrow sense means “reuse the *idea* and a small amount of structural code after scrubbing names.” Prefer reimplementation in central-hub over verbatim file copies from Live Processing.

---

## 5. Central Hub Suggested Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                     UI Dashboard (simple)                   │
│         Repos · Jobs · Logs · Audit · Health                │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP
┌───────────────────────────▼─────────────────────────────────┐
│              FastAPI or Flask backend (hub)                 │
│  registry · adapters · jobs API · authz (later)             │
└───────┬─────────────────┬─────────────────┬─────────────────┘
        │                 │                 │
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌──────────────────────────┐
│ Repo registry │ │  Job queue    │ │ File storage / artifacts │
│ (YAML/SQLite) │ │  + workers    │ │ uploads · results · logs │
└───────┬───────┘ └───────┬───────┘ └──────────────────────────┘
        │                 │
        ▼                 ▼
┌───────────────────────────────────────┐
│           Adapter manager             │
│  ┌─────────────┐  ┌─────────────────┐ │
│  │ API adapter │  │ Command adapter │ │
│  │ (HTTP)      │  │ (subprocess)    │ │
│  └──────┬──────┘  └────────┬────────┘ │
└─────────┼──────────────────┼──────────┘
          │                  │
          ▼                  ▼
   Connected repo APIs   Local scripts / CLIs
```

**Components**

| Component | Role |
|-----------|------|
| **Backend** | FastAPI (preferred) or Flask — job CRUD, registry, health |
| **Repository registry** | Loads `repositories.yaml`; capabilities and adapters |
| **Adapter manager** | Resolves `api` vs `command` adapter per repo/capability |
| **API adapter** | HTTP calls to connected services |
| **Command adapter** | Guarded subprocess / shell execution in repo cwd |
| **Job queue + worker** | Async or threaded workers updating job state |
| **File storage** | Uploads, result bundles, log files |
| **Logs** | Per-job execution logs |
| **Audit history** | Who did what (submit, cancel, download, config change) |
| **UI dashboard** | Minimal HTML/JS or small SPA |

Connected repos stay independent. Hub never imports their Python packages for business logic.

---

## 6. Suggested Folder Structure

```text
central-hub/
  app/
    main.py                 # FastAPI/Flask entry
    config/
      settings.py           # env loading
    registry/
      loader.py             # parse repositories.yaml
      models.py
    adapters/
      base.py               # Adapter protocol / ABC
      api_adapter.py
      command_adapter.py
      manager.py
    jobs/
      models.py             # job schema
      service.py            # create / list / cancel
      schemas.py            # request/response DTOs
    workers/
      runner.py             # dequeue and run
      progress.py
    storage/
      files.py              # upload/result paths
      db.py                 # SQLite engine (MVP)
    logs/
      job_logger.py
    audit/
      service.py
      actions.py            # action constants
    ui/
      static/
      templates/            # or separate frontend later
      routes.py
  config/
    repositories.yaml
  data/                     # gitignored runtime data
    jobs/
    uploads/
    results/
    logs/
    audit/
  tests/
    test_registry.py
    test_adapters.py
    test_jobs.py
    fixtures/
  README.md
  CENTRAL_HUB_REFERENCE.md  # this guide (or a copy)
  .env.example
  requirements.txt
  .gitignore
```

---

## 7. Repository Registry Idea

`config/repositories.yaml` example:

```yaml
repositories:
  - id: sample-cli
    name: Sample CLI Repo
    type: command
    enabled: true
    local_path: "C:/repos/sample-cli"
    working_directory: "C:/repos/sample-cli"
    health_check:
      type: command
      command: ["python", "-c", "print('ok')"]
      timeout_seconds: 10
    allowed_roles: ["owner"]
    capabilities:
      - id: echo_dry_run
        label: Echo (dry-run)
        adapter_type: command
        command_template:
          - "python"
          - "tools/echo_job.py"
          - "--input"
          - "{input_file}"
          - "--dry-run"
        input_types: ["text", "csv"]
        output_locations:
          - "outputs/{job_id}/result.json"
        dry_run_default: true

  - id: live-processing
    name: PMNP Live Processing
    type: api
    enabled: true
    local_path: "C:/PMNP/pmnp-live-processing"
    base_url: "http://127.0.0.1:5050"
    health_check:
      type: http
      method: GET
      path: "/api/healthz"
      timeout_seconds: 15
    allowed_roles: ["owner"]
    capabilities:
      - id: health
        label: Health check
        adapter_type: api
        endpoint:
          method: GET
          path: "/api/healthz"
        input_types: []
        output_locations: []
      # Do NOT encode convergence/immunization rules here —
      # only expose endpoints the Live Processing app already owns.
      - id: bulk_apply_status
        label: Poll bulk-apply job
        adapter_type: api
        endpoint:
          method: GET
          path: "/api/bulk-apply/status"
          query:
            run_id: "{job_ref}"
        input_types: ["job_ref"]
        output_locations: []

defaults:
  job_timeout_seconds: 3600
  max_concurrent_jobs: 2
  require_explicit_apply: true
```

**Rules**

- Registry is config-driven; no hardcoded repo paths in code.
- Capabilities declare adapter type, inputs, and outputs.
- Live Processing appears as **one connected API/command repo**, not as hub internals.

---

## 8. Shared Job Schema

Generic job record (SQLite JSON columns or normalized tables):

```json
{
  "job_id": "job_20260724_001",
  "repository_id": "sample-cli",
  "capability_id": "echo_dry_run",
  "adapter_type": "command",
  "submitted_by": "local-owner",
  "submitted_at": "2026-07-24T14:00:00Z",
  "started_at": null,
  "finished_at": null,
  "input_files": [
    {
      "name": "input.csv",
      "path": "data/uploads/job_20260724_001/input.csv",
      "sha256": "…"
    }
  ],
  "parameters": {
    "dry_run": true,
    "extra": {}
  },
  "status": "queued",
  "progress": {
    "percent": 0,
    "phase": "queued",
    "message": "Waiting for worker",
    "resumable": false
  },
  "logs": {
    "path": "data/logs/job_20260724_001.log",
    "tail_url": "/api/jobs/job_20260724_001/logs"
  },
  "result": {
    "summary": null,
    "artifacts": []
  },
  "error": null,
  "cancel_requested": false,
  "pause_requested": false
}
```

**Suggested statuses:** `queued` | `running` | `paused` | `completed` | `failed` | `canceled`.

---

## 9. Adapter Interface

```python
from typing import Protocol, Any, Iterator

class RepositoryAdapter(Protocol):
    def validate_job(self, job: dict[str, Any]) -> None:
        """Raise ValueError if job is invalid for this capability."""

    def start_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """Begin work; return initial progress payload."""

    def get_status(self, job: dict[str, Any]) -> dict[str, Any]:
        """Return status + progress (+ resumable flags)."""

    def stream_logs(self, job: dict[str, Any]) -> Iterator[str]:
        """Yield log lines (file follow or HTTP stream)."""

    def cancel_job(self, job: dict[str, Any]) -> None:
        """Request cooperative cancel when supported."""

    def collect_results(self, job: dict[str, Any]) -> dict[str, Any]:
        """Return result summary + artifact paths."""

    def health_check(self) -> dict[str, Any]:
        """Return {ok: bool, detail: str, latency_ms: int}."""
```

| Adapter | Mechanism |
|---------|-----------|
| **API** | HTTP to `base_url` + capability endpoint; poll status URLs |
| **Command** | `subprocess` in `working_directory` with allowlisted argv from template; capture stdout/stderr to job log |

**Safety for command adapter**

- Allowlist executables / templates from YAML only (no free-form shell from UI in MVP).
- Default `dry_run` when capability supports it.
- Timeouts, cwd jail under `local_path`, no `shell=True` unless explicitly configured and reviewed.

---

## 10. Logging and Audit Ideas

### Job logs (execution)

- One file per job: `data/logs/{job_id}.log`
- Contains stdout/stderr, phase transitions, adapter messages
- UI: tail / download

### Audit logs (operator actions)

- Append-only table or `data/audit/audit.jsonl`
- Who / when / action / target / metadata (no secrets)

Suggested audit actions:

| Action | When |
|--------|------|
| `SUBMIT_JOB` | Job created |
| `START_JOB` | Worker begins |
| `CANCEL_JOB` | Cancel requested |
| `RETRY_JOB` | New job from failed/canceled |
| `DOWNLOAD_RESULT` | Artifact downloaded |
| `CHANGE_REPOSITORY_CONFIG` | Registry/config edit |
| `HEALTH_CHECK` | Manual or scheduled health |
| `VIEW_LOGS` | Sensitive log view (optional) |

Keep job logs and audit logs **separate**. Job logs can be large and noisy; audit logs should stay small and queryable.

---

## 11. Safety Rules for Grok

When building **central-hub**:

1. **Do not** import Live Processing PMNP business logic.
2. **Do not** recreate convergence, immunization, DHIS2 scorecard, DDS, tetanus, or indicator rules.
3. Build **generic infrastructure only** (registry, adapters, jobs, UI, audit).
4. Use **fake/sample repositories** first (`sample-cli` echo job).
5. Prefer **dry-run / sample jobs** before real command execution.
6. Keep repository connections **config-driven** (`repositories.yaml`).
7. Keep secrets in **`.env`**, never hardcoded; ship `.env.example` only.
8. Add **tests** before risky features (command execution, cancel, file delete).
9. Keep command execution **guarded and explicit** (allowlisted templates, confirm apply).
10. Treat Live Processing as an **external connected repo**, not a Python dependency for domain code.
11. If a feature needs PMNP rules, add a **capability that calls Live Processing** — do not copy modules from `lookup/convergence/`.

---

## 12. MVP Build Plan

### Phase 1 — Skeleton + registry + health

- App entry, settings from env
- Load `repositories.yaml`
- List repositories in UI/API
- Per-repo `health_check`
- Sample “ok” command repo

### Phase 2 — Jobs + dashboard + audit

- SQLite job tables
- Submit / list / get job
- Job dashboard + status polling
- Job log file + audit log
- `SUBMIT_JOB` / `START_JOB` / `VIEW_LOGS` audit events

### Phase 3 — Command adapter (safe)

- Allowlisted command templates
- Dry-run sample script
- Cancel cooperative flag (best-effort)
- Timeouts and cwd jail

### Phase 4 — API adapter

- HTTP health/status against a local sample API (or Live Processing `/api/healthz` only)
- Map capability endpoints from YAML
- No domain apply endpoints until Phase 5+ and explicit config

### Phase 5 — Files + results

- Upload input files
- Store under `data/uploads/{job_id}/`
- `collect_results` copies/lists artifacts
- Download result with audit `DOWNLOAD_RESULT`

### Phase 6 — Permissions and safeguards

- Simple local role (`owner` only is fine for personal MVP)
- Confirm gates for non-dry-run
- Max concurrent jobs
- Config change audit
- Optional: pause/resume parity with Live Processing job chrome

---

## 13. Live Processing Files Worth Referencing

| File / module | Why useful | Extract conceptually |
|---------------|------------|----------------------|
| `lookup/app_lookup.py` | App entry, health, reconnect, job route families | Startup flags, `/healthz`, job HTTP shapes |
| `lookup/bulk_apply_job.py` | Disk jobs, cancel/pause, progress payload, connection-loss flags | Job state machine + resumable errors |
| `lookup/listed_apply_progress.py` | ETA / speed helpers | Shared progress enrichment |
| `lookup/csv_canonical.py` | Pure validate/normalize without I/O | Generic input schema validation pattern |
| `lookup/csv_processor.py` | Preview → apply → progress API shell | Orchestration without domain rules |
| `_support/_env_config.py` | Prefixed environments | Multi-profile env loading |
| `_support/_postgres_env_connection_impl.py` | Managed connections, timeouts, logging setup | Optional hub DB; timeout/reconnect ideas |
| `.env.example` | Safe secret template | Hub `.env.example` structure |
| `lookup/templates/lookup.html` (progress / connection UI only) | Progress bar, run lock, mode banners | Dashboard UX patterns — do not copy the whole page |
| `lookup/test_bulk_apply_connection_loss.py` | Tests for failure classification | How to test resumable failures |
| `lookup/test_bulk_apply_resume_safety.py` | Resume contracts | Job resume test patterns |
| `lookup/test_connection_mode.py` / `test_bulk_reconnect_ui.py` | Connection UX contracts | Health/reconnect tests |
| `README.md` | Operator startup contract | Hub README tone and run instructions |
| `AI_REFERENCE/AI_MEMORY.md` (env / job notes only) | Documented resolution order | Operator/agent runbooks |

---

## 14. Live Processing Files / Logic to Avoid

| Area | Examples | Why avoid |
|------|----------|-----------|
| Convergence engine | `lookup/convergence/**` | PMNP indicator scoring |
| Immunization / FIC / CIC | `*immunization*`, `derive_age_vaccines.py` | Domain vaccine rules |
| DDS / diet | `diet_diversity_compliance.py`, DDS UI processes | Nutrition domain |
| Tetanus / ANC / PNC domain | `derive_tetanus.py`, `derive_pnc_four.py`, ANC modules | Clinical scoring |
| DHIS2 write & UID maps | `lookup_dhis2_api.py` (as domain client), `uid_mapping_registry.py` | Product-specific writes |
| Linelist / SQL scorecards | `linelist_loader.py`, `validate_linelist_*` | PMNP data model |
| Simulators | `*_simulator*.py`, `whole_convergence_simulator.py` | Domain simulation |
| Reports | `reports/**`, household convergence HTML | PMNP reporting |
| Child age / HH status **rules** | `child_age_correction.py`, `hh_member_status_fill.py` | Domain; job *shell* may inspire adapters only |
| Sheets OU trackers as core | `sheets_tracker.py` (as required dependency) | Ops coupling; optional later |
| Secrets / live credentials | any real `.env`, passwords in scripts | Security |

---

## 15. Final Instruction for Grok

Use this document as a **reference guide only**.

Build **central-hub** as a **new generic personal control center from scratch**:

- Coordinate repositories through a registry and adapters.
- Track jobs, logs, results, and audit events.
- Do **not** copy or reimplement Live Processing PMNP / DHIS2 / convergence / immunization / DDS / indicator logic.
- Connected repositories remain the source of truth for their own processing.

Start with sample adapters and dry-run jobs. Wire Live Processing later as an external API/command repository if needed — never as an internal business library.
