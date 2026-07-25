# AI Start Here

This is the entry point for any AI coding assistant working on `central-hub`.

## Required Reading

Before making changes, read these files in order:

1. [AGENTS.md](AGENTS.md)
2. [AI_REFERENCE.md](AI_REFERENCE.md)
3. [ARCHITECTURE.md](ARCHITECTURE.md)
4. [SECURITY.md](SECURITY.md)
5. [SKILLS.md](SKILLS.md)
6. [docs/AI_HANDOFF.md](docs/AI_HANDOFF.md)
7. Any feature-specific document, such as [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md)

`AGENTS.md` is the canonical instruction file. If documents conflict, follow `AGENTS.md`.

## First Actions

1. Inspect the current repository structure.
2. Review the existing implementation before editing.
3. Check `git status` and existing uncommitted changes.
4. Identify the files relevant to the requested task.
5. Verify whether features are implemented, partial, placeholder, or planned.
6. Do not assume documentation is more current than the code.

## Required First Response

Before coding, briefly report:

- your understanding of Central Hub
- the verified current state
- relevant architecture boundaries
- files likely to change
- proposed implementation plan
- risks or safety concerns

Do not modify files until the requested task and its boundaries are clear.

## Core Boundary

Central Hub coordinates connected repositories.

Connected repositories remain the source of truth for their own processing logic, validation, inputs, outputs, and safeguards.

Do not duplicate repository-owned business logic inside Central Hub.

## Safety

- Never expose or hardcode secrets.
- Do not modify `.env` values.
- Do not run destructive or unrestricted commands.
- Preserve existing uncommitted work.
- Keep sensitive operations read-only or preview-first.
- Follow [docs/DHIS2_SAFETY.md](docs/DHIS2_SAFETY.md) for DHIS2 work.
- Do not claim tests passed unless they were actually run.
- Avoid unrelated refactoring.

## Completion Report

After making changes, report:

- changed files
- tests and checks performed
- test results
- risks and limitations
- documentation updated
- recommended next step
