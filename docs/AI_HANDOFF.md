# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**AI Connections — shared provider registry for isolated Aira and Okarun profiles (2026-08-01)**

- Aira: /personal/aira; Okarun: /work/okarun
- One orchestration engine with profile-isolated runs, prompts, conversations,
  summaries, context, settings, permissions, lookup, cancel, and retry
- Dynamic adapter/model selection; Ask/Find/Plan/Review; context preview;
  streaming, files, tool activity, usage, and redacted Audit
- Search-first, workspace-forced read-only tools and selected repository instructions
- No file/command/SQL/email/calendar/DHIS2/repository execution or writes

Focused verification: `python -m pytest tests/test_ai_connections.py tests/test_agent_center.py
tests/test_openai_agent.py tests/test_ai_assistant_center.py -q`.

Implemented providers: Codex app-server/browser-device auth, Claude Code browser auth,
Cursor browser auth, OpenAI API env key, and Grok/xAI env key. Model IDs are discovered
dynamically. Claude Code uses a provider-default sentinel because no supported headless
catalog command is documented. API-provider disconnect is Hub-local and does not mutate env.

The Find Missing UIDs milestone below remains implemented and is retained for history.

**Find Missing UIDs UI** — selection + compact layout

- Select all visible / all filtered / clear; selection survives filter & pagination
- Sticky bulk bar (`Selected: N | Add to Local Index | Clear`); Add disabled until selection
- Preview + typed confirm still required before local index update
- Compact Scan toolbar; clear steps 1–4; scrollable results; collapsible Scan Summary
- Readable type labels (Data Element, Program Indicator, …)

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_dhis2_find_missing tests.test_dhis2_find_missing_ui tests.test_dhis2_uid_mapping tests.test_uid_index_admin tests.test_dhis2_enrichment tests.test_dhis2_discovery -v
node tests/dhis2_find_missing_selection.test.js
python app.py
```

1. DHIS2 → Find Missing UIDs → Scan → select visible / all filtered → Add to Local Index (preview)
2. Confirm with `ADD MISSING UIDS TO INDEX`
3. Change page/filters — selection count should persist
4. Scan button is compact in the toolbar; Scan Summary collapses on the side

## Next task

Do **not** implement yet unless asked:

- Writing DHIS2-imported UIDs back into Live Processing’s `AI_UID_INDEX.csv` automatically
- Auto-killing processes that occupy fixed ports
- Free-form terminal / unrestricted shell

Keep DHIS2 writes off. Never preload mail/calendar; assistant lookup stays explicit,
read-only, minimal, and workspace-scoped.
