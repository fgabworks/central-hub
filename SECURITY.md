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
- **Email Center (Gmail) / Calendar Center (shared Google accounts):**
  - OAuth client id/secret only via `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`.
  - Gmail scope is **`gmail.readonly` only** (no modify/send).
  - Calendar scopes (incremental): **`calendar.calendarlist.readonly`** and
    **`calendar.events.readonly`** only (no create/update/delete/RSVP).
  - Refresh/access tokens encrypted at rest (Fernet from `CENTRAL_HUB_SECRET_KEY`)
    in `data/email.db`; never returned to templates, JSON, or audit detail.
  - Google passwords are never collected or stored.
  - Disconnect clears local ciphertext and attempts Google token revocation.
  - Attachment download validates `attachment_id` against the message metadata
    and streams through the hub (Google tokens stay server-side).
  - No automatic agent/API consumer of mailbox or calendar content.

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
- Email actions: `EMAIL_OAUTH_*`, `EMAIL_VIEW`, `EMAIL_REFRESH`, `EMAIL_CONVERT_*`,
  `EMAIL_LINK_REPO`, `EMAIL_ATTACHMENT_DOWNLOAD`, `EMAIL_ACCOUNT_ASSIGN` (no token values).
- Calendar / Google Connections: `CALENDAR_*`, `GOOGLE_CONNECTIONS_VIEW` (no token values).

## Known gaps

- No CSRF tokens yet (localhost personal use).
- Flask debug may be on locally — disable if exposed.
- Owner token is shared-secret, not full multi-user RBAC.
- Gmail / Calendar push not implemented; cache is TTL + manual refresh only.
