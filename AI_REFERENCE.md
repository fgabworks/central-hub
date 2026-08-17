# AI_REFERENCE.md — Verified Current State

Last verified: 2026-08-17 (VANTA-scoped Code Workspace/Repositories + shared CLIMATE shell).
Canonical agent rules: [AGENTS.md](AGENTS.md). Handoff: [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).

## Status

**AiriX · CLIMATE Chat** is a standalone top-level Ask-only chat at `/work/chat`
(VANTA) and `/personal/chat` (ARCTIC). It reuses Agent Center conversations/runs,
the Gemini adapter, exact model discovery/selection, streaming, cancellation, and
`display_prompt` separation. It does not scan or upload a repository. Coding
diagnostics stay in the Workspace Assistant.

**CLIMATE Code Workspace v1** (Workspace Assistant) remains at `/work/climate` (VANTA) and
`/personal/climate` (ARCTIC). It reuses the guarded Repository Workspace file/Git
services and existing Agent Center CLI adapters/runner. The IDE shell provides a
read-only Monaco file viewer that fills the remaining editor area and scrolls long files (textarea fallback); AI answers link repository path/function traces into that viewer, Explorer/search, multiple tabs,
Markdown Source | Preview, persisted per-workspace/repository tabs/layout, Git
status/diff, resizable panels, and Problems | Output | Debug Console | Terminal |
Ports (Tests and Git remain secondary). Opening a file does not write it; the
viewer does not expose save. AI chat renders GFM Markdown (marked + DOMPurify +
highlight.js) with compact DHIS2 UID chips and does not change provider text. Problems shows real editor/runtime
diagnostics; Output uses channelled CLIMATE
runtime logs without raw provider protocol; Debug Console shows hub-managed run-profile
stdout/stderr or “No active debug session”; Terminal reuses the repository-scoped PTY;
Ports is read-only local listener discovery. Its provider-neutral coding adapter exposes Codex,
Claude Code, and Cursor Agent availability/model discovery/exact selection/cancel/result
without owning credentials or provider argv. AI runs remain read-only; replacement edits
are parsed as proposals and require Accept/Reject with base-content conflict checks.
Codex remains VANTA-only under its existing profile policy; ARCTIC surfaces that state
explicitly and can use authenticated Claude Code or Cursor Agent with selected ARCTIC files.
Codex capacity in the AI usage chrome comes from authenticated `codex app-server`
(`account/rateLimits/read` + `account/rateLimits/updated`), not from session token
estimates; unavailable/non–ChatGPT auth shows `Codex limit unavailable`.
AI execution **Mode** is orchestration, separate from Provider and Model:
`climate_assisted` (CLIMATE Assisted) or `direct` (Direct Provider). There is no Smart
mode and no provider-specific Direct Codex mode. Assisted preserves the current Context
Resolver flow (compact repo hints/packet; native provider investigation still allowed).
Direct sends the raw user prompt plus the selected repo/cwd and normal provider/project
instructions — no Context Resolver packet and no CLIMATE candidate-source/evidence hints.
Direct bypasses CLIMATE retrieval assistance only; ASK read-only sandbox, approved repo
boundary, controlled EDIT proposals, cancel, diagnostics, token accounting, and
Git/terminal protections stay in force. Mode persists per workspace/repo prefs and per
conversation. The CLIMATE AI panel is visibly identified as **AiriX · CLIMATE Chat**
while provider/model controls remain explicit. Browser sessions reconcile with the
existing SQLite `agent_conversations` and `agent_runs` store through scoped CLIMATE
conversation list/detail/rename APIs. Completed server runs can therefore be restored
when browser storage is unavailable without creating a second chat database; local UI
state remains a fast mirror for in-progress rendering. Gemini is the first API-backed
CLIMATE Chat provider: models are discovered from Google's Models API, the selected exact
model is required (no silent fallback), and Gemini receives the user prompt plus only
explicitly selected file bodies and/or the bounded Context Resolver packet. Responses
stream into the existing Agent Center run lifecycle. Gemini supports ASK only;
it has no file-write, command, SQL, email, calendar, agent, or native repository-exploration
capability. Configure it with an environment-only `GEMINI_API_KEY` or `GOOGLE_API_KEY`
(`GOOGLE_API_KEY` takes precedence), then optionally constrain models with
`GEMINI_ALLOWED_MODELS`. Keys can also be set, replaced, or removed from
**Settings → AI Providers** (`/settings/ai-providers`); stored values stay on the
server and are never returned to the browser. No new edit or command capability was added.
CLIMATE coding runs in Assisted mode use a deterministic zero-token Context Resolver
(AGENTS/SKILLS/provider/nested instructions + RI/local search). Implementation questions
rank executable files/symbols above docs/tests and expand locally once when evidence is
weak. For Codex, a valid VANTA repository is the evidence boundary: ASK may independently
search/read/trace the approved cwd under `--sandbox read-only` even when local confidence
is low. Packed prompts distinguish empty Hub tools from native Codex repository
inspection; they must not tell Codex that read-only tools are unavailable. Completed
CLIMATE chrome reports `Explored N files` from provider investigation/read activity
only — including PowerShell `Get-Content` — while search-hit paths stay in search
matches and preflight source candidates stay in Sources. Chat **End-to-end runtime**
is the browser wall clock; Token Efficiency **Provider runtime** is the provider
`started_at`/`finished_at` span. The resolver sends compact
instruction/skill/path+symbol hints, not duplicated
source bodies. Packet-only providers retain the evidence gate; calls without enough authoritative
evidence remain local (`Not enough repository evidence. Model not invoked · 0 tokens`).
Completed Codex runs can show a compact Token Efficiency card. The comparison process is
manual only (`Compare with Direct` after an Assisted run, `Compare with CLIMATE` after a
Direct run); normal prompts never spawn a second `codex exec`. The opposite-mode side is a
fresh ephemeral read-only process using the recorded prompt/repo/commit/provider/model and
does not resume the original provider session. Assisted originals do not send the Context
Resolver packet to the Direct comparison; Direct originals build an Assisted packet only
for that comparison. Fair comparison requires the same repo, commit, provider, model/config,
prompt, read-only mode, and fresh-session condition. Preflight token counts remain local
estimates; CLIMATE and Direct totals are provider-reported when present.
Historical runs recorded without a commit SHA cannot be measured against the
current tree. Token Efficiency labels Candidate Sources separately from files
the provider actually inspected. A resumed CLIMATE Codex session is not a
same-context comparison against fresh Direct `--ephemeral`; the Context Resolver
packet is a few hundred estimated tokens, not hundreds of thousands.
ASK questions about implementation, indicator, scoring, eligibility, or threshold
logic get a presentation-only outline (core rule → table → example → edge cases →
roll-up → exact files/functions → one-line summary). Investigation, scoring,
citations, and provider execution are unchanged; unrelated prompts keep normal
prose. A conservative post-process only reorders recognizable sections and does
not invent tables, thresholds, or a one-line summary.
Context Resolver search prefers exact phrases, UIDs, acronyms, aliases, filenames,
and hierarchy/reference/index hits over generic tokens such as `region` or `child`.
`lookup/logs/**`, bulk-apply results, dry-run exports, generated artifacts, and huge
JSON/CSV reference dumps are not normal ASK evidence unless the question is about
logs, history, or those files. Simple factual/reference questions use a lightweight
filename/index lookup first and send a small hint packet; Codex still investigates
the approved repository independently when local hints are weak. Generic searches
return bounded excerpts (line/character/file/timeout caps) and do not dump entire
huge records into provider context. Search diagnostics prefer paths, counts, matched
symbols, and redacted snippets — not unrelated names, emails, phones, or usernames.
`Explored N files` counts files whose contents were opened/read, not rg hit paths or
resolver candidates. Search-matched files and candidate sources are labeled separately.

VANTA and ARCTIC repository/run/proposal scopes are server-isolated; repositories tagged
`personal`/`arctic` belong only to ARCTIC and all others default to VANTA. AiriX Tool
Runtime and ECLIPSE are unchanged.

The visible application shell is CLIMATE. Its switcher presents only VANTA and
ARCTIC while retaining the existing `work` and `personal` route/storage identities.
Code Workspace context labels use `VANTA / DOH / <Repository>` or
`ARCTIC / <Personal Context>` without adding Work/Personal workspace subtitles.
VANTA tools are a workspace-specific submenu (Code Workspace, Repositories,
SQL, Data Explorer, DHIS2, Workspace Assistant, Email, Calendar). ARCTIC tools are Personal
Files, Aira, Email, and Calendar.
Shared CLIMATE items (Dashboard, Chat, Tasks, Notebook, Settings) stay visible
in both workspaces. Code Workspace and Repositories remain implemented at
`/work/climate`, `/personal/climate`, and `/repositories` but are hidden from
ARCTIC navigation. Code Workspace reuses
that navigation instead of rendering a second activity rail. Explorer trees hide generated/cache/temp
directories by default and can reveal them explicitly without exposing `.git` or
blocked secret files.

**AiriX Unified Tool Runtime Phase 2** — same `hub/agent_center/tool_runtime/` package
as Phase 1 (no parallel runtime). Adds dynamic scored tool selection (intent /
context / RI / mode), on-demand `repository_intelligence` + `skill_recall`,
grounded-fact-preserving observation prune, T0→runtime continuation without
rebuilding unchanged context, provider session reuse (`previous_response_id` +
fingerprint), soft stuck recovery before hard stop, cheapest-capable synthesis
selection with exact manual override preservation, and richer per-run telemetry
(steps, tool calls, context chars/tokens, RI entries, session reused, retries,
provider/model, AI tokens, runtime, task solved, grounded). Phase 1 registry,
unified RO executor, iterative API adapter loop, RBAC, Stage/Live, timeout/cancel,
audit, budgets, and completion/grounding stop conditions remain authoritative.
Provider failures are classified (quota/auth hard; rate_limit/timeout bounded retry);
Smart/Auto may continue the same execution on another compatible Tool Runtime API
provider while preserving context; manual selection never silently substitutes.
Approval belongs to the action/tool policy — provider identity (including Codex)
does not require interactive approval for RO execution. MCP / browser / shell /
writes / CLI native loops deferred.

Inspect/Ask/Plan/Smart/Agent share one repository-context resolver (explicit → persisted
dock selection → active workspace). Grouped API/local selections resolve through configured
`repository_group_id` to the one selectable local member before RI lookup. AiriX retrieves
bounded Repository Intelligence before T0/AI execution; Current profiles never report
`not_learned`. Files/context sources add search tools and context items without disabling RI.
T0 emits a real `repository_intelligence` tool event. Parent orchestration preserves the
terminal execution context, and diagnostics derive RI from that attached context.

For explanation contracts, grounded deterministic evidence that is insufficient for prose
completion escalates to the cheapest available appropriate LLM with only the bounded evidence
packet and retrieved RI entries. The child terminal answer propagates to the parent
(Hybrid / `T0 → provider/model`); empty child content is `synthesis_failed`. Tasks whose
completion contract is satisfied by T0 do not escalate. Evidence/Task Solved/Grounded are
Yes when the synthesized explanation is supported by the bounded T0/RI evidence.

Repository Intelligence UI lives under Repositories nested navigation
(`/repositories/sections/intelligence` + `/repositories/<id>/intelligence`):
compact status table, per-repo detail, and the same manual scan / persistent compact
profiles / searchable per-file summaries / indexed Git commit / incremental refresh /
bounded AiriX retrieval backend. The full index is never prompt-packed; runtime
database and DHIS2 evidence remains authoritative.

Every standard scan/refresh now persists deterministic telemetry: no LLM, provider, or
model; zero AI tokens; files scanned/indexed/changed; runtime; and indexed commit. The
disabled `Deep AI Analysis` control is future-only. AiriX run diagnostics report whether
Repository Intelligence was used, commit freshness, entries used, and contributed context
size alongside the existing token, Task Solved, and Grounded fields.

**Phases 1–6 MVP + connected Live Processing + DHIS2 enrichment + Repository Notebook
+ Personal/Work workspace switcher + registry Add/Edit/Disable + SQL Workspace (read-only)
+ Email Center (Gmail readonly) + Calendar Center (Calendar readonly, shared Google accounts)
+ AI Assistant Center (read-only Aira/AiriX profiles, including OpenAI Responses API)
+ persistent VS Code-style assistant dock across pages
+ navigation performance (async secondary panels; cached AI connection status)
+ Repository Workspace Phases 1–2 + Connect Local Workspace
+ DHIS2 Reports — Standard Report Manager Phase 1 (sync/view) + catalog shortcuts.**
Hub coordinates repos via registry/adapters; DHIS2 stays GET-only; jobs run
allowlisted capabilities only; Gmail is `gmail.readonly`; Calendar is
`calendar.calendarlist.readonly` + `calendar.events.readonly` only.
Agent Center invokes external CLIs with allowlisted argv only (`shell=False`),
or the OpenAI Responses API with read-only function tools when enabled.

| Area | State |
|---|---|
| Registry + health | `config/repositories.yaml`, `${VAR:-default}` expansion, `hub/adapters/` |
| Registry grouping | Optional `repository_group_id` merges adapters into one UI row (`hub/registry/grouping.py`); Workspace / Application / API statuses independent |
| Registry management | Add / Edit / Enable / Disable via UI → YAML (`hub/registry/store.py`); no auto-clone |
| CLIMATE Chat | `/work/chat` + `/personal/chat`; ChatGPT-like AiriX Ask-only UI over the existing Agent Center/Gemini stack; no editor, no repository upload |
| CLIMATE Code Workspace | `/work/climate` (VANTA) + `/personal/climate` (ARCTIC); Workspace Assistant / Monaco IDE shell over existing safe file/Git and authenticated coding-provider services; exact provider/model, cancel/output, proposal diff Accept/Reject; no unrestricted shell |
| Repository Workspace | Phases 1–2 + Connect + Run Profile Builder + Active Application + Repository Intelligence: General / Connection / Repository Intelligence / Files & Changes / Settings / Logs & History; process vs HTTP health reconciled (`hub/repository_workspace/run_status.py`); YAML templates + SQLite repo profiles |
| DHIS2 Reports | `/dhis2/reports` — Phase 1 Standard Report Manager: sync Stage/Live `/api/reports` metadata cache, filters, View / Open in DHIS2 / HTML source / Download / Refresh; period+OU controls; iframe embed with Open-in-DHIS2 fallback. Catalog shortcuts remain for repository/static HTML (`hub/dhis2_reports/`) |
| Central Hub HCSC–RF | `/dhis2/hcsc-indicators` — Phase 0–3 registry + batched Overview/report/category + Compare Sources (`hub/hcsc_indicators/`); quarters **2025Q3–2026Q4**; National (DHIS2 level-1 `Philippines`) plus Region → Province → Municipality/City → Barangay via env-isolated SQLite cache + DHIS2 GET refresh; National passes the root UID through the unchanged batched analytics/registry path without child enumeration; generated reports have server-side CSV download with result/N/D/source/scope/timestamp lineage; optional **Geographic Breakdown** remains batched below the selected level; Population Filter = All Households; no formula engine |
| Report Output Comparison | `/dhis2/hcsc-indicators/compare/progress-npmo` — Progress NPMO DHIS2 report `IKlKwg7ZS07` vs Central Hub HCSC–RF via structured analytics; compact comparison setup UI (`hub/hcsc_indicators/progress_compare.py`, `config/hcsc_progress_comparison.yaml`) |
| Health probes | Parallel checks; states: Healthy / Unreachable / Not Cloned / Disabled; owner-gated Central Hub Process Manager |
| Live Processing | `live-processing` (API GET-only) + `live-processing-local` (path + git_url) |
| Data-Script / Report Template | Registered with GitHub URLs; local path optional (`DATA_SCRIPT_PATH`, `REPORT_TEMPLATE_PATH`) |
| Workspaces | VANTA / ARCTIC switcher over one CLIMATE shell (`work`/`personal` storage); shared Dashboard, Chat, Tasks, Notebook, Settings; VANTA-only Code Workspace, Repositories, SQL/DHIS2/Data Explorer; ARCTIC-only Personal Files |
| Personal Dashboard | `/personal` — personal tasks/notes + upcoming calendar + floating Quick Notepad |
| Work Dashboard | `/work` (legacy `/` redirects here by remembered workspace) — repos, work queue, DHIS2 |
| Repository Notebook | Scoped notes (`personal` \| `work`); work keeps repo links; personal needs none |
| Email Center | Shared Gmail service; accounts assigned Personal/Work; readonly OAuth |
| Calendar Center | Shared Calendar service + FullCalendar grid (month/week/day) + agenda/upcoming |
| Google Connections | System page to connect/assign/enable Gmail+Calendar scopes |
| SQL Workspace | Read-only query library/runner (`/sql`); sqlglot allowlist; optional trusted-host-key Stage/Live SSH tunnels; Live warning; layout `minmax(260px,320px) | 1fr` under shell |
| Data Explorer | `/data-explorer` — unified RO schema/data/relationship/lineage browser plus allowlisted CSV/XLSX/csv.gz exports, large jobs, presets, history, masking, and audit. `/live-data-export` redirects to `?tab=export`; one runtime service/store/export engine with shared SELECT/security primitives; no ad-hoc SQL or arbitrary table input; Stage/Live remain isolated |
| AI Assistant Center | Aira at `/personal/aira`; AiriX at `/work/airix` (legacy `/work/okarun` redirects); full-height right dock + fixed composer (`hub/agent_center/dock.py`); **Smart Routing Phase 5** + **Routing Mode** Smart vs Direct Agent (`hub/agent_center/routing/` — cost intelligence, RBAC, relevance findings, budgets, orchestration; `/api/assistants/airix/routing/*`); Find/Ask/Plan/Review; Codex CLI, Claude Code, Cursor, Grok, OpenAI |
| Workspace Console | Bottom panel under main content only (`left: var(--sidebar-w)`); bounded height; Ctrl+J; collapsed by default |
| Activity Rail | Far-right icons for AI Assistant, Quick Notepad, Workspace Console (future utilities placeholders); reduces main width only |
| App shell | Fixed sidebar 210–216px + `padding-left` on `.app-shell`; `.main-column` / `.content` `flex:1; min-width:0`; `.sidebar-scroll` for nav |
| AI Connections | `/system/ai-connections`; CLIMATE settings cards for Gemini/Codex/Claude Code/Cursor Agent from the coding registry; Installed/Authenticated/Version/Last Checked + connect/test/refresh/disconnect; coding defaults below cards; Codex never stores tokens |
| AI Provider Settings | `/settings/ai-providers`; Settings submenu + registry-driven cards for Gemini, Grok/xAI, OpenAI, Claude/Anthropic (planned adapter), Local Models (UI-ready); Add/Replace/Remove key + Test Connection; secrets in gitignored `data/ai_provider_secrets.env`; APIs return metadata only; CLI providers stay on AI Connections |
| DHIS2 | GET client, discovery, UID mapping, preview builder |
| UID index admin | LP-style controlled update: dry-run → preview → typed confirm → archive/versions/restore |
| Metadata enrichment | Read-only DHIS2 enrich → local SQLite relationships + audit statuses |
| Explorer | Prefer enrichment snapshot when present; tabs + filters; lazy raw metadata |
| Jobs (Phase 2) | SQLite `data/hub.db`, submit/list/get, worker, logs |
| Command exec (Phase 3) | YAML `command_template`, `shell=False`, cwd jail |
| API exec (Phase 4) | GET/HEAD only from YAML `http_path` |
| Files (Phase 5) | Uploads/results under `data/{uploads,results}/{job_id}/` |
| Safeguards (Phase 6) | Dry-run default, confirm for apply, max concurrent, owner token |
| Tests | `tests/` — includes `test_perf_navigation.py`, `test_ai_assistant_center.py`, `test_openai_catalog.py`, `test_openai_agent.py`, `test_agent_center.py` |
| DHIS2 writes | **Disabled** |
| Gmail writes | **Disabled** (no send/reply/delete/label/mark-read) |
| Calendar writes | **Disabled** (no create/update/delete/RSVP) |

## Connected repositories (active registry)

| id | Role |
|---|---|
| `live-processing` | API — GET health/history/preview · group `pmnp-live-processing` |
| `live-processing-local` | Local checkout of same GitHub repo (`LIVE_PROCESSING_PATH`) · same group |
| `data-script` | Git URL `PMNP-IS/Data-Script` — Not Cloned until path set |
| `report-template` | Git URL `PMNP-IS/REPORT_TEMPLATE` — Not Cloned until path set |

Demo `sample-*` entries removed from the active registry; job tests use
`tests/fixtures/repositories.yaml`.

## Repository Notebook + workspaces

| Route | Purpose |
|---|---|
| `/` | Redirects to remembered Personal or Work dashboard |
| `/workspace/<personal\|work>` | Switch workspace (cookie + `hub_prefs`) |
| `/personal` | Personal Dashboard + floating Quick Notepad (no sidebar entry) |
| `/personal/notebook` | Personal notes/tasks (no repository required) |
| `/personal/tasks` | Personal open tasks list + floating Quick Notepad |
| `/work` | Work Dashboard (repos, work queue, DHIS2) |
| `/work/notebook` | Work notes with repository links |
| `/notebook` | Compat: GET redirects by note scope / workspace; POST handled in scope |
| `/notebook/<id>/export` | Download note JSON |
| `/api/notebook/preview` | Markdown → HTML preview |
| `/api/notebook/notepad*` | Quick Notepad GET/PUT/clear/convert/restore (`?scope=`) |

Store: `data/notebook.db` (`hub/notebook/`) migrations include `pinned`, `quick_notepad`,
`scope` (`personal`\|`work`) + `hub_prefs`, separate Quick Notepads (`personal` / `work`),
`panel_size` (`normal`\|`expanded`\|`maximized`) for the shared floating drawer, Official
References (`009` + subject columns in `010`), and TODAY missions. Work Notebook
`?view=references` is the Official References library (Year→Type; optional Subject via
bounded TXT/PDF/DOCX extract — no OCR/LLM). Existing notes migrate to **work**. Existing
Quick Notepad content migrates to the **personal** pad; work starts empty. Convert → note
uses the same scope as the pad. Work Dashboard queue shows work-scoped notes only.
Assistant context never preloads Notebook content; selected lookup tools search the
active profile scope.

Work Dashboard also shows TODAY Mission Control as a compact, content-height panel.
It renders up to five mission rows as a dashboard preview. Rows expose
priority/status badges and direct completion; quick add targets today. The Work
Queue stays directly below the widget in the left dashboard column. The notebook
mission model, reminders, carry-over rules, and notebook synchronization remain
shared and unchanged.

## AI Assistant Center (read-only MVP)

| Route | Purpose |
|---|---|
| Persistent dock | Aira/AiriX full-height panel on all pages; prefs `/api/assistant-dock/prefs`; lazy agents; composer fixed at bottom |
| `/personal/aira` | Personal UI; no repository/SQL/DHIS2/jobs/logs/Audit access |
| `/work/airix` | Work UI (AiriX); selected repositories and Work read-only services; Smart Routing Phase 5 |
| `/api/assistants/airix/routing/*` | Smart Routing recommend / execute / cancel / status / settings / providers / analytics / roles / permissions / acl / sessions (legacy `okarun` slug accepted) |
| `/api/assistants/<profile>/agents` | Profile-bound adapter availability |
| `/api/assistants/<profile>/agents/<id>/models` | Dynamic adapter model list |
| `/api/assistants/<profile>/context/preview` | Included/excluded sources and secret-safe context |
| `/api/assistants/<profile>/runs` | Start run / isolated history |
| `/api/assistants/<profile>/runs/<id>` | Profile-bound status, stream, files, tools, usage |
| `/api/assistants/<profile>/runs/<id>/cancel` | Cooperative cancel |
| `/api/assistants/<profile>/runs/<id>/retry` | Retry in the same scoped conversation |
| `/api/assistants/<profile>/prompts` | Isolated saved prompt library |

Implementation: `hub/agent_center/` (incl. `dock.py`, `routing/lifecycle.py`), `config/agents.yaml`, SQLite `data/agent_center.db`,
`templates/partials/assistant_dock_panel.html`, `static/js/assistant_dock.js` (`shell-dock-23`).
Coding CLIs (Codex / Claude Code / Cursor Agent) resolve repository context via
`hub/agent_center/repository_context.py` (explicit → persisted dock selection →
active workspace terminal repo → sole connected; never first-of-many).
Selected-context grounding (`hub/agent_center/grounding.py` + `scope.py` + `data_intent.py`
+ `completion.py` + `capability.py`) classifies prompt scope (project / dhis2 / national / GK / web /
ambiguous) and detects structured data queries from value intent + admin/OU/period/UID
filters (abbreviations like `Brgy.` included). Each prompt also gets a dynamic completion
contract (intent → required output); Evidence Found / Task Solved / Grounded are separate —
discovery alone is not completion; Grounded=Yes only with authoritative evidence.
After T0 unsolved, capability resolution tries connected RO SQL (saved queries + filters)
before AI; escalate only when AI can materially help; otherwise Cannot verify.
Explicit broader scope overrides the selected repo; ambiguous +
selected repo stays project-bound. Authoritative data questions prefer T0 tools and never
route to Hub Simulator; T0 miss → cannot-verify (no demo/GK substitute). Project T0 miss →
cannot-verify; national/GK/web T0 miss (non-data) → lowest-tier model. Evidence deduped by UID.
Results expose Evidence Found / Task Solved / Grounded Yes/No + Sources used.
Manual provider selection (`agent_override` / Choose Agent) is authoritative: never silently
swap Codex/Claude/Cursor/Grok to Hub Simulator; unavailable providers fail with the real
error; Smart Routing recommendations require Use Recommended acceptance. Executions log
selected/recommended/resolved provider+model, `manual_override`, and `fallback_reason`.
**Interaction Mode** is a single policy layer over the existing router: **Smart** owns
provider/model/tier and keeps cheapest-capable T0 → AI escalation; **Ask** is read-only
Q&A; **Inspect** is deterministic/tools-first; **Plan** investigates and uses the existing
read-only provider plan mode; **Agent** skips T0/recommendations/auto-escalation and runs
the selected provider+model exactly (no silent swap; Simulator only when explicitly
selected). Composer state (mode/provider/model/repository/context) persists per workspace.
First-class context sources are DHIS2 environment, RO database/Data Explorer, relevant
files, workspace, and prior findings. They add only scoped read-only tools/files; whole-repo
packing remains off. A selected DHIS2 Stage/Live environment is forced server-side in Hub
tools. Provider sessions/context fingerprints are reused when supplied and supported.
VANTA maps each CLIMATE chat to its Agent Center conversation; Codex persists the first
same-provider exec session and resumes it by explicit session UUID only for the same
provider, model, repository scope, and immediately preceding conversation run.
Cross-provider handoff resets CLI continuation and remains a compact summary.
Every Smart Routing execution records event-sourced AI usage telemetry
(`hub/agent_center/routing/telemetry.py`): tier, Deterministic/AI/Hybrid, LLM Yes/No,
provider/model, tokens (actual when provider-reported, else marked estimate), tools,
runtime, child AI run id, T0 failure reason, next capability, DB query attempted, AI escalate,
plus actual interaction mode / session reused / context items when present. Public execution
summaries always include resolved provider/model, T0/LLM use, tokens, tools, Task Solved,
and Grounded (unknown values remain explicit rather than inferred).
Pure T0 forces zero AI tokens and null provider/model/run id.
Codex models are discovered via `codex debug models` / CLI models cache
(`hub/agent_center/codex_models.py`); Smart Routing recommends Provider + Model;
selected model reaches `codex exec --model`.
Dock polls unwrap `{run: ...}` and stop on `completed|failed|cancelled|paused_for_approval|timed_out`;
T0 lookups auto-execute; Choose Agent is a one-shot manual override (skip recommend once only).
Selected provider/model are validated and passed through (`hub/agent_center/model_selection.py`);
legacy completion IDs are never used as silent defaults.
Modes: Find / Ask / Plan / Review. Edit / Test labeled **Not yet available**.
Adapters: Hub Simulator (demo), **OpenAI API** and **Grok/xAI** Responses APIs,
plus Claude Code / Cursor Agent / Codex CLIs. Provider accounts are managed at
`/system/ai-connections`; Aira and AiriX are profiles, never providers.
OpenAI and Grok models come from the provider model-list endpoint; Codex MVP uses the
authenticated Codex default model only (`__provider_default__`, no discovery yet) via
read-only JSONL `codex exec` runs at the approved repo cwd. Windows discovery prefers PATH,
then `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`, and requires sibling
`codex-code-mode-host.exe` (incomplete `.sandbox-bin` is skipped).
Non-conversation runs stay
ephemeral; same-conversation VANTA runs use official explicit `codex exec resume <UUID>`
continuation while retaining `--sandbox read-only`. Cursor uses
`agent models`. Claude Code currently exposes only its provider default because its
supported non-interactive CLI has no model-list command.
Inaccessible models are never shown. Optional `OPENAI_ALLOWED_MODELS` can further restrict
the live list; cache TTL and Pro timeout settings remain available.

Connection persistence stores only Hub disconnect/check metadata. CLI credentials stay in
provider-managed storage; API keys stay in server environment variables. Audit events contain
provider ID, operation, and outcome only.
Reasoning-effort selector only for models that support it.
Read-only tools: repository search/read, scoped Notebook/Quick Notepad, SQL-library
lookup, DHIS2 UID metadata, scoped Email/Calendar search, jobs, and redacted Audit.
Schemas are filtered by profile and user selection. Repository instructions load only
for AiriX's selected repositories. No repositories, emails, documents, or old messages
are bulk-loaded. Never packs `.env`, credentials, token paths, binaries, or oversized
files. Output is untrusted. No SQL/shell/repository execution or external writes.

## Repository Workspace runs

YAML templates live in `config/run_profiles.yaml` (`REPO_WS_RUN_PROFILES`).
Repository-specific profiles (Settings → Run Profiles) are stored in SQLite
(`REPO_WS_PROFILE_DATABASE` / `data/repository_workspace.db`) and override templates
by profile id without rewriting YAML. Connect suggestions are saved untrusted/
disabled until approved in the builder.

Executable + argv arrays only; placeholders `{port}`, `{repository_path}`, `{environment}`.
Port modes: `none` | `fixed` | `argument` | `environment_variable`. Fixed ports block
startup when occupied (never auto-kill). Env values stay server-side (UI shows names).
**Repository Processes** (Run tab): detects hub-tracked and related local PIDs (cwd /
command path / entry point / profile port — never name-only). Stop Gracefully / Force
Stop only a verified PID tree; Medium external requires typed `STOP PROCESS <PID>`;
Low is view-only. Start blocks on conflicts / occupied fixed ports and points users
to Repository Processes (no silent fixed-port switching). Health → Local Process
Monitor is a read-only cross-repo summary.

**Central Hub Process Manager** extends the same verified-PID, port, graceful-stop,
and audit patterns on `/health`. `data/central_hub_process/instance.lock.json` is an
atomic PID/identity registry; `owned_processes.json` tracks owned PIDs with
PID/command/script/cwd/start-time ownership tokens and reconciles against live
`psutil` inventory on scan/startup. Controls are owner-only: per-process Stop/Restart
(owned only), Stop Stale Instances, typed **Stop Central Hub** (complete owned tree),
typed Stop All Central Hub Instances, and Restart Cleanly. Self-stop/restart uses a
detached fixed-argv supervisor. Launcher: `python scripts/run_central_hub.py` (Ctrl+C /
terminal-close cleanup; orphans remain stoppable in Process Manager). Generic /
unrelated Python processes are visible but never stoppable.
UI: `/repositories/<id>/settings#run-profiles`, `/run`, `/logs`. State/logs under
`data/repository_runs/`. Live / write-capable live profiles require
`REPO_WS_ALLOW_LIVE_RUNS` + confirm. No unrestricted terminal; stop/restart only
hub-tracked process groups.

## Email Center (Gmail readonly)

Shared implementation in `hub/email/` (one service for Personal and Work). Accounts are
assigned to a workspace; UI routes are `/personal/email` and `/work/email`.

| Route | Purpose |
|---|---|
| `/personal/email` · `/work/email` | Mailbox list (inbox/unread/starred/sent), search, labels, pagination |
| `/email` | Redirect by remembered workspace |
| `/email/oauth/start` · `/email/oauth/callback` | OAuth 2.0 web-server flow (`gmail.readonly`) |
| `/email/accounts/<id>/assign` | Assign account to Personal or Work |
| `/email/accounts/<id>/disconnect` | Local token wipe + Google revoke |
| `/email/accounts/<id>/refresh` | Invalidate limited local cache; reload from Gmail |
| `/email/accounts/<id>/messages/<id>` | Message detail |
| `/email/accounts/<id>/threads/<id>` | Thread detail |
| `/email/.../attachments/<id>` | Attachment download (validated against message metadata) |
| POST convert-note / convert-task / link-repo | Create Notebook note/task (work can link a registry repo) |

Store: `data/email.db`. Refresh tokens encrypted at rest (Fernet derived from
`CENTRAL_HUB_SECRET_KEY`). Client id/secret via `GMAIL_*` in `.env` (see `.env.example`).
List rows use Gmail’s `UNREAD` label for unread styling (bold sender/subject/timestamp,
dot + badge, left accent) vs muted read rows; hover / selected / focus-visible stay
distinct. Opening a message does **not** mark it read (`gmail.readonly` only).
No push notifications; limited TTL cache + manual refresh. **No automatic agent access
to email content.** Passwords never stored; tokens never rendered in UI/logs.
OAuth supports **incremental scopes** (`include_granted_scopes=true`) so Calendar can be
added to an existing Gmail account without dropping mail access.

## Calendar Center (Google Calendar readonly)

Shared `hub/calendar/` service reuses the same Google accounts / encrypted tokens /
Personal|Work assignment from Email Center. Scopes (incremental):
`calendar.calendarlist.readonly` + `calendar.events.readonly`.

| Route | Purpose |
|---|---|
| `/personal/calendar` | Personal Calendar — FullCalendar month/week/day grid + agenda/upcoming |
| `/work/calendar` | Work Calendar (same shared service + grid) |
| `/calendar` | Redirect by remembered workspace |
| `/api/calendar/accounts/<id>/events` | JSON feed for FullCalendar (reuses `CalendarService` cache) |
| `/api/calendar/.../events/<id>` | JSON event detail (sanitized description) for read-only drawer |
| `/system/google-connections` | Connect, assign workspace, enable Gmail/Calendar scopes, disconnect |
| `/email/oauth/calendar/start` | Incremental Calendar OAuth start |
| `/calendar/.../events/<id>` | HTML event detail (attendees, location, sanitized description, Meet) |
| POST convert-note / convert-task / link-repo | Notebook actions (repo link Work-only) |

UI: Today / Prev / Next + date-range title; all-day band; today highlight; colors by
source calendar; click event → **right-side read-only drawer** (sticky header/footer,
scrollable sections, sanitized description); search + calendar + timezone filters.
Default timezone is the browser/account zone (selector preserved). Small screens default
to Agenda; drawer goes full-width. Create/edit/delete/drag/resize/RSVP remain disabled.

Personal Dashboard shows **Upcoming Personal Events**. Limited local cache + manual
refresh; no push; no create/update/delete/RSVP; **no automatic agent access**.

## SQL Workspace

| Route | Purpose |
|---|---|
| `/sql` | Query library + editor + results (Save / Format / Explain / Run) |
| `/api/sql/run` | Validate + execute one read-only statement |
| `/api/sql/queries` | Create/update saved queries (versions; never auto-run) |
| `/api/sql/connections/<id>/test` | Server-side connection probe |
| `/api/sql/runs/<id>/csv` | Export run results CSV |
| `/api/sql/runs/<id>/cancel` | Cooperative cancel |

Local store: `data/sql_workspace.db`. Connections: `config/sql_connections.yaml` + env secrets (`.env.example`).
Safety: sqlglot AST validation (not regex-only); SELECT / read-only WITH / EXPLAIN only; one statement; RO transaction + statement timeout + row cap; credentials never in UI/logs; Live connections show a strong warning.

Stage and Live profiles can opt into automatic SSH forwarding through
`ssh_tunnel_env_prefix` in `config/sql_connections.yaml` and matching
`<PREFIX>_SSH_*` environment settings. Forwarders start lazily on loopback with a
dynamic local port, require a trusted/pinned SSH host key, remain isolated per
environment, and are shared by SQL Workspace and Data Explorer.

## Data Explorer

| Route | Purpose |
|---|---|
| `/data-explorer` | Browse Data / Schema / Relationships / Lineage / Export / Export Jobs / History |
| `/live-data-export` | Compatibility redirect to `/data-explorer?tab=export` |
| `/api/data-explorer/tree` | Cached schema tree |
| `/api/data-explorer/browse` | Paginated SELECT from discovered objects only |
| `/api/data-explorer/inventory` | Grouped source inventory |
| `/api/data-explorer/export` | CSV/XLSX/csv.gz with sensitivity policy |
| `/api/data-explorer/exports/preview` | Allowlisted source count + masked sample |
| `/api/data-explorer/exports` | Allowlisted sync/background export |
| `/api/data-explorer/export-jobs*` | Jobs, cancellation, and token+TTL download |
| `/api/data-explorer/export-history` | Export audit history without row payloads |

Connection/discovery failures return redacted JSON errors; raw PostgreSQL connection
strings and Flask HTML error pages are not exposed to the Data Explorer client.

The /data-explorer UI is data-first: compact header and primary tabs, one control
toolbar, a 280px searchable explorer, a horizontally scrollable sticky-header grid,
and a 320px dark metadata/selected-row drawer. The drawer becomes an overlay below
1280px and the explorer/grid stack below 820px. Loading, error, empty, selected-row,
and range-aware pagination states are explicit. These are presentation-only changes;
the existing APIs, permission checks, query builder, export engine, jobs, audit,
masking, row limits, and Stage/Live isolation remain authoritative.

Browse filtering and sorting operate on the full database result, never only the
loaded page. The UI supports up to 20 AND filters with removable chips and typed
operators derived from discovered column metadata. The server revalidates every
column/operator pair, rejects hidden columns and invalid sort directions, binds
values as query parameters, and runs the filtered COUNT before paginated SELECT.
Environment, selected object, page, filters, quick search, sort column, and sort
direction are restored from the URL; filter/sort changes reset to page 1.

Config/policies: `config/data_explorer.yaml` and the approved-source registry
`config/live_data_exports.yaml`. Data Explorer owns one `ExplorerStore` at
`data/data_explorer.db` for browse audit, favorites, jobs, presets, and export history;
artifacts are under `data/data_explorer_exports/`. The legacy API family remains as a
compatibility alias. SQL Workspace remains the place for approved ad hoc queries.

## Connected Live Processing

Env (see `.env.example`): `LIVE_PROCESSING_BASE_URL`, `LIVE_PROCESSING_PATH`.

| Repo id | Type | Hub may do |
|---|---|---|
| `live-processing` | API | Health + GET `healthz`, `bulk_apply_history`, `bulk_preview` jobs |
| `live-processing-local` | command | Path presence health only (no domain commands) |

No LP apply/write proxies. No import of LP Python packages for business logic.

## Next

**AiriX Unified Tool Runtime Phase 2 is implemented.** See [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md).
Phase 3+ (MCP, browser, scheduler, shell/`run_command`, write tools, workflow editor,
CLI native tool loops) stays deferred.

**Interactive repository terminal is implemented** (PTY + WebSocket + xterm.js).
Windows ConPTY lone LF is normalized to CRLF so PowerShell prompts stay on their own
line. CLIMATE never fits/resizes a hidden terminal (that spawned PowerShell at ~8–20
columns and glued the next prompt onto `echo one`). The Terminal toolbar is a compact
VS Code-like row. See [SECURITY.md](SECURITY.md).

Next development target: **DHIS2 Standard Reports** (credentialed HTML viewer / library polish).
DHIS2 Standard Report Manager Phase 2+ (replacement / design write-back) is **not** started.
Optional: more GET-only LP capabilities via YAML; enrichment Phase A completeness.
Repository Workspace Phase 3+ (commit/push/pull UI and autonomous/unreviewed agent edits)
stays deferred. CLIMATE file viewing is read-only; save/Git write UI is not exposed.
AI replacement edits still require review-gated Accept/Reject.
Do **not** enable DHIS2 writes without [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
Do **not** expand Gmail or Calendar beyond readonly without an explicit safety design.
