"""Flask routes for Email Center (shared Personal / Work)."""

from __future__ import annotations

import io
from typing import Any

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from hub.audit import AuditStore
from hub.audit import actions as audit_actions
from hub.email.convert import convert_message_to_notebook
from hub.email.models import ACCOUNT_STATUS_LABELS, MAILBOX_VIEWS, normalize_workspace
from hub.email.service import EmailService, EmailServiceError
from hub.notebook import NotebookStore
from hub.notebook.workspace import apply_workspace_cookie, persist_workspace


def _mailbox_view_counts(labels: list[dict[str, Any]]) -> dict[str, int | None]:
    """Map Gmail system labels to Inbox/Unread/Starred/Sent badge counts."""
    by_id = {str(lbl.get("id") or ""): lbl for lbl in labels}

    def _total(label_id: str) -> int | None:
        raw = by_id.get(label_id, {}).get("messages_total")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _unread(label_id: str) -> int | None:
        raw = by_id.get(label_id, {}).get("messages_unread")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    unread = _total("UNREAD")
    if unread is None:
        unread = _unread("INBOX")
    return {
        "inbox": _total("INBOX"),
        "unread": unread,
        "starred": _total("STARRED"),
        "sent": _total("SENT"),
    }


def register_email_routes(app: Flask) -> None:
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

    def _email_endpoint(workspace: str) -> str:
        return "personal_email" if normalize_workspace(workspace) == "personal" else "work_email"

    def _render_email_center(workspace: str):
        ws = normalize_workspace(workspace)
        persist_workspace(_notebook().db, ws)
        service = _email()
        audit = _audit()
        accounts = service.list_accounts(ws)
        account_id = (request.args.get("account") or "").strip()
        if not account_id and accounts:
            account_id = accounts[0]["id"]
        view = (request.args.get("view") or "inbox").strip().lower()
        q = (request.args.get("q") or "").strip()
        label = (request.args.get("label") or "").strip()
        page_token = (request.args.get("page") or "").strip() or None
        force = request.args.get("refresh") in {"1", "true", "yes"}
        selected_message_id = (request.args.get("selected") or "").strip()
        error = None
        listing: dict[str, Any] | None = None
        labels: list[dict[str, Any]] = []
        selected = None
        mailbox_counts: dict[str, int | None] = {
            "inbox": None,
            "unread": None,
            "starred": None,
            "sent": None,
        }
        if account_id:
            selected = next((a for a in accounts if a["id"] == account_id), None)
            if selected is None:
                error = "Selected account is not in this workspace."
            elif selected.get("status") in {"revoked", "unavailable", "needs_reauth"}:
                error = f"Account status: {ACCOUNT_STATUS_LABELS.get(selected.get('status'), selected.get('status'))}"
            else:
                try:
                    if force:
                        service.refresh_account_cache(account_id)
                    listing = service.list_messages(
                        account_id,
                        view=view,
                        q=q,
                        label=label,
                        page_token=page_token,
                        force_refresh=force,
                    )
                    labels = service.list_labels(account_id)
                    mailbox_counts = _mailbox_view_counts(labels)
                except EmailServiceError as exc:
                    error = str(exc)
        audit.append(
            action=audit_actions.EMAIL_VIEW,
            target=account_id or ws,
            detail=f"Email Center {ws} view={view}",
            ok=error is None,
            metadata={"workspace": ws, "view": view},
        )
        resp = app.make_response(
            render_template(
                "email/center.html",
                workspace=ws,
                oauth=service.oauth_public(),
                accounts=accounts,
                selected_account=selected,
                selected_account_id=account_id,
                selected_message_id=selected_message_id,
                views=MAILBOX_VIEWS,
                view=view,
                q=q,
                label=label,
                labels=labels,
                listing=listing,
                mailbox_counts=mailbox_counts,
                error=error,
                status_labels=ACCOUNT_STATUS_LABELS,
                registry_repos=_registry_options(),
                email_base=url_for(_email_endpoint(ws)),
            )
        )
        return apply_workspace_cookie(resp, ws)

    @app.get("/email")
    def email_redirect():
        notebook: NotebookStore = app.config["NOTEBOOK"]
        from hub.notebook.workspace import read_workspace

        ws = read_workspace(request, notebook.db)
        return redirect(url_for(_email_endpoint(ws)))

    @app.get("/personal/email")
    def personal_email():
        return _render_email_center("personal")

    @app.get("/work/email")
    def work_email():
        return _render_email_center("work")

    @app.get("/email/oauth/start")
    def email_oauth_start():
        service = _email()
        workspace = normalize_workspace(request.args.get("workspace") or "work")
        account_id = (request.args.get("account") or "").strip() or None
        try:
            started = service.start_oauth(workspace=workspace, account_id=account_id)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for(_email_endpoint(workspace)))
        _audit().append(
            action=audit_actions.EMAIL_OAUTH_START,
            target=account_id or workspace,
            detail=f"OAuth start workspace={workspace}",
            ok=True,
            metadata={"workspace": workspace},
        )
        return redirect(started["authorization_url"])

    @app.get("/email/oauth/callback")
    def email_oauth_callback():
        service = _email()
        err = (request.args.get("error") or "").strip()
        state = (request.args.get("state") or "").strip()
        code = (request.args.get("code") or "").strip()
        if err:
            flash(f"Google OAuth error: {err}", "error")
            return redirect(url_for("email_redirect"))
        try:
            account = service.complete_oauth(state=state, code=code)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            _audit().append(
                action=audit_actions.EMAIL_OAUTH_CONNECT,
                target="callback",
                detail=str(exc)[:200],
                ok=False,
            )
            # Prefer Connections page so the user can start a fresh Connect click.
            return redirect(url_for("google_connections"))
        ws = account.get("workspace") or "work"
        _audit().append(
            action=audit_actions.EMAIL_OAUTH_CONNECT,
            target=account.get("id"),
            detail=f"Connected Google account to {ws}",
            ok=True,
            metadata={
                "workspace": ws,
                "email": account.get("email"),
                "has_gmail": account.get("has_gmail"),
                "has_calendar": account.get("has_calendar"),
            },
        )
        flash(f"Connected {account.get('email') or 'Google account'} ({ws}).", "ok")
        if account.get("has_calendar"):
            return redirect(url_for("google_connections"))
        return redirect(
            url_for(_email_endpoint(ws), account=account.get("id"))
        )

    @app.post("/email/accounts/<account_id>/assign")
    def email_account_assign(account_id: str):
        service = _email()
        data = request.get_json(silent=True) or {}
        workspace = normalize_workspace(
            request.form.get("workspace") or data.get("workspace") or "work"
        )
        try:
            account = service.assign_workspace(account_id, workspace)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for("email_redirect"))
        _audit().append(
            action=audit_actions.EMAIL_ACCOUNT_ASSIGN,
            target=account_id,
            detail=f"Assigned account to {workspace}",
            ok=True,
            metadata={"workspace": workspace, "email": account.get("email")},
        )
        flash(f"Account assigned to {workspace}.", "ok")
        return redirect(url_for(_email_endpoint(workspace), account=account_id))

    @app.post("/email/accounts/<account_id>/disconnect")
    def email_account_disconnect(account_id: str):
        service = _email()
        revoke = (request.form.get("revoke") or "1") not in {"0", "false", "no"}
        acct = service.store.get_account(account_id)
        workspace = (acct or {}).get("workspace") or "work"
        try:
            result = service.disconnect(account_id, revoke=revoke)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for(_email_endpoint(workspace)))
        _audit().append(
            action=audit_actions.EMAIL_OAUTH_DISCONNECT,
            target=account_id,
            detail=f"Disconnected revoke={result.get('revoked')}",
            ok=True,
            metadata={"workspace": workspace},
        )
        flash("Account disconnected. Tokens removed locally.", "ok")
        return redirect(url_for(_email_endpoint(workspace)))

    @app.post("/email/accounts/<account_id>/refresh")
    def email_account_refresh(account_id: str):
        service = _email()
        acct = service.store.get_account(account_id)
        workspace = (acct or {}).get("workspace") or "work"
        try:
            service.refresh_account_cache(account_id)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for(_email_endpoint(workspace), account=account_id))
        _audit().append(
            action=audit_actions.EMAIL_REFRESH,
            target=account_id,
            detail="Manual cache refresh",
            ok=True,
        )
        return redirect(
            url_for(
                _email_endpoint(workspace),
                account=account_id,
                view=request.form.get("view") or "inbox",
                q=request.form.get("q") or None,
                label=request.form.get("label") or None,
                refresh=1,
            )
        )

    @app.get("/email/accounts/<account_id>/messages/<message_id>")
    def email_message_detail(account_id: str, message_id: str):
        service = _email()
        force = request.args.get("refresh") in {"1", "true", "yes"}
        try:
            data = service.get_message(account_id, message_id, force_refresh=force)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            acct = service.store.get_account(account_id)
            ws = (acct or {}).get("workspace") or "work"
            return redirect(url_for(_email_endpoint(ws), account=account_id))
        acct = data["account"]
        ws = acct.get("workspace") or "work"
        _audit().append(
            action=audit_actions.EMAIL_VIEW,
            target=message_id,
            detail="Message detail",
            ok=True,
            metadata={"account_id": account_id, "workspace": ws},
        )
        resp = app.make_response(
            render_template(
                "email/message.html",
                workspace=ws,
                account=acct,
                message=data["message"],
                from_cache=data.get("from_cache"),
                registry_repos=_registry_options(),
                email_base=url_for(_email_endpoint(ws)),
                status_labels=ACCOUNT_STATUS_LABELS,
            )
        )
        return apply_workspace_cookie(resp, ws)

    @app.get("/email/accounts/<account_id>/threads/<thread_id>")
    def email_thread_detail(account_id: str, thread_id: str):
        service = _email()
        try:
            data = service.get_thread(account_id, thread_id)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            acct = service.store.get_account(account_id)
            ws = (acct or {}).get("workspace") or "work"
            return redirect(url_for(_email_endpoint(ws), account=account_id))
        acct = data["account"]
        ws = acct.get("workspace") or "work"
        _audit().append(
            action=audit_actions.EMAIL_VIEW,
            target=thread_id,
            detail="Thread detail",
            ok=True,
            metadata={"account_id": account_id, "workspace": ws},
        )
        resp = app.make_response(
            render_template(
                "email/thread.html",
                workspace=ws,
                account=acct,
                thread_id=thread_id,
                messages=data["messages"],
                registry_repos=_registry_options(),
                email_base=url_for(_email_endpoint(ws)),
            )
        )
        return apply_workspace_cookie(resp, ws)

    @app.get("/email/accounts/<account_id>/messages/<message_id>/attachments/<attachment_id>")
    def email_attachment_download(account_id: str, message_id: str, attachment_id: str):
        service = _email()
        try:
            content, filename, mime = service.download_attachment(
                account_id, message_id, attachment_id
            )
        except EmailServiceError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        _audit().append(
            action=audit_actions.EMAIL_ATTACHMENT_DOWNLOAD,
            target=message_id,
            detail=f"Attachment download name={filename[:80]}",
            ok=True,
            metadata={"account_id": account_id, "size": len(content)},
        )
        return send_file(
            io.BytesIO(content),
            mimetype=mime,
            as_attachment=True,
            download_name=filename,
        )

    def _convert_action(account_id: str, message_id: str, *, note_type: str, link_repo: bool):
        service = _email()
        try:
            data = service.get_message(account_id, message_id)
        except EmailServiceError as exc:
            flash(str(exc), "error")
            return redirect(url_for("email_redirect"))
        acct = data["account"]
        ws = acct.get("workspace") or "work"
        repo_id = ""
        repo_label = ""
        if link_repo or note_type:
            repo_id = (request.form.get("repository_id") or "").strip()
            if repo_id and ws == "work":
                for opt in _registry_options():
                    if opt["id"] == repo_id:
                        repo_label = opt["label"]
                        break
        note = convert_message_to_notebook(
            _notebook(),
            message=data["message"],
            workspace=ws,
            account_email=acct.get("email") or "",
            note_type=note_type,
            repository_id=repo_id if (link_repo or repo_id) else "",
            repository_label=repo_label,
        )
        action = audit_actions.EMAIL_CONVERT_TASK if note_type == "task" else audit_actions.EMAIL_CONVERT_NOTE
        if link_repo:
            action = audit_actions.EMAIL_LINK_REPO
        _audit().append(
            action=action,
            target=note.get("id"),
            detail=f"From Gmail message {message_id[:16]}",
            ok=True,
            metadata={
                "workspace": ws,
                "message_id": message_id,
                "repository_id": repo_id or None,
            },
        )
        flash(f"Created {note_type} from email.", "ok")
        return redirect(note.get("redirect") or url_for(_email_endpoint(ws), account=account_id))

    @app.post("/email/accounts/<account_id>/messages/<message_id>/convert-note")
    def email_convert_note(account_id: str, message_id: str):
        return _convert_action(account_id, message_id, note_type="note", link_repo=False)

    @app.post("/email/accounts/<account_id>/messages/<message_id>/convert-task")
    def email_convert_task(account_id: str, message_id: str):
        return _convert_action(account_id, message_id, note_type="task", link_repo=False)

    @app.post("/email/accounts/<account_id>/messages/<message_id>/link-repo")
    def email_link_repo(account_id: str, message_id: str):
        return _convert_action(account_id, message_id, note_type="note", link_repo=True)
