# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Persistent Aira/Okarun assistant dock (2026-08-01)**

- VS Code-style right panel on all main pages (Aira=Personal, Okarun=Work)
- Desktop: main content grid-resizes beside the panel (no overlay); mobile: full-height drawer
- Open / close / pin / minimize / drag-resize; prefs stored per workspace (`assistant_dock:{workspace}`)
- Conversation + Output tabs, page-aware suggestions, context preview, agent/model selector, fixed prompt
- Lazy-loads providers only after open; bootstrap never probes adapters/connections
- Full Assistant Center pages (`/personal/aira`, `/work/okarun`) kept for advanced settings/history (dock skipped there)
- Read-only safeguards unchanged; no voice/TTS

Focused verification: `python -m unittest tests.test_assistant_dock -v`.

The AI Connections milestone below remains implemented and is retained for history.

**AI Connections — shared provider registry for isolated Aira and Okarun profiles**

- Aira: /personal/aira; Okarun: /work/okarun
- One orchestration engine with profile-isolated runs, prompts, conversations,
  summaries, context, settings, permissions, lookup, cancel, and retry
- Dynamic adapter/model selection; Ask/Find/Plan/Review; context preview;
  streaming, files, tool activity, usage, and redacted Audit
- Search-first, workspace-forced read-only tools and selected repository instructions
- No file/command/SQL/email/calendar/DHIS2/repository execution or writes

Focused verification: `python -m unittest tests.test_ai_connections tests.test_agent_center
tests.test_openai_agent tests.test_ai_assistant_center -v`.

Implemented providers: Codex app-server/browser-device auth, Claude Code browser auth,
Cursor browser auth, OpenAI API env key, and Grok/xAI env key. Model IDs are discovered
dynamically. Claude Code uses a provider-default sentinel because no supported headless
catalog command is documented. API-provider disconnect is Hub-local and does not mutate env.

**Find Missing UIDs UI** — selection + compact layout (retained)

- Select all visible / all filtered / clear; selection survives filter & pagination
- Sticky bulk bar; preview + typed confirm before local index update

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_assistant_dock -v
python app.py
```

1. Open Work dashboard → floating **Okarun** button → panel docks right; main content shrinks
2. Resize by dragging the left edge; close/minimize/pin; reload — prefs restore
3. Switch to Personal → **Aira** panel; Work prefs stay independent
4. Narrow viewport → panel becomes overlay drawer with backdrop
5. Full Assistant Center still at `/work/okarun` and `/personal/aira`

## Next task

Do **not** implement yet unless asked:

- Writing DHIS2-imported UIDs back into Live Processing’s `AI_UID_INDEX.csv` automatically
- Auto-killing processes that occupy fixed ports
- Free-form terminal / unrestricted shell
- Voice input / text-to-speech for the assistant dock

Keep DHIS2 writes off. Never preload mail/calendar; assistant lookup stays explicit,
read-only, minimal, and workspace-scoped.
