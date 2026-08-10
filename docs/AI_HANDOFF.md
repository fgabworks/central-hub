# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Inspect explanation synthesis answer propagation (2026-08-10)**

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
