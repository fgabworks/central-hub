# Central Hub

Personal multi-repository control center.

Central Hub registers connected repositories, checks their health, and (in later phases) routes jobs, monitors progress, and keeps audit history. Connected repositories remain the source of truth for their own business logic.

**Current status: Phase 1** — skeleton, repository registry, health checks, basic UI.

## What Phase 1 includes

- Flask app entry (`app.py`)
- Config-driven repository registry (`config/repositories.yaml`)
- Registry loader and typed models (`hub/registry/`)
- API + command adapters with health probes only (`hub/adapters/`)
- UI pages: Dashboard, Repositories, Health
- Demo/sample repositories only (safe, fake connections)

## What Phase 1 does *not* include

- Job submission or execution
- File uploads / result collection
- Audit log persistence
- Live PMNP, DHIS2, or other production systems
- Free-form terminal command execution from the UI

## Setup

```powershell
cd C:\PMNP\personal\central-hub
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

## Run locally

```powershell
python app.py
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080).

Useful endpoints:

| Path | Purpose |
|------|---------|
| `/` | Dashboard |
| `/repositories` | Registry list |
| `/health` | Per-repo health checks |
| `/api/healthz` | Hub process health JSON |
| `/api/repositories` | Registry JSON |
| `/api/health` | All repo health JSON |

## Configure repositories

Edit `config/repositories.yaml`. Relative `local_path` values resolve from the hub root.

Sample entries shipped with Phase 1:

| ID | Type | Expected health |
|----|------|-----------------|
| `sample-cli` | command | Healthy if `samples/sample-cli` exists and `python` is on PATH |
| `sample-api` | api | Unhealthy unless something listens on `http://127.0.0.1:9099/health` |
| `sample-missing-path` | command | Unhealthy (path intentionally missing) |
| `sample-disabled` | api | Shown as disabled; still listed |

Command health probes are allowlisted. Phase 1 only permits the harmless `python -c "print('ok')"` style check (or path/executable existence checks).

## Manual test checklist

1. Start the app and open the Dashboard — summary counts should show 4 registered repos.
2. Open **Repositories** — confirm sample entries and enabled/disabled pills.
3. Open a repository detail page — connection + health config should render.
4. Open **Health**:
   - `sample-cli` should be healthy
   - `sample-api` should be unreachable/unhealthy (unless you start a fake API)
   - `sample-missing-path` should be unhealthy
5. Hit `/api/healthz` — `ok: true` and `registry_loaded: true`.
6. Hit `/api/repositories` — JSON list matches the YAML registry.
7. Confirm no job-run UI or command execution beyond health probes.

## Safety

- Secrets belong in `.env` (never commit them). Use `.env.example` as the template.
- Repository connections stay config-driven.
- Keep demo repos fake until a later phase intentionally wires a real personal repo.
- Do not copy PMNP domain logic into this project.

## Architecture note

`CENTRAL_HUB_REFERENCE.md` is an architecture guide adapted from Live Processing infrastructure patterns. It is not a license to recreate convergence, immunization, DHIS2, scorecard, DDS, tetanus, or indicator logic here.

## Next: Phase 2 plan

Recommended next slice:

1. SQLite schema for jobs + audit events under `db/` / `data/`
2. Job create / list / get APIs (queued only — no worker yet, or a stub worker)
3. Dashboard job history panel
4. Per-job log file path scaffolding
5. Audit actions: `SUBMIT_JOB`, `VIEW_LOGS`, `HEALTH_CHECK`
6. Still no real command/API job execution (Phase 3/4)
