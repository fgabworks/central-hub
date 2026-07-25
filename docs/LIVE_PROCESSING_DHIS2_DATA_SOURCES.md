# LIVE_PROCESSING_DHIS2_DATA_SOURCES.md

**Status:** research only (2026-07-25). Findings document — **no application code was
changed** for this investigation.

**Repos inspected (read-only):**

| Tree | Paths |
|---|---|
| Live Processing | `C:\PMNP\pmnp-live-processing` — `_support/_env_config.py`, `_support/_postgres_env_connection_impl.py`, `lookup/lookup_dhis2_api.py`, `lookup/app_lookup.py`, `lookup/uid_mapping_registry.py`, `lookup/lookup_sql_uids.py`, `lookup/lookup_household_member.py`, `lookup/linelist_loader.py`, `AI_REFERENCE/AI_UID_INDEX.csv`, `AI_REFERENCE/update_ai_uid_index/update_ai_uid_index.py`, `AI_REFERENCE/reference-json/pi-sql-mapping.json`, `AI_REFERENCE/sql/*` |
| Central Hub | `hub/dhis2/enrichment/*`, `hub/dhis2/client.py`, `hub/dhis2/uid_mapping/*`, `config/uid_mapping_sources.yaml`, `app.py` (explorer/detail/enrichment routes), related templates |

**Safety applied:** no Live Processing or Central Hub code changes; no `.env` values,
credentials, URLs, tokens, or SSH keys displayed; no DHIS2 or database writes; no live
production benchmarks.

**Recommendation key:**

| Tag | Meaning |
|---|---|
| **COPY** | Reuse the *idea* / small structural shape; reimplement in hub (do not paste LP files) |
| **ADAPT** | Same idea, redesigned for hub GET-first multi-repo safety |
| **AVOID** | Product-specific, unsafe for hub ownership, or technical debt |

---

## 1. Connection architecture

### 1.1 Live Processing

```
.env (+ optional .env.runtime)
        │
        ▼
_support/_env_config.py
  resolve_runtime_environment / load_environment_profile
  prefixes: LIVE_* | STAGE_*
        │
        ├──────────────────────┐
        ▼                      ▼
lookup/lookup_dhis2_api.py   _support/_postgres_env_connection_impl.py
  Dhis2Config                SSH tunnel (optional) → Postgres engine
  Dhis2Lookup                SQLAlchemy + psycopg2
  requests.Session + Basic   pool_pre_ping, recycle, statement_timeout
        │                      │
        └──────────┬───────────┘
                   ▼
         lookup/app_lookup.py
         modes: auto | api | ssh_readonly
         API-first reads; SQL fallback when payloads look incomplete
         Writes: API only (blocked in ssh_readonly / unverified)
```

| Concern | LP implementation | Hub today | Rec |
|---|---|---|---|
| Profiled env | `LIVE_` / `STAGE_` prefixes; workflow overrides | Single `DHIS2_*` profile | **ADAPT** multi-profile later |
| API auth | `requests.Session` + Basic Auth | Same (GET-only Session) | **COPY** session reuse |
| API version | Unversioned `/api/...` | Unversioned `/api/...` | **COPY** |
| Postgres | Optional SSH tunnel + engine | None | **AVOID** in hub core |
| Modes | `auto` / `api` / `ssh_readonly` | Structural GET-only | **ADAPT** fail-closed; **AVOID** SSH mode names |

### 1.2 Central Hub (current)

- Config: `hub/settings.py` + `.env` (`DHIS2_*` names only; values never documented here).
- Client: `hub/dhis2/client.py` — GET-only Session, probe/operation timeouts, retries on
  429/502/503, hard `max_pages` ceilings, field filters.
- Enrichment: `hub/dhis2/enrichment/` — bulk GET → local SQLite `data/dhis2/enrichment.db`.
- Repository UID list: `config/uid_mapping_sources.yaml` → scan LP CSV into
  `data/dhis2/uid_index/` (hub-local JSON). **Never writes LP’s CSV.**
- **No** SSH, Postgres, or DHIS2 write methods.

---

## 2. Data-source inventory

For every major source: file, function/class, type, data, endpoints/tables, joins,
environment/cache, hub recommendation.

### 2.1 Configuration and connectivity

| # | Source file | Function / class | Type | Data retrieved | Endpoints / tables | Relationship logic | Env / cache | Hub |
|---|---|---|---|---|---|---|---|---|
| 1 | `_support/_env_config.py` | `load_environment_profile`, `resolve_runtime_environment`, `read_prefixed_env_values` | registry | Prefixed DHIS2 + Postgres + SSH knobs (names only) | — | LIVE vs STAGE prefix | Workflow env can force profile | **ADAPT** |
| 2 | `lookup/lookup_dhis2_api.py` | `load_dhis2_config`, `Dhis2Config`, `Dhis2Lookup` | API | Authenticated Session; TEI/events; stage DE sets; OU names | `/api/system/info`, `/api/tracker/*`, `/api/programStages/{id}`, `/api/organisationUnits/{id}` | Stage→DE list for **event completeness**, not DE registry graph | In-memory `_ou_names`, `_stage_names`, `_stage_data_elements`; ~30s GET timeout | **ADAPT** read client |
| 3 | `_support/_postgres_env_connection_impl.py` | `start_ssh_tunnel`, `create_postgres_engine`, `managed_postgres_engine` | SQL | DB connectivity | Postgres via optional SSH | — | `pool_pre_ping`, recycle 1800s, statement/lock timeouts, connect attempt budget | **AVOID** hub core; **ADAPT** only if a future capability explicitly needs DB |

### 2.2 UID catalogs and registries

| # | Source file | Function / class | Type | Data retrieved | Endpoints / tables | Relationship logic | Env / cache | Hub |
|---|---|---|---|---|---|---|---|---|
| 4 | `AI_REFERENCE/AI_UID_INDEX.csv` | (data file) | CSV | Canonical UID catalog: kind, id, name, code, domainType, valueType, aggregationType, program, expression, filter, … | Exported from `/api/{dataElements\|programIndicators\|indicators\|trackedEntityAttributes}.json` | PI `program` string from export; **no** stage/dataset/optionSet columns in export fields | `dhis2_environment` column | **AVOID** as hub SoT; **ADAPT** as **UID list input only** (already via `uid_mapping_sources.yaml`) |
| 5 | `AI_REFERENCE/update_ai_uid_index/update_ai_uid_index.py` | `export_from_dhis2`, `fetch_metadata_endpoint`, `OBJECT_TYPE_FIELDS` | API→CSV | Offline index refresh | Paged metadata collections (`pageSize=500`) | Formats PI `program[id,name]` → `"uid - name"` | stage/live export modes | **ADAPT** paging + field lists; **AVOID** writing LP CSV from hub |
| 6 | `lookup/lookup_sql_uids.py` | `load_index`, field tuples | CSV + registry | Index load; SQL-surface UID lists | — | alias↔UID for linelist/SQL | Process load | **AVOID** field lists |
| 7 | `lookup/uid_mapping_registry.py` | `build_registry`, `_resolve_stage`, `_resolve_program`, `apply_dhis2_overlay`, `apply_db_scan_overlay` | registry + derived | Explorer enrichment view for **PMNP-relevant** UIDs only | Analytics table name prefixes; `information_schema` confirm | **Hardcoded** DE→stage/program; indicator link graph | Process-memory overlays; preserve on failure | **AVOID** PMNP maps; **ADAPT** overlay architecture |
| 8 | `lookup/lookup_household_member.py` | program/stage/TEA/DE constants + SQL fetchers | registry + SQL | Hardcoded program/stage UIDs; instance reads | `trackedentityinstance`, TEA tables, `programinstance`, `programstageinstance.eventdatavalues` (JSONB) | Hardcoded membership | — | **AVOID** |
| 9 | `lookup/linelist_loader.py` | `HH_DE_TO_COLUMN`, patch DE sets, loaders | registry + SQL | DE→column; stage patches | `_pmnp_linelist_*` year tables + PSI overlay | DE membership → stage patch | Year-resolved table names | **AVOID** |
| 10 | `AI_REFERENCE/reference-json/pi-sql-mapping.json` | (static) | registry | PI UID → precomputed analytics SQL | Analytics-style SQL strings | Stage/DE already inlined in SQL | — | **AVOID** as logic |
| 11 | `AI_REFERENCE/sql/rev_hh_linelist.sql`, `rev_member_linelist.sql` | (SQL files) | SQL | Linelist definitions | Custom views/tables | Product schema | — | **AVOID** |

### 2.3 Live overlay and runtime completeness

| # | Source file | Function / class | Type | Data retrieved | Endpoints / tables | Relationship logic | Env / cache | Hub |
|---|---|---|---|---|---|---|---|---|
| 12 | `lookup/app_lookup.py` | `api_uid_mapping_explorer_refresh_dhis2_metadata` | API | Live DE/TEA name/types + `optionSet[id,name]`; known programs/stages | `dataElements.json`, `trackedEntityAttributes.json`, `programs.json`, `programStages.json` with `filter=id:in:[…]` chunks of 80, `paging=false` | Does **not** rebuild DE→stage | Preserves previous overlay on failure | **ADAPT** chunked overlay |
| 13 | `lookup/app_lookup.py` | connection modes + completeness gates | derived | API-first / SQL fallback for profiles & events | Tracker API + SQL readers | `STAGE_DE_MIN_COUNTS` + live stage DE counts | Mode in process state | **ADAPT** mode idea; **AVOID** PMNP thresholds |
| 14 | `lookup/app_lookup.py` | `api_uid_mapping_explorer_scan_database_sources` | SQL | Column presence for known maps | `information_schema.columns` / tables | Confirms known maps only — does not invent | Read-only | **ADAPT** idea only if hub ever has DB |

### 2.4 What Live Processing does **not** provide

| Capability | LP status |
|---|---|
| DE → Data Set | **Not implemented** (no `/api/dataSets` enrichment) |
| Runtime PI expression UID extraction | **Not implemented** (expressions stored in CSV; static `pi-sql-mapping.json` is not a parser) |
| Full option list on overlay | **Missing** (`optionSet[id,name]` only; no `options[...]`) |
| General DE → Program Stage from metadata API | **Not used for registry**; hardcoded `_resolve_stage` instead |
| “Answer type” as a first-class field | **No** — display uses normalized `valueType` + hardcoded option codes |

---

## 3. API endpoint inventory

### 3.1 Live Processing (runtime + export)

| Endpoint | Used by | Fields / notes | Purpose |
|---|---|---|---|
| `/api/system/info` | `Dhis2Lookup.ping` | version / health | Connectivity |
| `/api/tracker/trackedEntities` | `Dhis2Lookup` | TEI search/profile | Runtime reads |
| `/api/tracker/events` | `Dhis2Lookup` | Event DE values | Runtime reads |
| `/api/tracker` (POST) | Import helpers | Writes | **AVOID** for hub |
| `/api/programStages/{uid}` | `_stage_data_element_uids` | `programStageDataElements[dataElement[id]]` | Completeness / sparse-event detection |
| `/api/organisationUnits/{uid}` | OU name cache | name | Display |
| `/api/dataElements.json` | Export + explorer overlay | Export: domain/value/aggregation; Overlay: + `optionSet[id,name]` | Index / overlay |
| `/api/trackedEntityAttributes.json` | Export + overlay | valueType (+ optionSet on overlay) | Index / overlay |
| `/api/programIndicators.json` | Export only | `program[id,name],expression,filter,aggregationType` | CSV index |
| `/api/indicators.json` | Export only | numerator/denominator | CSV index |
| `/api/programs.json` | Overlay (known UIDs) | id,name | Name refresh |
| `/api/programStages.json` | Overlay (known UIDs) | id,name,program | Name refresh |
| `/api/dataSets*` | — | **Not used** for enrichment | Gap |
| `/api/optionSets*` | Documented in capture refs; **not** used by overlay for options list | — | Gap |

### 3.2 Central Hub enrichment (current)

| Endpoint | Used by | Fields | Caps |
|---|---|---|---|
| `/api/programStages` (collection) | `_build_stage_graph` | `id,name,program[id,name],programStageDataElements[dataElement[id]]` | `page_size=100`, **`max_pages=20`** |
| `/api/dataSets` (collection) | `_build_dataset_graph` | `id,name,dataSetElements[dataElement[id]]` | `page_size=100`, **`max_pages=10`** |
| `/api/programs` (collection) | TEA→program graph | `programTrackedEntityAttributes[…]` | `page_size=100`, `max_pages=10` |
| `/api/dataElements` | bulk `id:in` | `_DE_FIELDS` (incl. optionSet, categoryCombo, groups, dataSetElements) | batches of 60 |
| `/api/trackedEntityAttributes` | bulk `id:in` | `_TEA_FIELDS` | batches of 60 |
| `/api/programIndicators` | bulk `id:in` | `_PI_FIELDS` (expression, filter, program) | batches of 60 |
| `/api/optionSets` | bulk `id:in` | options with sortOrder/style | batches of 60 |
| Per-UID reverse filters | `uid_mapping/reverse_trace.py` | DE→stages/datasets, TEA→programs | Smaller page ceilings |

Hub does **not** call Tracker write endpoints or Postgres.

---

## 4. Database query and table inventory

Live Processing SQL is for **instance/linelist data and schema confirmation**, not for a
general metadata relationship graph.

| Surface | How used | File(s) | Hub |
|---|---|---|---|
| `trackedentityinstance` | Profiles, search seeds | `lookup_household_member.py` | **AVOID** |
| `trackedentityattribute` / `trackedentityattributevalue` | TEA values | same | **AVOID** |
| `organisationunit` | Hierarchy / OU scope | same | **AVOID** |
| `programinstance` | Event joins | same | **AVOID** |
| `programstageinstance.eventdatavalues` (JSONB) | Event DE values; `jsonb` key access / `jsonb_each` | same + `linelist_loader` | **AVOID** |
| `_pmnp_linelist_hh_{year}` / `_pmnp_linelist_hh_member_{year}` | Fast bulk path | `linelist_loader.py` | **AVOID** |
| `analytics_event_*` (hardcoded prefixes) | Flat DE-UID columns; registry fallback | `uid_mapping_registry.py` | **AVOID** |
| `information_schema.columns` / `tables` | Confirm known maps / linelist support | `app_lookup` explorer scan | **ADAPT** idea only |
| SQL files under `AI_REFERENCE/sql/` | Linelist definitions | product SQL | **AVOID** |

**Central Hub:** SQLite only (`enrichment.db`, `hub.db`). No DHIS2 Postgres access.

---

## 5. UID and relationship resolution flow

### 5.1 Live Processing (how operators see mappings)

```
AI_UID_INDEX.csv  ──load_index──►  base metadata (name, valueType, domainType, PI program/expression)
        │
        ▼
uid_mapping_registry.build_registry()
  scope = union of SQL field lists, linelist maps, TEA/DE constants, convergence UIDs
  (NOT a full dump of every CSV row for explorer “relevant” set)
        │
        ├─ _resolve_stage(uid)     ← HARDCODED membership tests → one PMNP stage
        ├─ _resolve_program(uid)   ← HARDCODED; Aggregate → Not mapped
        ├─ _resolve_db_mapping()   ← analytics/linelist column hints
        │
        ├─ optional apply_dhis2_overlay()
        │     GET dataElements/TEAs (optionSet id+name only)
        │     GET known programs/stages (names)
        │     does NOT rewrite DE→stage
        │
        └─ optional apply_db_scan_overlay()
              information_schema confirms known columns only
```

| Relationship | LP resolution | Source type |
|---|---|---|
| DE → Program Stage | Hardcoded maps (`_resolve_stage`) | registry |
| DE → Program | Hardcoded (`_resolve_program`) | registry |
| DE → Data Set | **Absent** | — |
| PI → Program | CSV export `program[id,name]` | API→CSV |
| PI expression → DE/stage UIDs | **Not parsed at runtime** | CSV stores raw text; static SQL map elsewhere |
| Option Set name | Overlay `optionSet[id,name]` | API |
| Ordered options | **Absent** in overlay | — |
| valueType / domainType | CSV + overlay | API→CSV / API |
| aggregationType | CSV export only (not overlay fields) | API→CSV |
| Answer type | Derived informally from valueType + hardcoded option codes | derived |

Live API `programStages/{uid}` DE lists are for **completeness thresholds**, not for
building the explorer’s DE→stage column.

### 5.2 Central Hub enrichment (intended flow)

```
uid_mapping_sources.yaml → scan LP CSV → data/dhis2/uid_index/latest.json
        │
        ▼
EnrichmentWorkflow.start_fetch
  EnrichmentFetcher.fetch_all(repo records)
        │
        ├─ reverse walk programStages (capped) → de_to_stages (many stages OK)
        ├─ reverse walk dataSets (capped) → de_to_datasets
        ├─ reverse walk programs → TEA→program
        ├─ bulk id:in dataElements / TEAs / PIs / optionSets
        ├─ derive_answer_type(valueType, optionSet…)
        ├─ extract_pi_references(expression, filter)
        ├─ classify_uid(repo vs live + checksum delta)
        │
        ▼
preview → typed APPLY DHIS2 ENRICHMENT → enrichment.db snapshot
        │
        ▼
Explorer/detail prefer snapshot when present
```

Hub correctly treats **DHIS2 GET as authoritative configuration** and the repository
index as the **UID set** — but several gaps (next section) prevent consistent UI.

---

## 6. Performance and caching patterns

| Pattern | Live Processing | Central Hub | Rec |
|---|---|---|---|
| Session reuse | Long-lived Session; per-chunk sessions for parallel import | One GET Session | **COPY** |
| Field filtering | Narrow `fields=` everywhere | Narrow fields on enrich | **COPY** |
| Metadata paging | Export `pageSize=500` until pager done | Collection walks with **hard max_pages** | **ADAPT** — raise/complete with progress, or switch to filter-based reverse GETs |
| UID batching | Overlay chunks of 80, `id:in`, `paging=false` | Batches of 60, `id:in` | **ADAPT** |
| In-process caches | OU/stage/DE caches; overlay dict | Programs/stages/optionSets caches inside one fetch | **ADAPT** |
| Persist secrets | Never | Never | **COPY** |
| Truncation signaling | Export logs pager totals | Enrichment **ignores** `truncated` flag from `iter_collection` | **ADAPT** — must surface incompleteness |
| DB pool | pre_ping + recycle | N/A | **AVOID** unless DB added |
| Retry / reconnect | Mode reconnect endpoints; attempt caps | HTTP retries on 429/502/503 | **ADAPT** |
| Cancel / progress | Job-style apply progress | Enrichment run rows + cancel flag | **ADAPT** (already started) |

---

## 7. Central Hub gap analysis

### 7.1 Symptom → cause matrix

| Symptom | Primary cause category | Evidence | Notes |
|---|---|---|---|
| Program Stage missing for tracker DE | **Incomplete pagination** + **UI display** | `_build_stage_graph` capped at ~2000 stages; errors swallowed; explorer column uses `program_uid` only (blank for most tracker DEs) | Stages may exist in `metadata_relationships` but list UI hides them |
| Not all stages for one DE | **Incomplete pagination** + **UI** | Graph supports many-to-many; truncation + detail “first stage only” + facet query missing `to_type='programStage'` | Facet pollution: program edges share `DATA_ELEMENT_IN_PROGRAM_STAGE` |
| Data Sets missing for aggregate DE | **Incomplete pagination** + **UI** + **repo mapping** | Dataset walk `max_pages=10`; datasets mainly on Relationships tab; nested `summary.data_sets` unused in overview; LP CSV never had datasets | LP cannot be copied for this — LP never resolved datasets |
| Program / stage refs for PIs incomplete | **Relationship parsing** + **snapshot** + **kind routing** | PI needs enrichment snapshot + correct `object_type`; `extract_pi_references` misses some DHIS2 function forms (`V{}`, `d2:…`); stage names depend on truncated stage cache | LP stores expression but does not parse UIDs either |
| value type / answer type blank or inconsistent | **Repository mapping** + **snapshot** + **dual label systems** | Enrichment needs successful live GET; missing-in-DHIS2 keeps sparse CSV; `derive_answer_type` vs `audit_profile.answer_kind` wording differs; `repo` filter forces repository mode | CSV has valueType; answer type is hub-derived |
| Option Set name / ordered options missing | **Incomplete API fields (repo/export)** + **snapshot** + **UI path** | LP export/overlay omit options list; hub fetches options only during enrichment; Option Set tab needs applied snapshot; live audit path caps/sorts differently | Not an LP SQL gap — LP never had ordered options in overlay |

### 7.2 Cause categories (requested checklist)

| Category | Applies? | Detail |
|---|---|---|
| Incomplete API fields | **Partial** | Hub DE/OS/PI field lists are richer than LP overlay. Gaps are more about **collection walk fields + truncation** than missing per-object fields. Repo CSV lacks optionSet/stage/dataset columns. |
| Missing pagination | **Yes (high)** | Hard `max_pages` on stage/dataset/program walks silently under-builds relationship graphs. Enrichment does not persist a `truncated` warning into snapshot stats. |
| Incomplete SQL access | **No (for these symptoms)** | Hub correctly avoids SQL. LP SQL does **not** supply DE→stage registry or datasets either. Adding SQL would not fix metadata graph completeness and would violate hub boundaries. |
| Relationship parsing | **Yes (medium)** | PI expression parser incomplete for some syntax; program edges mis-tagged under stage relationship type; explorer/facets conflate program vs stage. |
| Snapshot storage | **Yes (medium)** | No first-class `program_stage_uids` / `data_set_uids` columns on objects; overview relies on `program_uid` (PI-oriented); options/relationships only after apply; raw not bulk-stored (intentional). |
| Environment mismatch | **Possible** | Hub single `DHIS2_*` vs LP LIVE/STAGE; CSV `dhis2_environment` may not match the API instance configured in hub. |
| Permission restrictions | **Possible** | Metadata GET 403/401 would empty graphs (errors often continued/swallowed). Not verified live in this research. |
| Repository mapping differences | **Yes** | Hub index is a scan of LP CSV (UID set + sparse columns). LP explorer stages come from **hardcoded maps**, not CSV. Hub must not copy those maps — it must use live metadata. |
| UI query / display behavior | **Yes (high)** | Explorer “Program / stage” shows `program_name`/`program_uid` only; Relationships tab holds stages/datasets; Option Set / PI tabs require enrichment object; pager links can drop enrichment filter params. |

### 7.3 Critical insight

**Live Processing does not already solve the enrichment problem Hub is trying to solve.**

LP’s reliable “Program Stage” column is **product hardcoded knowledge**, not a portable
metadata algorithm. Hub’s design (live reverse graph + normalized relationships) is the
correct generic approach — but current caps, tagging, and UI undercut it.

---

## 8. Recommended generic Central Hub architecture

Stay within AGENTS.md boundaries: **adapters + config**, GET-only DHIS2, no LP domain
imports, no Postgres/SSH in hub core, no writing LP CSV.

```
┌─────────────────────────────────────────────────────────────┐
│ Repository provenance (UID Index Management — unchanged)    │
│  CSV/JSON scans → versioned hub uid_index                   │
│  Provides: uid, kind, sparse fields, source_repository      │
└────────────────────────────┬────────────────────────────────┘
                             │ UID set + audit baseline
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ DHIS2 authoritative configuration (Metadata Enrichment)     │
│  Session reuse · field filters · cancel/progress            │
│  Prefer filter-based reverse GETs for target UIDs           │
│  Fallback: complete collection walks with truncation flags  │
│  Normalize: objects · relationships · options · checksums   │
│  Classify: matched / missing / mismatch / changed           │
└────────────────────────────┬────────────────────────────────┘
                             │ versioned enrichment.db snapshot
                             ▼
┌─────────────────────────────────────────────────────────────┐
│ Provenance-aware Explorer / Detail                          │
│  Show both: repository claim vs DHIS2 fact                  │
│  List columns: stages (N), datasets (N), answer type, OS    │
│  Tabs already sketched; fix list/overview projection        │
└─────────────────────────────────────────────────────────────┘
```

| Layer | Pattern | Rec |
|---|---|---|
| Connectivity | GET Session, timeouts, retries, profiles | **ADAPT** from LP client |
| UID inventory | Config-driven repo scans | **ADAPT** (exists) |
| Relationships | Live metadata reverse links (many-to-many) | **ADAPT** (exists; fix caps) |
| Option sets | Full options with order | **ADAPT** (exists in fetch; fix UX/snapshot path) |
| PI refs | Expression/filter parser | **ADAPT** (exists; extend syntax) |
| Completeness | Persist truncation / permission errors | **ADAPT** |
| LP hardcoded stage maps | — | **AVOID** |
| SSH + DHIS2 Postgres | — | **AVOID** in hub core |
| API-first + SQL fallback | — | **AVOID** for metadata enrichment |
| Calling LP as a capability | Optional GET capability for LP-owned views | **ADAPT** later if needed — do not reimplement |

---

## 9. Proposed implementation phases

Research only — **do not implement in this session.**

### Phase A — Make current API enrichment trustworthy (no SQL)

1. Replace or supplement capped full-collection stage/dataset walks with
   **per-target-UID reverse filters** (as `reverse_trace.py` already does), batched
   where possible, with progress/cancel.
2. Record `truncated`, page counts, and fetch errors into snapshot `stats`.
3. Fix relationship typing: separate `DATA_ELEMENT_IN_PROGRAM` from
   `DATA_ELEMENT_IN_PROGRAM_STAGE` (stop dual-use of one rel type).
4. Persist denormalized display helpers on objects: `program_stage_uids[]`,
   `data_set_uids[]`, primary labels — without collapsing one-to-many.
5. Explorer list: show stage count / primary stage names and dataset count; do not
   rely on `program_uid` for tracker DEs.
6. Align answer-type labels between enrichment and audit profile.

### Phase B — Provenance-aware UX

1. Always show repository vs DHIS2 columns side-by-side on detail Overview.
2. Keep UID Index Management workflow unchanged.
3. Environment selector / mismatch badge when CSV `dhis2_environment` ≠ active API profile.
4. Persist enrichment preview across restarts (optional).

### Phase C — Optional connected-repo capabilities (still no hub SQL)

1. YAML GET capabilities against Live Processing for LP-owned explorer views if operators
   need PMNP hardcoded stage maps — **call LP**, do not copy maps.
2. Multi-profile `DHIS2_*` Stage vs other targets (names only; fail-closed).

### Explicit non-goals

- Hub-owned SSH tunnel to DHIS2 Postgres.
- Copying `uid_mapping_registry._resolve_stage` / linelist / convergence.
- Writing LP `AI_UID_INDEX.csv` or DHIS2 metadata.

---

## 10. Tests required

| Test | Asserts |
|---|---|
| Tracker DE with two stages | Both `DATA_ELEMENT_IN_PROGRAM_STAGE` edges stored and listed |
| Aggregate DE with dataset | `DATA_ELEMENT_IN_DATA_SET` edge stored and visible in list/detail |
| Truncation flag | When max_pages hit, snapshot stats mark incomplete (not silent) |
| BOOLEAN / TRUE_ONLY / option set | Answer types Yes / No, Yes only, Option Set |
| Option set order | Options ordered by `sortOrder`; name present |
| PI program link | `PROGRAM_INDICATOR_BELONGS_TO_PROGRAM` + program name |
| PI expression `#{stage.de}` + `A{}` + `C{}` | Referenced UIDs extracted; unresolved listed |
| Missing DE in DHIS2 | Audit `Missing in DHIS2` |
| Checksum change | `Changed Since Last Scan` |
| Secrets redaction | Auth headers / passwords never in stored raw / errors |
| Explorer filters | program_stage / dataset / answer / option_set / audit survive paging |
| UID Index Management | Unchanged dry-run → confirm phrase behavior; no LP CSV writes |
| No write methods | Client still has no create/update/delete/import |

---

## Final recommendation

**Use: API plus repository mappings (provenance-aware).**

| Option | Verdict |
|---|---|
| API only | Insufficient — loses repository provenance, source file, and audit baseline for “Missing in Repository / Duplicate Mapping”. |
| API-first with database fallback | **Reject for hub core.** LP’s SQL path serves instance/linelist completeness, **not** portable metadata relationships. It would pull SSH/Postgres secrets and PMNP schema into the hub. |
| API plus repository mappings | **Accept.** Repository index = UID set + claimed fields; DHIS2 GET = authoritative configuration and relationships. |
| Combined provenance-aware approach | **Accept as the product shape of the above** — always distinguish *claimed in repo* vs *observed in DHIS2*, version both, and never invent stage maps from LP constants. |

**Do not** expect Live Processing’s explorer stage column to be a reusable algorithm.
**Do** harden Hub’s existing enrichment reverse-graph + snapshot model (Phase A), then
fix list/overview projection so operators actually see stages, datasets, option sets,
and answer types that the store already aims to capture.
