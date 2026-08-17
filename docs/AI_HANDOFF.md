# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Settings → AI Providers visual Settings (2026-08-17)**

`/settings/ai-providers` now uses the CLIMATE Settings submenu
(General, Appearance, AI Providers, Integrations, Security, Notifications,
Advanced) and horizontal provider cards from the Agent Center registry.
API-key providers (Gemini, Grok/xAI, OpenAI) plus planned Claude/Anthropic
and Local Models cards share one metadata contract. Add/Replace Key opens a
password modal that never preloads or returns the stored value. Test
Connection reuses the existing adapter probe and returns sanitized
success/failure copy only. Secrets remain in gitignored
`data/ai_provider_secrets.env` plus existing `.env` variables; storage is
local/server-side, not encrypted. Gemini Chat still uses the same Ask-only
adapter (`GEMINI_API_KEY` / `GOOGLE_API_KEY`, Google key wins). CLI
providers stay on AI Connections.

Prior: **Settings → AI Providers backend (2026-08-17)**

CLIMATE can manage provider credentials from **Settings → AI Providers**
(`/settings/ai-providers`). Cards are derived from the Agent Center connection
registry. API-key providers support Set/Replace/Remove Key and Test Connection.
Stored secrets stay in gitignored `data/ai_provider_secrets.env` plus existing
`.env` variables; settings APIs return metadata only. Gemini still uses the
same Agent Center adapter. Adding a future API provider is adapter + metadata
registration, not a Settings rebuild.

Prior: **Standalone AiriX · CLIMATE Chat (2026-08-17)**

General CLIMATE Chat is now a top-level ChatGPT-like page at `/work/chat` and
`/personal/chat`, with a sidebar **CLIMATE Chat** entry in VANTA and ARCTIC.
The existing `/work/climate` and `/personal/climate` IDE remains the **Workspace
Assistant** (editor, Explorer, diagnostics, repo-scoped AiriX panel).

The standalone page reuses Agent Center SQLite conversations/runs, the Gemini
adapter, dynamic exact-model discovery, streaming, Stop/cancel, rename, and
`display_prompt` vs packed provider prompt. Chat runs send no repository IDs or
file bodies. Gemini stays Ask-only with no silent model fallback and env-only
keys. Conversation lists use `surface=chat` so editor threads stay in the
Workspace Assistant.

Prior: **AiriX CLIMATE Chat + Gemini read-only provider (2026-08-17)**

The existing CLIMATE AI panel presents **AiriX · CLIMATE Chat** and explains that the
selected provider powers AiriX rather than replacing its identity. CLIMATE reuses Agent
Center's SQLite `agent_conversations`/`agent_runs`: scoped list, detail, and rename APIs
let the browser reconcile server conversations for the active repository, hydrate
completed messages, and persist renames. The browser store remains a local mirror for
rich in-progress UI state.

Gemini is now the first API provider for this surface. The `gemini_api` adapter uses
environment-only credentials, discovers accessible text models from Google's Models API,
requires an exact user-selected model with no silent fallback, packs only explicitly
selected file bodies plus the bounded Context Resolver packet, and streams SSE text
through the shared queued/running/completed/cancelled run lifecycle. CLIMATE stores the
clean user prompt separately from internal provider context. Same-provider, same-model
completed turns can be reused as bounded conversation history. Gemini v1 is ASK-only and
advertises no editing, command execution, SQL, email/calendar action, agent, tool-runtime,
or native repository-investigation capability.

Configure `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the server environment; when both are
present, `GOOGLE_API_KEY` takes precedence. Optional controls are documented in
`.env.example`, including the default/allowed models, endpoint, timeout, cache TTL, and
output-token limit.

Focused verification: `python -m unittest tests.test_gemini_provider` (8 passed),
`python -m unittest tests.test_climate` (29 passed), `node --check static/js/climate.js`,
the CLIMATE viewer browser test (1 passed), and `git diff --check`.

Prior: **CLIMATE execution mode selector (2026-08-15)**

CLIMATE chat has a compact **Mode** selector next to Provider/Model:
`CLIMATE Assisted` (`climate_assisted`) and `Direct Provider` (`direct`). Mode is
orchestration, not provider identity — there is no Smart mode and no Direct Codex
mode. Assisted keeps the Context Resolver packet/hints (native repo investigation
still allowed). Direct sends the raw user prompt with selected repo/cwd and normal
provider instructions; it never builds or sends a resolver packet or candidate-source
hints. Direct bypasses retrieval assistance only: approved repo boundary, ASK
read-only sandbox, controlled EDIT, cancel, diagnostics, token accounting, and
Git/terminal protections remain. Mode persists in workspace prefs and per
conversation. Token Efficiency records `execution_mode` and labels the button
**Compare with Direct** or **Compare with CLIMATE**. Fair comparison still requires
the same repo, commit, provider, model/config, prompt, read-only mode, and fresh
session.

Prior: **CLIMATE retrieval for simple factual/reference questions (2026-08-15)**

Simple ASK questions (provinces of a region, name of a UID, which file defines a
symbol) now do a lightweight filename/index lookup first. Context Resolver downranks
`lookup/logs/**`, bulk-apply jobs, dry-run exports, generated artifacts, and huge
`reference-json` dumps unless the prompt is about logs/history. Generic words such as
`region` no longer dominate stronger phrases (`Region VIII`, `Eastern Visayas`).
Hub search is bounded (lines, characters, unique files, timeout); timed-out searches
count as failures. Diagnostics redact emails/usernames in snippets. Native Codex
investigation, ASK read-only safety, ANC/PNC logic targeting, and Token Efficiency
are unchanged. The resolver remains hints, not a gate.

Prior: **CLIMATE Markdown chat readability polish (2026-08-15)**

Chat Markdown stays compact. Section headings and lists have a little more
air, table cells have slightly more padding, and wide tables scroll
horizontally instead of wrapping/crushing. UID chips, inline code, and
file/function chips stay visually distinct. Raw `msg.text` is unchanged.

Prior: **CLIMATE clickable file/function references (2026-08-15)**

AI answers turn repository path traces such as
`` `lookup/convergence/anc_timing.py` — `anc_trimester_rule_summary` ``
into in-chat buttons. Clicking one opens the current repository’s read-only
Monaco viewer, focuses the editor, and jumps near the named function when
that symbol can be found in the file (or via content search). File-only
paths still open the file. Markdown, UID chips, Sources/Details, and
read-only mode are unchanged.

Prior: **CLIMATE read-only Monaco long-file scroll (2026-08-15)**

The main editor host is a bounded box (`overflow: hidden`, `minmax(0,1fr)`).
Monaco fills that remaining area and lays out from the host size, including
after bottom-panel resize and window resize. Long files scroll from first to
last line via Monaco’s own scrollbar. Markdown Preview still scrolls
independently. View state is saved per tab. The viewer stays read-only.

Prior: **CLIMATE read-only viewer blank-file fix (2026-08-15)**

Opening a repository text file no longer shows an empty Monaco buffer.
`captureActive()` was copying the current editor value (Monaco's default
empty model) onto the tab *after* the file API had filled `content`. The
viewer is still read-only: it does not write that buffer back, creates or
reuses a per-path model with the API text, ignores stale fetches for the
active tab, and distinguishes binary / read errors / genuine empty files.

Prior: **CLIMATE Markdown UID chips + spacing polish (2026-08-15)**

Rendered CLIMATE chat Markdown now highlights standalone likely DHIS2 UIDs
(11 alphanumeric characters, starting with a letter, containing a digit) as
compact copyable chips. Decoration runs after marked + DOMPurify on the HTML
tree and skips fenced `pre` blocks, links, and path-like tokens. Raw `msg.text`
is unchanged. Heading/paragraph/table spacing is slightly more open, still
compact for the narrow AI panel.

Prior: **CLIMATE daily IDE: Markdown chat + read-only file viewer (2026-08-14)**

AI chat now renders GFM Markdown with marked + DOMPurify + highlight.js
(Obsidian-like hierarchy, tables, fenced code with Copy, sanitized HTML). Raw
`msg.text` is unchanged. Repository Explorer opens text/source files in the
main Monaco area as **read-only** (line numbers, syntax, find, select/copy,
tabs). Markdown files have Source | Preview using the same renderer. Binary
types show `Preview unavailable for this file type`. Path jail is unchanged.
Save/Git write/commit/push are **not** in this viewer.

`Explored N files` / Files inspected count provider file-read commands,
including PowerShell `Get-Content` / `-Path`, and still ignore search-hit
paths. Chat elapsed time is **End-to-end runtime**; Token Efficiency shows
**Provider runtime** (they measure different clocks).

Prior: **CLIMATE investigation targeting + inspect telemetry (2026-08-14)**

Repository ASK runs now rank exact domain concepts/acronyms/aliases (FIC, CH_FIC,
FIC_STATUS, immunization) above generic leftovers such as `child`. Resolver
search prefers phrase → acronym → module, then a stdlib AST hint pass. ASK
instructions tell Codex to investigate progressively, bound `rg` output, and
avoid PowerShell-invalid globs (`tests *.py`, `lookup/test_*`). Failed searches
are not counted as inspections. `Explored N files` is files actually opened/read.
Candidate sources, search-matched files, and tool calls are separate metrics.
Token Efficiency uses the same inspected-file definition. Graphify is **not**
adopted; a local read-only AST helper is optional hints only.

Prior: **CLIMATE ASK logic-answer formatting (2026-08-14)**

Implementation / indicator / scoring / eligibility / threshold questions now get a
decision-oriented answer outline: core rule, compact table, one example, edge cases,
household roll-up, exact `path/file.py` — `function_name` traces, then a one-line
summary. This is presentation only. `hub/climate/logic_format.py` detects the user
Task (not source snippets) and injects ASK instructions in
`ClimateCodingAdapter.execute`; `humanize_answer` may reorder recognizable
markdown/`Label:` sections and exact-line-dedupe. It does not invent thresholds,
DEs, tables, or a one-line summary, and it leaves insufficient-evidence answers
unchanged. EDIT mode and unrelated prompts are not forced into this structure.
Repo investigation, scoring, source selection, citations, and provider execution
are unchanged.

Prior: **CLIMATE Token Efficiency (manual Direct Codex comparison) (2026-08-14)**

Completed Codex runs can store an immutable benchmark snapshot (raw user prompt,
repo, commit SHA, model, reasoning/config, Codex version, read-only, CLIMATE
provider usage, runtime, files inspected, Context Resolver packet size, source
candidates, fresh vs resumed). Direct Codex is **never** launched from a normal
prompt. The Token Efficiency card is compact: CLIMATE vs Direct totals, runtime,
and files inspected; one result line (green savings / red increase); cached tokens
shown as a subset of input; Candidate Sources distinct from files actually
inspected. A resumed CLIMATE session is labeled in Benchmark details because Direct
is always fresh/ephemeral — that session gap, not the Context Resolver packet, was
the cause of the 708,792 vs 280,908 PNC measurement.
fresh `codex exec --json --ephemeral --sandbox read-only` with the same exe /
repo cwd / recorded commit / model / raw prompt, no `resume`, no provider
session, no CLIMATE packet or chat history. If HEAD moved, comparison is
`Not comparable` with `Benchmark cannot be reproduced: repository commit has
changed.` (no checkout). Historical runs without a recorded SHA stay
`Benchmark unavailable`. Results persist beside the original run
(`data/agent_center/runs/<id>/token_efficiency.json`) so reopen does not rerun.
Missing provider fields stay Unavailable, not zero. Direct JSONL is diagnostics
on disk only.

Prior: **CLIMATE Terminal compact toolbar + ConPTY newlines (2026-08-14)**

CLIMATE Terminal keeps the repo-scoped PTY/WebSocket/xterm path. Root cause of
PowerShell prompt glue was fitting/resizing the PTY while `#wc-term-stage` was
hidden (tiny cols), then painting the ConPTY snapshot into xterm at that width.
Fix: hold WS output until the host is at least 40×10, never resize below that,
normalize lone LF to CRLF, and do not enable xterm `windowsMode`/`convertEol` on
Windows ConPTY (it already emits CRLF). Toolbar is a compact VS Code-like row
(scoped to `.climate-wc-terminal`).

Prior: **Windows Codex standalone discovery (2026-08-14)**

CLIMATE/Agent Center still prefer PATH, then discover the official Windows standalone
install at `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe` (no username hardcoded).
Every Windows candidate is used only when sibling `codex-code-mode-host.exe` exists, so a
stale `.sandbox-bin` CLI cannot be selected when the hub process has an old PATH.
Portable `~/.codex/bin` remains valid when complete. Test Connection / diagnostics expose
the resolved executable, runtime health, and discovery source. Sandbox stays
`--sandbox read-only`.

Prior: **Windows Codex executable discovery (2026-08-14)**

`discover_codex_executable()` still prefers PATH, then `~/.codex/.sandbox-bin` and
`~/.codex/bin`. On Windows a candidate is selected only when sibling
`codex-code-mode-host.exe` is present, so a stale sandbox-bin CLI cannot be invoked.
Incomplete installs surface `Codex installation incomplete: codex-code-mode-host.exe is missing`
instead of a later spawn failure. Linux/macOS discovery is unchanged. Sandbox remains
`--sandbox read-only`. Hub never deletes or rewrites Codex install files.

Prior: **CLIMATE VS Code-like bottom panel (2026-08-14)**

CLIMATE’s bottom panel is Problems | Output | Debug Console | Terminal | Ports, with
Tests and Git kept as secondary tabs so proposal/diff review still works. Problems
lists Monaco/JSON/save/runtime diagnostics only and opens the file at line.
Output is channelled (CLIMATE, Runs/Tests, Git, AI/System), timestamped, clearable, and
must not show raw Codex/provider protocol. Debug Console reuses hub-managed run-profile
logs and tells the truth when idle (`No active debug session`); it does not evaluate
expressions. Terminal is still the repository-scoped PTY/WebSocket (`WCTerminal`) and
survives tab switches. Ports discovers local listeners via existing process/port APIs,
annotates terminal/run ownership when known, and never forwards or stops processes.

Prior: **VANTA native Codex repository investigation (2026-08-14)**

For a valid VANTA repository, the local Context Resolver is now an accelerator rather
than a Codex gate. It still runs at zero tokens, ranks/qualifies likely sources, and
expands once when weak, but sends compact instruction/skill/path+symbol hints without
duplicating source bodies. Agent Center permits only adapters advertising native
repository investigation to proceed without a usable initial evidence packet. Codex ASK
runs at the approved repository cwd with `--sandbox read-only` and may use safe read-only
search/file/symbol/reference/import/test/git inspection. Packed prompts keep
`tool_ids=[]` and say `Hub tools: none` plus native Codex inspection allowed — they must
not inject `Enabled read-only tools: none.` Completed `Explored N files` counts provider
read/inspection activity only; source candidates remain a separate Sources list.
Providers without repository access keep the prior cannot-verify/zero-token evidence
gate; EDIT keeps the existing proposal/diff Accept/Reject flow.

CLIMATE chat IDs map to Agent Center conversations. When reuse is enabled, Codex's
official persisted exec session ID is captured from JSONL and resumed explicitly only for
the same immediately preceding provider/model/repository scope. Cross-provider handoff
stays compact and breaks continuation. Raw JSONL remains diagnostics-only.

Prior: **CLIMATE Context Resolver (2026-08-14)**

Deterministic local context resolution runs before any coding-provider call (0 AI
tokens): resolve repo → applicable/nearest instructions → metadata-scored skills →
RI/local search → executable-symbol qualification → one local path/terminology expansion
when weak → bounded packet → confidence gate. Docs/tests may lead to implementation but
do not qualify as authoritative for implementation questions. High confidence invokes
the selected provider through the existing Agent Center grounding/runtime; low or
unqualified evidence returns `Not enough repository evidence. Model not invoked · 0
tokens.` ASK stays read-only; EDIT gathers evidence first. Context packets are capped at
18,000 characters, same-provider session reuse remains supported, and cross-provider
handoff stays compact. Diagnostics record candidate/authority decisions, symbols,
scores, reasons, confidence, context size, invocation state, and current-run tokens.

Prior: **CLIMATE zero-token preflight + evidence gate (2026-08-14)**

Before every CLIMATE provider call, a local preflight resolves the repo, loads
applicable AGENTS/SKILLS/provider/nested instructions (task-relevant only),
searches RI + local files, and builds a bounded context packet. Repo-specific
Ask/Edit calls are gated until instructions + at least one authoritative source
exist; otherwise the UI returns a zero-token blocked answer. Superseded by the
Context Resolver milestone above.

Prior: **CLIMATE Codex account rate limits (2026-08-14)**

Codex capacity in the AI usage chrome is no longer estimated from chat/session tokens.
CLIMATE starts/reuses `codex app-server`, calls `account/rateLimits/read`, listens for
`account/rateLimits/updated`, and normalizes multi-bucket `rateLimitsByLimitId` windows
(`usedPercent` → `remainingPercent`, reset times, plan, credits). Session tokens stay
separate from Codex capacity. Unavailable / non–ChatGPT-backed auth shows
`Codex limit unavailable` with no fabricated percentage. Refresh on Codex connect, AI
panel open, completed Codex run, and Refresh. Brief server cache avoids respawning on
every open. Verify: `CLIMATE_BASE_URL=… node scripts/climate_codex_limits_verify.js`.

Prior: **CLIMATE AI Stop control (2026-08-14)**

While a run is active, Send is replaced by a red `■ Stop` that calls the real
`/runs/<id>/cancel` path (provider terminate). UI shows Stopping… → Stopped by user,
freezes live activity, keeps partial answer, never stages incomplete edits, and returns
to Send for a new prompt. Activity UI shows one current pulse + ✓ completed steps
(no Planning next moves duplicate). Verify:
`CLIMATE_BASE_URL=… node scripts/climate_stop_verify.js`.

Prior: **CLIMATE Session Usage compact UI (2026-08-14)**

Token pill is a single compact row (`38.4k tokens ▰▰▰▰▱ 78%`); popover is IDE-sized
(14px session total, 12px current-run/values). Codex capacity now uses account rate
limits (see current milestone). Maximized AI widens the panel only — usage fonts stay
fixed. Verify: `CLIMATE_BASE_URL=… node scripts/climate_usage_compact_verify.js`.

Prior: **CLIMATE AI chat ask/edit rendering (2026-08-14)**

Requests are classified as ASK/EXPLAIN vs EDIT before execution. Ask stays read-only
(no staged proposals / Undo-Keep-Review) even if the provider returns `{"edits":…}`;
human answers are extracted for chat and raw protocol stays in Details. Edit keeps
reviewed diffs, large-diff warnings, and Run Summary. Session titles come from the
first task; token header remains session total with a labeled usage popover.
Verify: `CLIMATE_BASE_URL=… node scripts/climate_chat_render_verify.js`.

Prior: **CLIMATE AI live activity UI (2026-08-14)** — evidence-only progress;
`scripts/climate_activity_verify.js`.

Prior: **CLIMATE IDE polish (2026-08-14)**

**CLIMATE IDE polish (2026-08-14)**

Implemented `/work/climate` (VANTA) and `/personal/climate` (ARCTIC) as one compact
VS Code-style shell: Monaco with fallback, guarded Explorer/open/search/save,
multi-tabs, Git status/diff, Problems | Output | Tests | Git, collapsible/resizable
panels, and per-workspace/repository local tab/layout persistence. `hub/climate/`
enforces repository/run/proposal isolation; `personal`/`arctic` tagged repositories
are ARCTIC-only and untagged repositories remain VANTA by default.

The single provider-neutral `ClimateCodingAdapter` delegates availability, model
discovery, execution, cancellation, results, usage, auth, argv construction, and
streaming logs to existing Agent Center connections/adapters/runner. Supported panel
providers are Codex, Claude Code, and Cursor Agent with exact selected models and no
silent substitution. Selected file/current selection/repo context are bounded to the
active scope. Providers stay read-only; fenced replacement proposals produce server-
owned diffs and require Accept/Reject, with base SHA-256 conflict detection before the
existing safe editor writes. No AiriX Tool Runtime changes, MCP/browser/scheduler,
marketplace/debugger, unrestricted shell, commit/push/pull, or ECLIPSE work.
Codex is explicitly VANTA-only under its existing Agent Center profile policy; ARCTIC
shows that limitation instead of crossing profiles, while Claude Code and Cursor Agent
can receive explicitly selected ARCTIC file content.

Focused tests: `tests/test_climate.py` (7 tests). Combined repository/provider/CLIMATE
regression: 32 passed, 1 skipped. Route smoke: both CLIMATE pages HTTP
200. Browser visual QA was unavailable because no browser backend was connected.

The base shell now brands only CLIMATE and exposes a `VANTA | ARCTIC` switcher.
Workspace context is displayed as `VANTA / DOH / <Repository>` or
`ARCTIC / <Personal Context>` without redundant Work/Personal workspace labels.
Work/Personal backend identifiers, routes, tables, environment variables, and storage
paths remain unchanged. VANTA navigation is flattened to the requested tool list; Code
Workspace no longer duplicates it with an inner activity rail. Explorer noise is hidden
by default behind a persisted Show excluded control; `.git` and secrets remain hidden.

Prior: **Official References — Subject detection (2026-08-12)**

**Official References — Subject detection (2026-08-12)**

Editable `subject` (+ internal `subject_source`: detected / suggested / manual) on
Work Notebook Official References. On upload/replace, bounded text extract from
TXT/MD/PDF/DOCX (`hub/notebook/text_extract.py`, no OCR) then deterministic
SUBJECT/RE detection or heading suggestion (`hub/notebook/subject_detect.py`, no
LLM). Quick Add autofills via `POST /api/notebook/references/detect-meta` before
submit; user can edit/clear. Legacy rows keep `subject=NULL`. Search includes
Subject. Migration `010_official_references_subject`.

Tests: `tests/test_official_references.py`.

Prior: **Official References — Work Notebook library (2026-08-12)**

Work-only sub-view at `/work/notebook?view=references`. Groups by Year → Type
(Department Memoranda / Advisories / Guidelines / Other). Supports local upload,
external link, or both; optional short note + source URL. Auto-sets added date,
storage kind, and path under `data/work-notebook/references/{year}/`. Year/type
inferred from filename when possible (user can correct). Quick Add: pick/drop file,
only Type/Year if undetected. Search + Year/Type filters. No AI/OCR/versioning/
approvals/tags. Migration `009_official_references` in `notebook.db`.

Tests: `tests/test_official_references.py`.

Prior: **ARCTIC — Personal profile + document control center (2026-08-12)**

CLIMATE naming: CLIMATE = system · VANTA = Work · ARCTIC = Personal · AiriX = shared AI ·
ECLIPSE = reserved. ARCTIC (`hub/arctic/`, `data/arctic.db`) is a compact Personal
control center with nav tabs **Dashboard | Profile | Files**. One structured Personal
Profile + one Document Registry for Local + Google Drive **references only** (no file
copies, no duplicate folder trees). Smart collections/tags; primary roles (CV, photo,
signature, cover letter, portfolio, diploma, transcript, employment certificate);
“latest CV” resolves the Primary CV. Career Pack is a logical view. Google Drive is a
clean source abstraction with sync **deferred**. AiriX/Aira isolation: ARCTIC context
only via explicit selection (`/api/arctic/ai-context`); never auto-inject into RI/logs/
Work (VANTA). Passwords/OTPs/banking blocked. UI reuses charcoal + crimson inside
`.arctic-shell`. Does **not** modify AiriX Tool Runtime, VANTA Work stack, or ECLIPSE.

Tests: `tests/test_arctic.py`.

Prior: **AiriX Unified Tool Runtime — Phase 2: Runtime Intelligence & Efficiency (2026-08-10)**

Extends Phase 1 (same `hub/agent_center/tool_runtime/` package — no parallel runtime).
Dynamic task-relevant tool selection scores intent, selected context, Repository
Intelligence categories, and mode (`intelligence.py`). Mid-loop on-demand
`repository_intelligence` + `skill_recall` replace overpacked initial instructions
when lean context is enabled. Observation prune preserves grounded facts and
completion-required tools. T0 → Tool Runtime continuation seeds prior observations
without rebuilding unchanged context (`continuation.py`). Process-local provider
session cache reuses `previous_response_id` when conversation+provider+model+fingerprint
match. Stuck guard soft-recovers with alternate-tool nudges before hard stop.
Cheapest-capable provider selection for synthesis/reasoning; explicit manual
provider/model choices are preserved with no silent fallback. Completion contract +
grounding remain the final stop condition. Per-run telemetry records steps, tool
calls, context chars/tokens, RI entries, session reused, retries, provider/model,
AI tokens, runtime, task solved, grounded.

**Fix (same day):** T0 `source_available_needs_query_construction` escalate no longer
rebuilds evidence and grounding-gates with Model None / Cannot verify. Capability
escalate preserves T0 packet/RI/filters, resolves a real configured API model, exposes
`sql_query_execute`, and enters Unified Tool Runtime. Regression:
`tests/test_airix_capability_escalation.py::QueryConstructionEscalationRuntimeTests`.

**Fix (same day, child finalize seam):** Empty/failed Tool Runtime child answers no longer
become UI `(no answer)`. Parent finalization surfaces the child error (e.g. provider
quota/stream failure), merges child `sql_query_execute` steps into evidence/telemetry,
and openai_runner marks blank terminal answers as `empty_answer` failures. E2E:
`tests/test_airix_query_construction_sql_e2e.py`.

**Fix (same day, provider failure + approval):** Provider failures are classified
(`quota`/`auth`/`rate_limit`/`unavailable`/`timeout`/`runtime`) in
`tool_runtime/provider_failures.py`. Quota/auth are hard (no retry loop); transient
rate-limit/timeout use bounded `max_retries`. Short-lived health cache steers Smart/Auto
away from just-hard-failed providers; when another compatible Tool Runtime API provider
(`openai-api`/`grok`) is configured+healthy, the **same** execution continues with
preserved prompt/repo/RI/T0 evidence/contract/filters (no context rebuild). Manual
provider/model never silently substitutes. Provider identity (including Codex) no longer
triggers interactive approval — **approval belongs to the action/tool policy**; Send
authorizes the selected provider/model for RO Ask/Inspect/Plan/Agent. Budget may still
block expensive Codex escalation; capacity warnings remain. Tests:
`tests/test_airix_provider_failure_and_approval.py`.

Preserved: RBAC, RO SQL/DHIS2, Stage/Live isolation, exact provider/model, timeout/
cancel, audit, budgets. CLI adapters still packed-context only.

**Live-provider limitation:** If OpenAI quota is exhausted and no alternate Tool Runtime
API provider (e.g. Grok) is configured/healthy, Smart/Auto still stops with the exact
quota error. Manual OpenAI selection never auto-switches.

Deferred Phase 3+: MCP, browser, scheduler, shell/`run_command`, write tools,
workflow editor, CLI native tool loops.

Tests: `tests/test_airix_tool_runtime_phase2.py` (+ Phase 1 suite),
`tests/test_airix_provider_failure_and_approval.py`.

Prior: **AiriX Unified Tool Runtime — Phase 1 (2026-08-10)**

Provider-neutral iterative **read-only** Tool Runtime so AiriX agents actively use Hub
tools instead of only packed context. Central `ToolSpec` registry + unified
`execute(tool, args, context) → {ok, summary, observation, source, duration, error}`
wrap existing handlers (`openai_tools` + RI / saved SQL RO / Data Explorer RO). API
adapters (OpenAI/Grok) run model → policy gate → execute → observation with active-tool
filtering, observation prune, max-step + hard runaway cap, duplicate/stuck guard,
timeout/cancel, and exact provider/model preservation (no silent fallback). T0 remains
first; completion contract still stops when solved+grounded. Transient live step feed
attaches to execution status (`tool_runtime_feed`); dock shows compact Tool steps.

Modes: Inspect RO auto; Ask minimal RO; Plan RO only; Agent fixed provider/model + RO;
Smart uses routing signals for when Tool Runtime is needed. CLI adapters unchanged
(packed context only in Phase 1).

Deferred: MCP, browser, scheduler, shell/`run_command`, write tools, workflow editor.

Tests: `tests/test_airix_tool_runtime_phase1.py`.

Prior: **Inspect explanation synthesis answer propagation (2026-08-10)**

When T0 gathers grounded RI evidence and escalates for explanation synthesis
(`t0_explanation_synthesis` → AI), the child provider's terminal answer is now
propagated to the parent execution/orchestration result. Empty child content
becomes `synthesis_failed` with an explicit reason. Telemetry marks these runs
Hybrid with route `T0 → <provider/model>`, LLM Yes only when the child ran, and
`usage unavailable` instead of fake zero token totals. Successful evidence-backed
synthesis scores Evidence/Task Solved/Grounded Yes. RI diagnostics and T0 evidence
sources are preserved. RI retrieval, routing policy, and context packing unchanged.

Tests: `tests/test_airix_explanation_synthesis.py`.

Prior: **Inspect-mode Repository Intelligence attachment (2026-08-10)**

Root cause: the grouped composer could submit the API member (`live-processing`) while RI is
indexed under the selectable command member (`live-processing-local`). That ID was not
canonicalized before RI lookup. The orchestration wrapper then rebuilt a parent execution
without the child context/RI diagnostics, so real repo-search evidence coexisted with default
`Repository: None` / `Entries: 0` telemetry.

Fix: the shared resolver translates group siblings to their one selectable local member using
`repository_group_id`; RI is attached before T0; Relevant Files contributes context items; T0
merges bounded RI hits and emits `tool:repository_intelligence`; the parent preserves the actual
terminal child context and telemetry derives RI from that context. Explanation contracts with
usable grounded evidence now escalate to the cheapest available appropriate model using only
the bounded evidence/RI entries. T0-complete tasks remain deterministic.

Tests: `tests/test_airix_inspect_repository_intelligence.py` (+ updated
`tests/test_airix_repository_context.py`); 83 focused AiriX/RI tests pass across the
runtime, telemetry, persistence, capability-escalation, and orchestration suites.

Prior: **Repository Intelligence testing and telemetry finalization (2026-08-10)**

Standard Scan & Learn, manual refresh, automatic Git refresh, and instruction refresh now
persist deterministic scan events with `LLM Invoked: No`, no provider/model, zero AI tokens,
file counts, runtime, and indexed commit. Deep AI Analysis is present but disabled and not
implemented. Learned selected repositories feed the existing classification, grounding,
tool, routing, planning, and bounded prompt paths. Per-run diagnostics expose repository,
indexed/current commit, freshness, entries, and contributed context. Cached intelligence
cannot satisfy authoritative runtime DB/DHIS2 value queries.

Tests: `tests/test_repository_intelligence.py` and
`tests/test_repository_intelligence_ui.py`.

Prior: **Repository Intelligence UI nested navigation (2026-08-10)**

Moved Repository Intelligence out of the oversized card grid into DHIS2-style nested
navigation under Repositories:

- Section tabs: General · Connection · Repository Intelligence · Files & Changes ·
  Settings · Logs & History (`/repositories/sections/*`)
- Compact status table (Repository | Connection | Intelligence Status | Last Updated |
  Indexed Commit | Actions) with Scan & Learn / View Knowledge / Refresh / More
- Per-repo detail at `/repositories/<id>/intelligence` (status, last learned, commit,
  changed files, files indexed, categories, recent activity)
- Backend scan/refresh/knowledge APIs unchanged
- Tests: `tests/test_repository_intelligence_ui.py`

Prior: **AiriX Repository Intelligence (2026-08-10)**

Connected local repositories now have Repository Intelligence for AiriX. The first scan
is manual. It stores a compact profile plus per-file searchable summaries in the existing
Agent Center SQLite database, records the Git commit, reports changes, incrementally
refreshes affected files, and immediately refreshes changed `AGENTS.md`/`SKILLS.md`/AI/security
instructions. Secret paths are excluded and stored summaries are redacted.

Selecting a learned repository in AiriX automatically retrieves at most six task-relevant
knowledge entries for classification, read-only tool selection, plans, and prompt context.
The full index is never prompt-packed. Git changes are refreshed before retrieval, deleted
entries cannot remain stale, and runtime DB/DHIS2 evidence explicitly overrides cached
repository knowledge. Existing five modes, RBAC, budgets, grounding, Stage/Live isolation,
and read-only execution remain unchanged. Focused tests:
`tests/test_repository_intelligence.py`.

Prior: **AiriX five-mode Cursor/VS Code-style agent architecture (2026-08-10)**

The existing Smart Routing/provider/context engine now exposes one composer:
`[Mode] [Agent] [Model] [Repository] [+ Context]`.

- **Smart** — Agent/Model are Auto; existing cheapest-capable T0/DB → AI path.
- **Ask** — read-only Q&A through selected or dynamically resolved provider/model.
- **Inspect** — deterministic/tools-first investigation.
- **Plan** — investigate, then use read-only provider plan mode; no writes.
- **Agent** — bypass T0/routing and execute exact provider/model with no fallback.

Mode/provider/model/repository/context persist per workspace. Context sources are DHIS2
environment, RO database/Data Explorer, relevant files, workspace, and prior findings;
they select scoped tools and never pack the whole repo. Stage/Live is forced in tool context.
Existing grounding/completion/telemetry/budgets/RBAC/RO controls and dynamic model discovery
remain authoritative. Executions expose mode, resolved provider/model, T0/LLM usage,
tokens, tools, Task Solved, Grounded, context size/items, and session reuse.
Tests: `tests/test_airix_routing_mode.py` plus existing provider/model/grounding/security suites.

Prior: **AiriX Routing Mode: Smart vs Direct Agent — Efficient (2026-08-10)**

Prior: **AiriX capability-aware escalation after T0 (2026-08-10)**

Root cause: after T0 found evidence but left the task unsolved, AiriX stopped at
Cannot verify even when a connected read-only database (or AI query construction)
could materially finish the request. Selected Codex also skipped deterministic work.

Fix: `hub/agent_center/capability.py` classifies T0 failure reasons and chooses the
cheapest next capability. Structured data: try saved RO SQL against configured
connections (bind detected filters) before LLM; escalate only when AI can help
(e.g. unbound params / query construction); preserve selected Codex model; otherwise
Cannot verify. Telemetry exposes T0 failure reason, next capability, DB attempted,
AI escalate. Dock `shell-dock-23`. Tests: `tests/test_airix_capability_escalation.py`.

Prior: **AiriX dynamic completion contract (2026-08-10)**

Root cause: T0 treated discovery (repo paths, related SQL/UIDs, prior findings) as task
completion and could declare success without producing the required output for the intent
(e.g. a count answered with file matches).

Fix: `hub/agent_center/completion.py` derives a per-prompt contract (intent, required
output, filters, authoritative sources, criteria) without hard-coded places/indicators.
Evidence Found / Task Solved / Grounded are tracked separately; Grounded=Yes only with
authoritative evidence. T0 validates against the contract before finishing; discovery-only
→ unsolved (escalate when allowed, else Cannot verify). Dock shows the three flags
(`shell-dock-23`). Tests: `tests/test_airix_completion_contract.py`.

Prior: **AiriX execution telemetry consistency (2026-08-10)**

Root cause: when `mode`/`tier` were missing on a finished T0 row, telemetry fell through
to the AI path and forced `llm_invoked=True` / `Tier: T?` / empty tools — even though no
provider child run existed (e.g. deterministic `repo_search`).

Fix: derive telemetry from actual execution events only. `llm_invoked=True` only when a
child AI run id exists; pure deterministic (no child) → T0 / Deterministic / LLM No /
0 tokens; tools collected from `tool_results` + evidence packet sources; never emit `T?`
when T0 is knowable. Dock cache `shell-dock-22`. Tests:
`tests/test_airix_usage_telemetry.py` (repo_search shape).

Prior: **AiriX manual provider selection is authoritative (2026-08-10)**

Root cause: (1) RouteExecutor silently substituted an alternate adapter when the
selected provider was unavailable — often `low-cost` → Hub Simulator; (2) the dock
overwrote the user's agent dropdown with the Smart Routing recommendation before
acceptance.

Fix: explicit `agent_override` / Choose Agent is authoritative; unavailable /
unauthenticated providers fail with the real error (no auto-fallback); Hub Simulator
runs only when explicitly selected or accepted via Use Recommended (low-cost);
selected + recommended + resolved provider/model and `manual_override` /
`fallback_reason` are logged and audited; dock cache `shell-dock-21`. Tests:
`tests/test_airix_manual_provider_selection.py`.

Prior: **AiriX dynamic data-query classification (2026-08-10)**

Root cause: locality abbreviations like `Brgy.` did not match geo regexes, so structured
count/indicator prompts (e.g. Baloy 2026 Q2) were classified as general knowledge and
routed to Hub Simulator (T1) instead of T0 tools.

Fix: `hub/agent_center/data_intent.py` detects structured data intent from value cues
(count/total/%/eligible/indicator/status) + admin/OU/period/UID filters — not fixed
place or beneficiary lists. `scope.py` applies data-query before simple GK; bare
`national` inside a count is admin scope, not a GK override. Classifier marks
`authoritative_data_query` → T0. Router never recommends `low-cost`/Hub Simulator for
these prompts. T0 miss → `Cannot verify from selected context` (no demo/GK substitute).
Tests: `tests/test_airix_data_query_classification.py`.

Prior: **AiriX AI usage telemetry (2026-08-10)**

Every Smart Routing execution stamps event-sourced usage telemetry
(`hub/agent_center/routing/telemetry.py`): tier, Deterministic/AI/Hybrid,
LLM Yes/No, provider, model, input/output/cached/total AI tokens, tools,
runtime, child AI run id. Pure T0 forces provider/model/run id = None and all
AI tokens = 0 (never inferred from UI labels). Persisted on
`airix_routing_events` (migration `009_airix_usage_telemetry`); shown in dock
diagnostics (`shell-dock-20`). Tests: `tests/test_airix_usage_telemetry.py`.

Prior: **AiriX dynamic scope detection + GK routing (2026-08-10)**

Root cause: grounding treated any province/region/OU phrase as project-bound whenever
a repo was selected (hard-coded topic regex), so national/general prompts were forced
into selected-context evidence and simple GK could still escalate oddly.

Fix: `hub/agent_center/scope.py` classifies each prompt as project / dhis2_data /
national_general / general_knowledge / current_web / ambiguous. Explicit broader scope
overrides the selected repo; selected repo is authoritative only for project or
ambiguous prompts. T0 answers when evidence exists; T0 miss + project → cannot-verify;
T0 miss + national/GK/web → fall through to lowest-tier model. Simple GK routes to T1
(never Codex). Evidence hits dedupe by UID. Prior findings drop on incompatible scope
change. Smart Routing still recommends Provider + Model. Tests:
`tests/test_airix_scope_routing.py`.

Prior: **AiriX grounding + dynamic Codex models (2026-08-10)**

1) Selected-context grounding: project OU/UID/DHIS2 questions use Hub tools +
selected repo evidence; no silent general-knowledge fallback; T0 first;
`Grounded: Yes/No` on results (`hub/agent_center/grounding.py`).

2) Dynamic provider models: Codex discovers models via official
`codex debug models` (+ `~/.codex/models_cache.json` fallback). Dropdown is
populated from the account catalog (Sol/Terra/Luna when listed). Never hard-codes
`__provider_default__` as the only choice when real models exist. Selected model
is passed as `codex exec --model …`; empty/default omits the flag so Codex uses
its configured default. Smart Routing recommends **Provider + Model**. UI shows
Selected/Resolved provider·model and grounding source. Cache `shell-dock-18`.
Tests: `tests/test_airix_codex_models.py`, `tests/test_airix_grounding.py`,
`tests/test_airix_model_selection.py`.

**Limitation:** Claude Code has no supported non-interactive model-catalog CLI;
Cursor discovers via `agent models`. Codex catalog depends on CLI install +
auth + `codex debug models` / models cache freshness.

Prior: **AiriX selected-context grounding (2026-08-10)**

When a repository is selected, project questions (OU / UID / DHIS2 / reports /
indicators / mappings / coverage / configuration) must be answered from Hub
tools + selected-repo evidence — never silent general-knowledge fallback.
Region III-style prompts prefer T0 (`org_unit_lookup`, UID index, repo search).
Coding CLIs without usable evidence return "Cannot verify from selected context"
with `Grounded: No`. Answers that admit lookup unavailable then invent facts are
marked `ungrounded_answer` / failed. Module: `hub/agent_center/grounding.py`.
Cache `shell-dock-17`. Tests: `tests/test_airix_grounding.py`.

Prior: **AiriX repository context for coding agents (2026-08-10)**

Codex / Claude Code / Cursor Agent require a connected repository. Resolution
priority (never blind first-of-many): explicit selection → persisted dock
selection → active workspace terminal repo → sole connected repo → else require
user selection. Dock `#ad-repo` selector; prefs `selected_repository_id` per
workspace; IDs pass through recommend / execute / manual / retry / resume;
T0/DHIS2/non-repo agents stay repo-free; access validated before run; preview
shows selected repo. Module: `hub/agent_center/repository_context.py`. Cache
`shell-dock-16`. Tests: `tests/test_airix_repository_context.py`.

Prior: **AiriX coding-CLI provider connections (2026-08-10)**

Account-backed coding agents (Codex, Claude Code, Cursor Agent): detect
installed/missing CLI, authenticated status, version, last checked; Connect /
Re-authenticate / Test / Sign out via official CLI auth only (no cookies/secrets
in Hub). Compact **AI Provider Connections** panel on Settings; full page at
`/system/ai-connections`. Smart Routing excludes providers that are not
installed+authenticated+healthy; dock keeps unavailable agents disabled.
Module notes: `hub/agent_center/connections.py`, CLI adapters. Tests:
`tests/test_airix_coding_cli_connections.py`.

Prior: **AiriX dynamic model selection fix (2026-08-10)**

UI-selected provider + model are passed end-to-end (dock → payload → AgentCenter →
adapter → API). Root cause: OpenAI `/v1/models` included legacy completion IDs
(`babbage-002`, …) that sorted first; dock defaulted to `models[0]`; Smart Routing
`start_run` omitted `model`. Fix: filter legacy completion families; dock prefers
`recommended_model` / preserves selection and reads the selector at send time;
shared `model_selection.resolve_model_for_run` validates availability (no silent
substitute); routing/execute/retry/fallback preserve or re-resolve with logged
`selected`/`resolved`/`fallback_reason`. Cache `shell-dock-13`. Tests:
`tests/test_airix_model_selection.py`.

Prior: **AiriX manual-run stuck "Running" fix (2026-08-10)**

Root cause: dock `pollRun` treated the GET `/runs/<id>` wrapper `{run: {...}}`
as the run object and only stopped on `succeeded|failed|cancelled`, while
AgentCenter finishes as `completed` — so the spinner never stopped after Choose
Agent / manual override. Fix: unwrap `data.run`, treat
`completed|failed|cancelled|paused_for_approval|timed_out` as terminal, poll the
child run id, stop spinner immediately; `skipRoutingOnce` skips recommend once
only (lifecycle polling always runs); Choose Agent runs the pending prompt in
one shot; T0 deterministic recommendations auto-execute instead of routing to
Grok. Cache `shell-dock-12`. Tests: `tests/test_airix_manual_run_lifecycle.py`.

Prior: **AiriX stuck-running lifecycle fix (2026-08-10)**

Executions always finalize to `completed | failed | cancelled | paused_for_approval |
timed_out`. RouteExecutor waits on async provider runs (timeout → timed_out);
orchestration maps parent status from child steps; Codex wait uses
`paused_for_approval` (not running); cancel finalizes step + session; stale
`active` sessions recover on status poll. Dock stops spinner on every terminal
status. Module: `hub/agent_center/routing/lifecycle.py`. Tests:
`tests/test_airix_routing_lifecycle.py`.

Prior: **AiriX Smart Routing Phase 5 (2026-08-10)**

Cost intelligence, explicit RBAC, and light semantic prior-finding retrieval on
the Phase 1–4 stack. Token budgets remain authoritative; optional USD estimates
use configured public rates only (no provider secrets). RBAC roles Viewer /
Analyst / Developer / Admin gate AI execution, providers, tools, Live, Codex
approval, and budget/settings. Finding retrieval uses keyword/alias + trigram
relevance (no embeddings). Order: capability/risk → permissions → budget →
history. Modules: `cost.py`, `rbac.py`; migration `008`. APIs add `/permissions`
and `/acl`. Tests: `tests/test_airix_routing_phase5.py`.

Prior: **AiriX Smart Routing Phase 4 (2026-08-10)**

Budgets, multi-step orchestration, specialized roles, and resumable sessions on
the Phase 1–3 stack. Hard daily/monthly/per-task token stops; orchestrated
plans (tool lookup → repo search → Grok → optional Codex with approval);
role scopes (Repository, DHIS2, SQL/Data, HCSC/Reports, UI/Playwright,
Operations); workspace/actor isolation for events/findings/sessions. New
modules: `budget.py`, `roles.py`, `orchestrate.py`; migration `007`. APIs add
`/roles` and session get. Tests: `tests/test_airix_routing_phase4.py`.

Prior: **AiriX Smart Routing Phase 3 (2026-08-10)**

History-aware routing: sanitized metrics/findings, success-rate bias, escalation
after repeated failures, explanations, analytics.

Prior: **AiriX Smart Routing Phase 2 (2026-08-10)**

Use Recommended executes via adapters; T0–T3; cancel/duplicate prevention.

Prior: **AiriX Smart Routing Phase 1 (2026-08-10)**

Classify + recommend only.

Prior: **TODAY Mission Control (2026-08-04)**

Work Notebook gains a `TODAY Mission Control` view (`?view=missions`) for
same-day missions stored as Work-scoped notebook notes (`note_type=mission`).
Fields: title, notes, priority, created/target dates, status, `completed_at`,
`reminder_status`, `carry_over`, `original_due_date` (migration `008_today_missions`).
Before 5 PM local time, unfinished TODAY missions are marked reminded on board/dashboard
load. Past-due unfinished missions move to Carry Over (red highlight) with Complete /
Reschedule. Work Dashboard shows a compact, content-height widget fed by the same
`MissionControl` service: compact completion count/ring, direct checkbox completion,
today-only quick add, and a five-row dashboard preview above the Work Queue.
Completed-all uses a subtle green success state; pending and carry-over remain blue
and red. APIs: `/api/notebook/missions*`. Tests: `tests/test_notebook_missions.py`.

Prior: **HCSC-RF National regional roll-up (2026-08-03)**

Philippines (National) no longer runs one nationwide `/api/analytics.json`.
It lists regions from the OU cache, generates each Region with the existing
HCSC–RF path (registry + adapters), caches regional reports
(`env|period|ou|indicator_version`, TTL 600s), and aggregates:
sum numerators, sum denominators, recompute % (never average %).
Progress: `GET .../national-rollup-progress`; retry failed regions:
`POST .../national-rollup-retry`. UI shows per-region status.
Tests: `tests/test_hcsc_national_rollup.py`, `tests/test_hcsc_national_export.py`.

Prior: **HCSC-RF National analytics 504 mitigation (2026-08-03)**

Live National previously failed with nginx **HTTP 504** / client **90s timeout**
on chunked nationwide dx. Regional roll-up supersedes relying on longer timeouts
alone; dx chunking remains for non-national / regional analytics calls.

Prior: **HCSC-RF National reporting and CSV export (2026-08-03)**

`/dhis2/hcsc-indicators` now exposes National and Region in one `Region / National`
selector, followed by Province, Municipality/City, and Barangay. National resolves the environment-specific
DHIS2 level-1 Philippines UID and sends that single UID through the unchanged registry
and batched analytics path; no child enumeration or national formulas were added.
National payloads show `Philippines (National)` and `National Level`. The new
`/api/dhis2/hcsc-indicators/export.csv` endpoint downloads all generated result rows
with result, numerator, denominator, source type/UID, OU, period, environment, and
last-updated timestamp. Focused coverage: `tests/test_hcsc_national_export.py` plus
the existing HCSC indicator, geographic-breakdown, and generation E2E modules.
Live metadata confirmed `DcGhhRsspFX` as `Philippines` level 1.

Prior: **Shared 48px page-header standardization (2026-08-02)**

All hub pages use `templates/partials/section_header.html`: 48px title row
(20px/semibold title + info tooltip + right badges/actions), optional separate
36px tab row, 8px gap to content. Inventory: `docs/HEADER_INVENTORY.md`.
Screenshots: `docs/screenshots/header-standard/`. Tests:
`tests/test_section_header_ui.py`.

Prior: **Central Hub Process Manager ownership upgrade (2026-08-02)**

Process Manager on `/health` now inventories all Python processes via `psutil`,
groups Central Hub-owned PIDs (labeling `app.py` as **Central Hub Server**) separately
from unrelated Python (view-only), tracks owned identities in
`data/central_hub_process/owned_processes.json` with PID/command/script/cwd/start-time
validation, and supports owner-only Stop / Restart / typed **Stop Central Hub**
(detached supervisor for self-termination). Launcher:
`python scripts/run_central_hub.py`. Tests: `tests/test_central_hub_process_manager.py`.
Screenshots: `docs/screenshots/process-manager/`.

Prior: **Compact shared section header (2026-08-02)**

All hub pages now use one shared compact header
(`templates/partials/section_header.html` + `.section-header` in `style.css`):
a 44px row with small title, optional info tooltip, optional inline section tabs,
and right-side status/access badges or compact actions. Large
breadcrumb/title/description blocks are removed. Toolbars and content start
immediately below. Top bar and sidebar are unchanged. Screenshots:
`docs/screenshots/section-header/`. Tests: `tests/test_section_header_ui.py`.

Prior: **Data Explorer server-side filtering and sorting (2026-08-02)**

The browse grid now supports three-state header sorting (ascending, descending,
reset), a typed column/operator/value filter builder, up to 20 removable AND
filters, Clear all, filtered counts, and URL-restored environment/object/page/sort/
filter/search state. Filter or sort changes reset to page 1. Object metadata exposes
only operators valid for each discovered column type; the server independently
revalidates names, types, operators, sort direction, and hidden-column policy.
Browse and export reuse the existing parameterized SELECT builder, full-result
COUNT, masking, access policy, and row caps. Explicit loading, empty, invalid-filter,
and general error states are rendered. Focused API/UI tests are in
tests/test_data_explorer.py and tests/test_data_explorer_ui.py.

Prior: **Data Explorer data-first redesign (2026-08-02)**

Redesigned /data-explorer around the existing read-only APIs: compact breadcrumb
header and status, one primary tab row, one environment/search/refresh/export
toolbar, a 280px searchable object explorer, flexible sticky-header data grid, and
a 320px dark contextual details drawer. Rows now have keyboard-accessible selection
and contextual value details; the grid has explicit loading/error/empty states,
range-aware pagination, horizontal scrolling, and selected-row highlighting. The
drawer collapses below 1280px and side panels stack below 820px. Backend query,
masking, pagination, export/job/history, permission, Stage/Live isolation, and
SELECT-only behavior are unchanged. Screenshots:
docs/screenshots/data-explorer-desktop.png and
docs/screenshots/data-explorer-reduced.png. Focused tests:
tests/test_data_explorer_ui.py, tests/test_data_explorer.py, and
tests/test_live_data_export.py (38 passed).

Prior: **Central Hub Process Manager (2026-08-02)**

Extended Repository Workspace process-control primitives into the existing `/health`
surface. Central Hub now has an atomic PID/identity lock, stale/invalid lock cleanup,
duplicate-start refusal, owner-only Stop Stale / typed Stop All / Restart Cleanly,
graceful-then-force exact-PID stopping, port-release verification, detached fixed-argv
restart, `/api/healthz` validation, new-PID status, and append-only audit. Verified
end to end: a second startup exited 2, clean restart changed PID, released port 8080,
returned one listener, and passed health. Focused tests:
`tests/test_central_hub_process_manager.py`, `tests/test_repository_processes.py`,
`tests/test_process_polling.py`, and `tests/test_perf_navigation.py`.

Prior: **Unified Data Explorer (2026-08-02)**

Merged Live Data Export into `/data-explorer` with tabs Browse Data / Schema /
Relationships / Lineage / Export / Export Jobs / History. The duplicate Work sidebar
item is removed; `/live-data-export` redirects to `?tab=export`; legacy export APIs are
compatibility aliases for the new `/api/data-explorer/exports*` and `export-jobs*`
routes. `DataExplorerService` owns the approved-source registry, shared
`ExplorerStore`, export jobs/history/presets, shared SELECT/security primitives, and
the shared file export engine. The environment-isolated SQL connection registry is
shared with SQL Workspace. Optional Stage/Live SSH forwarders start lazily from
environment-only settings, require a trusted host key, bind to a dynamic loopback
port, and stop with the application. PostgreSQL metadata enrichment is catalog-batched
rather than per-relation, reducing a 390-relation Live inventory from more than 1,500
SSH round trips to bounded read-only catalog queries. A Live tunnel, connection test,
and Data Explorer tree response were verified locally on 2026-08-02; no database write
was performed. Existing
discovery browsing and allowlisted export behavior remain SELECT-only, masked,
row-capped, and Stage/Live isolated. Database/tunnel failures are normalized to safe
JSON API errors so the browser never exposes an HTML/JSON parser failure. Focused tests: `tests/test_data_explorer.py` and
`tests/test_live_data_export.py`.

Prior: **Progress NPMO report comparison (2026-08-02)**

Read-only compare page `/dhis2/hcsc-indicators/compare/progress-npmo` for DHIS2 report
**Progress of Data Collection and Validation-(NPMO)** UID **`IKlKwg7ZS07`** vs HCSC–RF.
Structured analytics extraction (no HTML scrape/OCR). Verified mappings: eligible +
approved eligible PIs; Partial CLIENT% vs IND `StDJxe7tIiS`; other Progress columns
Unresolved/Not Comparable. Config `config/hcsc_progress_comparison.yaml`; module
`hub/hcsc_indicators/progress_compare.py`. Mockup UID `plQxuUO8XJd1` not found.
Focused tests: `tests/test_hcsc_progress_compare.py`.
UI label: **Report Comparison**. The route uses a compact **Report Output Comparison**
header and a responsive setup panel that identifies **DHIS2 Report Output** vs
**Central Hub HCSC–RF Result**; comparison semantics and endpoints are unchanged.

Prior: **Data Explorer Phase 1 (2026-08-02)**

New Work-nav module `/data-explorer` — Navicat-like **read-only** browse of configured
SQL RO connections. Discovers schemas/tables/views/matviews + columns/keys/indexes;
classifies into Linelist/Tracker/Analytics/Reporting/HCSC·RF/OU/Application/Unknown via
name patterns only (no invented Live mappings). Lineage from HCSC registry + Live Data
Export allowlist; DHIS2 Standard Reports have no DB table maps (unresolved). Live/Stage
RO were not configured at build time — inventory today is local-demo. Package
`hub/data_explorer/`, config `config/data_explorer.yaml`. Focused tests:
`tests/test_data_explorer.py`.

Prior: **Live Data Export Phase 1 (2026-08-02)**

New Work-nav module `/live-data-export` — allowlisted CSV/XLSX/csv.gz exports from
approved Live DB sources only (no arbitrary SQL/tables). Config registry
`config/live_data_exports.yaml`; package `hub/live_data_export/`. Preview → Generate;
sync under `max_rows_sync` (5000), background job otherwise; token+TTL downloads;
audit without row payloads. Verified source today: local demo household linelist.
Production candidates (household linelist, member linelist, eligible HH view, HCSC
summary, beneficiary masterlist, saved SQL) are registered but **unavailable** until
object/columns are verified. Focused tests: `tests/test_live_data_export.py`.

Prior: **HCSC–RF geographic breakdown (2026-08-02)**

Optional child-OU breakdown on the same HCSC–RF page (one Generate Report). Renamed
Disaggregation → **Population Filter** (`All Households` only). Added **Geographic
Breakdown** (None / By Region|Province|Municipality/City|Barangay) scoped strictly below
the selected OU level. Parent **Selected Area Summary** stays visible; breakdown panel
loads in a second client phase. Server batches multi-OU `GET /api/analytics.json` (chunked),
caches by env/quarter/OU/population/breakdown, dedupes in-flight, rejects invalid levels.
Large-breakdown estimate + confirm (`HCSC_BREAKDOWN_*` env thresholds). Focused tests:
`tests/test_hcsc_geographic_breakdown.py`.

Prior: **HCSC–RF report generation E2E (2026-08-02)**

Root cause (client): cascade `onLevelChange` deferred `syncSelection` until child OU
options finished loading, so the hidden OU UID / Generate enablement lagged selection.
Live report API itself was healthy (GET-only). Fix: commit UID immediately on level
change; distinguish empty successful reports; catch render exceptions. Added
`tests/test_hcsc_report_generation_e2e.py` (mocked + optional Live).

Prior: **HCSC–RF status strip copy de-dupe (2026-08-02)**

Badge and helper are distinct per generation phase (`statusTextsForPhase`); Ready no longer
repeats “Ready to generate”. Helpers carry context/elapsed/freshness; status card min-height
stable; spinner only while a request ID is active.

Prior: **HCSC–RF filter-card OU layout stability (2026-08-02)**

Cause: selecting an OU unhid `#hcsc-ou-path` and `#hcsc-ou-sync` under Selected OU,
growing that column and shifting Disaggregation / Generate / Refresh. Fix: single-line
36px Selected OU field with ellipsis + title tooltip; path/sync removed from card
(sync on refresh-metadata tooltip); `align-items: start`; metadata refresh spins the
icon in-place without changing field height.

Prior: **HCSC–RF report-generation state machine (2026-08-02)**

One authoritative client generation state machine (`idle` / `awaiting_selection` /
`ready` / `generating` / `slow` / `success_fresh` / `success_cached` / `success_stale` /
`cancelled` / `timed_out` / `error`). Animation only while a request ID is active;
terminal paths stop timers/spinners; late responses ignored; param changes mark results
stale (not loading); prior values kept under “Updating in background”; Refresh becomes
Cancel during flight; status strip tones + badges + Retry/Copy Diagnostics.

Prior: **HCSC–RF preview layout match (2026-08-02)**

Filter card matches preview: Row1 six equal fields (Env/Quarter/Region/Province/Mun/Brgy);
Row2 Search 25% / Selected 35% (bordered field with refresh+clear icons) / Disagg 15% /
Generate 15% / Refresh 10%. Deferred validation; no auto analytics; placeholder cards
with `Last refreshed: —`; status strip unchanged in behavior.

Prior: **HCSC–RF parameter card layout refine (2026-08-02)**

Two-row responsive param card; deferred OU validation; Generate gated on quarter+OU;
Refresh enabled unless report in-flight; awaiting selection does not auto-call analytics.
Status: Awaiting selection → Ready to generate → Generating report….

Prior: **HCSC–RF Generate Report form fix (2026-08-02)**

Generate was doing a native GET page navigation (`/dhis2/hcsc-indicators?...`) instead of
fetching `/api/dhis2/hcsc-indicators/report`. Fixed: Generate is `type="button"`, form has
`onsubmit="return false;"`, named fields removed so Enter cannot navigate, URL hydrate
restores controls without auto-run. Report API itself was already healthy (Live OK).

Prior: **Shell + SQL Workspace layout fix (2026-08-02)**

Fixed compact-sidebar regression: fixed sidebar must not also reserve a grid column /
`margin-left`. Desktop shell is `padding-left: var(--sidebar-w)` + full-width `.main-column`
(`flex: 1`, `min-width: 0`). Sidebar nav/actions scroll in `.sidebar-scroll`; header/switcher/
collapse stay fixed. SQL Workspace restored to library + editor grid with min widths.
Workspace Console docks under main only (`left: var(--sidebar-w)`), bounded height when expanded.

Prior: **HCSC–RF preview UI + compact sidebar (2026-07-30)**

Matched the attached HCSC–RF preview: compact filter card (two rows), status strip badges,
skeleton overview cards, category + technical tabs, toolbar/table/empty states. Left sidebar is
fixed (~216px), collapsible icon-only with remembered state, expandable DHIS2 group (expanded when
HCSC–RF is active). OU SQLite cache + 2025Q3–2026Q4 quarters unchanged; no API/registry rebuild.

Prior: **HCSC–RF OU SQLite cache + quarter cap**.

### Prior milestone

**DHIS2 Run Report parameter pickers (2026-08-02)**

Run Report Period + Organisation Unit are searchable dropdown/combobox controls (no typed free-text submit). Reuses `/api/dhis2/reports/periods` and `/api/dhis2/reports/org-units`.

Prior: **Central Hub HCSC–RF rename + classification grouping** — see below.

### Compare Sources (Phase 3)

Read-only Compare Sources workspace comparing report results to:
- same-batch analytics N/D
- local evidence snapshots
- approved SQL / capabilities marked **Comparison Source Unavailable** (no auto-execute)

API: `GET /api/dhis2/hcsc-indicators/validation`, snapshot + investigation notes POSTs. Evidence DB under `data/hcsc_validation_evidence.db` (gitignored).

### Classifications (verified; no guessing)

- **HCSC** — scorecard / eligible beneficiary counts
- **RF** — maternal / child / WASH–SBC / food-security rates
- **Unresolved** — convergent units, Pct_Convergence_Mun, Overview IP/non-IP, nutritious-food frequency, SQL lineage SoT
- No **HCSC + RF** duals invented

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dhis2_report_params tests.test_dhis2_reports_bridge -v
.\.venv\Scripts\python.exe -m unittest tests.test_hcsc_indicators -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Open Work → DHIS2 → Reports → Run Report, or `/dhis2/reports/run` (hard refresh for JS).
Open Work → DHIS2 → HCSC–RF, or `/dhis2/hcsc-indicators`.
