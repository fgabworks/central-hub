# DHIS2_SAFETY.md — DHIS2 Interaction Rules

Canonical rules: [AGENTS.md](../AGENTS.md). Current state: [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current state (verified)

- **Read-only foundation is implemented** in `hub/dhis2/` (GET only).
- Supported: connection/status check, metadata search (UID or name), metadata detail,
  instance discovery/catalog, UID mapping index compare (GET), relationship reads.
- **Client hardening:** `requests.Session` + pool, distinct probe/operation timeouts,
  bounded GET retries (timeout/connection/429/502/503 only), capped
  `iter_collection` pagination (`DHIS2_MAX_PAGES` — never full metadata export).
- **Writes are disabled and unimplemented.** `ALLOW_DHIS2_WRITES` must remain `false`.
  The client has no create/update/delete/import methods; `writes_allowed()` returns `False`.
- Credentials load from `.env` only; passwords are never shown in UI or audit logs.
- Errors are timeout-aware and redacted (`hub/dhis2/redact.py`).
- Status checks, lookups, previews, draft saves, discovery, catalog views, and UID
  index scan/import/view append audit events (`DHIS2_STATUS_CHECK`,
  `DHIS2_METADATA_LOOKUP`, `DHIS2_METADATA_DETAIL`, `DHIS2_METADATA_PREVIEW`,
  `DHIS2_METADATA_DRAFT_SAVE`, `DHIS2_INSTANCE_DISCOVER`, `DHIS2_CATALOG_VIEW`,
  `DHIS2_UID_INDEX_SCAN`, `DHIS2_UID_INDEX_IMPORT`, `DHIS2_UID_INDEX_VIEW`).
- **Instance discovery** builds a local capability catalog from schemas/OpenAPI
  summaries — not a full metadata export.
- **UID mapping index** is local (`data/dhis2/uid_index/`). Scanning repository CSV/JSON
  and comparing via GET does not write to DHIS2. Conflicts never overwrite silently.
- **Unified Metadata Builder** (`/dhis2/metadata-builder`) is preview-only:
  catalog-driven specialized + generic schema builders, UID-index dependency
  checks, duplicate checks, exact JSON envelope, local drafts.
  Create/Update/Delete/Import remain disabled; `apply_enabled` is always false.

## What this is not

- Not a PMNP / Live Processing client.
- Not a DHIS2 write console.
- Not a copy of scorecard, convergence, immunization, DDS, tetanus, or reporting logic.
- Metadata endpoints are generic Web API reads against allowlisted resource types only.

## Mandatory write lifecycle (future — not available)

Every future DHIS2 write capability must implement, in order:

1. **Read** — fetch current server state.
2. **Validate** — check payload shape and references.
3. **Preview** — show exactly what would change; dry-run default.
4. **Duplicate Check** — detect existing objects/values.
5. **Confirm** — explicit operator confirmation.
6. **Apply** — only if `ALLOW_DHIS2_WRITES=true` **and** confirmed.
7. **Verify** — re-read and compare after write.
8. **Audit** — actor, timestamp, target, summary (no secrets).

Until that lifecycle exists and is reviewed, keep `ALLOW_DHIS2_WRITES=false`.

## Boundaries

- Prefer sandbox/demo instances for testing.
- Do not commit real DHIS2 hostnames, usernames, or passwords.
- Do not add POST/PUT/PATCH/DELETE helpers without implementing the full lifecycle above.
- Domain-specific indicator or program logic stays in connected repositories, not in the hub.
