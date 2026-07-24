"""Central Hub — Phase 1 entrypoint (registry + health UI)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, render_template, url_for

from hub import __version__
from hub.adapters import AdapterManager
from hub.registry import load_registry
from hub.registry.loader import RegistryError
from hub.settings import ROOT_DIR, load_settings

settings = load_settings()

# UI-only demo fixtures for the dashboard mockup. Not real jobs / DHIS2 calls.
_DHIS2_TOOLS = [
    {"label": "Metadata Lookup", "icon": "⌕"},
    {"label": "Data Elements", "icon": "▦"},
    {"label": "Option Sets", "icon": "☰"},
    {"label": "Program Indicators", "icon": "◎"},
    {"label": "Program Rules", "icon": "⚙"},
    {"label": "Import / Export", "icon": "⇄"},
]


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(ROOT_DIR / "templates"),
        static_folder=str(ROOT_DIR / "static"),
    )
    app.config["SETTINGS"] = settings

    try:
        registry = load_registry(_resolve_config_path(settings.repositories_config))
        registry_error: str | None = None
    except RegistryError as exc:
        registry = None
        registry_error = str(exc)

    app.config["REGISTRY"] = registry
    app.config["REGISTRY_ERROR"] = registry_error
    app.config["ADAPTERS"] = (
        AdapterManager(registry, default_timeout=settings.request_timeout_seconds)
        if registry is not None
        else None
    )

    @app.context_processor
    def inject_globals():
        return {
            "app_name": settings.app_name,
            "env_profile": settings.env_profile,
            "hub_version": __version__,
            "registry_error": app.config.get("REGISTRY_ERROR"),
            "dhis2_tools": _DHIS2_TOOLS,
            "nav_items": [
                {
                    "endpoint": "dashboard",
                    "label": "Dashboard",
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
                    "endpoint": "jobs",
                    "label": "Jobs",
                    "icon": "▶",
                    "active_prefix": None,
                },
                {
                    "endpoint": "dhis2",
                    "label": "DHIS2",
                    "icon": "⬡",
                    "badge": "NEW",
                    "active_prefix": None,
                },
                {
                    "endpoint": "health",
                    "label": "Health",
                    "icon": "♡",
                    "active_prefix": None,
                },
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
            ],
            "quick_actions": [
                {
                    "label": "Add Repository",
                    "endpoint": "repositories",
                    "available": True,
                },
                {
                    "label": "Run Health Check",
                    "endpoint": "health",
                    "available": True,
                },
                {
                    "label": "DHIS2 Maintenance",
                    "endpoint": "dhis2",
                    "available": True,
                },
                {
                    "label": "Create Demo Job",
                    "endpoint": "jobs",
                    "available": False,
                    "phase": "Phase 2",
                },
                {
                    "label": "View Logs",
                    "endpoint": "audit",
                    "available": False,
                    "phase": "Phase 2",
                },
            ],
        }

    @app.get("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            last_updated="2 min ago",
            summary_cards=_demo_summary_cards(),
            demo_repos=_demo_repositories(),
            demo_jobs=_demo_jobs(),
            demo_activity=_demo_activity(),
        )

    @app.get("/repositories")
    def repositories():
        registry = app.config["REGISTRY"]
        repos = registry.repositories if registry else []
        return render_template(
            "repositories.html",
            repositories=repos,
            defaults=registry.defaults if registry else None,
        )

    @app.get("/repositories/<repo_id>")
    def repository_detail(repo_id: str):
        registry = app.config["REGISTRY"]
        if registry is None:
            abort(503)
        repo = registry.get(repo_id)
        if repo is None:
            abort(404)
        return render_template("repository_detail.html", repository=repo)

    @app.get("/health")
    def health():
        adapters: AdapterManager | None = app.config["ADAPTERS"]
        results = adapters.check_all(enabled_only=False) if adapters else []
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

    @app.get("/jobs")
    def jobs():
        return render_template("jobs.html")

    @app.get("/dhis2")
    def dhis2():
        return render_template("dhis2.html")

    @app.get("/audit")
    def audit():
        return render_template("audit.html")

    @app.get("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            settings_view={
                "host": settings.host,
                "port": settings.port,
                "debug": settings.debug,
                "repositories_config": str(settings.repositories_config),
                "request_timeout_seconds": settings.request_timeout_seconds,
            },
        )

    @app.get("/api/healthz")
    def api_healthz():
        registry = app.config["REGISTRY"]
        ok = registry is not None
        return jsonify(
            {
                "ok": ok,
                "service": "central-hub",
                "version": __version__,
                "env": settings.env_profile,
                "registry_loaded": ok,
                "error": app.config["REGISTRY_ERROR"],
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
        results = adapters.check_all(enabled_only=False)
        return jsonify(
            {
                "ok": all(item.get("ok") for item in results if item.get("enabled")),
                "results": results,
            }
        )

    return app


def _resolve_config_path(configured: Path) -> Path:
    """Allow relative paths in repositories.yaml to resolve from hub root."""
    if configured.is_file():
        return configured
    fallback = ROOT_DIR / "config" / "repositories.yaml"
    return fallback


def _demo_summary_cards() -> list[dict]:
    """Mockup-aligned summary counts (UI demo only)."""
    return [
        {
            "label": "Repositories",
            "value": 4,
            "sub": "Total registered",
            "icon": "▣",
            "link_endpoint": "repositories",
        },
        {
            "label": "Healthy",
            "value": 3,
            "sub": "Online & healthy",
            "icon": "♡",
            "link_endpoint": "health",
        },
        {
            "label": "Running Jobs",
            "value": 1,
            "sub": "In progress",
            "icon": "▶",
            "link_endpoint": "jobs",
        },
        {
            "label": "Failed Jobs",
            "value": 0,
            "sub": "Last 24 hours",
            "icon": "!",
            "link_endpoint": "jobs",
        },
        {
            "label": "Jobs Today",
            "value": 2,
            "sub": "Total executed",
            "icon": "☰",
            "link_endpoint": "jobs",
        },
    ]


def _demo_repositories() -> list[dict]:
    return [
        {
            "name": "Live Processing",
            "subtitle": "API repository",
            "type": "api",
            "status": "healthy",
            "branch_path": "fritz",
            "last_check": "2 min ago",
            "icon": "API",
        },
        {
            "name": "Data Scripts",
            "subtitle": "Command repository",
            "type": "command",
            "status": "healthy",
            "branch_path": "main",
            "last_check": "5 min ago",
            "icon": "CMD",
        },
        {
            "name": "Report Generator",
            "subtitle": "Command repository",
            "type": "command",
            "status": "offline",
            "branch_path": "main",
            "last_check": "1 hour ago",
            "icon": "CMD",
        },
        {
            "name": "Metadata Tools",
            "subtitle": "API repository",
            "type": "api",
            "status": "healthy",
            "branch_path": "develop",
            "last_check": "3 min ago",
            "icon": "API",
        },
    ]


def _demo_jobs() -> list[dict]:
    return [
        {
            "name": "Demo Health Check",
            "repository": "central-hub",
            "status": "completed",
            "started_at": "10:14 PM",
            "duration": "3s",
            "action": "Open",
        },
        {
            "name": "Registry Scan",
            "repository": "central-hub",
            "status": "running",
            "started_at": "10:10 PM",
            "duration": "8s",
            "action": "Logs",
        },
        {
            "name": "Config Reload",
            "repository": "central-hub",
            "status": "completed",
            "started_at": "10:05 PM",
            "duration": "2s",
            "action": "Open",
        },
        {
            "name": "Health Check",
            "repository": "live-processing",
            "status": "completed",
            "started_at": "10:02 PM",
            "duration": "4s",
            "action": "Open",
        },
    ]


def _demo_activity() -> list[dict]:
    return [
        {
            "time": "10:16 PM",
            "action": "DHIS2_STATUS_CHECK",
            "tone": "dhis2",
            "detail": "Connection probe reported Online (demo)",
            "actor": "Admin",
        },
        {
            "time": "10:14 PM",
            "action": "HEALTH_CHECK",
            "tone": "health",
            "detail": "Repository health sweep completed",
            "actor": "Admin",
        },
        {
            "time": "10:12 PM",
            "action": "JOB_COMPLETED",
            "tone": "job-ok",
            "detail": "Demo Health Check finished successfully",
            "actor": "Admin",
        },
        {
            "time": "10:10 PM",
            "action": "JOB_STARTED",
            "tone": "job-run",
            "detail": "Registry Scan started",
            "actor": "Admin",
        },
        {
            "time": "09:58 PM",
            "action": "REPO_ADDED",
            "tone": "repo",
            "detail": "Metadata Tools registered in UI demo set",
            "actor": "Admin",
        },
    ]


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
