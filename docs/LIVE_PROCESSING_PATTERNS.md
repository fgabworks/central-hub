# LIVE_PROCESSING_PATTERNS.md — Reusable Infrastructure Patterns

**Status:** research only (2026-07-25). No Central Hub application code was changed
for this document.

**Sources inspected (read-only):**

- Central Hub: `hub/dhis2/client.py`, `hub/settings.py`, `.env.example`,
  `hub/dhis2/catalog.py`, `hub/dhis2/uid_index.py`, `hub/dhis2/uid_mapping/`,
  `hub/adapters/`, `hub/audit/`, `docs/DHIS2_SAFETY.md`, `AGENTS.md`
- Live Processing: `_support/_env_config.py`,
  `_support/_postgres_env_connection_impl.py`, `lookup/lookup_dhis2_api.py`,
  `lookup/app_lookup.py`, `lookup/bulk_apply_job.py`,
  `lookup/listed_apply_progress.py`, `lookup/test_connection_mode.py`,
  and the digest `CENTRAL_HUB_REFERENCE.md`

**Rules applied:** do not modify Live Processing; do not copy PMNP business logic;
do not expose credentials, URLs, tokens, or database secrets; do not benchmark live
systems.

---

## 1. Understanding and boundaries

Central Hub **coordinates** connected repositories. Live Processing remains the
source of truth for PMNP processing, tracker writes, SQL linelist, and domain
rules. Hub should borrow **generic connectivity / reliability / job lifecycle
ideas**, then reimplement them cleanly — not import LP packages or paste write
clients.

| Belongs in Central Hub | Belongs in connected repository (LP) |
|---|---|
| Read-only DHIS2 GET client quality (session, timeout, retry, fields, pager) | Tracker import / event writes / post-write verify |
| Local catalog + UID mapping index (already started) | Completeness heuristics, stage DE mins, TEA rules |
| Disk/SQLite job shell (progress, cancel, resume) for *hub* work | Apply-to-DHIS2 job phases (`deriving`, convergence, …) |
| Environment / mode banners, health aggregation | SSH tunnel + DHIS2 Postgres backbone |
| Confirm → execute → audit for hub-owned ops | Confirm → apply → verify for LP domain writes |
| Adapter timeouts / reconnect budgets when calling repos | API-first + SQL fallback for household/member reads |

---

## 2. Pattern catalog

Recommendation key:

| Tag | Meaning |
|---|---|
| **COPY** | Reuse the *idea* and a small structural shape; reimplement in hub (do not paste LP files) |
| **ADAPT** | Same idea, but redesign for hub phase, multi-repo, and GET-first safety |
| **AVOID** | Product-specific, unsafe for hub ownership, or technical debt |

### 2.1 Environment loading

| | |
|---|---|
| **LP files** | `_support/_env_config.py`, `.env.example`, `lookup/lookup_dhis2_api.py` (`load_env_file` / `load_dhis2_config`), `app_lookup.py` CLI overrides |
| **How it works** | Profiled secrets via `LIVE_*` / `STAGE_*` prefixes; `load_dotenv(override=False)`; optional `.env.runtime`; workflow env vars can force profile. DHIS2 client has a second, lighter loader so API tools can start without Postgres. |
| **Hub today** | Single-profile `CENTRAL_HUB_*` + `DHIS2_*` via `hub/settings.py` (`override=False`). |
| **Rec** | **ADAPT** multi-profile prefixes for Stage vs other DHIS2 targets; **AVOID** dual loaders and LP’s hard `live`/`stage`-only normalize. |
| **Benefits** | Safer Stage/Live separation; fewer accidental Live calls. |
| **Risks** | Mis-selected profile; more env complexity. |
| **Hub shape** | Optional `DHIS2_PROFILE=stage\|…` mapping to prefixed vars, or separate `DHIS2_STAGE_*` / future alias — keep one loader in `hub/settings.py`. |
| **Config knobs** | `DHIS2_ENABLED`, `DHIS2_BASE_URL`, `DHIS2_USERNAME`, `DHIS2_PASSWORD`, `DHIS2_TIMEOUT_SECONDS`, `ALLOW_DHIS2_WRITES` (existing); optional later: `DHIS2_PROFILE`, `DHIS2_STAGE_BASE_URL`, `DHIS2_LIVE_BASE_URL` (names only; Live URL unused until explicitly enabled). |

### 2.2 DHIS2 authentication and session reuse

| | |
|---|---|
| **LP files** | `lookup/lookup_dhis2_api.py` (`Dhis2Lookup`, `requests.Session` + Basic Auth); `app_lookup.py` (`get_dhis2`, process cache) |
| **How it works** | Long-lived `Session` with `session.auth`; parallel import workers create **per-chunk** temporary sessions (avoid sharing one Session across threads). |
| **Hub today** | Per-call `requests.get(..., auth=...)` — no Session. |
| **Rec** | **COPY** Session reuse for sequential GETs; **ADAPT** thread policy if hub ever parallelizes reads. |
| **Benefits** | Lower TCP/TLS overhead; connection keep-alive. |
| **Risks** | Stale connections; thread-safety if shared naively. |
| **Hub shape** | `Dhis2Client` owns one `requests.Session`; recreate on auth/config change; never share Session across threads without a pool/adapter strategy. |

### 2.3 API-first and SQL fallback

| | |
|---|---|
| **LP files** | `app_lookup.py` (`auto` / `api` / `ssh_readonly`), `lookup_dhis2_api.py` completeness helpers, SQL readers in `lookup_household_member.py` |
| **How it works** | Prefer API reads; if payload looks incomplete, enrich from Postgres over SSH; writes stay on API. |
| **Hub today** | HTTP GET only; no SQL/SSH. |
| **Rec** | **AVOID** in hub core. If Stage API is incomplete, call LP as a capability or document limitation. |
| **Benefits (LP)** | Resilience when Tracker API is sparse. |
| **Risks (if hub copies)** | Second schema client; secret sprawl; violates “no domain DB” boundary. |

### 2.4 Connection modes and write safeguards

| | |
|---|---|
| **LP files** | `app_lookup.py` (`VALID_CONNECTION_MODES`, `_readonly_block`, `connection_verified`, confirm gates); `test_connection_mode.py` |
| **How it works** | Modes: `auto` / `api` / `ssh_readonly`. All writes pass `_readonly_block()` (403 in readonly; 409 if unverified). Apply endpoints require `confirm=true`. Post-write read-back on some paths. |
| **Hub today** | Structural GET-only client; `writes_allowed()` always `False`; `ALLOW_DHIS2_WRITES` documented but no write methods exist. |
| **Rec** | **ADAPT** fail-closed + explicit confirm for any future hub write path; **COPY** “unverified ⇒ no writes”; **AVOID** LP mode names tied to SSH/SQL. |
| **Benefits** | Hard to accidentally write; operator-visible mode. |
| **Risks** | Over-complex modes before hub needs them. |
| **Hub shape (near term)** | Keep GET-only. Later: `DHIS2_MODE=readonly` (default) and refuse any non-GET even if env flips. |

### 2.5 Request timeouts

| | |
|---|---|
| **LP files** | `Dhis2Lookup(timeout=30)`, `ping(timeout=10)`, `_wait_tracker_job(timeout_s=300)`; Postgres/SSH timeouts in `_postgres_env_connection_impl.py` |
| **How it works** | Per-request HTTP timeout; separate short probe timeout; async tracker job poll deadline; SSH/DB health budgets. |
| **Hub today** | Single `DHIS2_TIMEOUT_SECONDS` (default 10) on every GET. |
| **Rec** | **ADAPT** split probe vs operation timeouts. |
| **Benefits** | Fast fail on health; allow slower schema/list calls. |
| **Risks** | Too-low defaults → flaky Stage; too-high → hung UI threads. |
| **Hub shape** | `DHIS2_TIMEOUT_SECONDS` (ops), `DHIS2_PROBE_TIMEOUT_SECONDS` (status/ping), optional `DHIS2_MAX_PAGES` / list caps. |

### 2.6 Retries, reconnect, and backoff

| | |
|---|---|
| **LP files** | `app_lookup.py` reconnect attempts (`CONNECTION_MAX_ATTEMPTS`); `bulk_apply_job.py` connection-failure markers; `lookup_dhis2_api.py` binary-split batch retry on POST failure |
| **How it works** | Bounded rebuild of engine/client + probe. Ordinary GETs mostly do **not** use urllib3 Retry. Bulk posts split failing batches and retry halves. Short sleeps for Windows file-lock races on job JSON. |
| **Hub today** | No HTTP retries; no reconnect budget. |
| **Rec** | **ADAPT** limited exponential backoff for **idempotent GETs** (429/502/503/timeout); **AVOID** string-marker classification as primary logic; **AVOID** binary-split POST retry until hub owns writes (it should not). |
| **Benefits** | Survives brief Stage blips. |
| **Risks** | Retries amplify load; hide persistent misconfig; POST retries are dangerous if ever added carelessly. |
| **Hub shape** | `urllib3.Retry` or small wrapper: max 2–3 attempts, backoff 0.5–2s, only safe methods; surface attempt count in errors/audit metadata. |
| **Config** | `DHIS2_RETRY_MAX`, `DHIS2_RETRY_BACKOFF_SECONDS`, `DHIS2_RECONNECT_MAX_ATTEMPTS` |

### 2.7 Connection pooling

| | |
|---|---|
| **LP files** | SQLAlchemy `pool_pre_ping`, `pool_recycle` for Postgres; HTTP relies on `Session`/urllib3 defaults; parallel workers open new Sessions |
| **Hub today** | No pooling (one-shot GET). |
| **Rec** | **ADAPT** HTTP Session (+ optional `HTTPAdapter` pool size); **AVOID** Postgres pool in hub unless hub itself needs a DB (Phase 2 SQLite is local, not DHIS2 PG). |
| **Benefits** | Faster repeated metadata GETs. |
| **Risks** | Pool exhaustion if unbounded parallelism. |
| **Config** | `DHIS2_HTTP_POOL_CONNECTIONS`, `DHIS2_HTTP_POOL_MAXSIZE` (optional) |

### 2.8 Pagination and field filtering

| | |
|---|---|
| **LP files** | `TRACKER_FIELDS`, `EVENT_FIELDS`, `pageSize` caps, OU-scoped search in `lookup_dhis2_api.py` |
| **How it works** | Narrow `fields=` projections; pageSize limits; avoid full TEI graphs. |
| **Hub today** | Uses `fields=` and single-page `page`/`pageSize`; no multi-page walk; samples capped at 3–5. |
| **Rec** | **COPY** aggressive field filtering; **ADAPT** explicit capped pager (`max_pages`) for builder UID warm / compare — never unbounded export. |
| **Benefits** | Major speed + payload reduction. |
| **Risks** | Missing fields if projections too tight; accidental full export if pager uncapped. |
| **Hub shape** | `iter_collection(plural, fields=..., page_size=…, max_pages=…)` with hard ceiling; discovery/catalog remain schema-only. |
| **Config** | `DHIS2_PAGE_SIZE`, `DHIS2_MAX_PAGES` |

### 2.9 Batching and chunking

| | |
|---|---|
| **LP files** | `import_events_bulk` / `import_event_updates` (chunk 50–100, workers 1–12 clamps); listed-records batch defaults |
| **How it works** | Split large write sets; parallel chunk posts; cancel between chunks. |
| **Hub today** | N/A for writes; UID mapping scan is in-process sequential. |
| **Rec** | **ADAPT** chunking for *hub-local* work (scan large CSV, compare N UIDs); **AVOID** tracker import batching inside hub. |
| **Benefits** | Memory bounds; progress granularity. |
| **Risks** | Parallel GETs can throttle Stage — keep worker caps low. |
| **Config** | `HUB_JOB_CHUNK_SIZE`, `HUB_JOB_MAX_WORKERS` (Phase 2) |

### 2.10 Caching and metadata reuse

| | |
|---|---|
| **LP files** | In-process OU/stage/DE caches in `lookup_dhis2_api.py`; process-level client cache; `uid_mapping_registry` overlays (**product**) |
| **Hub today** | Disk catalog; TTL in-memory builder `UidIndex` (300s, single page); disk UID mapping index from repo files. |
| **Rec** | **ADAPT** TTL + disk for schema/list caches; **AVOID** LP UID overlay / lineage registry logic. |
| **Benefits** | Reuse discovery results; fewer Stage round-trips. |
| **Risks** | Stale metadata after Stage changes — need refresh + timestamp UI (hub already shows discovery time). |
| **Hub shape** | Keep catalog + mapping index; add ETag/TTL on optional list caches; always show `last_synced` / `discovered_at`. |

### 2.11 Parallel / worker processing

| | |
|---|---|
| **LP files** | `ThreadPoolExecutor` in import paths; daemon job threads in `bulk_apply_job.py` |
| **How it works** | In-process threads + disk job state — not Celery/RQ. |
| **Hub today** | Sync request handlers; demo `/jobs` only. |
| **Rec** | **ADAPT** for Phase 2 hub jobs (discovery enrich, bulk UID compare); **AVOID** LP’s domain worker bodies. |
| **Benefits** | UI stays responsive for long scans. |
| **Risks** | Flask + threads complexity; need cooperative cancel. |

### 2.12 Progress tracking

| | |
|---|---|
| **LP files** | `bulk_apply_job.progress_payload`; `listed_apply_progress.timing_fields` / ETA |
| **How it works** | Stable poll DTO: percent, phase, message, resumable, cancel/pause flags; ETA after N completions. |
| **Hub today** | None (sync). |
| **Rec** | **ADAPT** generic progress DTO for hub jobs; strip PMNP phase names (`deriving`, etc.). |
| **Benefits** | Operator clarity for long work. |
| **Risks** | Fake precision if percent mapping is arbitrary. |

### 2.13 Checkpoints and resume

| | |
|---|---|
| **LP files** | `bulk_apply_job.py` (`_save_import_checkpoint`, `resume_job`, `refresh_job_state`, `completed_event_uids`) |
| **How it works** | Disk checkpoints; resume skips completed units; orphaned worker → failed+resumable. |
| **Hub today** | None. |
| **Rec** | **ADAPT** for hub jobs that iterate UIDs/pages; **AVOID** event-UID rewrite semantics. |
| **Benefits** | Survive process restart during large compares/scans. |
| **Risks** | Checkpoint corruption — use atomic write (`tmp` + `replace`) as LP does. |

### 2.14 Cancellation

| | |
|---|---|
| **LP files** | `request_cancel` / `request_pause`, `JobCancelled`, `cancel_check` between chunks |
| **How it works** | Cooperative flags on disk; workers check between units; pool shutdown with cancel_futures where available. |
| **Hub today** | None (documented as future for command adapters). |
| **Rec** | **COPY** cooperative cancel idea for Phase 2; never kill OS processes from UI. |
| **Benefits** | Safe stop without corrupting Stage. |
| **Risks** | Non-cooperative code paths ignore flags. |

### 2.15 Health checks

| | |
|---|---|
| **LP files** | `/api/healthz`, `_probe_dhis2`, `Dhis2Lookup.ping()` → `system/info` with narrow fields |
| **How it works** | Mode-aware: API mode need not have Postgres; readonly need not write-probe DHIS2. |
| **Hub today** | `check_status()` + UI/API; `/api/healthz` reports config flag, not live DHIS2 ping. |
| **Rec** | **ADAPT** aggregate health: hub liveness vs DHIS2 probe vs per-repo adapter health. |
| **Benefits** | Clear “configured but offline” vs “healthy”. |
| **Risks** | Health endpoints that hit Stage too often — cache last probe briefly. |

### 2.16 Logging and error handling

| | |
|---|---|
| **LP files** | `setup_logging`; job JSON + `bulk_apply_history.jsonl`; `request_stats`; `ApiDataUnavailable` → 502 |
| **Hub today** | Append-only audit JSONL + redaction; no per-request stats counters. |
| **Rec** | **ADAPT** separate **audit** (operator actions) from **job execution state**; optional GET/retry counters on client. |
| **Benefits** | Debuggable without secrets. |
| **Risks** | Logging response bodies can leak PII — keep redaction. |

### 2.17 Branch, commit, and environment display

| | |
|---|---|
| **LP files** | Env/mode banners in `lookup.html` / `index()`; git helpers for UID index provenance (**product**) |
| **How it works** | Surfaces runtime environment + connection mode + DHIS2 host label (no credentials). |
| **Hub today** | Env profile in chrome; DHIS2 online/offline badge; no hub git SHA banner. |
| **Rec** | **ADAPT** banners: hub env, DHIS2 profile/mode, last probe; optional hub git SHA. **AVOID** LP release-ref UID panels. |
| **Benefits** | Prevents Stage/Live confusion. |
| **Risks** | Showing full base URL may be sensitive in screenshots — prefer redacted host label (hub already redacts). |

### 2.18 Long-running job safeguards

| LP safeguard | Rec for hub |
|---|---|
| Disk-backed job state | **ADAPT** (Phase 2 SQLite/files) |
| One active job per scope | **ADAPT** |
| Lock mode switches mid-run | **ADAPT** (lock hub DHIS2 mode while job runs) |
| Fail-closed until verified | **COPY** idea |
| Confirm before apply | **COPY** for any non-GET |
| Chunk/worker clamps | **COPY** |
| Atomic job JSON writes | **COPY** |
| Orphan job refresh | **ADAPT** |
| `persistable_scope` thinning | **ADAPT** |
| `MAX_CONVERGENCE_UPDATE_ATTEMPTS` | **AVOID** (product) |
| Google Sheets sync / Excel OU trackers | **AVOID** |

---

## 3. Speed vs reliability

### Patterns that improve speed

| Pattern | Why |
|---|---|
| `requests.Session` reuse | Fewer handshakes |
| Narrow `fields=` | Smaller JSON parse/transfer |
| Aggressive `pageSize` + hard `max_pages` | Bound work |
| Disk catalog / UID index reuse | Skip Stage round-trips |
| In-process metadata caches with TTL | Hot dependency selectors |
| Chunked local scans | Better CPU/memory locality |
| Parallel GETs (low worker count) | Overlap I/O — only after rate limits understood |

### Patterns that improve reliability (may reduce speed)

| Pattern | Tradeoff |
|---|---|
| Retries + backoff | Extra latency on failure paths; can amplify load |
| Fail-closed until verified | Blocks work during reconnect |
| Confirm gates / preview | Extra operator step |
| Cooperative cancel checks | Slight overhead per chunk |
| Disk checkpoints + atomic writes | I/O cost |
| Probe-before-job | Extra Round-trip |
| Single-page-safe defaults / low concurrency | Slower bulk warm, safer Stage |
| Read-only mode / no SQL fallback in hub | May show “missing” when LP would enrich from SQL |

---

## 4. Technical debt Central Hub should not inherit

1. **Monolith Flask + giant domain HTML/JS** glued to infra.
2. **Dual environment loaders** (dotenv support vs hand-rolled `setdefault` in DHIS2 module).
3. **Process-global `_state`** for engine/client/mode/locks (hard to test; not multi-tenant).
4. **String-matching connection failure markers** as the primary classifier.
5. **Owning a DHIS2 write/import client** inside the hub.
6. **SSH + raw DHIS2 Postgres** as a hub requirement.
7. **Google Sheets / Excel OU trackers** as infrastructure.
8. **Hardcoded TEA/DE completeness thresholds** and stage min counts.
9. **Product job phase names** and convergence retry loops.
10. **`SIMULATOR_*` feature flags** controlling production batching.
11. **Importing `uid_mapping_registry` / linelist / convergence modules**.
12. **Verbatim file copies** from Live Processing.
13. **Writing LP’s `AI_REFERENCE/AI_UID_INDEX.csv` from the hub**, or reimplementing
    `update_ai_uid_index.py` / explorer registry (HH/Member/linelist/scorecard overlays).

### UID Index Management — decision (2026-07-25)

LP surfaces (in Lookup EXTRAS): (1) Mapping Explorer = LP-field registry, **AVOID** in hub;
(2) UID Index Management = dry-run → typed confirm → versioned archive of the CSV, **ADAPT** UX.

| Pattern | Hub action |
|---|---|
| Controlled update lifecycle + typed confirm + backups | **ADAPT** → `hub/dhis2/uid_mapping/admin.py`, `/dhis2/uid-index/manage` |
| Change cards (new / name / type / missing / unchanged) | **ADAPT** on hub-local JSON merge preview |
| Version list / compare / restore | **ADAPT** under `data/dhis2/uid_index/archive/` |
| Scan LP CSV as a configured source | **ADAPT** via `config/uid_mapping_sources.yaml` (read-only input) |
| LP `uid_mapping_registry`, DB scan, scorecard/lineage tabs | **AVOID** |
| CLI DHIS2 export → LP `inputs/` → apply to LP CSV | **AVOID** in hub; stay an LP capability if needed later via YAML command adapter |
| Release↔git “used by release” panel | **AVOID** |

Hub SoT for this feature remains `data/dhis2/uid_index/latest.json`, not LP’s CSV.

**DHIS2 reverse trace (hub GET):** DE→stage/program via
`/api/programStages?filter=programStageDataElements.dataElement.id:eq:{uid}`;
TEA→program via `programTrackedEntityAttributes…`; optionSet nested or
`optionSets/{id}`. Metadata API does **not** expose physical DB/analytics
tables — hub shows logical store hints only; linelist/`information_schema`
stays in LP.

### Metadata Enrichment — decision (2026-07-25)

| Pattern | Hub action |
|---|---|
| Bulk GET + field filters + paging | **ADAPT** → `hub/dhis2/enrichment/fetch.py` (`id:in` batches, shared session) |
| Cache programs / stages / option sets / catCombos | **ADAPT** in-fetch caches; avoid one request per UID |
| Controlled update (preview → typed confirm → version) | **ADAPT** → `/dhis2/enrichment`, phrase `APPLY DHIS2 ENRICHMENT` |
| Normalized relationship graph (one DE → many stages) | **ADAPT** → SQLite `metadata_relationships` |
| Answer-type / option-set / PI expression audit | **ADAPT** locally from GET metadata |
| LP UID overlay registry / linelist / Postgres schema | **AVOID** |
| DHIS2 create/update/delete/import | **AVOID** (forbidden) |
| Writing LP `AI_UID_INDEX.csv` | **AVOID** (UID Index Management + Enrichment both stay hub-local) |

Hub enrichment SoT: `data/dhis2/enrichment.db`. Repository UID list remains a separate
source under `data/dhis2/uid_index/`.

---

## 5. Suggested generic Central Hub implementations

### Near-term (client quality — still GET-only)

```text
hub/dhis2/client.py
  ├─ Session + Basic Auth
  ├─ probe_timeout vs request_timeout
  ├─ Retry on idempotent GET (bounded)
  ├─ iter_collection(..., max_pages=N)   # hard ceiling
  ├─ request_stats {get, retry, errors}
  └─ writes_allowed() stays False; no POST/PUT/PATCH/DELETE
```

### Medium-term (Phase 2 jobs — hub work only)

```text
hub/jobs/
  ├─ store (SQLite or JSON under data/jobs/)
  ├─ progress_payload (generic phases: queued/running/finalizing)
  ├─ cooperative cancel / pause
  ├─ checkpoint cursor (page or UID)
  └─ adapters invoke connected repos for domain work
```

### Do not build in hub

- Tracker event import pipeline
- SQL linelist / household-member readers
- Convergence / immunization / DDS / tetanus / scorecard engines

Those remain Live Processing capabilities invoked via registry adapters when needed.

---

## 6. Recommended configuration variables

**Keep (existing):**

- `DHIS2_ENABLED`, `DHIS2_BASE_URL`, `DHIS2_USERNAME`, `DHIS2_PASSWORD`
- `DHIS2_TIMEOUT_SECONDS`, `ALLOW_DHIS2_WRITES=false`
- `CENTRAL_HUB_REQUEST_TIMEOUT`, `CENTRAL_HUB_AUDIT_LOG`, `CENTRAL_HUB_ENV`

**Add when implementing client hardening (names only):**

| Variable | Purpose |
|---|---|
| `DHIS2_PROBE_TIMEOUT_SECONDS` | Short status/ping timeout |
| `DHIS2_RETRY_MAX` | Max GET retries (e.g. 2) |
| `DHIS2_RETRY_BACKOFF_SECONDS` | Base backoff |
| `DHIS2_PAGE_SIZE` | Default list page size |
| `DHIS2_MAX_PAGES` | Hard pager ceiling (safety) |
| `DHIS2_HTTP_POOL_MAXSIZE` | Optional urllib3 pool size |
| `DHIS2_PROFILE` | Optional stage/other profile selector |

**Add with Phase 2 jobs:**

| Variable | Purpose |
|---|---|
| `HUB_JOB_CHUNK_SIZE` | Units per chunk |
| `HUB_JOB_MAX_WORKERS` | Parallelism clamp |
| `HUB_JOB_TIMEOUT_SECONDS` | Wall clock per job |
| `HUB_JOB_CHECKPOINT_DIR` | Disk checkpoint root |

Never commit values. Never put Live credentials in docs or YAML.

---

## 7. Safe testing approach

1. **Unit / mocked HTTP** — `unittest.mock` / `responses`-style stubs for Session GET,
   pager multi-page, retry countdown, timeout errors (extend `tests/test_dhis2_*.py`).
2. **No live writes** — assert client has no write methods; `ALLOW_DHIS2_WRITES` ignored
   for method presence.
3. **Stage-only manual smoke** (optional, operator-driven) — status, discovery, single
   UID compare; do **not** benchmark; do **not** bulk-export.
4. **Job shell tests** (Phase 2) — fake iterator + cancel flag + checkpoint round-trip
   on temp dirs; no Stage required.
5. **Never** point automated CI at Live; never log response bodies with TEI attributes.

---

## 8. Prioritized implementation plan

| Priority | Item | Rec | Milestone fit |
|---|---|---|---|
| P0 | Document boundaries (this file) | — | **Done** |
| P1 | `requests.Session` + split probe/op timeouts | COPY/ADAPT | **Done** (2026-07-25) |
| P1 | Bounded GET retry/backoff + stats | ADAPT | **Done** |
| P1 | `iter_collection` with `max_pages` + field filters | COPY/ADAPT | **Done** |
| P2 | Readonly mode + reliability knobs on Settings / Overview | ADAPT | **Done** (partial) |
| P2 | Aggregate health (`/api/healthz` + optional `?probe=1`) | ADAPT | **Done** |
| P3 | Generic job shell: progress, cancel, checkpoint | ADAPT | Phase 2 (pending) |
| P3 | Chunked/parallel hub-local scans (UID compare) | ADAPT | After job shell |
| — | SQL fallback / SSH / tracker writes / domain modules | AVOID | Never in hub core |

P1/P2 client hardening is implemented. **DHIS2-3** Unified Metadata Builder
(preview-only) is implemented. Phase 2 job shell stays deferred.

---

## 9. Concise recommendation — Central Hub DHIS2 client architecture

Keep a **hub-owned, GET-only DHIS2 client** that is small, explicit, and safe:

1. **Transport:** one `requests.Session` (Basic Auth), optional connection pool sizing,
   distinct probe vs operation timeouts, bounded retries on idempotent GETs only.
2. **Query discipline:** allowlisted or catalog-resolved collections; narrow `fields=`;
   pagers with a **hard max_pages** (never full metadata export).
3. **State:** reuse disk catalog + UID mapping index; TTL caches for hot lists; show
   freshness timestamps.
4. **Safety:** no write methods; fail closed if disabled/unconfigured; redaction + audit
   on operator actions; `ALLOW_DHIS2_WRITES` remains false and non-functional until a
   future lifecycle exists.
5. **Delegation:** anything that needs Tracker writes, SQL enrichment, or PMNP rules
   stays in Live Processing and is invoked through the registry/adapters.
6. **Jobs (later):** hub job shell owns progress/cancel/resume for *hub* work; it does
   not reimplement LP’s apply pipeline.

**Bottom line:** steal LP’s **lifecycle and transport hygiene**, not its **domain
client**. Central Hub should become a reliable Stage metadata observer and coordinator —
not a second Live Processing.
