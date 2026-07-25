# SKILLS.md — Capability Status

Verified state: [AI_REFERENCE.md](AI_REFERENCE.md).

## Available

| Capability | Where |
|---|---|
| Repository registry + health | `hub/registry/`, `hub/adapters/` |
| Registry Add / Edit / Disable | `/repositories/new`, store writes YAML; no auto-clone |
| `${VAR:-default}` in registry YAML | `hub/registry/loader.py` |
| Live Processing (GET-only API + path health) | `config/repositories.yaml` |
| Data-Script / Report Template (git + optional path) | `config/repositories.yaml` |
| Live dashboard (health / notebook queue / audit) | `/work` (legacy `/` redirects) |
| Personal / Work workspace switcher | `/workspace/<name>`, cookie + `hub_prefs` |
| Personal Dashboard + Tasks + Quick Notepad | `/personal`, `/personal/tasks` |
| Repository Notebook (scoped personal\|work notes) | `/personal/notebook`, `/work/notebook`, `hub/notebook/` |
| SQL Workspace (read-only query library/runner) | `/sql`, `hub/sql_workspace/`, `data/sql_workspace.db` |
| Dashboard Notebook Work Queue | `/work` Open Tasks + queue tabs (work scope only) |
| Health probe cache + parallel checks | `hub/adapters/manager.py`, `CENTRAL_HUB_HEALTH_CACHE_TTL` |
| UID index controlled update (LP-style) | `/dhis2/uid-index/manage`, `hub/dhis2/uid_mapping/admin.py` |
| UID audit mapping profile | answer kind, program/stage, option-set choices on detail |
| DHIS2 metadata enrichment + relationship audit | `/dhis2/enrichment`, `hub/dhis2/enrichment/`, `data/dhis2/enrichment.db` |
| Enrichment explorer tabs/filters | overview / configuration / relationships / option set / PI / sources / history / raw |
| DHIS2 GET / discovery / UID mapping / preview builder | `hub/dhis2/`, `/dhis2/*` |
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
| SQL Workspace | Implemented — SELECT/WITH/EXPLAIN only; Live warning; never auto-run |
| Repository Notebook | Manual notes with `personal`\|`work` scope + Quick Notepad under Personal — no agent assist |
| Dashboard Quick Notepad | Same scratchpad; Personal Dashboard + Personal Notebook only |
| API writes | Blocked even if YAML `allow_write` (Phase 4 GET-only) |
| Pause | Cooperative between capability steps (short demos finish quickly) |
| Owner auth | Single shared token; not multi-user RBAC |
| DHIS2 builder apply | Disabled |

## Placeholder / Planned

- More GET-only connected-repo capability packs (via YAML only)
- Notebook agent integration / automatic repository scanning
- DHIS2 writes after full safety lifecycle
- CSRF for browser POSTs if exposed beyond localhost
