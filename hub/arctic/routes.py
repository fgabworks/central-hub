"""Flask routes for ARCTIC (Personal profile + document registry)."""

from __future__ import annotations

from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from hub.arctic.context import build_arctic_ai_context
from hub.arctic.models import PRIMARY_ROLE_LABELS, PRIMARY_ROLES, SMART_COLLECTIONS
from hub.arctic.service import ArcticError, ArcticService
from hub.audit import AuditStore
from hub.audit import actions as audit_actions
from hub.notebook.workspace import apply_workspace_cookie, persist_workspace


def register_arctic_routes(app: Flask) -> None:
    def _arctic() -> ArcticService:
        return app.config["ARCTIC"]

    def _audit() -> AuditStore:
        return app.config["AUDIT"]

    def _force_personal() -> None:
        notebook = app.config.get("NOTEBOOK")
        if notebook is not None:
            persist_workspace(notebook.db, "personal")

    def _tabs(active: str) -> str:
        items = [
            ("arctic_dashboard", "Dashboard", "arctic_dashboard"),
            ("arctic_profile", "Profile", "arctic_profile"),
            ("arctic_files", "Files", "arctic_files"),
        ]
        parts: list[str] = []
        for endpoint, label, key in items:
            cls = "section-tab is-active" if active == key else "section-tab"
            parts.append(
                f'<a class="{cls}" href="{url_for(endpoint)}">{label}</a>'
            )
        return "".join(parts)

    def _render(page: str, **ctx: Any):
        _force_personal()
        html = render_template(
            page,
            arctic_tabs_active=ctx.pop("arctic_tabs_active", "arctic_dashboard"),
            role_labels=PRIMARY_ROLE_LABELS,
            primary_roles=PRIMARY_ROLES,
            collections=SMART_COLLECTIONS,
            **ctx,
        )
        resp = app.make_response(html)
        return apply_workspace_cookie(resp, "personal")

    @app.get("/personal/arctic")
    def arctic_dashboard():
        data = _arctic().dashboard()
        selected_id = (request.args.get("doc") or "").strip()
        selected = _arctic().get_document(selected_id) if selected_id else None
        if selected:
            _arctic().touch_accessed(selected["id"])
        return _render(
            "arctic/dashboard.html",
            arctic_tabs_active="arctic_dashboard",
            dash=data,
            selected=selected,
        )

    @app.route("/personal/arctic/profile", methods=["GET", "POST"])
    def arctic_profile():
        svc = _arctic()
        if request.method == "POST":
            skills_raw = request.form.get("skills") or ""
            links_raw = request.form.get("links") or ""
            skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
            links = []
            for line in links_raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "|" in line:
                    label, url = line.split("|", 1)
                    links.append({"label": label.strip(), "url": url.strip()})
                else:
                    links.append({"label": line, "url": line})
            try:
                svc.update_profile(
                    {
                        "display_name": request.form.get("display_name"),
                        "headline": request.form.get("headline"),
                        "email": request.form.get("email"),
                        "phone": request.form.get("phone"),
                        "location": request.form.get("location"),
                        "summary": request.form.get("summary"),
                        "skills": skills,
                        "links": links,
                    }
                )
                _audit().append(
                    action=audit_actions.ARCTIC_PROFILE_UPDATED,
                    target="arctic_profile",
                    detail="ARCTIC profile updated",
                    metadata={"section": "ARCTIC", "workspace": "personal"},
                )
                flash("ARCTIC profile saved.", "ok")
            except ArcticError as exc:
                flash(str(exc), "error")
            return redirect(url_for("arctic_profile"))
        profile = svc.get_profile()
        links_text = "\n".join(
            f"{item.get('label', '')}|{item.get('url', '')}".strip("|")
            if isinstance(item, dict)
            else str(item)
            for item in (profile.get("links") or [])
        )
        return _render(
            "arctic/profile.html",
            arctic_tabs_active="arctic_profile",
            profile=profile,
            skills_text=", ".join(profile.get("skills") or []),
            links_text=links_text,
            latest_cv=svc.latest_cv(),
        )

    @app.route("/personal/arctic/files", methods=["GET", "POST"])
    def arctic_files():
        svc = _arctic()
        if request.method == "POST":
            action = (request.form.get("action") or "register").strip()
            try:
                if action == "register":
                    doc = svc.register_document(
                        {
                            "title": request.form.get("title"),
                            "source_type": request.form.get("source_type") or "local",
                            "source_ref": request.form.get("source_ref"),
                            "primary_role": request.form.get("primary_role"),
                            "tags": request.form.get("tags"),
                            "notes": request.form.get("notes"),
                            "is_favorite": request.form.get("is_favorite") in {"1", "on", "true"},
                        }
                    )
                    flash(f"Registered: {doc.get('title')}", "ok")
                    return redirect(url_for("arctic_files", doc=doc["id"]))
                doc_id = (request.form.get("doc_id") or "").strip()
                if action == "set_primary":
                    svc.set_primary_role(doc_id, request.form.get("primary_role") or "")
                    flash("Primary role updated.", "ok")
                elif action == "favorite":
                    svc.update_document(
                        doc_id, {"is_favorite": request.form.get("is_favorite") in {"1", "on", "true"}}
                    )
                    flash("Favorite updated.", "ok")
                elif action == "delete":
                    svc.delete_document(doc_id)
                    flash("Registry entry removed (file left in place).", "ok")
                    return redirect(url_for("arctic_files"))
                elif action == "update":
                    svc.update_document(
                        doc_id,
                        {
                            "title": request.form.get("title"),
                            "primary_role": request.form.get("primary_role"),
                            "tags": request.form.get("tags"),
                            "notes": request.form.get("notes"),
                            "is_favorite": request.form.get("is_favorite") in {"1", "on", "true"},
                        },
                    )
                    flash("Document metadata updated.", "ok")
                return redirect(url_for("arctic_files", doc=doc_id))
            except ArcticError as exc:
                flash(str(exc), "error")
                return redirect(url_for("arctic_files"))

        collection = (request.args.get("collection") or "").strip()
        q = (request.args.get("q") or "").strip()
        selected_id = (request.args.get("doc") or "").strip()
        documents = svc.list_documents(collection=collection or None, q=q or None)
        selected = svc.get_document(selected_id) if selected_id else None
        if selected:
            svc.touch_accessed(selected["id"])
        return _render(
            "arctic/files.html",
            arctic_tabs_active="arctic_files",
            documents=documents,
            selected=selected,
            collection=collection,
            q=q,
            sources=svc.list_sources(),
            career_pack=svc.career_pack() if collection == "career_pack" else None,
        )

    # --- JSON APIs (Personal only) ---

    @app.get("/api/arctic/dashboard")
    def api_arctic_dashboard():
        _force_personal()
        return jsonify({"ok": True, "dashboard": _arctic().dashboard()})

    @app.get("/api/arctic/documents")
    def api_arctic_documents():
        _force_personal()
        docs = _arctic().list_documents(
            primary_role=request.args.get("role"),
            source_type=request.args.get("source"),
            tag=request.args.get("tag"),
            collection=request.args.get("collection"),
            q=request.args.get("q"),
            favorite_only=request.args.get("favorite") in {"1", "true"},
        )
        return jsonify({"ok": True, "documents": docs, "workspace": "personal"})

    @app.get("/api/arctic/documents/<doc_id>")
    def api_arctic_document(doc_id: str):
        _force_personal()
        doc = _arctic().get_document(doc_id)
        if not doc:
            return jsonify({"ok": False, "error": "not_found"}), 404
        _arctic().touch_accessed(doc_id)
        return jsonify({"ok": True, "document": doc})

    @app.get("/api/arctic/latest-cv")
    def api_arctic_latest_cv():
        _force_personal()
        cv = _arctic().latest_cv()
        return jsonify({"ok": True, "latest_cv": cv, "resolved_via": "primary_cv"})

    @app.get("/api/arctic/career-pack")
    def api_arctic_career_pack():
        _force_personal()
        return jsonify({"ok": True, "career_pack": _arctic().career_pack()})

    @app.post("/api/arctic/ai-context")
    def api_arctic_ai_context():
        """Explicit ARCTIC → AiriX context pack (Personal only; no auto RI)."""
        _force_personal()
        body = request.get_json(silent=True) or {}
        ids = body.get("document_ids") or request.args.getlist("id")
        if isinstance(ids, str):
            ids = [ids]
        pack = build_arctic_ai_context(
            _arctic().store,
            document_ids=list(ids or []),
            include_profile=bool(body.get("include_profile")),
            include_latest_cv=bool(body.get("include_latest_cv")),
            workspace="personal",
        )
        return jsonify({"ok": True, "context": pack})

    # Expose tab builder for templates via context if needed later.
    app.jinja_env.globals["arctic_section_tabs"] = _tabs
