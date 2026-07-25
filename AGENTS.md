# AGENTS.md — Canonical AI Instructions for Central Hub

This is the **single instruction file for all AI agents** working in this repository.
Do not create provider-specific files (`CLAUDE.md`, `GROK.md`, `CODEX.md`, etc.).

Related docs: [AI_REFERENCE.md](AI_REFERENCE.md) (verified state) · [ARCHITECTURE.md](ARCHITECTURE.md) (boundaries) · [SECURITY.md](SECURITY.md) (controls) · [SKILLS.md](SKILLS.md) (capability status) · [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md) (current milestone).

## What this project is

Central Hub is a **personal multi-repository control center**. It coordinates connected
repositories through a config-driven registry and adapters. Connected repositories
execute their own logic and remain the **source of truth** for it. The hub never
becomes a second implementation of any connected system.

## Workflow rules

1. **Inspect before editing.** Verify current behavior in code; do not trust docs or
   summaries over source. Update [AI_REFERENCE.md](AI_REFERENCE.md) and
   [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md) when the state changes.
2. **Respect phase discipline.** Build only the phase the user asked for
   (see roadmap in [AI_REFERENCE.md](AI_REFERENCE.md#next-milestone--phase-2)).
   Do not implement future-phase features early.
3. **Never invent completed features.** Documentation and UI labels must distinguish
   *implemented*, *demo/placeholder*, and *planned* (see [SKILLS.md](SKILLS.md)).
4. **Smoke-test after changes.** Use the Flask test client or run the app; the manual
   checklist lives in `README.md`.

## Coding rules

- **Config-driven integrations.** All repository connections live in
  `config/repositories.yaml`. No hardcoded repo paths, URLs, or credentials in code.
- **Adapters only.** Repository interaction goes through `hub/adapters/`
  (API adapter for HTTP, command adapter for local repos). No direct domain calls.
- **Command execution controls:** allowlisted argv templates only, `shell=False`,
  timeouts, working directory restricted to the repo's configured path. Details in
  [SECURITY.md](SECURITY.md#command-execution-controls).
- **Sensitive operation lifecycle:** Validate → Preview → Confirm → Execute → Verify → Audit.
  Never imply silent writes. DHIS2-specific version in
  [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md).
- **Lightweight UI:** server-rendered Jinja templates, one plain CSS file, system
  fonts, minimal JavaScript only when needed. No React, Bootstrap, Tailwind, or
  frontend build tooling.
- **Read-only by default.** New capabilities start as dry-run/read-only; writes
  require explicit configuration and confirm gates.
- **Secrets** only via `.env` (gitignored); ship `.env.example` without values that matter.

## Prohibited actions

- Copying or reimplementing PMNP, DHIS2, reporting, convergence, immunization,
  DDS, tetanus, or scorecard logic. If a feature needs those rules, expose them as
  a capability that *calls the connected repository*.
- Importing connected repositories' Python packages for business logic.
- Real DHIS2 **writes**, or copying live credentials into the repo. Read-only DHIS2
  access is allowed only via `.env` configuration (see [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md)).
- Free-form shell execution from UI or config; `shell=True` anywhere.
- Hardcoding or committing secrets, tokens, or real hostnames/credentials.
- Adding heavy frontend frameworks or dependencies beyond `requirements.txt` needs.
- Creating provider-specific AI instruction files.
