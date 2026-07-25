"""Central Hub — Flask entrypoint (registry, health, DHIS2 read-only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for, Response

from hub import __version__
from hub.adapters import AdapterManager
from hub.audit import AuditStore
from hub.audit import actions as audit_actions
from hub.jobs.auth import current_actor, login_owner
from hub.jobs.db import JobDatabase
from hub.jobs.files import FileSafetyError, list_artifacts, resolve_download, save_upload
from hub.jobs.store import JobStore, progress_payload
from hub.jobs.worker import JobWorker
from hub.dhis2 import ALLOWED_RESOURCES, Dhis2Client, Dhis2Error
from hub.dhis2.builders import get_builder
from hub.dhis2.catalog import CatalogStore, filter_types, run_discovery
from hub.dhis2.drafts import DraftStore
from hub.dhis2.instance_profiles import (
    build_dhis2_settings_for_instance,
    default_instance_selection,
    list_dhis2_instance_profiles,
)
from hub.dhis2.instance_store import Dhis2InstanceStore
from hub.dhis2.type_config import load_metadata_builder_config
from hub.dhis2.uid_index import UidIndex
from hub.dhis2.workspace import catalog_schema_summary, workspace_stats, workspace_types
from hub.dhis2.uid_mapping import (
    MappingIndexStore,
    classify_against_dhis2,
    classify_index_records,
    extract_relationships,
    load_sources_config,
    merge_preview,
    scan_all_sources,
)
from hub.dhis2.uid_mapping.admin import (
    CONFIRM_APPLY,
    CONFIRM_RESTORE,
    apply_with_confirmation,
    compare_versions,
    enrich_controlled_preview,
    list_versions,
    restore_with_confirmation,
)
from hub.dhis2.enrichment import CONFIRM_APPLY as ENRICH_CONFIRM, EnrichmentStore, EnrichmentWorkflow
from hub.dhis2.enrichment.derive import derive_answer_type
from hub.dhis2.uid_mapping.audit_profile import answer_kind, build_audit_profile
from hub.dhis2.uid_mapping.compare import resolve_plural
from hub.dhis2.uid_mapping.reverse_trace import logical_storage_hint, reverse_trace_links
from hub.dhis2.uid_mapping.scan import parse_csv_text, parse_json_text
from hub.dhis2.uid_mapping.search import facet_values, filter_records
from hub.dhis2.redact import redact_mapping
from hub.notebook import (
    NOTE_TYPE_LABELS,
    NOTE_TYPES,
    PRIORITIES,
    PRIORITY_LABELS,
    REPO_ROLE_LABELS,
    REPO_ROLES,
    SCOPE_LABELS,
    STATUS_LABELS,
    STATUSES,
    WORKSPACES,
    NotebookStore,
    QuickNotepadStore,
    normalize_scope,
    normalize_workspace,
    render_markdown,
)
from hub.notebook.dashboard import dashboard_work_queue, open_tasks_severity
from hub.notebook.workspace import (
    apply_workspace_cookie,
    dashboard_endpoint,
    notebook_endpoint,
    persist_workspace,
    read_workspace,
)
from hub.sql_workspace import (
    SqlExecutor,
    SqlWorkspaceStore,
    load_connection_registry,
)
from hub.sql_workspace.demo import ensure_demo_database
from hub.sql_workspace.safety import SqlSafetyError, extract_named_params, format_sql
from hub.registry import load_registry
from hub.registry.git_util import default_search_roots, find_local_checkout, slugify_repo_id
from hub.registry.loader import RegistryError
from hub.registry.status import ui_repo_status
from hub.registry.store import RegistryStore, build_entry_from_form
from hub.settings import ROOT_DIR, load_settings

settings = load_settings()

# UI-only demo fixtures for the dashboard mockup (not live data).
_DHIS2_TOOLS = [
    {"label": "Instance Details", "icon": "◎", "endpoint": "dhis2_instance"},
    {"label": "Metadata Catalog", "icon": "▦", "endpoint": "dhis2_catalog"},
    {"label": "Authorities", "icon": "☰", "endpoint": "dhis2_authorities"},
    {"label": "Metadata Lookup", "icon": "⌕", "endpoint": "dhis2_lookup"},
    {"label": "UID & Mapping Explorer", "icon": "⇄", "endpoint": "dhis2_uid_explorer"},
    {"label": "UID Index Management", "icon": "⧉", "endpoint": "dhis2_uid_index_manage"},
    {"label": "Metadata Enrichment", "icon": "⊕", "endpoint": "dhis2_enrichment"},
    {"label": "Metadata Builder", "icon": "✎", "endpoint": "dhis2_metadata_builder"},
    {"label": "Run Discovery", "icon": "↻", "endpoint": "dhis2_discover"},
]


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(ROOT_DIR / "templates"),
        static_folder=str(ROOT_DIR / "static"),
    )
    app.config["SETTINGS"] = settings
    app.secret_key = settings.secret_key

    try:
        registry_path = _resolve_config_path(settings.repositories_config)
        registry = load_registry(registry_path)
        registry_error: str | None = None
    except RegistryError as exc:
        registry_path = _resolve_config_path(settings.repositories_config)
        registry = None
        registry_error = str(exc)

    app.config["REGISTRY_CONFIG_PATH"] = registry_path
    app.config["REGISTRY"] = registry
    app.config["REGISTRY_ERROR"] = registry_error
    app.config["ADAPTERS"] = (
        AdapterManager(
            registry,
            default_timeout=settings.request_timeout_seconds,
            cache_ttl_seconds=settings.health_cache_ttl_seconds,
        )
        if registry is not None
        else None
    )
    app.config["AUDIT"] = AuditStore(settings.audit_log_path)
    instance_store = Dhis2InstanceStore()
    app.config["DHIS2_INSTANCE_STORE"] = instance_store
    profiles = list_dhis2_instance_profiles()
    available_ids = [p["id"] for p in profiles if p.get("available")]
    env_default = (os.getenv("DHIS2_ENVIRONMENT") or "").strip().lower()
    selected_instance = default_instance_selection(
        available_ids=available_ids,
        persisted=instance_store.get_instance(),
        env_default=env_default,
    )
    app.config["DHIS2_INSTANCE"] = selected_instance
    dhis2_settings = build_dhis2_settings_for_instance(selected_instance)
    app.config["DHIS2"] = Dhis2Client(dhis2_settings)
    app.config["DHIS2_LAST_STATUS"] = None
    app.config["DHIS2_BUILDER_CONFIG"] = load_metadata_builder_config()
    app.config["DHIS2_DRAFTS"] = DraftStore()
    app.config["DHIS2_CATALOG"] = CatalogStore()
    app.config["DHIS2_MAPPING_INDEX"] = MappingIndexStore()
    app.config["DHIS2_UID_INDEX"] = UidIndex(
        app.config["DHIS2"], mapping_store=app.config["DHIS2_MAPPING_INDEX"]
    )
    app.config["DHIS2_MAPPING_PREVIEW"] = None
    enrichment_store = EnrichmentStore()
    app.config["DHIS2_ENRICHMENT_STORE"] = enrichment_store
    app.config["DHIS2_ENRICHMENT"] = EnrichmentWorkflow(
        app.config["DHIS2"],
        mapping_store=app.config["DHIS2_MAPPING_INDEX"],
        enrichment_store=enrichment_store,
    )
    app.config["NOTEBOOK"] = NotebookStore()
    ensure_demo_database()
    sql_store = SqlWorkspaceStore()
    app.config["SQL_WS_STORE"] = sql_store
    app.config["SQL_WS_CONNECTIONS"] = load_connection_registry()
    app.config["SQL_WS_EXECUTOR"] = SqlExecutor(
        sql_store,
        max_rows=int(os.environ.get("SQL_WS_MAX_ROWS") or 1000),
        statement_timeout_ms=int(os.environ.get("SQL_WS_STATEMENT_TIMEOUT_MS") or 15000),
    )

    def _apply_dhis2_client(client: Dhis2Client, *, instance: str | None) -> None:
        previous = app.config.get("DHIS2")
        if previous is not None and previous is not client:
            try:
                previous.close()
            except Exception:  # noqa: BLE001
                pass
        app.config["DHIS2"] = client
        app.config["DHIS2_INSTANCE"] = instance
        uid_index: UidIndex = app.config["DHIS2_UID_INDEX"]
        uid_index.client = client
        enrichment: EnrichmentWorkflow = app.config["DHIS2_ENRICHMENT"]
        enrichment.client = client

    job_store = JobStore(JobDatabase(settings.database_path))
    app.config["JOB_STORE"] = job_store

    def _job_audit(**kwargs):
        app.config["AUDIT"].append(**kwargs)

    max_concurrent = registry.defaults.max_concurrent_jobs if registry else 2
    worker = JobWorker(
        job_store,
        registry_provider=lambda: app.config.get("REGISTRY"),
        max_concurrent=max_concurrent,
        audit=_job_audit,
    )
    worker.start()
    app.config["JOB_WORKER"] = worker

    @app.context_processor
    def inject_globals():
        dhis2_client: Dhis2Client = app.config["DHIS2"]
        last_status = app.config.get("DHIS2_LAST_STATUS")
        cfg = dhis2_client.public_config()
        instance = app.config.get("DHIS2_INSTANCE")
        if not instance:
            topbar_dhis2 = {"label": "DHIS2: Select instance", "class": "badge-disabled"}
        elif not cfg["configured"]:
            topbar_dhis2 = {"label": f"DHIS2 {str(instance).title()}: Incomplete", "class": "badge-disabled"}
        elif last_status and last_status.get("ok"):
            topbar_dhis2 = {
                "label": f"DHIS2 {str(instance).title()}: Online",
                "class": "badge-healthy",
            }
        elif last_status and last_status.get("ok") is False:
            topbar_dhis2 = {
                "label": f"DHIS2 {str(instance).title()}: Offline",
                "class": "badge-offline",
            }
        else:
            topbar_dhis2 = {
                "label": f"DHIS2 {str(instance).title()}: Configured",
                "class": "badge-warning",
            }

        notebook: NotebookStore = app.config["NOTEBOOK"]
        workspace = read_workspace(request, notebook.db)
        ep = request.endpoint or ""
        if ep in {
            "work_dashboard",
            "work_notebook",
            "repositories",
            "repository_new",
            "repository_edit",
            "repository_detail",
            "sql_workspace",
            "dhis2",
            "jobs",
            "job_detail",
            "health",
        } or (ep.startswith("dhis2") if ep else False) or (ep.startswith("repository") if ep else False) or (ep.startswith("sql_") if ep else False):
            workspace = "work"
        elif ep in {
            "personal_dashboard",
            "personal_notebook",
            "personal_tasks",
        }:
            workspace = "personal"

        personal_nav = [
            {
                "endpoint": "personal_dashboard",
                "label": "Personal Dashboard",
                "icon": "⌂",
                "active_prefix": None,
            },
            {
                "endpoint": "personal_notebook",
                "label": "Personal Notebook",
                "icon": "✎",
                "active_prefix": "personal_notebook",
            },
            {
                "endpoint": "personal_dashboard",
                "label": "Quick Notepad",
                "icon": "▦",
                "active_prefix": None,
                "anchor": "quick-notepad",
            },
            {
                "endpoint": "personal_tasks",
                "label": "Personal Tasks",
                "icon": "☑",
                "active_prefix": None,
            },
        ]
        work_nav = [
            {
                "endpoint": "work_dashboard",
                "label": "Work Dashboard",
                "icon": "⌂",
                "active_prefix": None,
            },
            {
                "endpoint": "repositories",
                "label": "Repositories",
                "icon": "▣",
                "active_prefix": "repository",
            },
            {
                "endpoint": "work_notebook",
                "label": "Work Notebook",
                "icon": "✎",
                "active_prefix": "work_notebook",
            },
            {
                "endpoint": "work_dashboard",
                "label": "Quick Notepad",
                "icon": "▤",
                "active_prefix": None,
                "anchor": "quick-notepad",
            },
            {
                "endpoint": "sql_workspace",
                "label": "SQL Workspace",
                "icon": "▦",
                "active_prefix": "sql_workspace",
            },
            {
                "endpoint": "dhis2",
                "label": "DHIS2",
                "icon": "⬡",
                "badge": None,
                "active_prefix": "dhis2",
            },
            {
                "endpoint": "jobs",
                "label": "Jobs",
                "icon": "▶",
                "active_prefix": None,
            },
            {
                "endpoint": "health",
                "label": "Health",
                "icon": "♡",
                "active_prefix": None,
            },
        ]
        system_nav = [
            {
                "endpoint": "audit",
                "label": "Audit",
                "icon": "☰",
                "active_prefix": None,
            },
            {
                "endpoint": "settings_page",
                "label": "Settings",
                "icon": "⚙",
                "active_prefix": None,
            },
        ]
        nav_sections = [
            {
                "id": "personal",
                "label": "Personal",
                "entries": personal_nav if workspace == "personal" else [],
            },
            {
                "id": "work",
                "label": "Work",
                "entries": work_nav if workspace == "work" else [],
            },
            {"id": "system", "label": "System", "entries": system_nav},
        ]
        nav_items = [item for section in nav_sections for item in section["entries"]]

        work_actions = [
            {"label": "Add Repository", "endpoint": "repository_new", "available": True},
            {"label": "Run Health Check", "endpoint": "health", "available": True},
            {"label": "DHIS2 Maintenance", "endpoint": "dhis2", "available": True},
            {
                "label": "Create Demo Job",
                "endpoint": "jobs",
                "available": False,
                "phase": "Phase 2",
            },
            {"label": "View Logs", "endpoint": "audit", "available": True},
        ]
        personal_actions = [
            {"label": "New Personal Note", "endpoint": "personal_notebook", "available": True},
            {"label": "Personal Tasks", "endpoint": "personal_tasks", "available": True},
            {"label": "View Logs", "endpoint": "audit", "available": True},
        ]

        return {
            "app_name": settings.app_name,
            "env_profile": settings.env_profile,
            "hub_version": __version__,
            "registry_error": app.config.get("REGISTRY_ERROR"),
            "dhis2_tools": _DHIS2_TOOLS,
            "topbar_dhis2": topbar_dhis2,
            "actor": current_actor(),
            "owner_token_configured": settings.owner_token_configured,
            "workspace": workspace,
            "workspaces": WORKSPACES,
            "workspace_labels": SCOPE_LABELS,
            "nav_sections": nav_sections,
            "nav_items": nav_items,
            "quick_actions": personal_actions if workspace == "personal" else work_actions,
        }

    def _set_workspace_and_redirect(workspace: str, next_url: str | None = None):
        notebook: NotebookStore = app.config["NOTEBOOK"]
        value = persist_workspace(notebook.db, workspace)
        target = next_url or url_for(dashboard_endpoint(value))
        resp = redirect(target)
        return apply_workspace_cookie(resp, value)

    @app.route("/workspace/<workspace_name>", methods=["GET", "POST"])
    def switch_workspace(workspace_name: str):
        value = normalize_workspace(workspace_name)
        next_url = (request.values.get("next") or "").strip() or None
        if next_url and not next_url.startswith("/"):
            next_url = None
        return _set_workspace_and_redirect(value, next_url)

    def _render_dashboard(*, scope: str):
        audit: AuditStore = app.config["AUDIT"]
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        registry = app.config["REGISTRY"]
        notebook: NotebookStore = app.config["NOTEBOOK"]
        scope_n = normalize_scope(scope)
        queue_tab = (request.args.get("queue") or "open").strip().lower()
        registered_ids = (
            {repo.id for repo in registry.repositories} if registry else set()
        )
        work_queue = dashboard_work_queue(
            notebook,
            tab=queue_tab,
            limit=5,
            registered_ids=registered_ids,
            scope=scope_n,
        )
        task_stats = work_queue["stats"]
        events = audit.list_recent(limit=8)
        activity_rows = [
            {
                "time": (ev.get("timestamp") or "")[11:19],
                "action": ev.get("action"),
                "tone": "job-ok" if ev.get("ok") else "job-run",
                "detail": ev.get("detail") or "",
                "actor": ev.get("actor") or "owner",
            }
            for ev in events
        ]
        notebook_ep = notebook_endpoint(scope_n)
        dash_ep = dashboard_endpoint(scope_n)
        show_notepad = True
        notepad = QuickNotepadStore(notebook.db, scope=scope_n).get()

        if scope_n == "personal":
            cards = [
                {
                    "kind": "open_tasks",
                    "label": "Personal Tasks",
                    "value": str(task_stats["open"]),
                    "badge": task_stats["open"],
                    "severity": open_tasks_severity(task_stats),
                    "metrics": {
                        "open": task_stats["open"],
                        "overdue": task_stats["overdue"],
                        "due_this_week": task_stats["due_this_week"],
                        "blocked": task_stats["blocked"],
                    },
                    "href": url_for("personal_tasks"),
                    "link_label": "View all →",
                },
                {
                    "kind": "default",
                    "label": "Personal Notes",
                    "value": str(notebook.status_counts(scope="personal")["all"]),
                    "sub": "Scoped to Personal",
                    "icon": "✎",
                    "href": url_for("personal_notebook"),
                    "link_label": "Open notebook →",
                },
                {
                    "kind": "default",
                    "label": "Quick Notepad",
                    "value": "Ready",
                    "sub": "Local scratchpad",
                    "icon": "▦",
                    "href": url_for("personal_dashboard") + "#quick-notepad",
                    "link_label": "Open panel →",
                },
                {
                    "kind": "default",
                    "label": "Audit Events",
                    "value": str(len(events)),
                    "sub": "Recent JSONL activity",
                    "icon": "🛡",
                    "href": url_for("audit"),
                    "link_label": "View all →",
                },
            ]
            live_repos: list = []
            dhis2_tools_local: list = []
        else:
            health_results = adapters.check_all(enabled_only=False) if adapters else []
            live_repos = _repos_from_health(registry, health_results)
            healthy = sum(1 for item in health_results if item.get("ok"))
            enabled = sum(1 for item in health_results if item.get("enabled"))
            dhis2_cfg = app.config["DHIS2"].public_config()
            last_dhis2 = app.config.get("DHIS2_LAST_STATUS")
            dhis2_instance = app.config.get("DHIS2_INSTANCE")
            dhis2_sub = "Read-only"
            if dhis2_instance:
                dhis2_sub = f"{str(dhis2_instance).title()} · Read-only"
            cards = [
                {
                    "kind": "default",
                    "label": "Repositories",
                    "value": str(len(registry.repositories) if registry else 0),
                    "sub": f"{enabled} enabled · {healthy} healthy",
                    "icon": "▣",
                    "href": url_for("repositories"),
                    "link_label": "View all →",
                },
                {
                    "kind": "open_tasks",
                    "label": "Open Tasks",
                    "value": str(task_stats["open"]),
                    "badge": task_stats["open"],
                    "severity": open_tasks_severity(task_stats),
                    "metrics": {
                        "open": task_stats["open"],
                        "overdue": task_stats["overdue"],
                        "due_this_week": task_stats["due_this_week"],
                        "blocked": task_stats["blocked"],
                    },
                    "href": url_for(notebook_ep, status="open"),
                    "link_label": "View all →",
                },
                {
                    "kind": "default",
                    "label": "DHIS2",
                    "value": (
                        "Online"
                        if last_dhis2 and last_dhis2.get("ok")
                        else ("Configured" if dhis2_cfg.get("configured") else "Off")
                    ),
                    "sub": dhis2_sub,
                    "icon": "◎",
                    "href": url_for("dhis2"),
                    "link_label": "View details →",
                },
                {
                    "kind": "default",
                    "label": "Audit Events",
                    "value": str(len(events)),
                    "sub": "Recent JSONL activity",
                    "icon": "🛡",
                    "href": url_for("audit"),
                    "link_label": "View all →",
                },
            ]
            dhis2_tools_local = _DHIS2_TOOLS

        persist_workspace(notebook.db, scope_n)
        html = render_template(
            "dashboard.html",
            last_updated="live",
            summary_cards=cards,
            live_repos=live_repos,
            work_queue=work_queue,
            activity_rows=activity_rows,
            notepad=notepad,
            show_notepad=show_notepad,
            show_repos=scope_n == "work",
            show_dhis2=scope_n == "work",
            note_scope=scope_n,
            scope_label=SCOPE_LABELS.get(scope_n, scope_n),
            notebook_endpoint=notebook_ep,
            dashboard_endpoint=dash_ep,
            queue_title=(
                "Personal Task Queue" if scope_n == "personal" else "Notebook Work Queue"
            ),
            page_title=(
                "Personal Dashboard" if scope_n == "personal" else "Work Dashboard"
            ),
            page_sub=(
                "Personal notes, tasks, and Quick Notepad."
                if scope_n == "personal"
                else "Repositories, work notebook tasks, DHIS2 and system status."
            ),
            dhis2_tools=dhis2_tools_local,
        )
        resp = app.make_response(html)
        return apply_workspace_cookie(resp, scope_n)

    @app.get("/")
    def dashboard():
        notebook: NotebookStore = app.config["NOTEBOOK"]
        workspace = read_workspace(request, notebook.db)
        args = request.args.to_dict(flat=True)
        return redirect(url_for(dashboard_endpoint(workspace), **args))

    @app.get("/personal")
    def personal_dashboard():
        return _render_dashboard(scope="personal")

    @app.get("/work")
    def work_dashboard():
        return _render_dashboard(scope="work")

    def _reload_registry() -> None:
        path = app.config["REGISTRY_CONFIG_PATH"]
        try:
            registry = load_registry(path)
            app.config["REGISTRY"] = registry
            app.config["REGISTRY_ERROR"] = None
            adapters = AdapterManager(
                registry,
                default_timeout=settings.request_timeout_seconds,
                cache_ttl_seconds=settings.health_cache_ttl_seconds,
            )
            app.config["ADAPTERS"] = adapters
            adapters.invalidate_health_cache()
        except RegistryError as exc:
            app.config["REGISTRY_ERROR"] = str(exc)
            raise

    def _registry_store() -> RegistryStore:
        return RegistryStore(app.config["REGISTRY_CONFIG_PATH"])

    def _repo_form_from_request() -> dict:
        return {
            "id": (request.form.get("id") or "").strip(),
            "name": (request.form.get("name") or "").strip(),
            "type": (request.form.get("type") or "command").strip().lower(),
            "enabled": request.form.get("enabled") in {"1", "on", "true"},
            "git_url": (request.form.get("git_url") or "").strip(),
            "local_path": (request.form.get("local_path") or "").strip(),
            "base_url": (request.form.get("base_url") or "").strip(),
            "description": (request.form.get("description") or "").strip(),
        }

    def _maybe_reuse_checkout(form: dict) -> tuple[dict, str | None]:
        """If git_url set and local_path empty, reuse a matching existing checkout."""
        notice = None
        if form.get("type") != "command":
            return form, notice
        git_url = form.get("git_url") or ""
        if not git_url or form.get("local_path"):
            return form, notice
        roots = default_search_roots(
            live_processing_path=os.getenv("LIVE_PROCESSING_PATH") or None
        )
        found = find_local_checkout(git_url, roots)
        if found is not None:
            form = {**form, "local_path": str(found)}
            notice = f"Reused existing checkout at {found}"
        return form, notice

    @app.get("/repositories")
    def repositories():
        registry = app.config["REGISTRY"]
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        health_results = adapters.check_all(enabled_only=False) if adapters else []
        by_id = {item.get("repository_id"): item for item in health_results}
        rows = []
        for repo in registry.repositories if registry else []:
            health = by_id.get(repo.id) or {}
            status = ui_repo_status(repo, health)
            connection = repo.base_url or repo.local_path or "—"
            rows.append(
                {
                    "id": repo.id,
                    "name": repo.name,
                    "description": repo.description,
                    "type": repo.type,
                    "enabled": repo.enabled,
                    "status": status,
                    "git_url": repo.git_url,
                    "connection": connection,
                    "capability_count": len(repo.capabilities),
                }
            )
        return render_template(
            "repositories.html",
            rows=rows,
            flash=request.args.get("notice"),
            error=request.args.get("error") or app.config.get("REGISTRY_ERROR"),
            defaults=registry.defaults if registry else None,
        )

    @app.route("/repositories/new", methods=["GET", "POST"])
    def repository_new():
        form = {
            "id": "",
            "name": "",
            "type": "command",
            "enabled": True,
            "git_url": "",
            "local_path": "",
            "base_url": "",
            "description": "",
        }
        error = None
        notice = None
        if request.method == "POST":
            form = _repo_form_from_request()
            form, notice = _maybe_reuse_checkout(form)
            try:
                entry = build_entry_from_form(
                    name=form["name"],
                    repo_type=form["type"],
                    enabled=form["enabled"],
                    git_url=form["git_url"] or None,
                    local_path=form["local_path"] or None,
                    base_url=form["base_url"] or None,
                    description=form["description"],
                    repo_id=form["id"] or None,
                )
                store = _registry_store()
                saved = store.add(entry)
                _reload_registry()
                audit: AuditStore = app.config["AUDIT"]
                adapters: AdapterManager | None = app.config["ADAPTERS"]
                health = None
                if adapters is not None:
                    registry = app.config["REGISTRY"]
                    repo = registry.get(saved["id"]) if registry else None
                    if repo is not None:
                        health = adapters.check_repository(repo)
                audit.append(
                    action=audit_actions.REGISTRY_ADD,
                    target=saved["id"],
                    detail=f"Added repository {saved['name']} ({saved['type']})",
                    ok=True,
                    metadata={
                        "status": (health or {}).get("status"),
                        "git_url": saved.get("git_url"),
                    },
                )
                msg = f"Added {saved['name']}."
                if notice:
                    msg = f"{msg} {notice}."
                if health:
                    msg = f"{msg} Health: {ui_repo_status(repo, health)}."
                return redirect(url_for("repositories", notice=msg))
            except RegistryError as exc:
                error = str(exc)
                if not form.get("id") and form.get("name"):
                    form["id"] = slugify_repo_id(form["name"])
        return render_template(
            "repository_form.html",
            title="Add Repository",
            subtitle="Register a Git URL and/or local path. Hub will not clone or pull.",
            mode="new",
            form=form,
            error=error,
            notice=notice,
            submit_label="Add repository",
        )

    @app.route("/repositories/<repo_id>/edit", methods=["GET", "POST"])
    def repository_edit(repo_id: str):
        store = _registry_store()
        raw = store.get_raw(repo_id)
        if raw is None:
            abort(404)
        form = {
            "id": repo_id,
            "name": str(raw.get("name") or ""),
            "type": str(raw.get("type") or "command"),
            "enabled": bool(raw.get("enabled", True)),
            "git_url": str(raw.get("git_url") or ""),
            "local_path": str(raw.get("local_path") or ""),
            "base_url": str(raw.get("base_url") or ""),
            "description": str(raw.get("description") or ""),
        }
        error = None
        notice = None
        if request.method == "POST":
            form = _repo_form_from_request()
            form["id"] = repo_id
            form, notice = _maybe_reuse_checkout(form)
            try:
                updates = {
                    "name": form["name"],
                    "type": form["type"],
                    "enabled": form["enabled"],
                    "git_url": form["git_url"] or None,
                    "local_path": form["local_path"] or None,
                    "working_directory": form["local_path"] or None,
                    "base_url": form["base_url"] or None,
                    "description": form["description"],
                }
                # Rebuild health_check defaults when type/path change.
                rebuilt = build_entry_from_form(
                    name=form["name"],
                    repo_type=form["type"],
                    enabled=form["enabled"],
                    git_url=form["git_url"] or None,
                    local_path=form["local_path"] or None,
                    base_url=form["base_url"] or None,
                    description=form["description"],
                    repo_id=repo_id,
                )
                updates["health_check"] = rebuilt.get("health_check")
                store.update(repo_id, updates)
                _reload_registry()
                audit: AuditStore = app.config["AUDIT"]
                adapters: AdapterManager | None = app.config["ADAPTERS"]
                registry = app.config["REGISTRY"]
                repo = registry.get(repo_id) if registry else None
                health = adapters.check_repository(repo) if adapters and repo else None
                audit.append(
                    action=audit_actions.REGISTRY_UPDATE,
                    target=repo_id,
                    detail=f"Updated repository {form['name']}",
                    ok=True,
                    metadata={"status": (health or {}).get("status")},
                )
                msg = f"Updated {form['name']}."
                if notice:
                    msg = f"{msg} {notice}."
                if health and repo:
                    msg = f"{msg} Health: {ui_repo_status(repo, health)}."
                return redirect(url_for("repositories", notice=msg))
            except RegistryError as exc:
                error = str(exc)
        return render_template(
            "repository_form.html",
            title=f"Edit {form['name'] or repo_id}",
            subtitle="Change connection details. Capabilities are preserved.",
            mode="edit",
            form=form,
            error=error,
            notice=notice,
            submit_label="Save changes",
        )

    @app.post("/repositories/<repo_id>/disable")
    def repository_disable(repo_id: str):
        try:
            _registry_store().set_enabled(repo_id, False)
            _reload_registry()
            app.config["AUDIT"].append(
                action=audit_actions.REGISTRY_DISABLE,
                target=repo_id,
                detail="Disabled repository",
                ok=True,
            )
            return redirect(url_for("repositories", notice=f"Disabled {repo_id}."))
        except RegistryError as exc:
            return redirect(url_for("repositories", error=str(exc)))

    @app.post("/repositories/<repo_id>/enable")
    def repository_enable(repo_id: str):
        try:
            _registry_store().set_enabled(repo_id, True)
            _reload_registry()
            app.config["AUDIT"].append(
                action=audit_actions.REGISTRY_ENABLE,
                target=repo_id,
                detail="Enabled repository",
                ok=True,
            )
            return redirect(url_for("repositories", notice=f"Enabled {repo_id}."))
        except RegistryError as exc:
            return redirect(url_for("repositories", error=str(exc)))

    @app.get("/repositories/<repo_id>")
    def repository_detail(repo_id: str):
        registry = app.config["REGISTRY"]
        if registry is None:
            abort(503)
        repo = registry.get(repo_id)
        if repo is None:
            abort(404)
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        health = adapters.check_repository(repo) if adapters else None
        status = ui_repo_status(repo, health)
        return render_template(
            "repository_detail.html",
            repository=repo,
            health=health,
            status=status,
        )

    @app.get("/health")
    def health():
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        results = (
            adapters.check_all(enabled_only=False, force=True) if adapters else []
        )
        healthy = sum(1 for item in results if item.get("ok"))
        disabled = sum(
            1
            for item in results
            if (not item.get("enabled")) or item.get("status") == "skipped"
        )
        offline = max(len(results) - healthy - disabled, 0)
        return render_template(
            "health.html",
            results=results,
            healthy_count=healthy,
            offline_count=offline,
            disabled_count=disabled,
            total_count=len(results),
        )

    @app.route("/jobs", methods=["GET", "POST"])
    def jobs():
        store: JobStore = app.config["JOB_STORE"]
        worker: JobWorker = app.config["JOB_WORKER"]
        audit: AuditStore = app.config["AUDIT"]
        registry = app.config["REGISTRY"]
        flash_error = None
        flash_notice = None
        actor = current_actor()

        if request.method == "POST":
            if actor != "owner":
                flash_error = "Owner authentication required to submit jobs."
            elif registry is None:
                flash_error = app.config["REGISTRY_ERROR"] or "Registry unavailable."
            else:
                try:
                    repo_id = (request.form.get("repository_id") or "").strip()
                    cap_raw = (request.form.get("capability_id") or "").strip()
                    if "::" in cap_raw:
                        repo_id, capability_id = cap_raw.split("::", 1)
                    else:
                        capability_id = cap_raw
                    dry_run = request.form.get("dry_run") == "1"
                    confirmed = request.form.get("confirm") == "1"
                    repo = registry.get(repo_id)
                    if repo is None or not repo.enabled:
                        raise ValueError(f"Repository unavailable: {repo_id}")
                    capability = next((c for c in repo.capabilities if c.id == capability_id), None)
                    if capability is None:
                        raise ValueError(f"Unknown capability: {capability_id}")
                    require_confirm = bool(registry.defaults.require_explicit_apply) and not dry_run
                    if require_confirm and not confirmed:
                        raise ValueError(
                            "Confirm checkbox required for non-dry-run jobs "
                            "(defaults.require_explicit_apply=true)."
                        )
                    if store.count_active() >= registry.defaults.max_concurrent_jobs * 5:
                        raise ValueError("Too many queued/active jobs — wait for capacity.")
                    job = store.create(
                        repository_id=repo_id,
                        capability_id=capability_id,
                        dry_run=dry_run,
                        confirmed=confirmed,
                        actor=actor,
                        metadata={"source": "ui"},
                    )
                    upload = request.files.get("input_file")
                    if upload and upload.filename:
                        saved = save_upload(Path(job["input_path"]), upload)
                        audit.append(
                            action=audit_actions.UPLOAD_INPUT,
                            target=job["id"],
                            detail=f"Uploaded {saved['filename']}",
                            ok=True,
                        )
                    audit.append(
                        action=audit_actions.SUBMIT_JOB,
                        target=job["id"],
                        detail=f"Submitted {repo_id}/{capability_id} dry_run={dry_run}",
                        ok=True,
                        metadata={"confirmed": confirmed},
                    )
                    worker.kick()
                    flash_notice = f"Job {job['id']} queued."
                except (ValueError, FileSafetyError) as exc:
                    flash_error = str(exc)

        repos = [r for r in (registry.repositories if registry else []) if r.enabled and r.capabilities]
        return render_template(
            "jobs.html",
            jobs=store.list_recent(limit=50),
            repositories=repos,
            flash_error=flash_error,
            flash_notice=flash_notice,
            actor=actor,
            max_concurrent=registry.defaults.max_concurrent_jobs if registry else 2,
            require_explicit_apply=registry.defaults.require_explicit_apply if registry else True,
            active_count=store.count_active(),
        )

    @app.route("/jobs/<job_id>", methods=["GET", "POST"])
    def job_detail(job_id: str):
        store: JobStore = app.config["JOB_STORE"]
        worker: JobWorker = app.config["JOB_WORKER"]
        audit: AuditStore = app.config["AUDIT"]
        job = store.get(job_id)
        if job is None:
            abort(404)
        flash_error = None
        flash_notice = None
        actor = current_actor()

        if request.method == "POST":
            action = request.form.get("action") or ""
            if actor != "owner":
                flash_error = "Owner authentication required."
            elif action == "cancel":
                job = store.request_cancel(job_id) or job
                flash_notice = "Cancel requested."
            elif action == "pause":
                job = store.request_pause(job_id) or job
                flash_notice = "Pause requested."
            elif action == "resume":
                job = store.resume(job_id) or job
                worker.kick()
                flash_notice = "Job re-queued."
            elif action == "upload":
                try:
                    saved = save_upload(Path(job["input_path"]), request.files.get("input_file"))
                    audit.append(
                        action=audit_actions.UPLOAD_INPUT,
                        target=job_id,
                        detail=f"Uploaded {saved['filename']}",
                        ok=True,
                    )
                    flash_notice = f"Uploaded {saved['filename']}"
                except FileSafetyError as exc:
                    flash_error = str(exc)
            else:
                flash_error = f"Unknown action: {action}"
            job = store.get(job_id) or job

        log_text = ""
        if job.get("log_path") and Path(job["log_path"]).is_file():
            log_text = Path(job["log_path"]).read_text(encoding="utf-8")[-20000:]
        artifacts = list_artifacts(Path(job["result_path"])) if job.get("result_path") else []
        return render_template(
            "job_detail.html",
            job=job,
            log_text=log_text,
            artifacts=artifacts,
            flash_error=flash_error,
            flash_notice=flash_notice,
            actor=actor,
        )

    @app.get("/jobs/<job_id>/download/<path:name>")
    def job_download(job_id: str, name: str):
        store: JobStore = app.config["JOB_STORE"]
        audit: AuditStore = app.config["AUDIT"]
        job = store.get(job_id)
        if job is None:
            abort(404)
        try:
            path = resolve_download(Path(job["result_path"]), name)
        except FileSafetyError:
            abort(404)
        audit.append(
            action=audit_actions.DOWNLOAD_RESULT,
            target=f"{job_id}/{name}",
            detail="Downloaded job artifact",
            ok=True,
        )
        return send_file(path, as_attachment=True)

    @app.get("/api/jobs")
    def api_jobs():
        store: JobStore = app.config["JOB_STORE"]
        return jsonify({"ok": True, "jobs": [progress_payload(j) for j in store.list_recent(limit=50)]})

    @app.get("/api/jobs/<job_id>")
    def api_job_detail(job_id: str):
        store: JobStore = app.config["JOB_STORE"]
        job = store.get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "job": progress_payload(job)})

    @app.post("/api/jobs")
    def api_jobs_submit():
        if current_actor() != "owner":
            return jsonify({"ok": False, "error": "owner required"}), 403
        store: JobStore = app.config["JOB_STORE"]
        worker: JobWorker = app.config["JOB_WORKER"]
        audit: AuditStore = app.config["AUDIT"]
        registry = app.config["REGISTRY"]
        if registry is None:
            return jsonify({"ok": False, "error": app.config["REGISTRY_ERROR"]}), 503
        data = request.get_json(silent=True) or {}
        repo_id = str(data.get("repository_id") or "")
        capability_id = str(data.get("capability_id") or "")
        dry_run = bool(data.get("dry_run", True))
        confirmed = bool(data.get("confirm", False))
        repo = registry.get(repo_id)
        if repo is None or not repo.enabled:
            return jsonify({"ok": False, "error": "repository unavailable"}), 400
        if not any(c.id == capability_id for c in repo.capabilities):
            return jsonify({"ok": False, "error": "unknown capability"}), 400
        if registry.defaults.require_explicit_apply and not dry_run and not confirmed:
            return jsonify({"ok": False, "error": "confirm required for apply"}), 400
        job = store.create(
            repository_id=repo_id,
            capability_id=capability_id,
            dry_run=dry_run,
            confirmed=confirmed,
            actor=current_actor(),
            metadata={"source": "api"},
        )
        audit.append(
            action=audit_actions.SUBMIT_JOB,
            target=job["id"],
            detail=f"API submit {repo_id}/{capability_id}",
            ok=True,
        )
        worker.kick()
        return jsonify({"ok": True, "job": progress_payload(job)}), 201

    def _notebook_registry_options():
        registry = app.config.get("REGISTRY")
        if registry is None:
            return []
        return [
            {
                "id": repo.id,
                "name": repo.name,
                "enabled": bool(repo.enabled),
                "available": bool(repo.enabled),
            }
            for repo in registry.repositories
        ]

    def _parse_notebook_form(form):
        repo_ids = form.getlist("repo_id")
        repo_roles = form.getlist("repo_role")
        repo_labels = form.getlist("repo_label")
        repositories = []
        for idx, rid in enumerate(repo_ids):
            rid = (rid or "").strip()
            if not rid:
                continue
            label = ""
            if idx < len(repo_labels):
                label = (repo_labels[idx] or "").strip()
            if not label:
                for opt in _notebook_registry_options():
                    if opt["id"] == rid:
                        label = opt["name"]
                        break
            role = repo_roles[idx] if idx < len(repo_roles) else "related"
            repositories.append(
                {
                    "repository_id": rid,
                    "repository_label": label or rid,
                    "role": role,
                }
            )

        check_texts = form.getlist("check_text")
        check_done = set(form.getlist("check_done"))
        checklist = []
        for idx, text in enumerate(check_texts):
            checklist.append(
                {
                    "text": text,
                    "done": str(idx) in check_done or f"on-{idx}" in check_done,
                }
            )
        # Also accept check_done as parallel "1"/"0" list if present
        if form.getlist("check_done_flag"):
            flags = form.getlist("check_done_flag")
            checklist = [
                {"text": t, "done": (flags[i] if i < len(flags) else "0") in {"1", "on", "true"}}
                for i, t in enumerate(check_texts)
            ]

        link_labels = form.getlist("link_label")
        link_urls = form.getlist("link_url")
        links = []
        for idx, url in enumerate(link_urls):
            links.append(
                {
                    "url": url,
                    "label": link_labels[idx] if idx < len(link_labels) else url,
                }
            )
        return repositories, checklist, links

    def _render_notebook(*, scope: str):
        store: NotebookStore = app.config["NOTEBOOK"]
        audit: AuditStore = app.config["AUDIT"]
        scope_n = normalize_scope(scope)
        flash = None
        error = None
        selected_id = (request.values.get("note") or "").strip()
        nb_ep = notebook_endpoint(scope_n)

        if request.method == "POST":
            action = (request.form.get("action") or "").strip()
            actor = current_actor()
            if action == "new":
                repo_id = (request.form.get("new_repo") or "").strip()
                label = ""
                if scope_n == "work":
                    for opt in _notebook_registry_options():
                        if opt["id"] == repo_id:
                            label = opt["name"]
                            break
                else:
                    repo_id = ""
                note_type = (request.form.get("new_type") or "note").strip()
                note = store.create(
                    title="Untitled note",
                    actor=actor,
                    repository_id=repo_id,
                    repository_label=label,
                    scope=scope_n,
                    note_type=note_type if scope_n == "personal" else "note",
                )
                audit.append(
                    action=audit_actions.NOTEBOOK_CREATE,
                    target=note["id"],
                    detail=f"Created {scope_n} notebook note",
                    ok=True,
                )
                return redirect(url_for(nb_ep, note=note["id"]))
            note_id = (request.form.get("note_id") or "").strip()
            if action == "save" and note_id:
                existing = store.get(note_id)
                if existing and normalize_scope(existing.get("scope")) != scope_n:
                    error = "Note belongs to a different workspace."
                else:
                    repositories, checklist, links = _parse_notebook_form(request.form)
                    if scope_n == "personal":
                        repositories = []
                    check_texts = request.form.getlist("check_text")
                    check_flags = request.form.getlist("check_done_flag")
                    if check_texts:
                        checklist = [
                            {
                                "text": t,
                                "done": (check_flags[i] if i < len(check_flags) else "0")
                                in {"1", "on", "true"},
                            }
                            for i, t in enumerate(check_texts)
                        ]
                    saved = store.save(
                        note_id,
                        title=request.form.get("title") or "",
                        body_md=request.form.get("body_md") or "",
                        note_type=request.form.get("note_type") or "note",
                        status=request.form.get("status") or "inbox",
                        priority=request.form.get("priority") or "medium",
                        due_date=request.form.get("due_date") or None,
                        tags=request.form.get("tags") or "",
                        repositories=repositories,
                        checklist=checklist,
                        links=links,
                        pinned=request.form.get("pinned") in {"1", "on", "true"},
                        actor=actor,
                        scope=scope_n,
                    )
                    if saved:
                        audit.append(
                            action=audit_actions.NOTEBOOK_SAVE,
                            target=note_id,
                            detail=f"Saved {scope_n} notebook note",
                            ok=True,
                        )
                        flash = "Note saved."
                        selected_id = note_id
                    else:
                        error = "Note not found."
            elif action == "archive" and note_id:
                existing = store.get(note_id)
                if existing and normalize_scope(existing.get("scope")) != scope_n:
                    error = "Note belongs to a different workspace."
                elif store.archive(note_id, actor=actor):
                    audit.append(
                        action=audit_actions.NOTEBOOK_ARCHIVE,
                        target=note_id,
                        detail="Archived notebook note",
                        ok=True,
                    )
                    flash = "Note archived."
                    selected_id = note_id
                else:
                    error = "Note not found."
            elif action == "restore" and note_id:
                existing = store.get(note_id)
                if existing and normalize_scope(existing.get("scope")) != scope_n:
                    error = "Note belongs to a different workspace."
                elif store.restore(note_id, actor=actor):
                    audit.append(
                        action=audit_actions.NOTEBOOK_RESTORE,
                        target=note_id,
                        detail="Restored notebook note",
                        ok=True,
                    )
                    flash = "Note restored."
                    selected_id = note_id
                else:
                    error = "Note not found."
            elif action == "delete" and note_id:
                title = ""
                existing = store.get(note_id)
                if existing and normalize_scope(existing.get("scope")) != scope_n:
                    error = "Note belongs to a different workspace."
                else:
                    if existing:
                        title = existing.get("title") or note_id
                    if store.delete(note_id, actor=actor):
                        audit.append(
                            action=audit_actions.NOTEBOOK_DELETE,
                            target=note_id,
                            detail=f"Deleted notebook note: {title}",
                            ok=True,
                        )
                        flash = "Note deleted."
                        selected_id = ""
                    else:
                        error = "Note not found."
            elif action:
                error = f"Unknown action: {action}"

        status = (request.args.get("status") or "all").strip().lower()
        repository_id = (request.args.get("repo") or "").strip()
        if scope_n == "personal":
            repository_id = ""
        note_type = (request.args.get("type") or "").strip()
        priority = (request.args.get("priority") or "").strip()
        tag = (request.args.get("tag") or "").strip()
        q = (request.args.get("q") or "").strip()

        notes = store.search(
            status=status,
            repository_id=repository_id,
            note_type=note_type,
            priority=priority,
            tag=tag,
            q=q,
            scope=scope_n,
        )
        selected = store.get(selected_id) if selected_id else None
        if selected and normalize_scope(selected.get("scope")) != scope_n:
            selected = None
            selected_id = ""
        if selected is None and notes:
            selected = store.get(notes[0]["id"])
            selected_id = selected["id"] if selected else ""

        preview_html = render_markdown((selected or {}).get("body_md") or "")
        counts = store.status_counts(scope=scope_n)
        show_notepad = True
        notepad = QuickNotepadStore(store.db, scope=scope_n).get()
        audit.append(
            action=audit_actions.NOTEBOOK_VIEW,
            target=selected_id or "list",
            detail=f"Notebook view scope={scope_n} status={status} matched={len(notes)}",
            ok=True,
        )
        persist_workspace(store.db, scope_n)
        html = render_template(
            "notebook.html",
            notes=notes,
            selected=selected,
            selected_id=selected_id,
            counts=counts,
            status=status,
            filters={
                "repo": repository_id,
                "type": note_type,
                "priority": priority,
                "tag": tag,
                "q": q,
            },
            registry_repos=_notebook_registry_options() if scope_n == "work" else [],
            all_tags=store.list_tags(scope=scope_n),
            statuses=STATUSES,
            status_labels=STATUS_LABELS,
            note_types=NOTE_TYPES,
            note_type_labels=NOTE_TYPE_LABELS,
            priorities=PRIORITIES,
            priority_labels=PRIORITY_LABELS,
            repo_roles=REPO_ROLES,
            repo_role_labels=REPO_ROLE_LABELS,
            preview_html=preview_html,
            notepad=notepad,
            show_notepad=show_notepad,
            note_scope=scope_n,
            scope_label=SCOPE_LABELS.get(scope_n, scope_n),
            notebook_endpoint=nb_ep,
            allow_repositories=scope_n == "work",
            page_title=(
                "Personal Notebook" if scope_n == "personal" else "Work Notebook"
            ),
            page_sub=(
                "Personal notes and tasks — no repository required."
                if scope_n == "personal"
                else "Local notes linked to registry repositories. Survives missing repos."
            ),
            flash=flash,
            error=error,
        )
        resp = app.make_response(html)
        return apply_workspace_cookie(resp, scope_n)

    @app.route("/notebook", methods=["GET", "POST"])
    def notebook():
        """Backward-compatible entry: redirect GET; handle POST in scoped notebook."""
        store: NotebookStore = app.config["NOTEBOOK"]
        note_id = (request.values.get("note") or "").strip()
        if note_id:
            existing = store.get(note_id)
            if existing:
                scope_n = normalize_scope(existing.get("scope"))
                if request.method == "POST":
                    return _render_notebook(scope=scope_n)
                args = {
                    k: v
                    for k, v in request.args.to_dict(flat=True).items()
                    if k != "note"
                }
                return redirect(url_for(notebook_endpoint(scope_n), note=note_id, **args))
        workspace = read_workspace(request, store.db)
        if request.method == "POST":
            return _render_notebook(scope=workspace)
        args = request.args.to_dict(flat=True)
        return redirect(url_for(notebook_endpoint(workspace), **args))

    @app.route("/personal/notebook", methods=["GET", "POST"])
    def personal_notebook():
        return _render_notebook(scope="personal")

    @app.route("/work/notebook", methods=["GET", "POST"])
    def work_notebook():
        return _render_notebook(scope="work")

    @app.get("/personal/tasks")
    def personal_tasks():
        store: NotebookStore = app.config["NOTEBOOK"]
        registered_ids: set[str] = set()
        queue = dashboard_work_queue(
            store,
            tab=(request.args.get("queue") or "open").strip().lower(),
            limit=50,
            registered_ids=registered_ids,
            scope="personal",
        )
        # Prefer task-typed items but keep other open personal notes visible.
        task_notes = [
            n for n in queue["notes"] if str(n.get("note_type") or "") == "task"
        ]
        other_notes = [
            n for n in queue["notes"] if str(n.get("note_type") or "") != "task"
        ]
        persist_workspace(store.db, "personal")
        html = render_template(
            "personal_tasks.html",
            work_queue=queue,
            task_notes=task_notes,
            other_notes=other_notes,
            notebook_endpoint="personal_notebook",
            notepad=QuickNotepadStore(store.db, scope="personal").get(),
            note_scope="personal",
        )
        resp = app.make_response(html)
        return apply_workspace_cookie(resp, "personal")

    @app.get("/notebook/<note_id>/export")
    def notebook_export(note_id: str):
        store: NotebookStore = app.config["NOTEBOOK"]
        audit: AuditStore = app.config["AUDIT"]
        payload = store.export_payload(note_id)
        if not payload:
            abort(404)
        audit.append(
            action=audit_actions.NOTEBOOK_EXPORT,
            target=note_id,
            detail="Exported notebook note JSON",
            ok=True,
        )
        body = json.dumps(payload, indent=2, ensure_ascii=True)
        return Response(
            body,
            mimetype="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="note-{note_id[:12]}.json"'
            },
        )

    @app.post("/api/notebook/preview")
    def api_notebook_preview():
        data = request.get_json(silent=True) or {}
        html_out = render_markdown(str(data.get("markdown") or ""))
        return jsonify({"ok": True, "html": html_out})

    def _notepad_scope_from_request() -> str:
        data = request.get_json(silent=True) or {}
        raw = (
            request.args.get("scope")
            or data.get("scope")
            or request.values.get("scope")
            or ""
        )
        return normalize_scope(str(raw), default="personal")

    def _quick_notepad(scope: str | None = None) -> QuickNotepadStore:
        store: NotebookStore = app.config["NOTEBOOK"]
        return QuickNotepadStore(
            store.db, scope=scope or _notepad_scope_from_request()
        )

    @app.get("/api/notebook/notepad")
    def api_notebook_notepad_get():
        pad = _quick_notepad()
        return jsonify({"ok": True, "notepad": pad.get()})

    @app.put("/api/notebook/notepad")
    def api_notebook_notepad_put():
        data = request.get_json(silent=True) or {}
        pad = _quick_notepad()
        kwargs: dict = {}
        if "content" in data:
            kwargs["content"] = str(data.get("content") or "")
        if "content_format" in data:
            kwargs["content_format"] = str(data.get("content_format") or "plain")
        if "panel_open" in data:
            kwargs["panel_open"] = bool(data.get("panel_open"))
        if "panel_width" in data:
            kwargs["panel_width"] = data.get("panel_width")
        try:
            saved = pad.save(**kwargs)
            return jsonify({"ok": True, "notepad": saved})
        except Exception as exc:  # noqa: BLE001 — surface as UI Error status
            return jsonify({"ok": False, "error": str(exc) or "Save failed"}), 500

    @app.post("/api/notebook/notepad/clear")
    def api_notebook_notepad_clear():
        audit: AuditStore = app.config["AUDIT"]
        pad = _quick_notepad()
        saved = pad.clear()
        audit.append(
            action=audit_actions.NOTEBOOK_NOTEPAD_CLEAR,
            target=f"quick-notepad:{pad.notepad_id}",
            detail=f"Cleared {pad.notepad_id} Quick Notepad (revision kept)",
            ok=True,
        )
        return jsonify({"ok": True, "notepad": saved})

    @app.post("/api/notebook/notepad/convert")
    def api_notebook_notepad_convert():
        store: NotebookStore = app.config["NOTEBOOK"]
        audit: AuditStore = app.config["AUDIT"]
        pad = _quick_notepad()
        note = pad.convert_to_note(store)
        if not note:
            return jsonify({"ok": False, "error": "Quick Notepad is empty."}), 400
        audit.append(
            action=audit_actions.NOTEBOOK_NOTEPAD_CONVERT,
            target=note["id"],
            detail=f"Converted {pad.notepad_id} Quick Notepad to note",
            ok=True,
        )
        audit.append(
            action=audit_actions.NOTEBOOK_CREATE,
            target=note["id"],
            detail=f"Created {pad.notepad_id} note from Quick Notepad",
            ok=True,
        )
        return jsonify(
            {
                "ok": True,
                "note_id": note["id"],
                "redirect": url_for(
                    notebook_endpoint(pad.notepad_id), note=note["id"]
                ),
            }
        )

    @app.post("/api/notebook/notepad/restore")
    def api_notebook_notepad_restore():
        audit: AuditStore = app.config["AUDIT"]
        data = request.get_json(silent=True) or {}
        revision_id = str(data.get("revision_id") or "").strip()
        pad = _quick_notepad()
        restored = pad.restore(revision_id)
        if not restored:
            return jsonify({"ok": False, "error": "Revision not found."}), 404
        audit.append(
            action=audit_actions.NOTEBOOK_NOTEPAD_RESTORE,
            target=revision_id,
            detail=f"Restored {pad.notepad_id} Quick Notepad revision",
            ok=True,
        )
        return jsonify({"ok": True, "notepad": restored})

    # ----- SQL Workspace (read-only) -----
    def _sql_store() -> SqlWorkspaceStore:
        return app.config["SQL_WS_STORE"]

    def _sql_executor() -> SqlExecutor:
        return app.config["SQL_WS_EXECUTOR"]

    def _sql_connections():
        return app.config["SQL_WS_CONNECTIONS"]

    @app.get("/sql")
    def sql_workspace():
        store = _sql_store()
        audit: AuditStore = app.config["AUDIT"]
        registry = _sql_connections()
        query_id = (request.args.get("query") or "").strip()
        selected = store.get_query(query_id) if query_id else None
        q = (request.args.get("q") or "").strip()
        tag = (request.args.get("tag") or "").strip()
        folder_id = (request.args.get("folder") or "").strip()
        fav = request.args.get("favorites") in {"1", "true", "on"}
        queries = store.list_queries(
            q=q, folder_id=folder_id, tag=tag, favorites_only=fav, limit=200
        )
        audit.append(
            action=audit_actions.SQL_WS_VIEW,
            target=query_id or "library",
            detail=f"SQL Workspace view matched={len(queries)}",
            ok=True,
        )
        page_size = int(os.environ.get("SQL_WS_PAGE_SIZE") or 100)
        return render_template(
            "sql_workspace.html",
            connections=registry.list_public(),
            folders=store.list_folders(),
            queries=queries,
            selected=selected,
            selected_id=query_id,
            filters={"q": q, "tag": tag, "folder": folder_id, "favorites": fav},
            recent_runs=store.list_runs(limit=30),
            page_size=page_size,
            max_rows=int(os.environ.get("SQL_WS_MAX_ROWS") or 1000),
            registry_repos=_notebook_registry_options(),
        )

    @app.get("/api/sql/connections")
    def api_sql_connections():
        return jsonify({"ok": True, "connections": _sql_connections().list_public()})

    @app.post("/api/sql/connections/<connection_id>/test")
    def api_sql_connection_test(connection_id: str):
        audit: AuditStore = app.config["AUDIT"]
        registry = _sql_connections()
        profile = registry.get(connection_id)
        if profile is None or not profile.enabled:
            return jsonify({"ok": False, "error": "Connection not found."}), 404
        if not profile.configured:
            detail = "Connection is not configured."
            audit.append(
                action=audit_actions.SQL_WS_TEST,
                target=connection_id,
                detail=detail,
                ok=False,
            )
            return jsonify({"ok": False, "error": detail}), 400
        result = _sql_executor().test_connection(profile)
        audit.append(
            action=audit_actions.SQL_WS_TEST,
            target=connection_id,
            detail=result.get("detail") or "",
            ok=bool(result.get("ok")),
            metadata={"latency_ms": result.get("latency_ms"), "environment": profile.environment},
        )
        return jsonify({"ok": bool(result.get("ok")), **result})

    @app.post("/api/sql/format")
    def api_sql_format():
        data = request.get_json(silent=True) or {}
        sql_text = str(data.get("sql") or "")
        dialect = str(data.get("dialect") or "postgres")
        try:
            return jsonify({"ok": True, "sql": format_sql(sql_text, dialect=dialect)})
        except SqlSafetyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/sql/params")
    def api_sql_params():
        data = request.get_json(silent=True) or {}
        names = extract_named_params(str(data.get("sql") or ""))
        return jsonify({"ok": True, "params": names})

    @app.post("/api/sql/run")
    def api_sql_run():
        audit: AuditStore = app.config["AUDIT"]
        data = request.get_json(silent=True) or {}
        connection_id = str(data.get("connection_id") or "").strip()
        sql_text = str(data.get("sql") or "")
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        query_id = str(data.get("query_id") or "").strip()
        explain = bool(data.get("explain"))
        page = int(data.get("page") or 1)
        page_size = int(data.get("page_size") or os.environ.get("SQL_WS_PAGE_SIZE") or 100)
        registry = _sql_connections()
        try:
            profile = registry.get_configured(connection_id)
        except LookupError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        result = _sql_executor().execute(
            profile,
            sql_text,
            params=params,
            query_id=query_id,
            page=page,
            page_size=page_size,
            explain=explain,
        )
        audit.append(
            action=audit_actions.SQL_WS_RUN,
            target=result.run_id,
            detail=(
                f"SQL run status={result.status} conn={connection_id} "
                f"env={profile.environment} rows={result.total_rows}"
            ),
            ok=result.ok,
            metadata={
                "connection_id": connection_id,
                "environment": profile.environment,
                "status": result.status,
                "kind": result.kind,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
                "query_id": query_id or None,
            },
        )
        return jsonify(
            {
                "ok": result.ok,
                "run_id": result.run_id,
                "status": result.status,
                "columns": result.columns,
                "rows": result.rows,
                "row_count": result.row_count,
                "total_rows": result.total_rows,
                "truncated": result.truncated,
                "duration_ms": round(result.duration_ms, 1),
                "error": result.error,
                "kind": result.kind,
                "page": result.page,
                "page_size": result.page_size,
                "is_live": profile.is_live,
            }
        )

    @app.post("/api/sql/runs/<run_id>/cancel")
    def api_sql_cancel(run_id: str):
        audit: AuditStore = app.config["AUDIT"]
        ok = _sql_executor().cancel(run_id)
        audit.append(
            action=audit_actions.SQL_WS_CANCEL,
            target=run_id,
            detail="Cancel requested" if ok else "Cancel ignored",
            ok=ok,
        )
        return jsonify({"ok": ok})

    @app.get("/api/sql/runs/<run_id>/csv")
    def api_sql_export_csv(run_id: str):
        audit: AuditStore = app.config["AUDIT"]
        path = _sql_executor().export_csv_path(run_id)
        if path is None:
            return jsonify({"ok": False, "error": "Result not found."}), 404
        audit.append(
            action=audit_actions.SQL_WS_EXPORT,
            target=run_id,
            detail="Exported SQL run CSV",
            ok=True,
        )
        return send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"sql-run-{run_id[:12]}.csv",
        )

    @app.get("/api/sql/runs")
    def api_sql_runs():
        query_id = (request.args.get("query_id") or "").strip()
        return jsonify(
            {"ok": True, "runs": _sql_store().list_runs(query_id=query_id, limit=50)}
        )

    @app.post("/api/sql/queries")
    def api_sql_create_query():
        audit: AuditStore = app.config["AUDIT"]
        data = request.get_json(silent=True) or {}
        # Never auto-run on save
        saved = _sql_store().create_query(
            title=str(data.get("title") or "Untitled query"),
            sql_text=str(data.get("sql") or ""),
            description=str(data.get("description") or ""),
            folder_id=(str(data.get("folder_id") or "").strip() or None),
            connection_id=str(data.get("connection_id") or ""),
            tags=data.get("tags"),
            favorite=bool(data.get("favorite")),
            repository_id=str(data.get("repository_id") or ""),
            notebook_note_id=str(data.get("notebook_note_id") or ""),
        )
        audit.append(
            action=audit_actions.SQL_WS_SAVE,
            target=saved["id"],
            detail="Created SQL query",
            ok=True,
        )
        return jsonify({"ok": True, "query": saved})

    @app.put("/api/sql/queries/<query_id>")
    def api_sql_save_query(query_id: str):
        audit: AuditStore = app.config["AUDIT"]
        data = request.get_json(silent=True) or {}
        saved = _sql_store().save_query(
            query_id,
            title=data.get("title"),
            sql_text=data.get("sql"),
            description=data.get("description"),
            folder_id=data.get("folder_id"),
            connection_id=data.get("connection_id"),
            tags=data.get("tags"),
            favorite=data.get("favorite"),
            repository_id=data.get("repository_id"),
            notebook_note_id=data.get("notebook_note_id"),
            new_version=bool(data.get("new_version", True)),
            version_note=str(data.get("version_note") or "saved"),
        )
        if not saved:
            return jsonify({"ok": False, "error": "Query not found."}), 404
        audit.append(
            action=audit_actions.SQL_WS_SAVE,
            target=query_id,
            detail=f"Saved SQL query v{saved.get('current_version')}",
            ok=True,
        )
        return jsonify({"ok": True, "query": saved})

    @app.post("/api/sql/folders")
    def api_sql_create_folder():
        data = request.get_json(silent=True) or {}
        folder = _sql_store().create_folder(str(data.get("name") or "Folder"))
        return jsonify({"ok": True, "folder": folder})

    @app.route("/dhis2", methods=["GET", "POST"])
    def dhis2():
        client: Dhis2Client = app.config["DHIS2"]
        catalog_store: CatalogStore = app.config["DHIS2_CATALOG"]
        audit: AuditStore = app.config["AUDIT"]
        instance_store: Dhis2InstanceStore = app.config["DHIS2_INSTANCE_STORE"]
        notice = request.args.get("notice")
        error = request.args.get("error")

        if request.method == "POST" and (request.form.get("action") or "") == "select_instance":
            choice = (request.form.get("instance") or "").strip().lower()
            profiles = list_dhis2_instance_profiles()
            by_id = {p["id"]: p for p in profiles}
            if choice not in by_id:
                return redirect(
                    url_for("dhis2", error="Unknown DHIS2 instance. Choose Stage or Live.")
                )
            if not by_id[choice].get("available"):
                missing = ", ".join(by_id[choice].get("missing_fields") or [])
                return redirect(
                    url_for(
                        "dhis2",
                        error=(
                            f"{by_id[choice]['label']} credentials are incomplete. "
                            f"Missing: {missing}."
                        ),
                    )
                )
            instance_store.save(choice)
            new_settings = build_dhis2_settings_for_instance(choice)
            new_client = Dhis2Client(new_settings)
            _apply_dhis2_client(new_client, instance=choice)
            status = new_client.check_status()
            app.config["DHIS2_LAST_STATUS"] = status
            audit.append(
                action=audit_actions.DHIS2_INSTANCE_SELECT,
                target=choice,
                detail=f"Selected DHIS2 instance {choice}; check={status.get('status')}",
                ok=bool(status.get("ok")),
                metadata={
                    "instance": choice,
                    "latency_ms": status.get("latency_ms"),
                    "configured": bool(new_settings.is_configured),
                },
            )
            if status.get("ok"):
                notice = f"Connected to DHIS2 {choice.title()}."
            else:
                notice = (
                    f"Switched to DHIS2 {choice.title()}, but connection check failed: "
                    f"{status.get('detail')}"
                )
            return redirect(url_for("dhis2", notice=notice))

        status = app.config.get("DHIS2_LAST_STATUS")
        selected = app.config.get("DHIS2_INSTANCE")
        profiles = list_dhis2_instance_profiles()
        discovery_ready = bool(selected and status and status.get("ok"))
        return render_template(
            "dhis2_overview.html",
            config=client.public_config(),
            status=status,
            catalog=catalog_store.load_latest(),
            notice=notice,
            error=error,
            hub_environment=settings.env_profile,
            dhis2_instance=selected,
            dhis2_profiles=profiles,
            discovery_ready=discovery_ready,
            live_warning=(selected == "live"),
        )

    @app.get("/dhis2/discover")
    def dhis2_discover():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        store: CatalogStore = app.config["DHIS2_CATALOG"]
        selected = app.config.get("DHIS2_INSTANCE")
        status = app.config.get("DHIS2_LAST_STATUS")
        if not selected:
            return redirect(
                url_for(
                    "dhis2",
                    error="Select a DHIS2 instance (Stage or Live) before running discovery.",
                )
            )
        if not (status and status.get("ok")):
            # Attempt a fresh check before blocking.
            status = client.check_status()
            app.config["DHIS2_LAST_STATUS"] = status
            if not status.get("ok"):
                return redirect(
                    url_for(
                        "dhis2",
                        error=(
                            "Discovery is disabled until the selected DHIS2 instance "
                            f"is connected. {status.get('detail') or ''}"
                        ).strip(),
                    )
                )
        try:
            catalog = run_discovery(
                client,
                store=store,
                enrich_samples=request.args.get("samples") == "1",
            )
            app.config["DHIS2_LAST_STATUS"] = client.check_status()
            audit.append(
                action=audit_actions.DHIS2_INSTANCE_DISCOVER,
                target=catalog.get("base_url"),
                detail=(
                    f"Discovered {catalog.get('type_count', 0)} types; "
                    f"version={catalog.get('dhis2_version')}; instance={selected}"
                ),
                ok=True,
                metadata={
                    "instance": selected,
                    "type_count": catalog.get("type_count"),
                },
            )
            return redirect(
                url_for(
                    "dhis2",
                    notice=(
                        f"Discovery complete on {str(selected).title()}: "
                        f"{catalog.get('type_count', 0)} types."
                    ),
                )
            )
        except Dhis2Error as exc:
            audit.append(
                action=audit_actions.DHIS2_INSTANCE_DISCOVER,
                target=selected,
                detail=exc.message,
                ok=False,
            )
            return redirect(url_for("dhis2", error=exc.message))


    @app.get("/dhis2/lookup")
    def dhis2_lookup():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        config = client.public_config()
        query = (request.args.get("q") or "").strip()
        force_check = request.args.get("check") == "1"
        status = app.config.get("DHIS2_LAST_STATUS")
        search = None
        error = None
        notice = None

        if force_check or (config["configured"] and status is None):
            status = client.check_status()
            app.config["DHIS2_LAST_STATUS"] = status
            audit.append(
                action=audit_actions.DHIS2_STATUS_CHECK,
                target=config.get("base_url"),
                detail=status.get("detail"),
                ok=bool(status.get("ok")),
                metadata={
                    "latency_ms": status.get("latency_ms"),
                    "version": (status.get("system") or {}).get("version"),
                },
            )
            if not force_check and not status.get("ok"):
                notice = "Automatic status check failed. You can retry with Check status."

        if query:
            try:
                search = client.search(query)
                audit.append(
                    action=audit_actions.DHIS2_METADATA_LOOKUP,
                    target=query,
                    detail=search.get("detail"),
                    ok=True,
                    metadata={
                        "mode": search.get("mode"),
                        "result_count": len(search.get("results") or []),
                    },
                )
            except Dhis2Error as exc:
                error = exc.message
                audit.append(
                    action=audit_actions.DHIS2_METADATA_LOOKUP,
                    target=query,
                    detail=exc.message,
                    ok=False,
                )

        recent = [
            {
                "when": _short_time(event.get("timestamp")),
                "action": event.get("action"),
                "detail": event.get("detail"),
                "actor": event.get("actor") or "local-owner",
            }
            for event in audit.list_recent(limit=8)
            if str(event.get("action", "")).startswith("DHIS2_")
        ]

        return render_template(
            "dhis2.html",
            config=config,
            status=status,
            query=query,
            search=search,
            error=error,
            notice=notice,
            recent_audit=recent,
        )

    @app.get("/dhis2/instance")
    def dhis2_instance():
        catalog = app.config["DHIS2_CATALOG"].load_latest()
        return render_template("dhis2_instance.html", catalog=catalog)

    @app.get("/dhis2/catalog")
    def dhis2_catalog():
        catalog = app.config["DHIS2_CATALOG"].load_latest()
        q = (request.args.get("q") or "").strip()
        category = (request.args.get("category") or "all").strip()
        builder_mode = (request.args.get("builder_mode") or "all").strip()
        types = filter_types(
            list((catalog or {}).get("types") or []),
            query=q,
            category=category,
            builder_mode=builder_mode,
        )
        categories = sorted((catalog or {}).get("categories") or {})
        builder_modes = sorted((catalog or {}).get("builder_modes") or {})
        if catalog:
            app.config["AUDIT"].append(
                action=audit_actions.DHIS2_CATALOG_VIEW,
                target="catalog",
                detail=f"Viewed catalog ({len(types)} types shown)",
                ok=True,
                metadata={"q": q, "category": category, "builder_mode": builder_mode},
            )
        return render_template(
            "dhis2_catalog.html",
            catalog=catalog,
            types=types,
            categories=categories,
            builder_modes=builder_modes,
            filters={"q": q, "category": category, "builder_mode": builder_mode},
        )

    @app.get("/dhis2/catalog/<type_id>")
    def dhis2_catalog_type(type_id: str):
        store: CatalogStore = app.config["DHIS2_CATALOG"]
        client: Dhis2Client = app.config["DHIS2"]
        item = store.get_type(type_id)
        if item is None:
            abort(404)
        enrich_error = None
        if request.args.get("enrich") == "1" and item.get("plural"):
            try:
                stats = client.get_resource_count_and_sample(item["plural"], sample_size=3)
                item = {
                    **item,
                    "count": stats.get("count"),
                    "sample": stats.get("sample") or [],
                }
                # Persist enrichment into latest catalog copy.
                catalog = store.load_latest() or {}
                updated = []
                for row in catalog.get("types") or []:
                    if row.get("id") == item.get("id"):
                        updated.append(item)
                    else:
                        updated.append(row)
                catalog["types"] = updated
                store.save(catalog)
            except Dhis2Error as exc:
                enrich_error = exc.message
        app.config["AUDIT"].append(
            action=audit_actions.DHIS2_CATALOG_VIEW,
            target=type_id,
            detail=f"Viewed catalog type {type_id}",
            ok=True,
        )
        return render_template(
            "dhis2_catalog_type.html",
            item=item,
            enrich_error=enrich_error,
        )

    @app.get("/dhis2/authorities")
    def dhis2_authorities():
        catalog = app.config["DHIS2_CATALOG"].load_latest()
        q = (request.args.get("q") or "").strip().lower()
        authorities = list((catalog or {}).get("authorities") or [])
        if q:
            authorities = [item for item in authorities if q in item.lower()]
        return render_template(
            "dhis2_authorities.html",
            authorities=authorities,
            q=request.args.get("q") or "",
        )

    @app.get("/dhis2/uid-explorer")
    def dhis2_uid_explorer():
        store: MappingIndexStore = app.config["DHIS2_MAPPING_INDEX"]
        enrich_store: EnrichmentStore = app.config["DHIS2_ENRICHMENT_STORE"]
        audit: AuditStore = app.config["AUDIT"]
        records = store.records()
        query = (request.args.get("q") or "").strip()
        object_type = (request.args.get("type") or "").strip()
        source_repository = (request.args.get("repo") or "").strip()
        environment = (request.args.get("env") or "").strip()
        program = (request.args.get("program") or "").strip().split("|", 1)[0]
        program_stage = (request.args.get("program_stage") or "").strip().split("|", 1)[0]
        domain_type = (request.args.get("domain") or "").strip()
        value_type = (request.args.get("value_type") or "").strip()
        answer_type = (request.args.get("answer") or "").strip()
        option_set = (request.args.get("option_set") or "").strip().split("|", 1)[0]
        audit_status = (request.args.get("audit") or "").strip()
        page_raw = (request.args.get("page") or "1").strip()
        per_page_raw = (request.args.get("per_page") or "200").strip().lower()
        try:
            page = max(1, int(page_raw))
        except ValueError:
            page = 1
        if per_page_raw in {"all", "0"}:
            per_page: int | None = None
        else:
            try:
                per_page = max(25, min(int(per_page_raw), 2000))
            except ValueError:
                per_page = 200

        use_enrichment = bool(enrich_store.current_snapshot_id()) and not source_repository
        enrichment_facets = enrich_store.facets() if use_enrichment else {}
        if use_enrichment:
            offset = 0 if per_page is None else (page - 1) * per_page
            results, matched_count = enrich_store.search(
                object_type=object_type,
                program=program,
                program_stage=program_stage,
                domain_type=domain_type,
                value_type=value_type,
                answer_type=answer_type,
                option_set=option_set,
                audit_status=audit_status,
                environment=environment,
                q=query,
                limit=per_page,
                offset=offset,
            )
            if per_page is None:
                page = 1
                total_pages = 1
                range_start = 1 if matched_count else 0
                range_end = matched_count
            else:
                total_pages = max(1, (matched_count + per_page - 1) // per_page)
                page = min(page, total_pages)
                range_start = offset + 1 if matched_count else 0
                range_end = offset + len(results)
            for row in results:
                row["answer_label"] = row.get("answer_type") or "—"
                row["program_label"] = row.get("program_name") or row.get("program_uid") or "—"
                row["flags"] = row.get("audit_status_list") or []
                row["object_type"] = row.get("object_type")
                row["value_type"] = row.get("value_type")
            facets = {
                "object_types": enrichment_facets.get("object_types") or [],
                "source_repositories": facet_values(records).get("source_repositories") or [],
                "environments": enrichment_facets.get("environments")
                or facet_values(records).get("environments")
                or [],
                "domain_types": enrichment_facets.get("domain_types") or [],
                "value_types": enrichment_facets.get("value_types") or [],
                "answer_types": enrichment_facets.get("answer_types") or [],
                "programs": enrichment_facets.get("programs") or [],
                "program_stages": enrichment_facets.get("program_stages") or [],
                "option_sets": enrichment_facets.get("option_sets") or [],
                "audit_statuses": enrichment_facets.get("audit_statuses") or [],
            }
            total = matched_count
            mode = "enrichment"
        else:
            facets_base = facet_values(records)
            facets = {
                **facets_base,
                "domain_types": [],
                "value_types": [],
                "answer_types": [],
                "programs": [],
                "program_stages": [],
                "option_sets": [],
                "audit_statuses": [],
            }
            matched = filter_records(
                records,
                query=query,
                object_type=object_type,
                source_repository=source_repository,
                environment=environment,
                limit=None,
            )
            matched_count = len(matched)
            if per_page is None:
                page = 1
                offset = 0
                results = matched
                total_pages = 1
            else:
                total_pages = max(1, (matched_count + per_page - 1) // per_page)
                page = min(page, total_pages)
                offset = (page - 1) * per_page
                results = matched[offset : offset + per_page]
            offline = classify_index_records(records)
            dup_uids = {item["uid"] for item in offline.get("duplicate") or []}
            conflict_uids = {item["uid"] for item in offline.get("conflicting") or []}
            for row in results:
                flags = []
                if row.get("uid") in dup_uids:
                    flags.append("duplicate")
                if row.get("uid") in conflict_uids:
                    flags.append("conflicting")
                row["flags"] = flags
                extras = row.get("extras") if isinstance(row.get("extras"), dict) else {}
                row["answer_label"] = derive_answer_type(
                    str(row.get("value_type") or extras.get("valueType") or ""),
                    option_set_uid=str(row.get("option_set_uid") or extras.get("optionSet") or ""),
                )
                row["program_label"] = str(
                    row.get("program_uid")
                    or extras.get("program")
                    or extras.get("program_name")
                    or ""
                )
            range_start = offset + 1 if matched_count else 0
            range_end = offset + len(results)
            total = len(records)
            mode = "repository"
            offline_stats = {
                "duplicates": len(offline.get("duplicate") or []),
                "conflicts": len(offline.get("conflicting") or []),
            }

        if use_enrichment:
            offline_stats = {"duplicates": 0, "conflicts": 0}

        index_meta = store.load_latest() or {}
        audit.append(
            action=audit_actions.DHIS2_UID_INDEX_VIEW,
            target="uid-explorer",
            detail=(
                f"Explored UID index ({range_start}-{range_end} of {matched_count} "
                f"matched; mode={mode})"
            ),
            ok=True,
        )
        return render_template(
            "dhis2_uid_explorer.html",
            records=results,
            total=total,
            matched=matched_count,
            shown=len(results),
            range_start=range_start,
            range_end=range_end,
            page=page,
            total_pages=total_pages,
            per_page=per_page if per_page is not None else "all",
            facets=facets,
            mode=mode,
            filters={
                "q": query,
                "type": object_type,
                "repo": source_repository,
                "env": environment,
                "program": request.args.get("program") or "",
                "program_stage": request.args.get("program_stage") or "",
                "domain": domain_type,
                "value_type": value_type,
                "answer": answer_type,
                "option_set": request.args.get("option_set") or "",
                "audit": audit_status,
            },
            index_meta=index_meta,
            offline_stats=offline_stats,
        )

    @app.get("/dhis2/uid-explorer/<uid>")
    def dhis2_uid_detail(uid: str):
        store: MappingIndexStore = app.config["DHIS2_MAPPING_INDEX"]
        catalog_store: CatalogStore = app.config["DHIS2_CATALOG"]
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        matches = store.get_by_uid(uid)
        if not matches:
            abort(404)
        primary = matches[0]
        catalog = catalog_store.load_latest() or {}
        catalog_types = list(catalog.get("types") or [])
        catalog_type = catalog_store.get_type(str(primary.get("object_type") or ""))
        offline = classify_index_records(matches)
        comparison = None
        relationships: list = []
        raw_dhis2 = None
        option_set_raw = None
        # Live compare needs Hub DHIS2_* credentials (not LP STAGE_/LIVE_ names).
        if client.public_config().get("configured"):
            try:
                comparison = classify_against_dhis2(
                    primary,
                    client,
                    catalog_types=catalog_types,
                )
                raw_dhis2 = comparison.get("dhis2")
                if isinstance(raw_dhis2, dict):
                    relationships = extract_relationships(
                        str(primary.get("object_type") or ""),
                        raw_dhis2,
                        catalog_type=catalog_type,
                    )
                    # Prefer nested option set from the object; else fetch by UID.
                    nested_os = raw_dhis2.get("optionSet")
                    if isinstance(nested_os, dict) and nested_os.get("options"):
                        option_set_raw = nested_os
                    else:
                        os_uid = ""
                        if isinstance(nested_os, dict):
                            os_uid = str(nested_os.get("id") or "")
                        os_uid = os_uid or str(primary.get("option_set_uid") or "")
                        if os_uid:
                            try:
                                os_payload = client.get_metadata_object(
                                    "optionSets",
                                    os_uid,
                                    fields="id,name,code,valueType,options[id,name,code,sortOrder]",
                                )
                                option_set_raw = os_payload.get("raw") or os_payload.get("item")
                            except Dhis2Error:
                                option_set_raw = nested_os if isinstance(nested_os, dict) else None
            except Dhis2Error as exc:
                comparison = {
                    "uid": uid,
                    "status": "unknown",
                    "detail": exc.message,
                    "diffs": [],
                    "dhis2": None,
                }
        else:
            comparison = {
                "uid": uid,
                "status": "unknown",
                "detail": "DHIS2 not configured — live option-set / compare unavailable.",
                "diffs": [],
                "dhis2": None,
            }

        audit_profile = build_audit_profile(
            primary,
            dhis2=raw_dhis2 if isinstance(raw_dhis2, dict) else None,
            option_set=option_set_raw if isinstance(option_set_raw, dict) else None,
        )

        reverse_trace: dict = {
            "ok": False,
            "edges": [],
            "queries": [],
            "errors": [],
            "counts": {},
            "storage": logical_storage_hint(
                object_type=str(primary.get("object_type") or ""),
                domain_type=str(
                    (raw_dhis2 or {}).get("domainType")
                    if isinstance(raw_dhis2, dict)
                    else primary.get("domain_type")
                    or ""
                ),
            ),
        }
        if client.public_config().get("configured"):
            try:
                reverse_trace = reverse_trace_links(
                    client,
                    object_type=str(primary.get("object_type") or ""),
                    uid=uid,
                    dhis2_obj=raw_dhis2 if isinstance(raw_dhis2, dict) else None,
                )
            except Dhis2Error as exc:
                reverse_trace["errors"] = [exc.message]

        # Relationships from audit profile connections + prior live extract
        for conn in audit_profile.get("connections") or []:
            related_uid = conn.get("uid") or ""
            if not related_uid:
                continue
            if any(r.get("related_uid") == related_uid for r in relationships):
                continue
            role = str(conn.get("role") or "Related")
            rtype = {
                "Program": "program",
                "Program stage": "programStage",
                "Option set": "optionSet",
            }.get(role, "")
            relationships.append(
                {
                    "relation": role,
                    "related_uid": related_uid,
                    "related_name": conn.get("name") or "",
                    "related_type": rtype,
                    "source": "audit_profile",
                    "detail": conn.get("detail") or "",
                }
            )

        for field, rel, rtype in (
            ("category_combo_uid", "Category Combination", "categoryCombo"),
        ):
            val = primary.get(field)
            if not val:
                continue
            if any(r.get("related_uid") == val for r in relationships):
                continue
            relationships.append(
                {
                    "relation": rel,
                    "related_uid": val,
                    "related_name": "",
                    "related_type": rtype,
                    "source": "repository_index",
                }
            )

        for edge in reverse_trace.get("edges") or []:
            related_uid = edge.get("related_uid") or ""
            if not related_uid:
                continue
            if any(
                r.get("related_uid") == related_uid and r.get("relation") == edge.get("relation")
                for r in relationships
            ):
                continue
            relationships.append(edge)

        audit.append(
            action=audit_actions.DHIS2_UID_INDEX_VIEW,
            target=uid,
            detail=(
                f"Viewed UID mapping detail ({primary.get('name') or uid}); "
                f"answer={audit_profile.get('answer', {}).get('label')}; "
                f"reverse_edges={(reverse_trace.get('counts') or {}).get('edges', 0)}"
            ),
            ok=True,
        )
        enrich_store: EnrichmentStore = app.config["DHIS2_ENRICHMENT_STORE"]
        enrich_obj = enrich_store.get_object(uid)
        tab = (request.args.get("tab") or "overview").strip().lower()
        load_raw = tab == "raw" or request.args.get("raw") == "1"
        raw_live = None
        if load_raw and client.public_config().get("configured"):
            try:
                plural_name = resolve_plural(
                    str((enrich_obj or primary).get("object_type") or primary.get("object_type") or ""),
                    catalog_types,
                )
                if plural_name:
                    payload = client.get_metadata_object(plural_name, uid)
                    raw_live = redact_mapping(payload.get("raw") or payload.get("item") or {})
            except Dhis2Error:
                raw_live = None

        return render_template(
            "dhis2_uid_detail.html",
            uid=uid,
            matches=matches,
            primary=primary,
            enrich_obj=enrich_obj,
            tab=tab,
            audit_profile=audit_profile,
            reverse_trace=reverse_trace,
            comparison=comparison,
            relationships=relationships,
            offline=offline,
            catalog_type=catalog_type,
            raw_index_json=json.dumps(matches, indent=2, ensure_ascii=True),
            raw_dhis2_json=(
                json.dumps(raw_live, indent=2, ensure_ascii=True)
                if raw_live is not None
                else (json.dumps(raw_dhis2, indent=2, ensure_ascii=True) if raw_dhis2 else None)
            ),
            plural=resolve_plural(str(primary.get("object_type") or ""), catalog_types),
        )

    @app.route("/dhis2/enrichment", methods=["GET", "POST"])
    def dhis2_enrichment():
        workflow: EnrichmentWorkflow = app.config["DHIS2_ENRICHMENT"]
        store: EnrichmentStore = app.config["DHIS2_ENRICHMENT_STORE"]
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        flash_error = None
        flash_notice = None
        run_id = request.args.get("run") or ""

        if request.method == "POST":
            action = request.form.get("action") or ""
            try:
                if action == "fetch":
                    environment = (request.form.get("environment") or "default").strip()
                    run_id = workflow.start_fetch(environment=environment)
                    flash_notice = f"Enrichment fetch started ({run_id[:8]}…)."
                    audit.append(
                        action=audit_actions.DHIS2_ENRICHMENT_FETCH,
                        target=environment,
                        detail=flash_notice,
                        ok=True,
                    )
                elif action == "cancel" and request.form.get("run_id"):
                    workflow.cancel(request.form.get("run_id") or "")
                    flash_notice = "Cancel requested."
                    run_id = request.form.get("run_id") or ""
                elif action == "apply":
                    result = workflow.apply_preview(request.form.get("confirmation") or "")
                    if not result.get("ok"):
                        flash_error = result.get("error") or "Apply failed."
                        if result.get("expected_phrase"):
                            flash_error += f" Type exactly: {result['expected_phrase']}"
                    else:
                        flash_notice = (
                            f"Enrichment snapshot saved ({result.get('snapshot_id')}). "
                            "No DHIS2 writes."
                        )
                        audit.append(
                            action=audit_actions.DHIS2_ENRICHMENT_APPLY,
                            target=str(result.get("snapshot_id")),
                            detail=flash_notice,
                            ok=True,
                            metadata=result.get("stats"),
                        )
                elif action == "discard":
                    workflow.discard_preview()
                    flash_notice = "Enrichment preview discarded."
                else:
                    flash_error = f"Unknown action: {action}"
            except Exception as exc:  # noqa: BLE001
                flash_error = str(exc)

        run = store.get_run(run_id) if run_id else None
        preview = workflow.preview
        # Lightweight preview summary for template (avoid dumping all objects)
        preview_view = None
        if preview:
            preview_view = {
                "environment": preview.get("environment"),
                "stats": preview.get("stats"),
                "counts": preview.get("counts"),
                "confirm_phrase": preview.get("confirm_phrase") or ENRICH_CONFIRM,
                "sample_objects": (preview.get("objects") or [])[:15],
                "sample_relationships": (preview.get("relationships") or [])[:20],
            }

        return render_template(
            "dhis2_enrichment.html",
            config=client.public_config(),
            run=run,
            preview=preview_view,
            snapshots=store.list_snapshots(limit=20),
            confirm_apply=ENRICH_CONFIRM,
            flash_error=flash_error,
            flash_notice=flash_notice,
        )

    @app.get("/api/dhis2/enrichment/runs/<run_id>")
    def api_dhis2_enrichment_run(run_id: str):
        store: EnrichmentStore = app.config["DHIS2_ENRICHMENT_STORE"]
        run = store.get_run(run_id)
        if not run:
            return jsonify({"ok": False, "error": "Unknown run"}), 404
        return jsonify({"ok": True, "run": run})

    @app.route("/dhis2/uid-index/manage", methods=["GET", "POST"])
    def dhis2_uid_index_manage():
        store: MappingIndexStore = app.config["DHIS2_MAPPING_INDEX"]
        audit: AuditStore = app.config["AUDIT"]
        sources_cfg = load_sources_config()
        flash_error = None
        flash_notice = None
        preview = app.config.get("DHIS2_MAPPING_PREVIEW")
        scan_summary = None
        version_compare = None

        if request.method == "POST":
            action = request.form.get("action") or ""
            try:
                if action == "scan":
                    scanned = scan_all_sources(sources_cfg)
                    scan_summary = {
                        "ok": scanned.get("ok"),
                        "count": scanned.get("count"),
                        "sources": scanned.get("sources"),
                    }
                    incoming = scanned.get("records") or []
                    preview = enrich_controlled_preview(
                        merge_preview(store.records(), incoming),
                        existing=store.records(),
                        incoming=incoming,
                        store=store,
                    )
                    app.config["DHIS2_MAPPING_PREVIEW"] = preview
                    flash_notice = (
                        f"Dry-run complete: {scanned.get('count', 0)} source rows. "
                        "Review changes, then type the confirmation phrase to apply."
                    )
                    audit.append(
                        action=audit_actions.DHIS2_UID_INDEX_SCAN,
                        target="configured-sources",
                        detail=flash_notice,
                        ok=bool(scanned.get("ok")),
                        metadata={"sources": scanned.get("sources")},
                    )
                elif action == "import_upload":
                    upload = request.files.get("import_file")
                    if not upload or not upload.filename:
                        flash_error = "Choose a JSON or CSV file to import."
                    else:
                        text = upload.read().decode("utf-8-sig")
                        filename = upload.filename
                        fmt = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
                        source = {
                            "id": "upload",
                            "repository_id": request.form.get("repository_id") or "upload",
                            "environment": request.form.get("environment") or "unknown",
                            "column_map": {},
                        }
                        if fmt == "csv":
                            incoming = parse_csv_text(text, source=source, source_file=filename)
                        elif fmt == "json":
                            incoming = parse_json_text(text, source=source, source_file=filename)
                        else:
                            raise ValueError("Unsupported file type. Use .csv or .json.")
                        preview = enrich_controlled_preview(
                            merge_preview(store.records(), incoming),
                            existing=store.records(),
                            incoming=incoming,
                            store=store,
                        )
                        app.config["DHIS2_MAPPING_PREVIEW"] = preview
                        cc = preview.get("change_counts") or {}
                        flash_notice = (
                            f"Dry-run for {filename}: "
                            f"{cc.get('NEW_UID', 0)} new, "
                            f"{cc.get('CHANGED_NAME', 0)} name changes, "
                            f"{cc.get('CHANGED_TYPE', 0)} type changes, "
                            f"{cc.get('CONFLICTING', 0)} conflicts."
                        )
                        audit.append(
                            action=audit_actions.DHIS2_UID_INDEX_IMPORT,
                            target=filename,
                            detail=flash_notice,
                            ok=True,
                            metadata=preview.get("change_counts"),
                        )
                elif action == "apply_preview":
                    if not preview:
                        flash_error = "No dry-run preview to apply. Scan or import first."
                    else:
                        result = apply_with_confirmation(
                            store,
                            preview,
                            request.form.get("confirmation") or "",
                            include_conflicts=request.form.get("include_conflicts") == "1",
                        )
                        if not result.get("ok"):
                            flash_error = result.get("error") or "Apply failed."
                            if result.get("expected_phrase"):
                                flash_error += f" Type exactly: {result['expected_phrase']}"
                        else:
                            index = result.get("index") or {}
                            app.config["DHIS2_MAPPING_PREVIEW"] = None
                            preview = None
                            flash_notice = (
                                f"Index applied (v{result.get('version')}, "
                                f"{index.get('record_count')} records). "
                                f"Backup: {result.get('backup_path') or 'n/a'}."
                            )
                            audit.append(
                                action=audit_actions.DHIS2_UID_INDEX_APPLY,
                                target=f"v{result.get('version')}",
                                detail=flash_notice,
                                ok=True,
                                metadata={
                                    "backup_path": result.get("backup_path"),
                                    "change_log": result.get("change_log"),
                                },
                            )
                elif action == "discard_preview":
                    app.config["DHIS2_MAPPING_PREVIEW"] = None
                    preview = None
                    flash_notice = "Preview discarded. Index unchanged."
                elif action == "compare_versions":
                    version_compare = compare_versions(
                        store,
                        request.form.get("version_a") or "current",
                        request.form.get("version_b") or "",
                    )
                    audit.append(
                        action=audit_actions.DHIS2_UID_INDEX_COMPARE,
                        target=f"{request.form.get('version_a')} vs {request.form.get('version_b')}",
                        detail=(
                            "Compare ok"
                            if version_compare.get("ok")
                            else version_compare.get("error")
                        ),
                        ok=bool(version_compare.get("ok")),
                        metadata=version_compare.get("counts"),
                    )
                    if not version_compare.get("ok"):
                        flash_error = version_compare.get("error")
                elif action == "restore_version":
                    result = restore_with_confirmation(
                        store,
                        request.form.get("version") or "",
                        request.form.get("confirmation") or "",
                    )
                    if not result.get("ok"):
                        flash_error = result.get("error") or "Restore failed."
                        if result.get("expected_phrase"):
                            flash_error += f" Type exactly: {result['expected_phrase']}"
                    else:
                        flash_notice = (
                            f"Restored version {result.get('version')} "
                            f"({result.get('record_count')} records). "
                            f"New archive stamp: {result.get('new_version')}."
                        )
                        audit.append(
                            action=audit_actions.DHIS2_UID_INDEX_RESTORE,
                            target=str(result.get("version")),
                            detail=flash_notice,
                            ok=True,
                        )
                else:
                    flash_error = f"Unknown action: {action}"
            except Exception as exc:  # noqa: BLE001
                flash_error = str(exc)

        index_meta = store.load_latest()
        versions = list_versions(store)
        return render_template(
            "dhis2_uid_index_manage.html",
            sources=sources_cfg.get("sources") or [],
            index_meta=index_meta,
            preview=preview,
            scan_summary=scan_summary,
            versions=versions,
            version_compare=version_compare,
            confirm_apply=CONFIRM_APPLY,
            confirm_restore=CONFIRM_RESTORE,
            flash_error=flash_error,
            flash_notice=flash_notice,
        )

    @app.get("/dhis2/uid-index/export")
    def dhis2_uid_index_export():
        store: MappingIndexStore = app.config["DHIS2_MAPPING_INDEX"]
        audit: AuditStore = app.config["AUDIT"]
        if not store.latest_path.is_file():
            abort(404)
        audit.append(
            action=audit_actions.DHIS2_UID_INDEX_EXPORT,
            target=str(store.latest_path),
            detail="Downloaded hub UID index JSON",
            ok=True,
        )
        return send_file(
            store.latest_path,
            mimetype="application/json",
            as_attachment=True,
            download_name="hub_uid_index_latest.json",
        )

    @app.get("/dhis2/metadata/<resource_type>/<uid>")
    def dhis2_detail(resource_type: str, uid: str):
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        if resource_type not in ALLOWED_RESOURCES:
            abort(404)
        try:
            detail = client.get_metadata(resource_type, uid)
            audit.append(
                action=audit_actions.DHIS2_METADATA_DETAIL,
                target=f"{resource_type}/{uid}",
                detail=f"Viewed {(detail.get('item') or {}).get('name') or uid}",
                ok=True,
            )
            return render_template("dhis2_detail.html", detail=detail)
        except Dhis2Error as exc:
            audit.append(
                action=audit_actions.DHIS2_METADATA_DETAIL,
                target=f"{resource_type}/{uid}",
                detail=exc.message,
                ok=False,
            )
            return (
                render_template(
                    "dhis2.html",
                    config=client.public_config(),
                    status=app.config.get("DHIS2_LAST_STATUS"),
                    query=uid,
                    search=None,
                    error=exc.message,
                    notice=None,
                    recent_audit=[],
                ),
                400,
            )

    @app.route("/dhis2/metadata-builder", methods=["GET", "POST"])
    def dhis2_metadata_builder():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        builder_config = app.config["DHIS2_BUILDER_CONFIG"]
        catalog = app.config["DHIS2_CATALOG"].load_latest()
        metadata_types = workspace_types(catalog, builder_config)
        uid_index: UidIndex = app.config["DHIS2_UID_INDEX"]
        drafts: DraftStore = app.config["DHIS2_DRAFTS"]

        selected_instance = (
            request.values.get("instance")
            or (builder_config.instances[0].id if builder_config.instances else "default")
        )
        if not any(item.id == selected_instance for item in builder_config.instances):
            selected_instance = builder_config.instances[0].id if builder_config.instances else "default"
        selected_operation = request.values.get("operation") or "create"
        selected_type = request.values.get("type") or (metadata_types[0].id if metadata_types else "")
        if selected_operation == "delete":
            abort(400)

        type_spec = next((item for item in metadata_types if item.id == selected_type), None)
        if type_spec is None and metadata_types:
            type_spec = metadata_types[0]
            selected_type = type_spec.id
        operation_spec = builder_config.get_operation(selected_operation)
        if operation_spec is None or not operation_spec.enabled:
            selected_operation = "create"
            operation_spec = builder_config.get_operation("create")

        form: dict = {}
        draft_id = request.values.get("draft_id") or request.args.get("draft")
        raw_json = request.form.get("raw_json", "") if request.method == "POST" else ""
        preview = None
        flash_error = None
        flash_notice = None

        if request.method == "GET" and draft_id:
            loaded = drafts.load(draft_id)
            if loaded:
                form = dict(loaded.get("form") or {})
                selected_type = loaded.get("metadata_type") or selected_type
                selected_operation = loaded.get("operation") or selected_operation
                type_spec = next((item for item in metadata_types if item.id == selected_type), type_spec)
                raw_json = str(loaded.get("raw_json") or "")
                flash_notice = f"Loaded local draft {draft_id}."
            else:
                flash_error = f"Draft not found: {draft_id}."

        if request.method == "POST" and type_spec is not None:
            form = _builder_form_from_request(type_spec)
            action = request.form.get("action") or "preview"
            try:
                builder = get_builder(type_spec, client, uid_index=uid_index)
                if action == "revalidate_raw":
                    preview = builder.preview_raw(
                        raw_json,
                        operation=selected_operation,
                        check_remote=bool(client.public_config().get("configured")),
                    )
                elif action == "save_draft" and raw_json.strip():
                    preview = builder.preview_raw(
                        raw_json,
                        operation=selected_operation,
                        check_remote=bool(client.public_config().get("configured")),
                    )
                else:
                    preview = builder.preview(
                        form,
                        operation=selected_operation,
                        check_remote=bool(client.public_config().get("configured")),
                    )
                    raw_json = preview.get("payload_json") or raw_json

                if action == "save_draft":
                    saved = drafts.save(
                        {
                            "instance": selected_instance,
                            "operation": selected_operation,
                            "metadata_type": selected_type,
                            "form": form,
                            "raw_json": raw_json or preview.get("payload_json"),
                            "payload": preview.get("payload"),
                            "validation_ok": preview.get("ok"),
                        },
                        draft_id=draft_id,
                    )
                    draft_id = saved["id"]
                    flash_notice = f"Draft saved locally as {draft_id} (not sent to DHIS2)."
                    audit.append(
                        action=audit_actions.DHIS2_METADATA_DRAFT_SAVE,
                        target=f"{selected_type}/{draft_id}",
                        detail=flash_notice,
                        ok=True,
                    )
                else:
                    audit.append(
                        action=audit_actions.DHIS2_METADATA_PREVIEW,
                        target=selected_type,
                        detail=preview.get("validation_summary"),
                        ok=bool(preview.get("ok")),
                        metadata={
                            "operation": selected_operation,
                            "name": form.get("name"),
                            "apply_enabled": False,
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - surface builder/config errors in UI
                flash_error = str(exc)

        index_status = None
        dependencies: dict[str, list] = {}
        if type_spec is not None:
            # Dependencies come from the local UID mapping index (no DHIS2 required).
            index_status = uid_index.ensure(type_spec.dependency_resources or [])
            for resource in type_spec.dependency_resources or []:
                dependencies[resource] = uid_index.search(resource, limit=40)
            if not (type_spec.dependency_resources or []):
                index_status = {
                    "ok": True,
                    "loaded": {},
                    "errors": {},
                    "detail": "No dependency resources for this type.",
                }
        else:
            index_status = {
                "ok": False,
                "loaded": {},
                "errors": {"*": "No catalog type selected. Run discovery first."},
            }

        schema = catalog_schema_summary(type_spec) if type_spec else {}

        return render_template(
            "dhis2_metadata_builder.html",
            instances=builder_config.instances,
            operations=[op for op in builder_config.operations if op.enabled],
            metadata_types=metadata_types,
            workspace_stats=workspace_stats(metadata_types),
            selected={
                "instance": selected_instance,
                "operation": selected_operation,
                "type": selected_type,
            },
            type_spec=type_spec,
            form=form,
            raw_json=raw_json,
            preview=preview,
            schema=schema,
            catalog=catalog,
            index_status=index_status,
            dependencies=dependencies,
            drafts=drafts.list_recent(limit=12),
            draft_id=draft_id,
            flash_error=flash_error,
            flash_notice=flash_notice,
        )

    @app.get("/api/dhis2/metadata-builder/deps")
    def api_dhis2_builder_deps():
        """Search UID index for dependency selectors."""
        resource = (request.args.get("resource") or "").strip()
        query = (request.args.get("q") or "").strip()
        if not resource:
            return jsonify({"ok": False, "error": "resource is required"}), 400
        uid_index: UidIndex = app.config["DHIS2_UID_INDEX"]
        try:
            uid_index.ensure([resource])
            results = uid_index.search(resource, query, limit=25)
            return jsonify({"ok": True, "resource": resource, "results": results})
        except Dhis2Error as exc:
            return jsonify({"ok": False, "error": exc.message, "results": []}), 400

    @app.get("/audit")
    def audit():
        store: AuditStore = app.config["AUDIT"]
        return render_template("audit.html", events=store.list_recent(limit=200))

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        flash_error = None
        flash_notice = None
        if request.method == "POST" and request.form.get("action") == "owner_login":
            token = request.form.get("owner_token") or ""
            if login_owner(token):
                flash_notice = "Owner session established."
                audit.append(
                    action=audit_actions.OWNER_LOGIN,
                    target="settings",
                    detail="Owner login ok",
                    ok=True,
                )
            else:
                flash_error = "Invalid owner token."
                audit.append(
                    action=audit_actions.OWNER_LOGIN,
                    target="settings",
                    detail="Owner login failed",
                    ok=False,
                )
        return render_template(
            "settings.html",
            settings_view={
                "host": settings.host,
                "port": settings.port,
                "debug": settings.debug,
                "repositories_config": str(settings.repositories_config),
                "request_timeout_seconds": settings.request_timeout_seconds,
                "audit_log_path": str(settings.audit_log_path),
                "database_path": str(settings.database_path),
                "owner_token_configured": settings.owner_token_configured,
            },
            dhis2_config=client.public_config(),
            flash_error=flash_error,
            flash_notice=flash_notice,
            actor=current_actor(),
        )

    @app.get("/api/healthz")
    def api_healthz():
        registry = app.config["REGISTRY"]
        client: Dhis2Client = app.config["DHIS2"]
        cfg = client.public_config()
        last_status = app.config.get("DHIS2_LAST_STATUS")
        # Optional live probe: /api/healthz?probe=1 — uses short probe timeout.
        if request.args.get("probe") == "1" and cfg.get("configured"):
            last_status = client.check_status()
            app.config["DHIS2_LAST_STATUS"] = last_status
        dhis2_health = {
            "configured": bool(cfg.get("configured")),
            "enabled": bool(cfg.get("enabled")),
            "mode": cfg.get("mode") or "readonly",
            "status": (last_status or {}).get("status"),
            "ok": (last_status or {}).get("ok"),
            "latency_ms": (last_status or {}).get("latency_ms"),
            "detail": (last_status or {}).get("detail"),
        }
        ok = registry is not None
        return jsonify(
            {
                "ok": ok,
                "service": "central-hub",
                "version": __version__,
                "env": settings.env_profile,
                "registry_loaded": ok,
                "error": app.config["REGISTRY_ERROR"],
                "dhis2_configured": cfg.get("configured"),
                "dhis2": dhis2_health,
            }
        ), (200 if ok else 503)

    @app.get("/api/repositories")
    def api_repositories():
        registry = app.config["REGISTRY"]
        if registry is None:
            return jsonify({"ok": False, "error": app.config["REGISTRY_ERROR"]}), 503
        return jsonify(
            {
                "ok": True,
                "repositories": [_repo_to_dict(repo) for repo in registry.repositories],
                "defaults": {
                    "job_timeout_seconds": registry.defaults.job_timeout_seconds,
                    "max_concurrent_jobs": registry.defaults.max_concurrent_jobs,
                    "require_explicit_apply": registry.defaults.require_explicit_apply,
                },
            }
        )

    @app.get("/api/repositories/<repo_id>/health")
    def api_repository_health(repo_id: str):
        registry = app.config["REGISTRY"]
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        if registry is None or adapters is None:
            return jsonify({"ok": False, "error": app.config["REGISTRY_ERROR"]}), 503
        repo = registry.get(repo_id)
        if repo is None:
            return jsonify({"ok": False, "error": f"Unknown repository: {repo_id}"}), 404
        return jsonify({"ok": True, "result": adapters.check_repository(repo)})

    @app.get("/api/health")
    def api_health_all():
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        if adapters is None:
            return jsonify({"ok": False, "error": app.config["REGISTRY_ERROR"]}), 503
        force = request.args.get("fresh", "").strip().lower() in {"1", "true", "yes"}
        results = adapters.check_all(enabled_only=False, force=force)
        return jsonify(
            {
                "ok": all(item.get("ok") for item in results if item.get("enabled")),
                "fresh": force,
                "results": results,
            }
        )

    @app.get("/api/dhis2/status")
    def api_dhis2_status():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        status = client.check_status()
        app.config["DHIS2_LAST_STATUS"] = status
        audit.append(
            action=audit_actions.DHIS2_STATUS_CHECK,
            target=client.public_config().get("base_url"),
            detail=status.get("detail"),
            ok=bool(status.get("ok")),
            metadata={"latency_ms": status.get("latency_ms")},
        )
        return jsonify({"ok": bool(status.get("ok")), "result": status})

    @app.get("/api/dhis2/search")
    def api_dhis2_search():
        client: Dhis2Client = app.config["DHIS2"]
        audit: AuditStore = app.config["AUDIT"]
        query = (request.args.get("q") or "").strip()
        try:
            result = client.search(query)
            audit.append(
                action=audit_actions.DHIS2_METADATA_LOOKUP,
                target=query,
                detail=result.get("detail"),
                ok=True,
                metadata={"mode": result.get("mode"), "result_count": len(result.get("results") or [])},
            )
            return jsonify(result)
        except Dhis2Error as exc:
            audit.append(
                action=audit_actions.DHIS2_METADATA_LOOKUP,
                target=query,
                detail=exc.message,
                ok=False,
            )
            return jsonify({"ok": False, "error": exc.message}), 400

    return app


def _resolve_config_path(configured: Path) -> Path:
    """Allow relative paths in repositories.yaml to resolve from hub root."""
    if configured.is_file():
        return configured
    fallback = ROOT_DIR / "config" / "repositories.yaml"
    return fallback


def _short_time(timestamp: str | None) -> str:
    if not timestamp:
        return "—"
    # 2026-07-24T15:30:00+00:00 -> 15:30
    if "T" in timestamp:
        return timestamp.split("T", 1)[1][:5]
    return timestamp[:16]


def _builder_form_from_request(type_spec) -> dict:
    form: dict = {}
    for field in type_spec.fields:
        if field.input == "checkbox":
            form[field.id] = request.form.get(field.id) in {"true", "on", "1", "yes"}
        else:
            form[field.id] = request.form.get(field.id, "")
    return form


def _repos_from_health(registry, health_results: list[dict]) -> list[dict]:
    """Build dashboard repository rows from live adapter health (not demo fixtures)."""
    by_id = {item.get("repository_id"): item for item in health_results}
    rows: list[dict] = []
    repos = registry.repositories if registry else []
    for repo in repos:
        health = by_id.get(repo.id) or {}
        status = ui_repo_status(repo, health)
        path_or_url = repo.git_url or repo.local_path or repo.base_url or "—"
        rows.append(
            {
                "repo_id": repo.id,
                "name": repo.name,
                "subtitle": repo.description or repo.type,
                "type": repo.type,
                "status": status,
                "branch_path": path_or_url,
                "last_check": (health.get("checked_at") or "")[:19].replace("T", " ") or "—",
                "icon": "API" if repo.type == "api" else "CLI",
            }
        )
    return rows


def _repo_to_dict(repo) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "type": repo.type,
        "enabled": repo.enabled,
        "description": repo.description,
        "local_path": repo.local_path,
        "working_directory": repo.working_directory,
        "base_url": repo.base_url,
        "git_url": repo.git_url,
        "tags": repo.tags,
        "capabilities": [
            {
                "id": cap.id,
                "label": cap.label,
                "adapter_type": cap.adapter_type,
                "input_types": cap.input_types,
                "dry_run_default": cap.dry_run_default,
            }
            for cap in repo.capabilities
        ],
        "health_check": None
        if repo.health_check is None
        else {
            "type": repo.health_check.type,
            "method": repo.health_check.method,
            "path": repo.health_check.path,
            "timeout_seconds": repo.health_check.timeout_seconds,
            "local_path": repo.health_check.local_path,
            "executable": repo.health_check.executable,
            "command": repo.health_check.command,
        },
        "detail_url": url_for("repository_detail", repo_id=repo.id),
    }


app = create_app()


if __name__ == "__main__":
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
