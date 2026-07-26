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
    **`calendar.events.readonly`** only (no create/update/delete/RSVP/drag/resize).
  - Event description HTML is **allowlist-sanitized** before drawer/detail display.
  - Refresh/access tokens encrypted at rest (Fernet from `CENTRAL_HUB_SECRET_KEY`)
    in `data/email.db`; never returned to templates, JSON, or audit detail.
  - Google passwords are never collected or stored.
  - Disconnect clears local ciphertext and attempts Google token revocation.
  - Attachment download validates `attachment_id` against the message metadata
    and streams through the hub (Google tokens stay server-side).
  - No automatic agent/API consumer of mailbox or calendar content.
- **Prompting & Agent Center:** read-only Find/Ask/Plan/Review only.
  - Adapter argv from `config/agents.yaml` templates (`shell=False`); cwd jailed
    to selected local repository roots.
  - **OpenAI API adapter:** `OPENAI_ENABLED` / `OPENAI_API_KEY` /
    `OPENAI_DEFAULT_MODEL` / optional `OPENAI_ALLOWED_MODELS` /
    `OPENAI_MODEL_CACHE_TTL_SECONDS` / `OPENAI_PRO_MODEL_TIMEOUT_SECONDS`.
    Key never returned to UI, logs, or audit. Curated catalog in
    `openai_catalog.py` intersected with `GET /v1/models` — inaccessible models
    are omitted (not errors). Mode recommendations + user override; reasoning
    effort only when supported; Pro models use background mode and longer timeout.
    Estimated tier labels only (no hardcoded pricing).
  - Context packing excludes `.env`, credentials, tokens, binaries, and oversized files.
  - Hub `.env` secret vars are stripped from child process env where obvious.
  - Agent stdout/stderr and answers are redacted before audit/history persistence.
  - Treat agent output as untrusted; Edit/Test modes are disabled.
  - Does not read Email or Calendar stores.
- **Repository Workspace (Phases 1–2):** local checkout files + approved run profiles.
  - Requires configured `local_path` / `working_directory` that exists on disk.
  - All paths resolved under the repo root; absolute paths, `..`, symlink/junction
    escapes rejected (`hub/repository_workspace/security.py`).
  - Blocks `.env`, credentials, tokens, private keys, and related secret patterns;
    secret-looking lines redacted from diffs, search snippets, logs, and Audit details.
  - Text formats only; binaries and oversized files blocked (limits via
    `REPO_WS_MAX_*` in `.env`).
  - Writes use Validate → Diff preview → Confirm → Execute → Audit
    (`REPO_WS_SAVE` / `CREATE` / `RENAME` / `DELETE` / …).
  - Git inspect is read-only (`status` / `diff`); no commit, push, pull, merge,
    reset, checkout, or discard-all.
  - External open allowlists `code` / `cursor` / `explorer` (`shell=False`).
  - **Runs (Phase 2):** profiles from `REPO_WS_RUN_PROFILES` / `config/run_profiles.yaml`
    use executable + argument arrays only (placeholders `{port}`, `{repository_path}`,
    `{environment}`). `shell=False`; new process group/session; stop/restart only
    hub-tracked fingerprints (stale PID / PID-reuse refused). No auto-restart on
    file edits; no unrestricted terminal. Env values stay server-side (UI shows names).
    Live / `live_profile` requires `REPO_WS_ALLOW_LIVE_RUNS` plus explicit confirmation.
    Port occupancy checked; alternate ports suggested; duplicate repo/profile/port
    runs blocked. Logs under `REPO_WS_RUN_LOG_DIR` with size/retention caps + redaction.
  - **Connect Local Workspace:** user-selected folder only; scan is read-only (no
    subprocess, no installs, no secret-file reads). Git remote mismatch and path
    replacement require explicit confirm. Suggested run profiles are untrusted until
    reviewed; saved as argv arrays only (`REPO_WS_CONNECT_SCAN` / `PREVIEW` / `SAVE`).
- **DHIS2 Reports / Standard Report Manager:** Stage/Live connections from `.env` only.
  - Sync caches metadata (+ optional `designContent`) in SQLite; never credentials/tokens.
  - View embeds allowlisted DHIS2 `/api/reports/{uid}/data.html` URLs (no secrets in URL);
    fallback is Open in DHIS2 (browser session). Live sync/view/download requires confirm.
  - Catalog YAML shortcuts remain for repository/static HTML; DHIS2-owned reports are
    discovered from the API, not hand-maintained as the source of truth.
  - No DHIS2 writes, no direct database access, no report replacement in Phase 1.
  - Audit: `DHIS2_REPORT_*` including `DHIS2_REPORT_SYNC` / `REFRESH` / `OPEN`.

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
- Agent Center: `AGENT_CENTER_VIEW`, `AGENT_RUN_*`, `AGENT_PROMPT_*` (no packed secrets;
  prompt length / ids only on submit).
- Repository Workspace: `REPO_WS_VIEW`, `REPO_WS_READ`, `REPO_WS_SEARCH`,
  `REPO_WS_DIFF_PREVIEW`, `REPO_WS_SAVE`, `REPO_WS_REVERT`, `REPO_WS_CREATE`,
  `REPO_WS_RENAME`, `REPO_WS_DELETE`, `REPO_WS_OPEN_EXTERNAL`,
  `REPO_WS_RUN_START` / `STOP` / `RESTART` / `FAIL` / `HEALTH` / `PORT`,
  `REPO_WS_CONNECT_SCAN` / `PREVIEW` / `SAVE`,
  `DHIS2_REPORT_VIEW` / `VIEW_HTML` / `PREVIEW` / `GENERATE` / `FAIL` / `FAVORITE` /
  `PRESET_SAVE` / `PRESET_DELETE` / `DOWNLOAD`
  (paths and commands redacted; no secret values / unredacted argv).

## Known gaps

- No CSRF tokens yet (localhost personal use).
- Flask debug may be on locally — disable if exposed.
- Owner token is shared-secret, not full multi-user RBAC.
- Gmail / Calendar push not implemented; cache is TTL + manual refresh only.
