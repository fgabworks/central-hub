"""Flask routes for Calendar Center and Google Connections."""

from __future__ import annotations

from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for

from hub.audit import AuditStore
from hub.audit import actions as audit_actions
from hub.calendar.convert import convert_event_to_notebook
from hub.calendar.models import CALENDAR_VIEWS, normalize_calendar_view, normalize_workspace
from hub.calendar.service import CalendarService, CalendarServiceError
from hub.email.models import ACCOUNT_STATUS_LABELS, CALENDAR_SCOPES, GMAIL_SCOPES
from hub.email.service import EmailService, EmailServiceError
from hub.notebook import NotebookStore
from hub.notebook.workspace import apply_workspace_cookie, persist_workspace, read_workspace


def register_calendar_routes(app: Flask) -> None:
    def _calendar() -> CalendarService:
        return app.config["CALENDAR"]

    def _email() -> EmailService:
        return app.config["EMAIL"]

    def _audit() -> AuditStore:
        return app.config["AUDIT"]

    def _notebook() -> NotebookStore:
        return app.config["NOTEBOOK"]

    def _registry_options() -> list[dict[str, str]]:
        registry = app.config.get("REGISTRY")
        if not registry:
            return []
        return [
            {"id": repo.id, "label": repo.name or repo.id}
            for repo in registry.repositories
            if getattr(repo, "enabled", True)
        ]

    def _cal_endpoint(workspace: str) -> str:
        return (
            "personal_calendar"
            if normalize_workspace(workspace) == "personal"
            else "work_calendar"
        )

    def _render_calendar(workspace: str):
        ws = normalize_workspace(workspace)
        persist_workspace(_notebook().db, ws)
        service = _calendar()
        accounts = service.list_accounts(ws)
        calendar_accounts = [a for a in accounts if a.get("has_calendar")]
        account_id = (request.args.get("account") or "").strip()
        if not account_id and calendar_accounts:
            account_id = calendar_accounts[0]["id"]
        view = normalize_calendar_view(request.args.get("view"))
        q = (request.args.get("q") or "").strip()
        calendar_id = (request.args.get("calendar") or "").strip()
        date_from = (request.args.get("from") or "").strip()
        date_to = (request.args.get("to") or "").strip()
        anchor = (request.args.get("anchor") or "").strip() or None
        page_token = (request.args.get("page") or "").strip() or None
        tz = (request.args.get("tz") or "").strip() or "UTC"
        force = request.args.get("refresh") in {"1", "true", "yes"}
        error = None
        listing: dict[str, Any] | None = None
        selected = None
        if account_id:
            selected = next((a for a in accounts if a["id"] == account_id), None)
            if selected is None:
                error = "Selected account is not in this workspace."
            elif not selected.get("has_calendar"):
                error = "This account needs Calendar scopes — enable Calendar under Google Connections."
            else:
                try:
                    if force:
                        service.refresh_cache(account_id)
                    listing = service.list_events(
                        account_id,
                        view=view,
                        calendar_id=calendar_id,
                        q=q,
                        date_from=date_from,
                        date_to=date_to,
                        page_token=page_token,
                        time_zone=tz,
                        force_refresh=force,
                        anchor=anchor,
                    )
                except CalendarServiceError as exc:
                    error = str(exc)
        _audit().append(
            action=audit_actions.CALENDAR_VIEW,
            target=account_id or ws,
            detail=f"Calendar Center {ws} view={view}",
            ok=error is None,
            metadata={"workspace": ws, "view": view},
        )
        resp = app.make_response(
            render_template(
                "calendar/center.html",
                workspace=ws,
                oauth=_email().oauth_public(),
                accounts=accounts,
                calendar_accounts=calendar_accounts,
                selected_account=selected,
                selected_account_id=account_id,
                views=CALENDAR_VIEWS,
                view=view,
                q=q,
                calendar_id=calendar_id,
                date_from=date_from,
                date_to=date_to,
                anchor=anchor or "",
                tz=tz,
                listing=listing,
                error=error,
                status_labels=ACCOUNT_STATUS_LABELS,
                registry_repos=_registry_options(),
                calendar_base=url_for(_cal_endpoint(ws)),
                page_title="Calendar" if ws == "personal" else "Work Calendar",
            )
        )
        return apply_workspace_cookie(resp, ws)

    @app.get("/calendar")
    def calendar_redirect():
        ws = read_workspace(request, _notebook().db)
        return redirect(url_for(_cal_endpoint(ws)))

    @app.get("/personal/calendar")
    def personal_calendar():
        return _render_calendar("personal")

    @app.get("/work/calendar")
    def work_calendar():
        return _render_calendar("work")

    @app.get("/email/oauth/calendar/start")
    def email_oauth_calendar_start():
        """Incremental Calendar scope grant for an existing or new Google account."""
        workspace = normalize_workspace(request.args.get("workspace") or "work")
        account_id = (request.args.get("account") or "").strip() or None
        try:
            started = _calendar().start_calendar_oauth(
                workspace=workspace, account_id=account_id
            )
        except (CalendarServiceError, EmailServiceError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("google_connections"))
        _audit().append(
            action=audit_actions.CALENDAR_OAUTH_START,
            target=account_id or workspace,
            detail="Incremental Calendar OAuth start",
            ok=True,
            metadata={"workspace": workspace, "scopes": list(CALENDAR_SCOPES)},
        )
        return redirect(started["authorization_url"])

    @app.get("/system/google-connections")
    def google_connections():
        email = _email()
        accounts = email.store.list_accounts()
        # Ensure no secrets in template
        safe = []
        for acct in accounts:
            row = dict(acct)
            for key in list(row.keys()):
                if "token" in key.lower() and key != "token_stored":
                    row.pop(key, None)
            safe.append(row)
        _audit().append(
            action=audit_actions.GOOGLE_CONNECTIONS_VIEW,
            target="google-connections",
            detail=f"Accounts={len(safe)}",
            ok=True,
        )
        return render_template(
            "google_connections.html",
            accounts=safe,
            oauth=email.oauth_public(),
            status_labels=ACCOUNT_STATUS_LABELS,
            gmail_scopes=GMAIL_SCOPES,
            calendar_scopes=CALENDAR_SCOPES,
        )

    @app.get("/calendar/accounts/<account_id>/calendars/<path:calendar_id>/events/<path:event_id>")
    def calendar_event_detail(account_id: str, calendar_id: str, event_id: str):
        service = _calendar()
        force = request.args.get("refresh") in {"1", "true", "yes"}
        tz = (request.args.get("tz") or "").strip()
        try:
            data = service.get_event(
                account_id, calendar_id, event_id, force_refresh=force, time_zone=tz
            )
        except CalendarServiceError as exc:
            flash(str(exc), "error")
            acct = service.store.get_account(account_id)
            ws = (acct or {}).get("workspace") or "work"
            return redirect(url_for(_cal_endpoint(ws), account=account_id))
        acct = data["account"]
        ws = acct.get("workspace") or "work"
        _audit().append(
            action=audit_actions.CALENDAR_VIEW,
            target=event_id,
            detail="Event detail",
            ok=True,
            metadata={"account_id": account_id, "calendar_id": calendar_id},
        )
        resp = app.make_response(
            render_template(
                "calendar/event.html",
                workspace=ws,
                account=acct,
                event=data["event"],
                from_cache=data.get("from_cache"),
                registry_repos=_registry_options(),
                calendar_base=url_for(_cal_endpoint(ws)),
            )
        )
        return apply_workspace_cookie(resp, ws)

    @app.post("/calendar/accounts/<account_id>/refresh")
    def calendar_account_refresh(account_id: str):
        service = _calendar()
        acct = service.store.get_account(account_id)
        workspace = (acct or {}).get("workspace") or "work"
        try:
            service.refresh_cache(account_id)
        except CalendarServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for(_cal_endpoint(workspace), account=account_id))
        _audit().append(
            action=audit_actions.CALENDAR_REFRESH,
            target=account_id,
            detail="Manual calendar cache refresh",
            ok=True,
        )
        return redirect(
            url_for(
                _cal_endpoint(workspace),
                account=account_id,
                view=request.form.get("view") or "month",
                refresh=1,
            )
        )

    def _convert_action(
        account_id: str,
        calendar_id: str,
        event_id: str,
        *,
        note_type: str,
        link_repo: bool,
    ):
        service = _calendar()
        try:
            data = service.get_event(account_id, calendar_id, event_id)
        except CalendarServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for("calendar_redirect"))
        acct = data["account"]
        ws = acct.get("workspace") or "work"
        repo_id = ""
        repo_label = ""
        if link_repo or ws == "work":
            repo_id = (request.form.get("repository_id") or "").strip()
            if repo_id and ws == "work":
                for opt in _registry_options():
                    if opt["id"] == repo_id:
                        repo_label = opt["label"]
                        break
        if link_repo and ws != "work":
            flash("Repository linking is Work-only.", "error")
            return redirect(
                url_for(
                    "calendar_event_detail",
                    account_id=account_id,
                    calendar_id=calendar_id,
                    event_id=event_id,
                )
            )
        note = convert_event_to_notebook(
            _notebook(),
            event=data["event"],
            workspace=ws,
            account_email=acct.get("email") or "",
            note_type=note_type,
            repository_id=repo_id if (link_repo or repo_id) else "",
            repository_label=repo_label,
        )
        action = (
            audit_actions.CALENDAR_CONVERT_TASK
            if note_type == "task"
            else audit_actions.CALENDAR_CONVERT_NOTE
        )
        if link_repo:
            action = audit_actions.CALENDAR_LINK_REPO
        _audit().append(
            action=action,
            target=note.get("id"),
            detail=f"From Calendar event {event_id[:24]}",
            ok=True,
            metadata={
                "workspace": ws,
                "event_id": event_id,
                "calendar_id": calendar_id,
                "repository_id": repo_id or None,
            },
        )
        flash(f"Created {note_type} from calendar event.", "ok")
        return redirect(note.get("redirect") or url_for(_cal_endpoint(ws), account=account_id))

    @app.post(
        "/calendar/accounts/<account_id>/calendars/<path:calendar_id>/events/<path:event_id>/convert-note"
    )
    def calendar_convert_note(account_id: str, calendar_id: str, event_id: str):
        return _convert_action(
            account_id, calendar_id, event_id, note_type="note", link_repo=False
        )

    @app.post(
        "/calendar/accounts/<account_id>/calendars/<path:calendar_id>/events/<path:event_id>/convert-task"
    )
    def calendar_convert_task(account_id: str, calendar_id: str, event_id: str):
        return _convert_action(
            account_id, calendar_id, event_id, note_type="task", link_repo=False
        )

    @app.post(
        "/calendar/accounts/<account_id>/calendars/<path:calendar_id>/events/<path:event_id>/link-repo"
    )
    def calendar_link_repo(account_id: str, calendar_id: str, event_id: str):
        return _convert_action(
            account_id, calendar_id, event_id, note_type="note", link_repo=True
        )
