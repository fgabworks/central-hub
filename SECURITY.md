# SECURITY.md — Security Controls

Scope: personal, local-first tool. Canonical agent rules: [AGENTS.md](AGENTS.md).

## General posture

- **Read-only by default** for external systems. Jobs default to dry-run.
- Binds to `127.0.0.1` by default (`CENTRAL_HUB_HOST`).
- Optional single **owner** token (`CENTRAL_HUB_OWNER_TOKEN`) gates job mutations.
  Empty token = open local single-user mode.

## Secrets and environment

- Secrets only in `.env` (gitignored). See `.env.example`.
- DHIS2 passwords never rendered; errors redacted (`hub/dhis2/redact.py`).
- DHIS2 client is GET-only; `ALLOW_DHIS2_WRITES` must stay false.
- **SQL Workspace:** connection passwords only in `.env`; never returned to UI.
  Dedicated read-only DB roles recommended. Execution uses sqlglot AST allowlist
  (SELECT / read-only WITH / EXPLAIN only), one statement, read-only transaction,
  statement timeout, and row cap (`hub/sql_workspace/`).

## Job / command controls (Phases 3–6)

- Capability argv comes **only** from YAML `command_template` / `dry_run_command_template`.
- `subprocess.run(..., shell=False)`; shell metacharacters rejected.
- Working directory jailed under the repository path when under the hub root.
- Timeouts from capability/registry defaults.
- Cooperative cancel/pause flags on disk-backed jobs.
- Non-dry-run requires confirm when `defaults.require_explicit_apply=true`.
- API capabilities: **GET/HEAD only** (POST/PUT/PATCH/DELETE blocked in hub).
- Max concurrent workers from `defaults.max_concurrent_jobs`.

## File controls (Phase 5)

- Uploads under `data/uploads/{job_id}/` — allowlisted suffixes, 5 MiB cap,
  `secure_filename`, path-escape blocked.
- Downloads from `data/results/{job_id}/` only; audited as `DOWNLOAD_RESULT`.

## Audit

- JSONL at `CENTRAL_HUB_AUDIT_LOG` plus job rows in SQLite `CENTRAL_HUB_DATABASE`.
- Job actions: `SUBMIT_JOB`, `START_JOB`, `JOB_*`, `UPLOAD_INPUT`, `DOWNLOAD_RESULT`,
  `OWNER_LOGIN`, plus existing DHIS2 events.

## Known gaps

- No CSRF tokens yet (localhost personal use).
- Flask debug may be on locally — disable if exposed.
- Owner token is shared-secret, not full multi-user RBAC.
