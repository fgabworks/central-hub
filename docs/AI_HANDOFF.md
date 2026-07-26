# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Curated OpenAI model catalog** for Prompting & Agent Center.

- Catalog in `hub/agent_center/openai_catalog.py` (Sol/Terra/Luna, 5.5/5.5-pro, 5.4 family)
- Accessible models = catalog ∩ `GET /v1/models` (optional `OPENAI_ALLOWED_MODELS`)
- Grouped selector: Recommended / Advanced / Balanced / Fast / Pro
- Mode defaults: Find→luna, Ask/Plan→terra, Review→sol (+ fallbacks); user override OK
- Reasoning-effort only when supported; Pro uses background + longer timeout
- Revalidate before each run; model ID stored in history/audit
- Prior: OpenAI Responses adapter, CLI agents, Calendar/Email, SQL, DHIS2 GET-only

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_openai_catalog tests.test_openai_agent tests.test_agent_center -v
python -m unittest discover -s tests -v
python app.py
```

1. Enable OpenAI in `.env`; open `/agents` → OpenAI API
2. Confirm only key-accessible curated models appear, grouped
3. Switch Find/Ask/Plan/Review → recommended model updates; override works
4. Pro model shows longer-run behavior; effort selector only on supported models

## Next task

Keep write paths off. Do not auto-feed mail/calendar to agents.
