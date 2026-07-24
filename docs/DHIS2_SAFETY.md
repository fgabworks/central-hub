# DHIS2_SAFETY.md — DHIS2 Interaction Rules

Applies to any future DHIS2 connectivity. Canonical rules: [AGENTS.md](../AGENTS.md).

## Current state (verified)

- **No DHIS2 client exists in this codebase.** The `/dhis2` page and dashboard
  panel are UI scaffolds; tool buttons perform no actions.
- **Writes are disabled by default and unimplemented.** `ALLOW_DHIS2_WRITES` in
  `.env.example` is a forward-looking gate; no code reads it yet.
- No live DHIS2 URLs or credentials exist anywhere in the repository.

## Mandatory write lifecycle (future)

Every DHIS2 write capability must implement all eight steps, in order:

1. **Read** — fetch current server state through the connected repository's API.
2. **Validate** — check payload shape and references before anything else.
3. **Preview** — show exactly what would change; dry-run is the default.
4. **Duplicate Check** — detect existing objects/values to avoid double-writes.
5. **Confirm** — explicit operator confirmation; never auto-apply.
6. **Apply** — execute only if `ALLOW_DHIS2_WRITES=true` (read from settings,
   default false) *and* the operator confirmed.
7. **Verify** — re-read and compare server state after the write.
8. **Audit** — record actor, timestamp, target, and summary (no secrets).

## Boundaries

- DHIS2 access goes through an **API adapter to a connected repository**
  (e.g. a Live Processing instance) — the hub never embeds a DHIS2 domain client,
  UID catalogs, or metadata mappings ([ARCHITECTURE.md](../ARCHITECTURE.md#core-rule)).
- Read-only status/health probes are the only DHIS2 calls allowed before the full
  lifecycle above is implemented and reviewed.
- Never copy PMNP scorecard, convergence, immunization, DDS, tetanus, or
  reporting logic into the hub to "help" a DHIS2 feature.
- Test against demo/sandbox instances only; production connections require
  explicit registry configuration plus the write gate plus per-operation confirm.
