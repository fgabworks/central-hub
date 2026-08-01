# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Workspace Console — Terminal split full-width fix (2026-08-02)**

### What changed

Empty right-hand terminal pane (split UI with one session) is fixed.

| Area | Change |
|---|---|
| Default layout | Single full-width terminal (`data-split="0"`); pane B stays `[hidden]` |
| Split | Only after explicit Split → duplicate session → attach WS → then show two panes |
| Collapse | Second-pane WS/ticket failure, missing session, Unsplit, or Close pane → full width + `scheduleFit` |
| Prefs | Persist `terminal_split` only with distinct `terminal_split_session_id`; restore only if both sessions still live |
| UI | Pane titles + active border when split; WS fail overlay with Reconnect / Close pane |
| Resize | `ResizeObserver` + double `requestAnimationFrame` fit after split/collapse |

### Files

- `static/js/wc_terminal.js`, `static/js/workspace_console.js`
- `templates/partials/workspace_console_panel.html`, `static/css/style.css`
- `hub/workspace_console/prefs.py`
- `tests/test_wc_terminal.py`, `tests/test_workspace_console.py`

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_wc_terminal tests.test_workspace_console -v
```

1. Open Terminal → one session fills width (no empty column)
2. Split → second session appears; Unsplit / Close pane → full width again
3. Hard-refresh with only one live session → no empty split restored

---

## Previous milestone

**DHIS2 Reports — Detail UI params + performance (2026-08-02)**

### What changed

Standard report detail (e.g. BARANGAY CONSOLIDATION SCORECARD) no longer uses the oversized Metadata panel + raw UID/period text fields.

| Area | Change |
|---|---|
| Summary | Compact two-column card (name, env, type, DHIS2 version, parameter summary, last sync) |
| Diagnostics | Collapsed: UID, raw flags, relative periods, data source, URLs, cache |
| Period | Searchable quarter dropdown (`2026Q2` id / `2026 Q2` label) via `hub/dhis2_reports/periods.py` |
| Org unit | Search (name/code/UID) + lazy child tree via existing `search_org_units` + `ORG_UNIT_CACHE` |
| Discovery | `hub/dhis2_reports/discovery.py` — reportParams + relativePeriods + design markers; incomplete → optional + warning |
| Viewer | Primary **Generate & View** on detail; Refresh / Full screen / Print / Download / Open in DHIS2 |
| Perf | Pooled client/env; longer RESULT/OU TTL; METADATA/PERIOD caches; timings; AbortController cancel; inflight dedupe |

### Reused services

- `ReportService.search_org_units` / `GET /api/dhis2/reports/org-units` (+ `parent_id`)
- `validate_period` / `period_to_dhis2_date` / `RESULT_CACHE` / pooled `_client(env)`
- `POST /api/dhis2/reports/generate-and-view` (same as Run Report)
- New (shared, not Live Processing duplicate): `periods.py`, `discovery.py`, `GET /api/dhis2/reports/periods`

### Parameter discovery source

Merged: `reportParams` flags → `relativePeriods` → HTML `designContent` heuristics. If none: optional period/OU + warning (Generate not blocked).

### Loading targets

- Cached HTML: RESULT_CACHE TTL 120s (was 45s); timings exposed in diagnostics
- Cached period/OU controls: PERIOD_CACHE 600s, ORG_UNIT_CACHE 180s, METADATA_CACHE 180s
- Uncached Stage `data.html` can still be slow (timeout/fallback to designContent remains)

### Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dhis2_report_params tests.test_dhis2_standard_reports tests.test_dhis2_reports_bridge -v
```

1. Restart hub → Library → open scorecard detail  
2. Compact summary + collapsed Diagnostics  
3. Quarter dropdown + OU search/tree  
4. Generate & View → iframe; second run should be cache-fast  

### Remaining slow operations

- First Stage `data.html` for heavy Jasper/HTML reports (network-bound; design fallback if 400/timeout)
- Root OU list without query still hits DHIS2 once per cache miss
- Full-screen print depends on iframe same-origin sandbox
