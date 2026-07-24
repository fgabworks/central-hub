# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).
Update this file at the end of significant work sessions.

## Current milestone

**Phase 1 complete** — skeleton, registry, health checks, dark dashboard UI.
Next: **Phase 2** (jobs + audit persistence), scope in
[AI_REFERENCE.md](../AI_REFERENCE.md#next-milestone--phase-2).

## Repository / branch state

- Branch: `main` — **no commits yet**; all files untracked. First task of any
  session that changes code: consider making an initial commit so diffs are reviewable.
- Virtualenv: `.venv/` (gitignored), dependencies from `requirements.txt` installed.
- `.env` exists locally (gitignored); template is `.env.example`.

## Known issues / caveats

1. Dashboard (`/`) shows **hard-coded demo data** (repos, jobs, activity, DHIS2
   panel) for visual parity with a mockup. Replace with real data in Phase 2.
2. `ALLOW_DHIS2_WRITES` is not read by code — UI banner/env placeholder only.
3. `/jobs`, `/dhis2`, `/audit` are placeholders; topbar icons are non-functional.
4. No automated tests; verification is the manual checklist in `README.md` plus
   Flask test-client smoke checks.
5. Root `README.md` predates the dark-UI redesign (still describes the earlier
   light theme pages and omits `/dhis2`); update it in a future approved change.

## How to run / test

```powershell
.\.venv\Scripts\Activate.ps1
python app.py    # http://127.0.0.1:8080
```

Smoke check: request `/`, `/repositories`, `/health`, `/api/healthz` via the Flask
test client and expect HTTP 200 (`/api/healthz` returns `ok: true`).

## Next task (recommended)

Phase 2, step 1: design SQLite schema for `jobs` and `audit_events`, add a small
storage module (e.g. `hub/storage/`), and wire `/jobs` + `/audit` to real (empty)
tables — still **no job execution**. Keep demo dashboard fixtures until real data
can replace them in the same change.
