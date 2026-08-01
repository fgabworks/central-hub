# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**DHIS2 Reports — Authenticated Report Rendering Bridge (2026-08-02)**

### Report source discovered

| Source | What it is | Hub action |
|---|---|---|
| **Native Standard Reports** | Individual entries from `GET /api/reports` | Render via `GET /api/reports/{uid}/data.html` with `.env` credentials |
| **DHIS2 Reports / Pivot app shells** | `/dhis-web-reports/index.html`, visualizer | Browser-only — **not** individual reports |
| Repository / static HTML | YAML catalog | Existing generate/view paths |

The previous Run tab treated the Reports **application shell** as a report; Generate could not show populated HTML. Run now lists synced native reports first.

### Endpoints / routes

| Route | Purpose |
|---|---|
| `GET /dhis2/reports/run` | Generate & View UI |
| `POST /api/dhis2/reports/generate-and-view` | Validate → render → viewer URL |
| `GET /dhis2/reports/standard/<env>/<uid>/rendered` | Credentialed HTML iframe body |
| `GET /dhis2/reports/proxy/<env>?path=` | Allowlisted asset/API proxy (SSRF-safe) |
| `GET /api/dhis2/reports/run-catalog` | Native + shell + other catalog |
| `GET /api/dhis2/reports/org-units` | Searchable org-unit picker |

Credentials never reach the browser. Live still requires one confirm per run.

### Remaining limitations

- Custom apps beyond `/api/reports` are not fully reverse-engineered; app shells stay browser-only
- Jasper JDBC reports may still need Open in DHIS2
- Relative assets outside allowlisted prefixes are not proxied
- No HTML edit / replacement / upload / DB compare (deferred)

## Verify

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_dhis2_reports_bridge tests.test_dhis2_standard_reports tests.test_dhis2_reports -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

1. Sync Stage reports from Library
2. Run Report → pick a **Native Standard Report** (not “Reports app shell”)
3. Set period + org unit → Generate & View → HTML appears in iframe
4. Confirm Diagnostics is collapsed; Live confirm once per run

## Do not implement unless asked

DHIS2 writes · report replacement · design upload · version restore · DB comparison
