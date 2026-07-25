# AI_HANDOFF.md — Session Handoff

Read first: [AGENTS.md](../AGENTS.md) · [AI_REFERENCE.md](../AI_REFERENCE.md).

## Current milestone

**Calendar Center (Google Calendar readonly)** — shared `hub/calendar/` reusing Email
Center Google accounts, encrypted tokens, and Personal/Work assignment.

- Incremental OAuth: `calendar.calendarlist.readonly` + `calendar.events.readonly`
- Nav: Personal → Calendar · Work → Work Calendar · System → Google Connections
- Views: month / week / day / agenda / upcoming; search; date filters; pagination; TZ
- Event detail: attendees, location, description, Meet link, source calendar, recurrence
- Convert to Note / Create Task / Work-only Link Repository
- Personal Dashboard: Upcoming Personal Events
- Read-only only — no create/update/delete/RSVP, no push, no agent access
- Prior: Email Center, Personal/Work workspaces, SQL Workspace, GET-only DHIS2

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest tests.test_calendar_center tests.test_email_center -v
python -m unittest discover -s tests -v
python app.py
```

1. Enable Calendar API on the Google Cloud OAuth client; grant Calendar from Google Connections
2. Personal / Work calendar pages load; charcoal/crimson (Work) preserved
3. Personal Dashboard shows upcoming events panel
4. Email, SQL, repos, jobs, health, audit, DHIS2 still load

## Next task

Optional: set `DATA_SCRIPT_PATH` / `REPORT_TEMPLATE_PATH` after manual clone.
Keep DHIS2 / Gmail / Calendar writes off. Do not auto-feed mail or calendar to agents.
