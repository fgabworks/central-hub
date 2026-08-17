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
  statement timeout, and row cap (`hub/sql_workspace/`). Optional Stage/Live SSH
  tunnels are lazy, environment-isolated, and bound to a dynamic loopback port.
  Tunnel credentials and private-key paths remain environment-only; a pinned host
  key (explicitly configured or already trusted in `known_hosts`) is required.
  Tunnels are closed during application shutdown.
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
  - AI Assistant Center never preloads mailbox/calendar content. Explicitly
    selected `email_search` / `calendar_lookup` tools are read-only and force
    the active Personal or Work account scope.
- **AI Assistant Center:** read-only Find/Ask/Plan/Review only.
  - Aira and Okarun are server-side policies. Run lookup, cancel, retry, prompts,
    histories, conversations, summaries, tools, and sources are profile-filtered;
    cross-profile run IDs return not found.
  - Aira cannot access repositories, Work Notebook, Work Email/Calendar, SQL,
    DHIS2, jobs, logs, or Audit. Okarun uses Work-scoped services.
  - Adapter argv from `config/agents.yaml` templates (`shell=False`); cwd jailed
    to selected local repository roots.
  - **OpenAI API adapter:** `OPENAI_ENABLED` / `OPENAI_API_KEY` /
    `OPENAI_DEFAULT_MODEL` / optional `OPENAI_ALLOWED_MODELS` /
    `OPENAI_MODEL_CACHE_TTL_SECONDS` / `OPENAI_PRO_MODEL_TIMEOUT_SECONDS`.
    Key never returned to UI, logs, or audit. `GET /v1/models` is authoritative;
    inaccessible models are omitted (not errors). User overrides are revalidated; reasoning
    effort only when supported; Pro models use background mode and longer timeout.
    Estimated tier labels only (no hardcoded pricing).
  - Context packing excludes `.env`, credentials, tokens, binaries, and oversized files.
  - Hub `.env` secret vars are stripped from child process env where obvious.
  - Agent stdout/stderr and answers are redacted before audit/history persistence.
  - Treat agent output as untrusted; Edit/Test modes are disabled.
  - **AI Connections:** CLI login uses only each provider's browser/device flow. The Hub never
    accepts passwords, browser cookies, private sessions, or CLI tokens. Codex uses
    `codex login`, Claude Code uses `claude auth login`, and Cursor uses
    `agent login`; their logout/status commands remain provider-owned.
    Gemini API keys on this page reuse **Settings → AI Providers** storage
    (`data/ai_provider_secrets.env`); saved values are never returned. Storage is
    local/server-side, not encrypted at rest.
  - OpenAI and xAI keys are environment-only (`OPENAI_API_KEY`, `XAI_API_KEY`). Disconnecting
    an API provider disables it in Hub metadata and never reads, returns, or deletes the env key.
  - **Settings → AI Providers:** Set/Replace/Remove writes allowlisted keys to gitignored
    `data/ai_provider_secrets.env` (and removes matching lines from `.env`). APIs, UI, logs,
    and audit details expose only metadata (`configured`, env **names**, status). Stored values
    are never returned. Existing process/`.env` variables continue to work.
  - Provider audit records contain provider ID, action, and boolean outcome; account labels,
    command output, model-list payloads, and credentials are excluded.
  - No assistant tool exists for file edits, commands, SQL execution, mail/calendar
    actions, DHIS2 writes, repository runs, voice input, or text-to-speech.
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
  - **Runs (Phase 2):** YAML templates from `REPO_WS_RUN_PROFILES` /
    `config/run_profiles.yaml` plus repository-specific profiles in
    `REPO_WS_PROFILE_DATABASE` / `data/repository_workspace.db` (DB overrides by id;
    UI builder never rewrites YAML for every create). Executable + argument arrays
    only (placeholders `{port}`, `{repository_path}`, `{environment}`). Port modes:
    `none` / `fixed` / `argument` / `environment_variable`. Fixed ports block when
    occupied and never auto-kill occupants; Find available port is disabled for
    fixed/none. `shell=False`; new process group/session; stop/restart only
    hub-tracked fingerprints (stale PID / PID-reuse refused). No auto-restart on
    file edits. Env values stay server-side (UI shows names).
    Live / write-capable live profiles require `REPO_WS_ALLOW_LIVE_RUNS` plus
    explicit confirmation. Duplicate repo/profile/port runs blocked. Logs under
    `REPO_WS_RUN_LOG_DIR` with size/retention caps + redaction. Audit:
    `REPO_WS_PROFILE_CREATE` / `UPDATE` / `DUPLICATE` / `ENABLE` / `DISABLE` /
    `DELETE` / `TEST` (field names only; paths/env values redacted).
  - **Interactive repository terminal (Workspace Console → Terminal):** real PTY
    (Windows ConPTY via `pywinpty`; Linux/macOS native `pty`) streamed over
    authenticated WebSocket (`flask-sock`) to vendored xterm.js. Creation only for
    enabled connected repositories; cwd jailed with `safe_join` (no traversal,
    absolute paths, symlink/junction escape, or arbitrary roots). Shells are
    allowlisted ids → resolved system binaries (PowerShell default on Windows;
    CMD only when `WC_TERMINAL_ALLOW_CMD=true`). Requires `CENTRAL_HUB_HOST` loopback,
    owner auth, Origin/Host checks, and short-lived WS tickets. Terminal output is
    **not** written to application logs or Audit by default. Audit metadata only:
    `WC_TERMINAL_START` / `STOP` / `INSERT_SUGGESTION` (session id, repo, shell, PID,
    exit code — never raw commands). AI cannot execute; Insert into Terminal fills
    the prompt without Enter. Closing an active session requires confirm and kills
    only that session’s process tree (`taskkill /T` / killpg). Sessions are not
    restored after hub restart. Stop only verified PIDs — never “kill all Python/Node”.
  - **Repository Processes:** OS inventory + hub state (`process_detect.py`). Match
    hub-tracked, cwd-in-repo, command path/entry, profile port — never generic
    runtime names alone. Stop only a verified PID (+ tree); Medium external needs
    typed `STOP PROCESS <PID>`; Low view-only; fingerprint before signal; confirm
    ended + port released. Start conflicts / occupied fixed ports block with a
    pointer to the Run tab (no silent fixed-port switch). Audit:
    `REPO_WS_PROCESS_SCAN` / `STOP` / `FORCE_STOP` / `STOP_BLOCKED`.
  - **Central Hub Process Manager:** extends the same exact-PID identity and port
    primitives on Health. Startup owns an atomic token-matched lock under
    `data/central_hub_process/`, plus `owned_processes.json` with
    PID/command/script/cwd/start-time ownership tokens reconciled via `psutil`.
    Unrelated Python processes are visible but never stoppable. Stops revalidate
    ownership before signaling; self-stop/restart and **Stop Central Hub** use a
    detached fixed-argv supervisor. Stop Central Hub requires typed `STOP CENTRAL HUB`.
    Launcher `scripts/run_central_hub.py` handles Ctrl+C / terminal-close cleanup;
    orphans remain stoppable after failed cleanup. Audits start/stop/restart/
    failed-stop/orphan-recovery. Owner-gated (`require_owner`). Never kills all
    Python processes.
  - **Connect Local Workspace:** user-selected folder only; scan is read-only (no
    subprocess, no installs, no secret-file reads). Git remote mismatch and path
    replacement require explicit confirm. Suggested run profiles are untrusted /
    disabled until reviewed in Settings → Run Profiles; rescans never overwrite
    approved profiles (`REPO_WS_CONNECT_SCAN` / `PREVIEW` / `SAVE`).
- **DHIS2 Reports / Standard Report Manager:** Stage/Live connections from `.env` only.
  - Sync caches metadata (+ optional `designContent`) in SQLite; never credentials/tokens.
  - View embeds hub-proxied `/api/reports/{uid}/data.html` HTML using Stage/Live
    credentials from `.env` (never sent to the browser). App shells
    (`/dhis-web-reports/index.html`) are browser-only shortcuts, not report UIDs.
    Asset/API proxy: `/dhis2/reports/proxy/<env>` with SSRF path allowlist.
    Live sync/view/download/generate requires confirm (once per run for Generate & View).
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

Repository Intelligence performs local read-only Git/file inspection with the registry path
jail, secret-path exclusion, redacted summaries, bounded text/file counts, and fixed Git
argv using `shell=False`. Scan and refresh endpoints are owner-gated and audited as
`REPOSITORY_INTELLIGENCE_SCAN` / `REPOSITORY_INTELLIGENCE_REFRESH`. Cached knowledge never
authorizes writes or overrides runtime DB/DHIS2 evidence.
Standard scan telemetry enforces deterministic execution, no provider/model invocation, and
zero AI tokens. Cached repository hits may ground repository/code questions but are never
accepted as usable authority for runtime database or DHIS2 value queries.

- JSONL at `CENTRAL_HUB_AUDIT_LOG` plus job rows in SQLite `CENTRAL_HUB_DATABASE`.
- Job actions: `SUBMIT_JOB`, `START_JOB`, `JOB_*`, `UPLOAD_INPUT`, `DOWNLOAD_RESULT`,
  `OWNER_LOGIN`, plus existing DHIS2 events.
- Email actions: `EMAIL_OAUTH_*`, `EMAIL_VIEW`, `EMAIL_REFRESH`, `EMAIL_CONVERT_*`,
  `EMAIL_LINK_REPO`, `EMAIL_ATTACHMENT_DOWNLOAD`, `EMAIL_ACCOUNT_ASSIGN` (no token values).
- Calendar / Google Connections: `CALENDAR_*`, `GOOGLE_CONNECTIONS_VIEW` (no token values).
- Agent Center: `AGENT_CENTER_VIEW`, `AGENT_RUN_*`, `AGENT_PROMPT_*` (no packed secrets;
  prompt length / ids only on submit).
- AI Connections: `AI_CONNECTIONS_VIEW`, `AI_CONNECTION_ACTION`
  (provider/action/outcome only).
- Repository Workspace: `REPO_WS_VIEW`, `REPO_WS_READ`, `REPO_WS_SEARCH`,
  `REPO_WS_DIFF_PREVIEW`, `REPO_WS_SAVE`, `REPO_WS_REVERT`, `REPO_WS_CREATE`,
  `REPO_WS_RENAME`, `REPO_WS_DELETE`, `REPO_WS_OPEN_EXTERNAL`,
  `REPO_WS_RUN_START` / `STOP` / `RESTART` / `FAIL` / `HEALTH` / `PORT`,
  `REPO_WS_CONNECT_SCAN` / `PREVIEW` / `SAVE`,
  `REPO_WS_PROFILE_CREATE` / `UPDATE` / `DUPLICATE` / `ENABLE` / `DISABLE` /
  `DELETE` / `TEST`,
  `REPO_WS_PROCESS_SCAN` / `STOP` / `FORCE_STOP` / `STOP_BLOCKED`,
  `WC_TERMINAL_START` / `WC_TERMINAL_STOP` / `WC_TERMINAL_INSERT_SUGGESTION`
  (session/repo/shell/PID/exit only — never raw commands or scrollback),
  `DHIS2_REPORT_VIEW` / `VIEW_HTML` / `PREVIEW` / `GENERATE` / `FAIL` / `FAVORITE` /
  `PRESET_SAVE` / `PRESET_DELETE` / `DOWNLOAD`
  (paths and commands redacted; no secret values / unredacted argv).

## Known gaps

- Flask debug may be on locally — disable if exposed beyond loopback.
- Owner token is shared-secret, not full multi-user RBAC.
- Gmail / Calendar push not implemented; cache is TTL + manual refresh only.
- Interactive terminal CSRF uses Origin/Host + owner session + WS tickets (no
  classic form CSRF token). Keep `CENTRAL_HUB_HOST=127.0.0.1`.
- Full descendant PID→port matching for terminals is best-effort (root PID +
  repository association); prefer Open URL / Stop Process on verified rows.
