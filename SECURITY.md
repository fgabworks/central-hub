# SECURITY.md — Security Controls

Scope: personal, local-first tool. Canonical agent rules: [AGENTS.md](AGENTS.md).

## General posture

- **Read-only by default.** Phase 1 performs no writes to any external system and
  executes no jobs. New capabilities must start read-only/dry-run.
- Binds to `127.0.0.1` by default (`CENTRAL_HUB_HOST`); not designed for public exposure.
- No authentication layer yet — acceptable only while local and personal
  (single `owner` role is planned in Phase 6).

## Secrets and environment

- Secrets live only in `.env` (gitignored). `.env.example` ships placeholders only.
- No credentials, tokens, or live hostnames in code, YAML, templates, or docs.
- Code reads only `CENTRAL_HUB_*` variables (`hub/settings.py`).
  `ALLOW_DHIS2_WRITES` in `.env.example` is a documented safety gate for future
  work; nothing reads it yet ([docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md)).
- Registry entries are demo-only; do not point them at live PMNP/DHIS2 systems.

## Command execution controls

Current (Phase 1, health probes only — `hub/adapters/command_adapter.py`):

- **Exact-match allowlist:** only `python|py -c print('ok')` variants may run.
  Anything else returns status `blocked` without executing.
- **No shell:** `subprocess.run(..., shell=False)` with fixed argv; interpreter
  resolved via `PATH` (`shutil.which`).
- **Timeouts:** per-config `timeout_seconds` (default 5s).
- **Working directory:** resolved relative to the hub root or the repo's configured
  path; probe runs in that directory only.

Required for future execution (Phases 3+): allowlisted argv templates from YAML
only, cwd jail under the repo's `local_path`, timeouts, cooperative cancel flags,
dry-run defaults, and explicit confirm before non-dry-run
(lifecycle in [ARCHITECTURE.md](ARCHITECTURE.md#sensitive-operation-lifecycle)).

## File controls

- No file upload or download endpoints exist yet.
- Future uploads (Phase 5) go under a gitignored `data/` tree, scoped per job,
  with validation before use and audited downloads.

## Audit controls

- Not yet persisted. Dashboard "Recent Activity" rows are demo UI.
- Phase 2 adds an append-only audit store (separate from job logs), recording
  who/when/action/target without secrets.

## Known gaps (accepted for Phase 1)

- Flask debug mode defaults on (`CENTRAL_HUB_DEBUG=true`) — fine locally; disable
  if ever exposed beyond localhost.
- No CSRF/auth — no state-changing endpoints exist yet; must be added before any.
