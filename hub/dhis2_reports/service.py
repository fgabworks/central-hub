"""DHIS2 Report Workspace service — catalog, sync, preview, generate, view."""

from __future__ import annotations

import fnmatch
import time
from pathlib import Path
from typing import Any, Callable

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2_reports.db import utcnow
from hub.dhis2_reports.catalog import get_report, load_report_catalog
from hub.dhis2_reports.models import ReportDefinition, ResolvedRun
from hub.dhis2_reports.security import (
    ReportSecurityError,
    build_dhis2_report_url,
    build_standard_report_data_url,
    build_standard_report_open_url,
    configured_output_roots,
    iframe_sandbox_flags,
    period_to_dhis2_date,
    redact_report_detail,
    resolve_report_html,
    scrub_parameters,
    validate_environment,
    validate_org_unit,
    validate_period,
)
from hub.dhis2_reports.standard_models import (
    SyncedStandardReport,
    favorite_key,
    parse_favorite_key,
)
from hub.dhis2_reports.standard_sync import StandardReportSyncService
from hub.dhis2_reports.store import ReportsStore
from hub.registry.models import Registry
from hub.repository_workspace.git_status import RepositoryGitStatus
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.run_profiles import load_run_profiles, prepare_launch
from hub.repository_workspace.security import resolve_repo_root
from hub.repository_workspace.settings import load_workspace_settings

AuditFn = Callable[[str, str, str, bool], None]
ClientFactory = Callable[[str], Dhis2Client]


class Dhis2ReportsService:
    def __init__(
        self,
        store: ReportsStore | None = None,
        *,
        process_manager: ProcessManager | None = None,
        audit: AuditFn | None = None,
        get_dhis2_base_url: Callable[[str], str | None] | None = None,
        client_factory: ClientFactory | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.store = store or ReportsStore()
        self.processes = process_manager or ProcessManager()
        self.audit = audit
        self.get_dhis2_base_url = get_dhis2_base_url or (lambda _env: None)
        self.client_factory = client_factory
        self.registry = registry
        self.standard_sync = (
            StandardReportSyncService(
                self.store,
                client_factory=client_factory,
                audit=self._audit if audit else None,
            )
            if client_factory
            else None
        )

    def _audit(self, action: str, target: str, detail: str, ok: bool = True) -> None:
        if self.audit:
            self.audit(action, target, redact_report_detail(detail), ok)

    def dashboard_summary(self) -> dict[str, Any]:
        catalog = load_report_catalog()
        summary = self.store.summary()
        synced = self.store.synced_summary()
        summary["report_count"] = synced["total_synced"] or len(catalog)
        summary["total_reports"] = synced["total_synced"]
        summary["catalog_count"] = len(catalog)
        summary["stage_synced"] = synced["stage_count"]
        summary["live_synced"] = synced["live_count"]
        summary["last_sync"] = synced.get("last_sync")
        return summary

    def list_library(
        self,
        *,
        q: str = "",
        report_type: str = "",
        repository_id: str = "",
        environment: str = "",
        favorites_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Legacy catalog library (repository/static + optional YAML shortcuts)."""
        favorites = self.store.list_favorites()
        rows: list[dict[str, Any]] = []
        needle = (q or "").strip().lower()
        for report in load_report_catalog():
            if report_type and report.report_type != report_type:
                continue
            if repository_id and report.repository_id != repository_id:
                continue
            if environment and environment not in report.environments:
                continue
            fav = report.id in favorites
            if favorites_only and not fav:
                continue
            if needle:
                hay = " ".join(
                    [
                        report.name,
                        report.description,
                        report.source,
                        report.report_type,
                        report.repository_id or "",
                        " ".join(report.tags),
                    ]
                ).lower()
                if needle not in hay:
                    continue
            rows.append(
                report.to_public(
                    last_run=self.store.last_run_for(report.id),
                    favorite=fav,
                )
            )
        return rows

    def list_standard_library(
        self,
        *,
        q: str = "",
        report_type: str = "",
        environment: str = "",
        html_available: str = "",
        favorites_only: bool = False,
    ) -> dict[str, Any]:
        """Synced DHIS2 standard reports, Stage/Live kept separate."""
        favorites = self.store.list_favorites()
        html_only: bool | None = None
        if html_available in {"1", "true", "yes"}:
            html_only = True
        elif html_available in {"0", "false", "no"}:
            html_only = False

        def _section(env: str) -> dict[str, Any]:
            rows = self.store.list_synced_reports(
                environment=env,
                report_type=report_type,
                html_only=html_only,
                q=q,
                favorites_only=favorites_only,
                favorites=favorites,
            )
            last = self.store.last_sync_for(env)
            return {
                "environment": env,
                "reports": [r.to_public() for r in rows],
                "count": len(rows),
                "last_sync": last,
            }

        if environment in {"stage", "live"}:
            sections = [_section(environment)]
        else:
            sections = [_section("stage"), _section("live")]
        types = sorted(
            {
                r.report_type
                for r in self.store.list_synced_reports()
                if r.report_type
            }
        )
        return {"sections": sections, "report_types": types}

    def set_favorite(self, report_id: str, favorite: bool) -> None:
        parsed = parse_favorite_key(report_id)
        if parsed:
            env, uid = parsed
            if self.store.get_synced_report(env, uid) is None:
                raise ReportSecurityError("Report not found.", code="not_found")
        elif get_report(report_id) is None:
            raise ReportSecurityError("Report not found.", code="not_found")
        self.store.set_favorite(report_id, favorite)
        self._audit("DHIS2_REPORT_FAVORITE", report_id, f"favorite={favorite}")

    def sync_standard_reports(
        self,
        environment: str,
        *,
        confirm_live: bool = False,
        cache_design_content: bool = False,
    ) -> dict[str, Any]:
        if not self.standard_sync:
            raise ReportSecurityError(
                "Standard report sync is not configured.",
                code="unavailable",
            )
        return self.standard_sync.sync_environment(
            environment,
            confirm_live=confirm_live,
            cache_design_content=cache_design_content,
        )

    def refresh_standard_metadata(
        self,
        environment: str,
        uid: str,
        *,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        if not self.standard_sync:
            raise ReportSecurityError(
                "Standard report sync is not configured.",
                code="unavailable",
            )
        report = self.standard_sync.refresh_one(
            environment,
            uid,
            confirm_live=confirm_live,
            cache_design_content=True,
        )
        return report.to_public()

    def get_standard_report(self, environment: str, uid: str) -> SyncedStandardReport:
        env = validate_environment(environment)
        report = self.store.get_synced_report(env, uid)
        if report is None:
            raise ReportSecurityError("Synced report not found. Run Refresh sync first.", code="not_found")
        favorites = self.store.list_favorites()
        report.favorite = favorite_key(env, uid) in favorites
        return report

    def standard_urls(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
    ) -> dict[str, Any]:
        report = self.get_standard_report(environment, uid)
        env = report.environment
        base = self.get_dhis2_base_url(env)
        if not base:
            raise ReportSecurityError(
                f"DHIS2 {env} base URL is not configured.",
                code="dhis2_unconfigured",
            )
        period_v = validate_period(period, required=False)
        ou_v = validate_org_unit(org_unit, required=False)
        if report.needs_period and not period_v:
            raise ReportSecurityError("Period is required for this report.", code="invalid_period")
        if report.needs_org_unit and not ou_v:
            raise ReportSecurityError(
                "Organisation unit UID is required for this report.",
                code="invalid_org_unit",
            )
        open_url = build_standard_report_open_url(base_url=base, uid=report.uid)
        embed_url = build_standard_report_data_url(
            base_url=base,
            uid=report.uid,
            period=period_v,
            org_unit=ou_v,
        )
        return {
            "report": report.to_public(),
            "open_url": open_url,
            "embed_url": embed_url,
            "period": period_v,
            "org_unit": ou_v,
            "date": period_to_dhis2_date(period_v),
            "prefer_embed": report.render_supported,
            "fallback_hint": (
                "If the embedded view is blank (CSP, auth, or iframe restrictions), "
                "use Open in DHIS2 — your browser must already be logged into that instance."
            ),
        }

    def standard_viewer_payload(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
        mode: str = "embed",
    ) -> dict[str, Any]:
        urls = self.standard_urls(environment, uid, period=period, org_unit=org_unit)
        report = urls["report"]
        self._audit(
            "DHIS2_REPORT_VIEW",
            report["id"],
            f"mode={mode} pe={urls.get('period')} ou={urls.get('org_unit')}",
            True,
        )
        return {
            "kind": "standard_embed" if mode != "open" else "external",
            "report": report,
            "report_name": report["name"],
            "embed_url": urls["embed_url"],
            "open_url": urls["open_url"],
            "url": urls["open_url"],
            "period": urls["period"],
            "org_unit": urls["org_unit"],
            "date": urls["date"],
            "sandbox": iframe_sandbox_flags(allow_scripts=True),
            "allow_scripts": True,
            "prefer_embed": urls["prefer_embed"],
            "fallback_hint": urls["fallback_hint"],
            "credentials_in_url": False,
        }

    def fetch_standard_html_source(
        self,
        environment: str,
        uid: str,
        *,
        confirm_live: bool = False,
        prefer_design: bool = True,
    ) -> dict[str, Any]:
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live HTML source fetch requires explicit confirmation.",
                code="confirm_required",
            )
        report = self.get_standard_report(env, uid)
        html = ""
        source = "cache"
        if prefer_design:
            html = self.store.get_synced_design_content(env, uid)
        if not html and self.client_factory:
            client = self.client_factory(env)
            try:
                detail = client.get_metadata_object(
                    "reports",
                    uid,
                    fields="id,name,type,designContent",
                )
                raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
                html = str(raw.get("designContent") or "")
                source = "api_designContent"
                if html:
                    self.store.upsert_synced_report(report, design_content=html)
            except Dhis2Error as exc:
                raise ReportSecurityError(
                    redact_report_detail(exc.message),
                    code="unauthorized" if exc.status_code in {401, 403} else "unavailable",
                ) from exc
        if not html:
            raise ReportSecurityError(
                "No HTML design content available for this report.",
                code="missing_output",
            )
        self._audit("DHIS2_REPORT_VIEW_HTML", report.id, f"source={source}", True)
        return {
            "ok": True,
            "uid": uid,
            "environment": env,
            "name": report.name,
            "source": source,
            "html": html,
            "content_type": "text/html; charset=utf-8",
        }

    def download_standard_html(
        self,
        environment: str,
        uid: str,
        *,
        period: str = "",
        org_unit: str = "",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        """GET /api/reports/{uid}/data.html via hub credentials (never exposed to browser)."""
        env = validate_environment(environment)
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live HTML download requires explicit confirmation.",
                code="confirm_required",
            )
        if not self.client_factory:
            raise ReportSecurityError("DHIS2 client factory unavailable.", code="unavailable")
        report = self.get_standard_report(env, uid)
        period_v = validate_period(period, required=report.needs_period)
        ou_v = validate_org_unit(org_unit, required=report.needs_org_unit)
        params: dict[str, str] = {}
        date = period_to_dhis2_date(period_v)
        if date:
            params["date"] = date
        if ou_v:
            params["ou"] = ou_v
        client = self.client_factory(env)
        try:
            html = client.get_text(
                f"/api/reports/{uid}/data.html",
                params=params or None,
                accept="text/html, */*",
            )
        except Dhis2Error as exc:
            self._audit("DHIS2_REPORT_DOWNLOAD", report.id, exc.message, False)
            code = "unauthorized" if exc.status_code in {401, 403} else "unavailable"
            if exc.status_code == 404:
                code = "not_found"
            raise ReportSecurityError(redact_report_detail(exc.message), code=code) from exc
        # Redact accidental secrets in body for audit only; return HTML for download.
        if any(s in html.lower() for s in ("password=", "authorization:", "bearer ")):
            raise ReportSecurityError(
                "Refusing to return HTML that appears to contain secrets.",
                code="secret_blocked",
            )
        self._audit(
            "DHIS2_REPORT_DOWNLOAD",
            report.id,
            f"data.html pe={period_v} ou={ou_v}",
            True,
        )
        filename = f"{report.uid}-{env}.html"
        return {
            "filename": filename,
            "html": html,
            "report": report.to_public(),
            "period": period_v,
            "org_unit": ou_v,
        }

    def _collect_params(
        self,
        report: ReportDefinition,
        *,
        environment: str,
        period: str,
        org_unit: str,
        parameters: dict[str, Any] | None,
        output_format: str,
    ) -> dict[str, str]:
        env = validate_environment(environment)
        if env not in report.environments:
            raise ReportSecurityError(
                f"Report does not support environment {env}.",
                code="environment_blocked",
            )
        params = scrub_parameters(parameters)
        # Map period / org unit
        needs_period = any(
            (p.name == "period" or p.param_type == "period") and p.required
            for p in report.parameters
        )
        needs_ou = any(
            (p.name in {"orgUnit", "org_unit", "ou"} or p.param_type == "org_unit") and p.required
            for p in report.parameters
        )
        period_v = validate_period(period or params.get("period", ""), required=needs_period)
        ou_v = validate_org_unit(
            org_unit or params.get("orgUnit") or params.get("org_unit") or params.get("ou", ""),
            required=needs_ou,
        )
        if period_v:
            params["period"] = period_v
        if ou_v:
            params["orgUnit"] = ou_v
            params["org_unit"] = ou_v
            params.setdefault("ou", ou_v)
        params["format"] = (output_format or "html").strip().lower() or "html"
        params["report_id"] = report.id
        # Required custom params
        for spec in report.parameters:
            if spec.required and not params.get(spec.name) and not spec.default:
                if spec.param_type == "period" and period_v:
                    continue
                if spec.param_type == "org_unit" and ou_v:
                    continue
                raise ReportSecurityError(
                    f"Parameter {spec.name} is required.", code="missing_parameter"
                )
            if spec.name not in params and spec.default:
                params[spec.name] = spec.default
            if spec.choices and params.get(spec.name) and params[spec.name] not in spec.choices:
                raise ReportSecurityError(
                    f"Invalid choice for {spec.name}.", code="invalid_parameter"
                )
        return params

    def preview(
        self,
        report_id: str,
        *,
        environment: str,
        period: str = "",
        org_unit: str = "",
        parameters: dict[str, Any] | None = None,
        output_format: str = "html",
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        report = get_report(report_id)
        if report is None:
            raise ReportSecurityError("Report not found.", code="not_found")
        env = validate_environment(environment)
        params = self._collect_params(
            report,
            environment=env,
            period=period,
            org_unit=org_unit,
            parameters=parameters,
            output_format=output_format,
        )
        resolved = ResolvedRun(
            report_id=report.id,
            environment=env,
            parameters=params,
            output_format=params.get("format", "html"),
            live_confirm_required=env == "live",
        )
        if env == "live" and not confirm_live:
            resolved.warnings.append("Live report runs require explicit confirmation.")

        if report.report_type == "dhis2_standard":
            base = self.get_dhis2_base_url(env) or ""
            resolved.resolved_url = build_dhis2_report_url(
                base_url=base,
                url_template=report.url_template or "",
                parameters=params,
            )
        elif report.report_type == "repository_html":
            if report.run_profile_id:
                profiles = {p.id: p for p in load_run_profiles()}
                profile = profiles.get(report.run_profile_id)
                if profile is None:
                    raise ReportSecurityError("Run profile not found.", code="not_found")
                resolved.command_preview = [profile.executable, *list(profile.args)]
            elif report.capability_id:
                resolved.command_preview = [
                    "hub-job",
                    report.repository_id or "",
                    report.capability_id,
                ]
            resolved.warnings.append(
                "Repository HTML generation uses approved profiles/capabilities only; "
                "calculation logic stays in the connected repository."
            )
        elif report.report_type == "static_html":
            path = self._resolve_static(report)
            resolved.resolved_url = f"/dhis2/reports/view?path={path.name}"
            resolved.warnings.append(f"Static file: {path.name}")

        self._audit(
            "DHIS2_REPORT_PREVIEW",
            report.id,
            f"env={env} type={report.report_type}",
        )
        return {
            "report": report.to_public(favorite=report.id in self.store.list_favorites()),
            "resolved": {
                "environment": resolved.environment,
                "parameters": resolved.parameters,
                "output_format": resolved.output_format,
                "resolved_url": resolved.resolved_url,
                "command_preview": resolved.command_preview,
                "warnings": resolved.warnings,
                "live_confirm_required": resolved.live_confirm_required,
            },
        }

    def _resolve_static(self, report: ReportDefinition) -> Path:
        roots = configured_output_roots(list(report.output_roots))
        local = None
        if report.repository_id and self.registry:
            repo = self.registry.get(report.repository_id)
            if repo:
                local = repo.local_path or repo.working_directory
        return resolve_report_html(
            report.static_relative_path or "",
            roots=roots,
            repository_id=report.repository_id,
            registry_local_path=local,
        )

    def _git_meta(self, repository_id: str | None) -> tuple[str, str]:
        if not repository_id or not self.registry:
            return "", ""
        repo = self.registry.get(repository_id)
        if not repo:
            return "", ""
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        if root is None:
            return "", ""
        try:
            git = RepositoryGitStatus(root, load_workspace_settings())
            summary = git.summary()
            return str(summary.get("branch") or ""), ""
        except Exception:  # noqa: BLE001
            return "", ""

    def generate(
        self,
        report_id: str,
        *,
        environment: str,
        period: str = "",
        org_unit: str = "",
        parameters: dict[str, Any] | None = None,
        output_format: str = "html",
        confirm_live: bool = False,
        actor: str = "",
        job_store: Any | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            report_id,
            environment=environment,
            period=period,
            org_unit=org_unit,
            parameters=parameters,
            output_format=output_format,
            confirm_live=confirm_live,
        )
        report = get_report(report_id)
        assert report is not None
        env = preview["resolved"]["environment"]
        params = preview["resolved"]["parameters"]
        if env == "live" and not confirm_live:
            raise ReportSecurityError(
                "Live report runs require explicit confirmation.",
                code="confirm_required",
            )

        branch, commit = self._git_meta(report.repository_id)
        run = self.store.create_run(
            {
                "report_id": report.id,
                "report_name": report.name,
                "report_type": report.report_type,
                "environment": env,
                "period": params.get("period", ""),
                "org_unit": params.get("orgUnit", ""),
                "parameters": params,
                "repository_id": report.repository_id or "",
                "git_branch": branch,
                "git_commit": commit,
                "status": "running",
                "run_profile_id": report.run_profile_id or "",
                "actor": actor,
            }
        )

        try:
            if report.report_type == "dhis2_standard":
                url = preview["resolved"]["resolved_url"]
                self.store.update_run(
                    run["id"],
                    status="completed",
                    output_url=url or "",
                    finished_at=utcnow(),
                )
                self._audit(
                    "DHIS2_REPORT_GENERATE",
                    report.id,
                    f"type=dhis2_standard env={env} run={run['id']}",
                    True,
                )
            elif report.report_type == "static_html":
                path = self._resolve_static(report)
                self.store.update_run(
                    run["id"],
                    status="completed",
                    output_path=str(path),
                    finished_at=utcnow(),
                )
                self._audit(
                    "DHIS2_REPORT_GENERATE",
                    report.id,
                    f"type=static_html env={env} run={run['id']}",
                    True,
                )
            elif report.report_type == "repository_html":
                self._generate_repository_html(
                    report, run_id=run["id"], env=env, params=params, job_store=job_store, actor=actor
                )
            else:
                raise ReportSecurityError("Unsupported report type.", code="invalid_type")
        except ReportSecurityError as exc:
            self.store.update_run(
                run["id"],
                status="failed",
                error=str(exc),
                finished_at=utcnow(),
            )
            self._audit("DHIS2_REPORT_FAIL", report.id, f"run={run['id']} {exc}", False)
            raise

        return self.store.get_run(run["id"]) or run

    def _generate_repository_html(
        self,
        report: ReportDefinition,
        *,
        run_id: str,
        env: str,
        params: dict[str, str],
        job_store: Any | None,
        actor: str,
    ) -> None:
        # Prefer hub job capability when available
        if report.capability_id and report.repository_id and job_store is not None and self.registry:
            repo = self.registry.get(report.repository_id)
            if repo is None:
                raise ReportSecurityError("Repository not found.", code="not_found")
            if not any(c.id == report.capability_id for c in repo.capabilities):
                raise ReportSecurityError(
                    "Report capability is not registered on the repository.",
                    code="capability_missing",
                )
            job = job_store.create(
                repository_id=report.repository_id,
                capability_id=report.capability_id,
                dry_run=False,
                confirmed=True,
                actor=actor or "system",
                metadata={"report_id": report.id, "environment": env},
            )
            self.store.update_run(run_id, hub_job_id=job["id"], status="running")
            self._audit(
                "DHIS2_REPORT_GENERATE",
                report.id,
                f"type=repository_html job={job['id']} env={env}",
                True,
            )
            # Job worker runs async; mark completed when artifacts appear is left to refresh
            self.store.update_run(
                run_id,
                status="running",
                log_text=f"Submitted hub job {job['id']}",
            )
            return

        if not report.run_profile_id or not report.repository_id:
            raise ReportSecurityError(
                "repository_html requires an approved run profile or capability.",
                code="invalid_catalog",
            )
        if not self.registry:
            raise ReportSecurityError("Registry unavailable.", code="unavailable")
        repo = self.registry.get(report.repository_id)
        if repo is None:
            raise ReportSecurityError("Repository not found.", code="not_found")
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        if root is None:
            raise ReportSecurityError(
                "Repository local path unavailable for report generation.",
                code="unavailable",
            )
        profiles = {p.id: p for p in load_run_profiles()}
        profile = profiles.get(report.run_profile_id)
        if profile is None or not profile.applies_to(report.repository_id):
            raise ReportSecurityError("Run profile not allowed for repository.", code="profile_scope")

        port = profile.default_port
        launch = prepare_launch(
            profile,
            repo_id=report.repository_id,
            repository_path=root,
            environment="development" if env == "stage" else env,
            port=port,
            confirm_live=(env == "live"),
        )
        managed = self.processes.start(repo_id=report.repository_id, launch=launch)
        self.store.update_run(
            run_id,
            status="running",
            log_text=f"Started profile {profile.id} pid={managed.pid}",
        )
        # Poll briefly for HTML output under repo / configured roots
        deadline = time.time() + min(float(profile.startup_timeout_seconds) + 15, 120)
        found: Path | None = None
        roots = configured_output_roots(list(report.output_roots)) + [root]
        while time.time() < deadline:
            for base in roots:
                try:
                    for path in base.rglob("*"):
                        if not path.is_file():
                            continue
                        if not fnmatch.fnmatch(path.name.lower(), report.output_glob.lower()):
                            continue
                        # Prefer recently modified files
                        if path.stat().st_mtime >= time.time() - 180:
                            found = path
                            break
                except OSError:
                    continue
                if found:
                    break
            if found:
                break
            time.sleep(0.5)

        # Stop the managed process after generation attempt
        try:
            self.processes.stop(managed.run_id, reason="report generation finished")
        except Exception:  # noqa: BLE001
            pass

        if found is None:
            self.store.update_run(
                run_id,
                status="missing_output",
                error="Generation finished but no matching HTML output was found.",
                finished_at=utcnow(),
            )
            self._audit("DHIS2_REPORT_FAIL", report.id, f"run={run_id} missing_output", False)
            raise ReportSecurityError(
                "No HTML output found after generation.", code="missing_output"
            )

        self.store.update_run(
            run_id,
            status="completed",
            output_path=str(found),
            finished_at=utcnow(),
            log_text=f"Output {found.name}",
        )
        self._audit(
            "DHIS2_REPORT_GENERATE",
            report.id,
            f"type=repository_html env={env} run={run_id}",
            True,
        )

    def open_output(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ReportSecurityError("Run not found.", code="not_found")
        if run.get("output_url"):
            return {"kind": "url", "url": run["output_url"], "run": run}
        if run.get("output_path"):
            path = resolve_report_html(run["output_path"], roots=configured_output_roots())
            return {"kind": "file", "path": str(path), "run": run}
        raise ReportSecurityError("No output available for this run.", code="missing_output")

    def viewer_payload(self, *, path: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        allow_scripts = False
        report_name = "Report"
        if run_id:
            run = self.store.get_run(run_id)
            if run is None:
                raise ReportSecurityError("Run not found.", code="not_found")
            report = get_report(run["report_id"])
            allow_scripts = bool(report.allow_scripts) if report else False
            report_name = run.get("report_name") or report_name
            if run.get("output_url") and not run.get("output_path"):
                return {
                    "kind": "external",
                    "url": run["output_url"],
                    "report_name": report_name,
                    "sandbox": iframe_sandbox_flags(allow_scripts=False),
                    "run": run,
                }
            path = run.get("output_path")
        if not path:
            raise ReportSecurityError("Output path required.", code="missing_output")
        file_path = resolve_report_html(path, roots=configured_output_roots())
        try:
            html = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ReportSecurityError("Unable to read report HTML.", code="read_failed") from exc
        return {
            "kind": "html",
            "path": str(file_path),
            "name": file_path.name,
            "report_name": report_name,
            "html": html,
            "sandbox": iframe_sandbox_flags(allow_scripts=allow_scripts),
            "allow_scripts": allow_scripts,
        }
