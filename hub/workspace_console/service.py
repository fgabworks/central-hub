"""Thin aggregators for Workspace Console tabs — reuse existing services."""

from __future__ import annotations

from typing import Any

from hub.agent_center.redact import redact_text
from hub.jobs.store import progress_payload
from hub.repository_workspace.process_polling import PROCESS_SCAN_INTERVAL_MS, polling_config_for_ui
from hub.repository_workspace.security import redact_audit_detail


MAX_PROBLEMS = 80
MAX_OUTPUT_LINES = 400
MAX_DEBUG_EVENTS = 60
MAX_PORTS = 120


class WorkspaceConsoleService:
    """Facade over repo workspace / jobs / audit / agent center — no process logic copy."""

    def __init__(
        self,
        *,
        registry: Any,
        repo_workspace: Any,
        job_store: Any | None = None,
        audit: Any | None = None,
        agent_center: Any | None = None,
        adapters: Any | None = None,
    ) -> None:
        self.registry = registry
        self.repo_workspace = repo_workspace
        self.job_store = job_store
        self.audit = audit
        self.agent_center = agent_center
        self.adapters = adapters

    def problems(self, *, limit: int = MAX_PROBLEMS) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        limit = max(1, min(int(limit), MAX_PROBLEMS))

        if self.job_store is not None:
            for job in self.job_store.list_recent(limit=40):
                payload = progress_payload(job)
                status = str(payload.get("status") or "").lower()
                err = payload.get("error") or ""
                if status in {"failed", "error"} or err:
                    rows.append(
                        {
                            "id": f"job:{payload.get('id')}",
                            "source": "job",
                            "severity": "error" if status in {"failed", "error"} else "warning",
                            "title": f"Job {payload.get('capability_id') or payload.get('id')}",
                            "detail": redact_text(str(err or payload.get("message") or status), limit=400),
                            "repository_id": payload.get("repository_id") or "",
                            "timestamp": payload.get("updated_at") or payload.get("finished_at") or "",
                        }
                    )

        if self.repo_workspace is not None and self.registry is not None:
            for run in self.repo_workspace.processes.list_runs(refresh=False)[:40]:
                public = run.to_public() if hasattr(run, "to_public") else dict(run)
                status = str(public.get("status") or public.get("display_status") or "").lower()
                err = public.get("error") or ""
                if "fail" in status or err or "unhealthy" in status:
                    rows.append(
                        {
                            "id": f"run:{public.get('run_id') or public.get('id')}",
                            "source": "repository",
                            "severity": "error" if "fail" in status else "warning",
                            "title": f"Repository run {public.get('profile_id') or public.get('run_id')}",
                            "detail": redact_text(str(err or status), limit=400),
                            "repository_id": public.get("repo_id") or "",
                            "timestamp": public.get("updated_at") or public.get("finished_at") or "",
                        }
                    )

        if self.audit is not None:
            for event in self.audit.list_recent(limit=40):
                if event.get("ok") is False:
                    rows.append(
                        {
                            "id": f"audit:{event.get('timestamp')}:{event.get('action')}",
                            "source": "audit",
                            "severity": "error",
                            "title": str(event.get("action") or "Audit failure"),
                            "detail": redact_text(str(event.get("detail") or ""), limit=400),
                            "repository_id": str((event.get("metadata") or {}).get("repository_id") or ""),
                            "timestamp": event.get("timestamp") or "",
                        }
                    )

        if self.adapters is not None and hasattr(self.adapters, "last_results"):
            try:
                for item in list(getattr(self.adapters, "last_results") or [])[:20]:
                    if not item.get("ok", True):
                        rows.append(
                            {
                                "id": f"health:{item.get('id')}",
                                "source": "provider",
                                "severity": "warning",
                                "title": f"Health {item.get('name') or item.get('id')}",
                                "detail": redact_text(str(item.get("detail") or item.get("error") or ""), limit=400),
                                "repository_id": item.get("id") or "",
                                "timestamp": item.get("checked_at") or "",
                            }
                        )
            except Exception:
                pass

        if self.agent_center is not None:
            try:
                for conn in self.agent_center.connections.list(probe=False)[:20]:
                    state = str(conn.get("state") or "")
                    if state in {"error", "unavailable", "authentication_required"}:
                        rows.append(
                            {
                                "id": f"ai:{conn.get('id')}",
                                "source": "provider",
                                "severity": "warning" if state != "error" else "error",
                                "title": f"AI Connection {conn.get('label') or conn.get('id')}",
                                "detail": redact_text(str(conn.get("detail") or state), limit=400),
                                "repository_id": "",
                                "timestamp": conn.get("last_check") or "",
                            }
                        )
            except Exception:
                pass

        rows = rows[:limit]
        return {"ok": True, "count": len(rows), "problems": rows}

    def output(
        self,
        *,
        source: str = "all",
        repo_id: str = "",
        run_id: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        source_n = (source or "all").strip().lower()
        limit = max(1, min(int(limit), MAX_OUTPUT_LINES))
        offset = max(0, int(offset))
        lines: list[dict[str, Any]] = []

        if source_n in {"all", "repository", "runs"} and self.repo_workspace is not None:
            target_run = run_id
            target_repo = repo_id
            if not target_run:
                runs = self.repo_workspace.processes.list_runs(
                    repo_id=target_repo or None, refresh=False
                )
                if runs:
                    newest = runs[0]
                    target_run = newest.run_id if hasattr(newest, "run_id") else newest.get("run_id")
                    target_repo = newest.repo_id if hasattr(newest, "repo_id") else newest.get("repo_id")
            if target_run and target_repo and self.registry is not None:
                repo = self.registry.get(target_repo)
                if repo is not None:
                    try:
                        chunk = self.repo_workspace.read_logs(
                            repo, target_run, offset=offset, limit=limit
                        )
                        for row in chunk.get("lines") or chunk.get("entries") or []:
                            if isinstance(row, dict):
                                text = redact_text(str(row.get("text") or row.get("line") or ""), limit=2000)
                                lines.append(
                                    {
                                        "source": "repository",
                                        "repository_id": target_repo,
                                        "run_id": target_run,
                                        "text": text,
                                        "timestamp": row.get("timestamp") or "",
                                    }
                                )
                            else:
                                lines.append(
                                    {
                                        "source": "repository",
                                        "repository_id": target_repo,
                                        "run_id": target_run,
                                        "text": redact_text(str(row), limit=2000),
                                        "timestamp": "",
                                    }
                                )
                    except Exception as exc:
                        lines.append(
                            {
                                "source": "repository",
                                "repository_id": target_repo,
                                "run_id": target_run,
                                "text": redact_text(str(exc), limit=400),
                                "timestamp": "",
                            }
                        )

        if source_n in {"all", "jobs"} and self.job_store is not None:
            for job in self.job_store.list_recent(limit=15):
                payload = progress_payload(job)
                msg = payload.get("error") or payload.get("message") or payload.get("status")
                lines.append(
                    {
                        "source": "jobs",
                        "repository_id": payload.get("repository_id") or "",
                        "run_id": payload.get("id") or "",
                        "text": redact_text(str(msg), limit=500),
                        "timestamp": payload.get("updated_at") or "",
                    }
                )

        if source_n in {"all", "agents", "ai"} and self.agent_center is not None:
            try:
                for run in self.agent_center.history(limit=10, profile_id="okarun")[:10]:
                    preview = run.get("prompt_preview") or run.get("error") or run.get("status")
                    lines.append(
                        {
                            "source": "agents",
                            "repository_id": ",".join(run.get("repository_ids") or []),
                            "run_id": run.get("id") or "",
                            "text": redact_text(
                                f"[{run.get('agent_id')}] {run.get('status')}: {preview}",
                                limit=500,
                            ),
                            "timestamp": run.get("created_at") or "",
                        }
                    )
            except Exception:
                pass

        if source_n in {"all", "audit", "service"} and self.audit is not None:
            for event in self.audit.list_recent(limit=20):
                lines.append(
                    {
                        "source": "service",
                        "repository_id": "",
                        "run_id": "",
                        "text": redact_text(
                            f"{event.get('action')}: {event.get('detail')}",
                            limit=500,
                        ),
                        "timestamp": event.get("timestamp") or "",
                    }
                )

        return {
            "ok": True,
            "source": source_n,
            "offset": offset,
            "limit": limit,
            "count": len(lines[:limit]),
            "lines": lines[:limit],
            "sources": ["all", "repository", "jobs", "agents", "service"],
        }

    def debug(self, *, limit: int = MAX_DEBUG_EVENTS) -> dict[str, Any]:
        limit = max(1, min(int(limit), MAX_DEBUG_EVENTS))
        events: list[dict[str, Any]] = []
        if self.audit is not None:
            for event in self.audit.list_recent(limit=limit):
                events.append(
                    {
                        "type": "audit",
                        "action": event.get("action"),
                        "ok": event.get("ok"),
                        "detail": redact_audit_detail(str(event.get("detail") or "")),
                        "timestamp": event.get("timestamp"),
                        "metadata": redact_audit_detail(str(event.get("metadata") or {})),
                    }
                )
        polling = polling_config_for_ui()
        return {
            "ok": True,
            "events": events[:limit],
            "diagnostics": {
                "polling": polling,
                "process_scan_interval_ms": PROCESS_SCAN_INTERVAL_MS,
                "note": "Structured diagnostics only. Process scans run when Ports tab is visible.",
            },
        }

    def terminal_catalog(self) -> dict[str, Any]:
        """Connected local repositories + optional approved run profiles.

        Interactive PTY shells are created via TerminalSessionManager and jailed to
        each repository's configured local path — never an arbitrary free shell.
        """
        import os

        repos: list[dict[str, Any]] = []
        if self.registry is None:
            return {"ok": True, "repositories": [], "free_shell": False, "interactive_pty": True}
        for repo in self.registry.enabled_repositories():
            if not (repo.local_path or repo.working_directory):
                continue
            profiles: list[dict[str, Any]] = []
            if self.repo_workspace is not None:
                try:
                    profiles = self.repo_workspace.list_profiles(repo)
                except Exception:
                    profiles = []
            repos.append(
                {
                    "id": repo.id,
                    "name": repo.name,
                    "path": repo.local_path or repo.working_directory or "",
                    "profiles": [
                        {
                            "id": p.get("id"),
                            "label": p.get("label") or p.get("name") or p.get("id"),
                            "environment_default": p.get("environment_default") or "development",
                            "live": bool(p.get("live") or p.get("provides_api")),
                        }
                        for p in profiles
                        if isinstance(p, dict)
                    ],
                }
            )
        return {
            "ok": True,
            "repositories": repos,
            "free_shell": False,
            "interactive_pty": True,
            "shells": (
                [{"id": "powershell", "label": "PowerShell"}, {"id": "cmd", "label": "CMD"}]
                if os.name == "nt"
                else [
                    {"id": "bash", "label": "bash"},
                    {"id": "sh", "label": "sh"},
                ]
            ),
            "message": (
                "Open an interactive terminal inside a connected repository path, "
                "or start an approved run profile. AI cannot execute terminal commands."
            ),
        }

    def ports(self) -> dict[str, Any]:
        """Reuse process monitor summary — no duplicated ownership logic."""
        if self.repo_workspace is None or self.registry is None:
            return {"ok": True, "ports": [], "count": 0}
        rows = self.repo_workspace.summarize_local_processes(list(self.registry.repositories))
        ports: list[dict[str, Any]] = []
        for row in rows[:MAX_PORTS]:
            ports.append(
                {
                    "port": row.get("port"),
                    "pid": row.get("pid"),
                    "process": redact_text(
                        str(row.get("command_redacted") or row.get("executable") or ""),
                        limit=200,
                    ),
                    "repository_id": row.get("repo_id") or "",
                    "repository_name": row.get("repository_name") or "",
                    "health": ", ".join(row.get("detection_reasons") or []) or (row.get("confidence") or ""),
                    "managed_by_hub": bool(row.get("managed_by_hub")),
                    "external": not bool(row.get("managed_by_hub")),
                    "confidence": row.get("confidence") or "",
                    "view_only": bool(row.get("view_only")),
                    "requires_typed_confirm": bool(row.get("requires_typed_confirm")),
                    "typed_confirm_phrase": row.get("typed_confirm_phrase") or "",
                    "identity_token": row.get("identity_token") or "",
                    "run_id": row.get("run_id") or "",
                    "open_url": f"http://127.0.0.1:{row['port']}" if row.get("port") else "",
                    "can_stop": bool(row.get("stoppable")) and not bool(row.get("view_only")),
                }
            )
        return {
            "ok": True,
            "count": len(ports),
            "ports": ports,
            "polling": polling_config_for_ui(),
            "stop_note": "Stops require confirmation and PID fingerprint verification via repository process APIs.",
        }
