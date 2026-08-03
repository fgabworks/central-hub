# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**HCSC-RF National analytics 504 mitigation (2026-08-03)**

Live National `2026Q2` / `DcGhhRsspFX` failed after ~10 minutes with nginx
**HTTP 504** on one large `/api/analytics.json` (all HCSC dx UIDs). Hub now:
chunks national dx (default 6 via `HCSC_ANALYTICS_DX_CHUNK_NATIONAL`), uses a
per-chunk timeout (`HCSC_ANALYTICS_CHUNK_TIMEOUT_SECONDS`, default 90), retries
504 in `Dhis2Client`, and keeps national browser abort disabled. Focused tests:
`tests/test_hcsc_national_export.py`.

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
