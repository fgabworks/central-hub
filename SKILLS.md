# SKILLS.md — Capability Status

Verified state: [AI_REFERENCE.md](AI_REFERENCE.md).

## Available

| Capability | Where |
|---|---|
| Repository registry + health | `hub/registry/`, `hub/adapters/` |
| Registry Add / Edit / Disable | `/repositories/new`, store writes YAML; no auto-clone |
| **AiriX · CLIMATE Chat** | `/work/chat` + `/personal/chat`; compact `[ AiriX ] [ Direct ]` mode; Context Scope = General (default) / All Repositories (bounded relevant hits) / specific repository; Direct = provider general knowledge with explicit attachments only; AiriX keeps CLIMATE orchestration plus hub registry facts in General; Details/reopen read persisted `climate_execution` (surface, mode, provider, exact model, scope, repo, attached/retrieved/inspected files); assistant identity is AiriX + CLIMATE mark vs selected provider logo; compact `Completed · Ns` status; Sources/Details/Token Efficiency collapsed by default; lightweight thinking/streaming/cancel/error states; no editor |
| **CLIMATE shell + Code Workspace v1** | Visible CLIMATE shell with `VANTA | ARCTIC` UI identities over compatible `work`/`personal` routes and compact scoped context labels; `/work/climate` + `/personal/climate` **AiriX · Code Assistant** IDE panel (`surface=workspace`, not the standalone Chat shell); read-only Monaco file viewer that fills the remaining editor area and scrolls long files; identity over compact `[ AiriX / Direct ] [ Provider ] [ Model ] [ Context Scope ]` (General / All Repositories / specific repo) plus explicit file chips (Add to Chat, Clear All, `@filename`); compact New session / Sessions icon actions; coding-context strip (repo / file / selection / attachments); answer-first messages with `Completed · Ns`, one collapsed Sources fold, and one collapsed Details fold (execution metadata, diagnostics, Token Efficiency nested inside Details) from persisted execution metadata; AiriX replies labeled AiriX with the icon-only CLIMATE/AiriX mark, Direct replies labeled with the selected provider icon (Gemini/Codex/Claude/Cursor); never the full CLIMATE wordmark in small avatars; composer `Ask about your code...`; server-backed conversation list/detail/rename via existing Agent Center SQLite conversations/runs with browser state as a rich local mirror (`climate:workspace:v1:`); AI answers make `path/file.py — function` traces clickable into that viewer; Markdown Source/Preview, GFM chat rendering (marked + DOMPurify), filtered Explorer with Show excluded, search, multi-tabs, Git status/diff, Problems/Output/Tests/Git, persisted/resizable layout; provider-neutral Gemini/Codex/Claude Code/Cursor Agent panel with exact model, cancel/output, bounded context, ASK logic-answer outline (prompt + conservative reorder), execution Mode (`AiriX` / `Direct`) separate from Provider/Model, domain-aware resolver hints in Assisted only (simple reference lookup first; logs/bulk dumps downranked), `Explored N` = files actually read (including Get-Content), and review-gated proposal Accept/Reject (`hub/climate/`). Gemini is API-backed, dynamically discovers accessible models, streams responses, and is ASK-only with no editing, commands, tools, or native repo scan. File save/Git write are not in this viewer. |
| Repository Intelligence | `/repositories/sections/intelligence` + `/repositories/<id>/intelligence`; nested Repositories sub-nav (General/Connection/Intelligence/Files & Changes/Settings/Logs); compact status table; manual first scan, persistent compact profile/search index, Git/instruction freshness, incremental refresh, and bounded task-relevant AiriX retrieval (grouped adapter IDs resolve config-first to the selectable local member; Inspect/Smart/Ask/Plan/Agent share one resolver; Current never reports not_learned) |
| Repository Intelligence telemetry | Deterministic scan history with zero LLM/provider/token use, file/runtime/commit metrics, disabled future Deep AI Analysis, and per-run AiriX repository/freshness/entries/context diagnostics (Tools = actual events including `repository_intelligence`; T0→AI synthesis = Hybrid with answer propagation) |
| Registry grouping | Optional `repository_group_id` — one UI row for local + API (+ future) adapters; independent Workspace / Application / API statuses (`hub/registry/grouping.py`) |
| Repository Workspace Phases 1–2 + Connect + Run Profile Builder + Processes + Active Application | `/repositories/<id>` tabs + `/connect` + Settings → Run Profiles + Run → Active Application / History / Processes; Health Local Process Monitor; `hub/repository_workspace/` |
| Central Hub Process Manager | `/health`; psutil inventory; owned registry + orphan recovery; Stop/Restart owned only; typed Stop Central Hub tree; launcher Ctrl+C cleanup; audit |
| DHIS2 Reports (Standard Report Manager Phase 1) | `/dhis2/reports` sync/view Stage+Live standard reports; `hub/dhis2_reports/`; catalog shortcuts `config/dhis2_reports.yaml` |
| Central Hub HCSC–RF (Phase 0–3) | `/dhis2/hcsc-indicators` — registry + Overview/report/category + Compare Sources; evidence packages local-only; no formula engine / no SQL auto-exec |
| `${VAR:-default}` in registry YAML | `hub/registry/loader.py` |
| Live Processing (GET-only API + path health) | `config/repositories.yaml` |
| Data-Script / Report Template (git + optional path) | `config/repositories.yaml` |
| Live dashboard (health / notebook queue / audit) | `/work` (legacy `/` redirects) |
| Personal / Work workspace switcher | `/workspace/<name>`, cookie + `hub_prefs`; CLIMATE nav is shared; Code Workspace + Repositories live under VANTA; CSS weather-galaxy sky on non-editor pages, quiet/static in Code Workspace |
| Personal Dashboard + Tasks + Quick Notepad | `/personal`, `/personal/tasks`; shared Tasks framework also at `/work/tasks` |
| **ARCTIC** (CLIMATE / Personal) | `/personal/arctic` — Profile + Document Registry (Local/Drive refs); Dashboard\|Profile\|Files; Career Pack logical view; Primary CV = latest CV; AiriX context explicit-only; Drive sync deferred (`hub/arctic/`, `data/arctic.db`) |
| Official References (Work Notebook) | `/work/notebook?view=references` — Year→Type library for memoranda/advisories/guidelines; local upload and/or external link; files under `data/work-notebook/references/{year}/`; editable Subject with bounded PDF/DOCX/TXT detection (no OCR/LLM); Quick Add autofill via `/api/notebook/references/detect-meta`; search/year/type filters (`hub/notebook/references.py`) |
| Repository Notebook (scoped personal\|work notes) | `/personal/notebook`, `/work/notebook`, `hub/notebook/` |
| SQL Workspace (read-only query library/runner) | `/sql`, `hub/sql_workspace/`, `data/sql_workspace.db` |
| Data Explorer (RO browse + lineage + allowlisted exports/jobs/history) | `/data-explorer`; legacy `/live-data-export` redirects to Export; `hub/data_explorer/`, `config/data_explorer.yaml`, `config/live_data_exports.yaml`, `data/data_explorer.db` |
| AI Assistant Center (read-only) | `/personal/aira`, `/work/airix`, dock on all pages via `templates/partials/assistant_dock_panel.html` + `static/js/assistant_dock.js`, `hub/agent_center/` (+ `routing/` Phase 5), `config/agents.yaml`, `data/agent_center.db` |
| Workspace Console | Bottom dock on all pages via `templates/partials/workspace_console_panel.html` + `static/js/workspace_console.js`, `hub/workspace_console/` — reuses repo runner / process monitor / jobs / audit |
| AI Connections | `/system/ai-connections` — CLIMATE provider cards + compact split Chat/Code Workspace defaults (`mode`/`provider`/`model`) and a one-row Provider Overrides grid; local logos; API Key/CLI method badges; Test Connection / Manage; Gemini keys stored locally via Settings APIs (`data/ai_provider_secrets.env`, not encrypted, never returned); Codex / Claude Code / Cursor remain CLI-auth; compact panel also on Settings |
| **AI Provider Settings** | `/settings/ai-providers` — Settings submenu; dynamic cards from the Agent Center provider registry plus planned Claude/Local Models cards; Add/Replace/Remove server-side API keys; Test Connection via existing adapters; never returns stored secrets; does not claim encryption |
| **CLIMATE Branding** | `/settings/branding` — two PNG/SVG/WEBP files under `data/branding/` (paths, not base64). **App Branding** = Wordmark / Full logo for sidebar, top-left header, and larger areas (`logo.*`, contain-fit only). **AiriX Chat Avatar** = separate icon-only upload (`avatar.*`, padded `object-fit: contain`) for CLIMATE Chat and Code Assistant; default `climate-mark.png` if none is uploaded; never the full wordmark. Replace/Remove per asset; live previews (header, 32/36/40, Chat, Code Assistant) update on file select. Direct Gemini/Codex/Claude/Cursor keep provider icons. Reset restores both defaults. |
| Email Center (Gmail readonly OAuth) | `/personal/email`, `/work/email`, `hub/email/`, `data/email.db` |
| Calendar Center (Calendar readonly) | `/personal/calendar`, `/work/calendar`, `hub/calendar/` |
| Google Connections | `/system/google-connections` — shared accounts + incremental scopes |
| Dashboard Notebook Work Queue | `/work` Open Tasks + queue tabs (work scope only) |
| Health probe cache + parallel checks | `hub/adapters/manager.py`, `CENTRAL_HUB_HEALTH_CACHE_TTL` |
| UID Index + Find Missing UIDs | `/dhis2/uid-index/manage`, `/dhis2/uid-index/find-missing`, `hub/dhis2/uid_mapping/` — CSV reload separate from DHIS2→index import |
| UID audit mapping profile | answer kind, program/stage, option-set choices on detail |
| Refresh UID Details (enrichment) | `/dhis2/enrichment`, `hub/dhis2/enrichment/`, `data/dhis2/enrichment.db` |
| Scan DHIS2 (discovery) + GET / UID mapping | `hub/dhis2/`, `/dhis2/*` |
| Job store (SQLite) | `hub/jobs/`, `data/hub.db` |
| Job worker (cancel/pause/resume) | `hub/jobs/worker.py` |
| Command capability execution | `hub/jobs/executor.py` + YAML templates |
| API capability execution (GET/HEAD) | `hub/jobs/executor.py` |
| Uploads + artifact download | `hub/jobs/files.py`, `/jobs/<id>` |
| Confirm gates + max concurrent | registry `defaults` |
| Owner role (optional token) | `hub/jobs/auth.py`, Settings |
| Audit JSONL | `hub/audit/` |
| Tests | `tests/test_*.py` |

## Partial

| Capability | Limitation |
|---|---|
| Live Processing jobs | GET health/history/preview only — no apply proxies |
| UID index dry-run preview | In-process memory (`DHIS2_MAPPING_PREVIEW`); lost on restart |
| Enrichment dry-run preview | In-process until confirm; run progress in SQLite; lost on process restart before apply |
| UID conflict resolve | Conflicts skipped by default; no per-UID take/keep form yet |
| Enrichment raw metadata | Not bulk-stored; live GET only when detail `?tab=raw&raw=1` |
| SQL Workspace | Implemented — SELECT/WITH/EXPLAIN only; optional trusted-host-key Stage/Live SSH tunnels; Live warning; never auto-run |
| Data Explorer | Implemented — unified RO discovery/browse/schema/relationships/lineage plus allowlisted exports, large jobs, masking, row caps, and history; prod candidates unavailable until verified; Live/Stage need their matching RO profiles; no arbitrary SQL/table input |
| AI Assistant Center | Implemented — isolated Aira/AiriX profiles; full-height persistent dock + fixed `[Mode] [Agent] [Model] [Repository] [+ Context]` composer; **AiriX Smart Routing Phase 5** plus five interaction modes: Smart (Auto + cheapest-capable T0→AI), Ask (RO Q&A), Inspect (tools-first), Plan (investigate/no writes), Agent (exact provider/model, routing bypass, no fallback); **Unified Tool Runtime Phase 2** (`hub/agent_center/tool_runtime/`) — Phase 1 iterative RO Hub tools for API adapters plus dynamic tool filtering, on-demand RI/skill recall, grounded prune, T0 continuation, session reuse, stuck recovery, cheapest-capable selection with manual override preservation, richer run telemetry, classified provider-failure handling (Smart continue on compatible API providers; manual never substitutes), and **action-based approval** (provider identity including Codex does not gate RO Send; writes/shell/side effects still policy-gated); per-workspace composer/context persistence; dynamic providers/models; scoped DHIS2 environment/RO database/Data Explorer/files/workspace/prior-finding context; existing RBAC, budgets, grounding, completion, Stage/Live and RO controls preserved; execution telemetry exposes actual mode/provider/model/T0/LLM/tokens/tools/Task Solved/Grounded (+ compact Tool steps / retries / session reuse / provider-failure fields). Coding CLIs still require repository context and stay read-only; no voice. |
| Workspace Console | Implemented — bottom VS Code-style panel (two-row title/tabs; Problems/Output/Debug/**interactive PTY Terminal**/Ports); xterm.js + ConPTY/`pty` + WebSocket; repo path jail; IDE toolbar (repo/shell/New/tabs/Split/Restart/Kill); empty state; collapsed by default; resize/minimize/maximize; per-workspace prefs (session id, not commands); lazy loads; pauses UI when hidden (PTY keeps running); Ports + process monitor ownership; controlled profile launcher retained; verified process stops only |
| Activity Rail / VS Code shell | Implemented — far-right activity rail; AI dock via topbar; Assistant Center history layout; notepad from rail (no floating pill); compact dashboard summary tiles |
| Email Center | Implemented — `gmail.readonly` only; encrypted tokens; no send/modify/mark-read; opt-in assistant metadata search forced to active workspace |
| Calendar Center | Implemented — FullCalendar grid + agenda/upcoming; readonly; sanitized descriptions; no create/RSVP/drag; opt-in assistant lookup forced to active workspace |
| Repository Notebook | Manual notes with `personal`\|`work` scope + separate Quick Notepads; profile-scoped assistant lookup is read-only |
| Quick Notepad | Floating edge tab on main Personal/Work pages (not a sidebar item); pads independent; legacy → personal |
| API writes | Blocked even if YAML `allow_write` (Phase 4 GET-only) |
| Pause | Cooperative between capability steps (short demos finish quickly) |
| Owner auth | Single shared token; not multi-user RBAC |
| DHIS2 builder apply | Disabled |

## Placeholder / Planned

- AiriX Tool Runtime Phase 3+ — MCP client, RO browser, CLI native tool loops, write tools with confirm gates, scheduler, workflow editor (explicitly deferred from Phase 2)
- DHIS2 Standard Report Manager Phase 2+ (report replacement / design write-back) — not started
- More GET-only connected-repo capability packs (via YAML only)
- Agent Center Edit/Test modes and write-capable confirm gates
- Notebook automatic repository scanning (manual notes remain)
- DHIS2 writes after full safety lifecycle
- Settings pages for Appearance, Integrations, Security, Notifications, and Advanced (visible Planned items in the Settings submenu)
