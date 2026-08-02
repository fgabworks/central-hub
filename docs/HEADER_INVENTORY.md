# Header inventory — Central Hub shared section header

Audit date: 2026-08-02. All listed pages use `templates/partials/section_header.html`
(`section-header-shell` + 48px `section-header` + optional 36px `section-header-tabs`).

| Page | Template | Title | Info tooltip | Actions / badges (`sh_meta`) | Tabs (`sh_tabs`) |
|---|---|---|---|---|---|
| Personal / Work Dashboard | `dashboard.html` | `page_title` | `page_sub` | Last updated + refresh | — |
| Notebook | `notebook.html` | `page_title` | `page_sub` | + New Note | — |
| Personal Tasks | `personal_tasks.html` | Personal Tasks | yes | + New Task | — |
| Assistant Center (Aira/Okarun) | `agent_center.html` | Assistant Center | profile subtitle | Read-only lock + open dock | — |
| Email Center | `email/center.html` | Email Center | yes | — | — |
| Email thread/message | `email/thread.html`, `message.html` | Thread / subject | yes | back links | — |
| Calendar | `calendar/center.html`, `event.html` | title / event | yes | back link on event | — |
| Repositories | `repositories.html` | Repositories | yes | count, Check health, + Add | — |
| Repository form | `repository_form.html` | `title` | `subtitle` | — | — |
| Repository workspace | `repository_detail.html`, `repository_workspace_*.html` | repo name | optional | type + status badges | Repository tabs (36px row) |
| SQL Workspace | `sql_workspace.html` | SQL Workspace | yes | — | — |
| Data Explorer | `data_explorer.html` | `page_title` | `subtitle` | Dev / Read-only | Workspace tabs |
| Jobs / Job detail | `jobs.html`, `job_detail.html` | Jobs / capability | yes | — | — |
| Health | `health.html` | Health | yes | — | — |
| DHIS2 Overview | `dhis2_overview.html` | DHIS2 Overview | yes | status badge + Run discovery | — |
| DHIS2 Lookup / instance / catalog / authorities / UID / enrichment / builder / detail | `dhis2*.html` | page-specific | yes | status / actions | — |
| DHIS2 Reports | `dhis2_reports_*.html` | page title / report name | yes | Active instance badge | Report workspace tabs |
| HCSC–RF | `hcsc_indicator_summary.html` | `page_title` | subtitle | Active + GET-only + Overview link | — |
| Progress Compare | `hcsc_progress_compare.html` | `page_title` | subtitle | DHIS2 Standard Report badge | Compare tabs; filters below |
| AI Connections | `ai_connections.html` | AI Connections | yes | — | — |
| Google Connections | `google_connections.html` | Google Connections | yes | — | — |
| Audit | `audit.html` | Audit | yes | recent count | — |
| Settings | `settings.html` | Settings | yes | Read-only badge | — |

## Spec compliance

- Title row: 48px; title 20px / weight 600 / line-height 24px
- Tabs: separate 36px row under title when present
- Descriptions: info tooltip only
- Content/toolbar spacing: 8px below shell (`section-header-shell` margin-bottom)
- Obsolete `.page-header`, `.page-header-compact`, `.ac-page-header`, `.hcsc-page-header`, `.pnc-header` / `.pnc-crumb` removed or unused
