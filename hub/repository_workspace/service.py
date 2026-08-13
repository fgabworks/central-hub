"""Facade over files / editor / git / external open / run manager for one repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hub.registry.models import Repository
from hub.repository_workspace.editor import RepositoryEditor
from hub.repository_workspace.files import RepositoryFiles
from hub.repository_workspace.git_status import RepositoryGitStatus
from hub.repository_workspace.logs import RunLogStore
from hub.repository_workspace.open_external import ExternalOpener
from hub.repository_workspace.profile_store import RunProfileStore
from hub.repository_workspace.process_detect import (
    detect_repository_processes,
    find_start_conflicts,
    stop_external_process,
    summarize_all_repositories,
)
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.run_profiles import (
    PreparedLaunch,
    live_runs_allowed,
    merged_profiles_for_repository,
    parse_profile,
    prepare_launch,
    profile_to_dict,
    profiles_for_repository,
    public_profile,
)
from hub.repository_workspace.run_status import build_run_dashboard, process_kind_label
from hub.repository_workspace.security import WorkspaceSecurityError, resolve_repo_root
from hub.repository_workspace.settings import WorkspaceSettings, load_workspace_settings

UNAVAILABLE_MESSAGE = (
    "Local workspace unavailable. Configure a local path to browse or edit this repository."
)

AuditFn = Callable[[str, str, str, bool], None]


class RepositoryWorkspaceService:
    def __init__(
        self,
        settings: WorkspaceSettings | None = None,
        *,
        process_manager: ProcessManager | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self.settings = settings or load_workspace_settings()
        self.logs = RunLogStore()
        self.processes = process_manager or ProcessManager(logs=self.logs, audit=audit)
        self.profile_store = RunProfileStore()

    def availability(self, repo: Repository) -> dict[str, Any]:
        root = resolve_repo_root(repo.local_path or repo.working_directory)
        available = root is not None
        return {
            "available": available,
            "repo_id": repo.id,
            "local_path": repo.local_path,
            "working_directory": repo.working_directory,
            "git_url": repo.git_url,
            "root": root.as_posix() if root else None,
            "message": None if available else UNAVAILABLE_MESSAGE,
        }

    def _require(
        self, repo: Repository
    ) -> tuple[RepositoryFiles, RepositoryEditor, RepositoryGitStatus, ExternalOpener]:
        info = self.availability(repo)
        if not info["available"]:
            raise WorkspaceSecurityError(UNAVAILABLE_MESSAGE, code="unavailable")
        root = Path(info["root"])
        return (
            RepositoryFiles(root, self.settings),
            RepositoryEditor(root, self.settings),
            RepositoryGitStatus(root, self.settings),
            ExternalOpener(root, self.settings),
        )

    def _root(self, repo: Repository) -> Path:
        info = self.availability(repo)
        if not info["available"]:
            raise WorkspaceSecurityError(UNAVAILABLE_MESSAGE, code="unavailable")
        return Path(info["root"])

    def tree(
        self, repo: Repository, *, include_excluded: bool = False
    ) -> dict[str, Any]:
        files, _, git, _ = self._require(repo)
        tree = files.build_tree(include_excluded=include_excluded)
        if git.is_git_repo():
            status = git.summary()
            by_path = {f["path"]: f["category"] for f in status["files"]}

            def annotate(nodes: list[dict[str, Any]]) -> None:
                for node in nodes:
                    if node["type"] == "file":
                        node["git_status"] = by_path.get(node["path"], "clean")
                    else:
                        annotate(node.get("children") or [])

            annotate(tree["entries"])
        return tree

    def preview(self, repo: Repository, path: str) -> dict[str, Any]:
        files, _, git, _ = self._require(repo)
        data = files.read_preview(path)
        try:
            data["git_status"] = git.file_status(path)
        except WorkspaceSecurityError:
            data["git_status"] = None
        return data

    def search(self, repo: Repository, *, q: str, mode: str = "filename") -> dict[str, Any]:
        files, _, _, _ = self._require(repo)
        if mode == "content":
            matches = files.search_content(q)
        else:
            matches = files.search_filenames(q)
        return {"q": q, "mode": mode, "matches": matches, "count": len(matches)}

    def preview_save(self, repo: Repository, path: str, content: str) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.preview_save(path, content)

    def save(self, repo: Repository, path: str, content: str, *, confirm: bool) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.save(path, content, confirm=confirm)

    def revert(self, repo: Repository, path: str) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.revert_to_disk(path)

    def create(
        self, repo: Repository, path: str, content: str = "", *, confirm: bool
    ) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.create_file(path, content, confirm=confirm)

    def rename(
        self, repo: Repository, path: str, new_path: str, *, confirm: bool
    ) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.rename(path, new_path, confirm=confirm)

    def delete(self, repo: Repository, path: str, *, confirm: bool) -> dict[str, Any]:
        _, editor, _, _ = self._require(repo)
        return editor.delete(path, confirm=confirm)

    def changes(self, repo: Repository) -> dict[str, Any]:
        _, _, git, _ = self._require(repo)
        return git.summary()

    def diff(
        self, repo: Repository, path: str | None = None, *, side_by_side: bool = False
    ) -> dict[str, Any]:
        _, _, git, _ = self._require(repo)
        return git.diff(path, side_by_side=side_by_side)

    def open_external(
        self, repo: Repository, target: str, path: str | None = None
    ) -> dict[str, Any]:
        _, _, _, opener = self._require(repo)
        return opener.open(target, path)

    # ---- Phase 2 runs + profile builder ----

    def list_profiles(self, repo: Repository) -> list[dict[str, Any]]:
        """Enabled + approved profiles for the Run UI."""
        return [
            public_profile(p)
            for p in profiles_for_repository(repo.id, store=self.profile_store)
        ]

    def list_managed_profiles(self, repo: Repository) -> dict[str, Any]:
        """Settings builder: templates, approved DB profiles, unapproved suggestions."""
        merged = merged_profiles_for_repository(
            repo.id,
            store=self.profile_store,
            include_disabled=True,
            include_unapproved=True,
        )
        templates: list[dict[str, Any]] = []
        approved: list[dict[str, Any]] = []
        suggestions: list[dict[str, Any]] = []
        for p in merged:
            row = public_profile(p)
            if p.source == "yaml":
                templates.append(row)
            elif p.approved:
                approved.append(row)
            else:
                suggestions.append(row)
        return {
            "templates": templates,
            "approved": approved,
            "suggestions": suggestions,
            "live_runs_allowed": live_runs_allowed(),
            "environments": ["development", "stage", "live", "custom"],
            "port_modes": [
                "none",
                "fixed",
                "argument",
                "environment_variable",
            ],
        }

    def _persist_profile(
        self,
        repo: Repository,
        raw: dict[str, Any],
        *,
        approve: bool,
        source: str,
    ) -> dict[str, Any]:
        self._root(repo)
        payload = dict(raw)
        if "environment" in payload and not payload.get("environments"):
            env = str(payload.get("environment") or "development").strip().lower()
            payload["environments"] = [env] if env else ["development"]
        payload["repository_ids"] = [repo.id]
        payload["approved"] = bool(approve)
        if not approve:
            payload["enabled"] = False
            payload["approved"] = False
        elif "enabled" not in payload:
            payload["enabled"] = True
        src = source if source in ("user", "suggestion", "yaml") else "user"
        payload["source"] = src
        profile = parse_profile(payload)
        stored = self.profile_store.upsert(repo.id, profile_to_dict(profile) | {
            "source": src,
            "approved": bool(approve),
            "enabled": bool(profile.enabled if approve else False),
        })
        return public_profile(parse_profile(stored))

    def save_managed_profile(
        self,
        repo: Repository,
        payload: dict[str, Any],
        *,
        approve: bool = True,
        source: str = "user",
    ) -> dict[str, Any]:
        return self._persist_profile(repo, payload, approve=approve, source=source)

    def duplicate_managed_profile(
        self, repo: Repository, profile_id: str, new_id: str | None = None
    ) -> dict[str, Any]:
        merged = {
            p.id: p
            for p in merged_profiles_for_repository(
                repo.id,
                store=self.profile_store,
                include_disabled=True,
                include_unapproved=True,
            )
        }
        src = merged.get(profile_id)
        if src is None or not src.applies_to(repo.id):
            raise WorkspaceSecurityError("Profile not found.", code="not_found")
        nid = (new_id or f"{src.id}-copy").strip()
        if nid in merged and merged[nid].source != "yaml":
            raise WorkspaceSecurityError(
                "A profile with that ID already exists.", code="conflict"
            )
        data = profile_to_dict(src)
        data["id"] = nid
        data["name"] = f"{src.name} (copy)"
        data["enabled"] = False
        data["approved"] = True
        data["source"] = "user"
        data["repository_ids"] = [repo.id]
        return self._persist_profile(repo, data, approve=True, source="user")

    def set_profile_enabled(
        self, repo: Repository, profile_id: str, enabled: bool
    ) -> dict[str, Any]:
        row = self.profile_store.get(repo.id, profile_id)
        if row is None:
            merged = {
                p.id: p
                for p in merged_profiles_for_repository(
                    repo.id,
                    store=self.profile_store,
                    include_disabled=True,
                    include_unapproved=True,
                )
            }
            src = merged.get(profile_id)
            if src is None or src.source != "yaml" or not src.applies_to(repo.id):
                raise WorkspaceSecurityError("Profile not found.", code="not_found")
            data = profile_to_dict(src)
            data["enabled"] = bool(enabled)
            data["approved"] = True
            data["repository_ids"] = [repo.id]
            return self._persist_profile(repo, data, approve=True, source="user")
        updated = self.profile_store.set_enabled(repo.id, profile_id, enabled)
        assert updated is not None
        return public_profile(parse_profile(updated))

    def delete_managed_profile(self, repo: Repository, profile_id: str) -> None:
        if not self.profile_store.delete(repo.id, profile_id):
            raise WorkspaceSecurityError(
                "Only repository-specific profiles can be deleted "
                "(YAML templates remain in config/run_profiles.yaml).",
                code="forbidden",
            )

    def test_managed_profile(
        self,
        repo: Repository,
        payload: dict[str, Any],
        *,
        port: int | None = None,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        self._root(repo)
        raw = dict(payload)
        raw["repositories"] = [repo.id]
        raw.setdefault("approved", True)
        raw.setdefault("enabled", True)
        profile = parse_profile(raw)
        env = str(
            raw.get("environment")
            or (profile.environments[0] if profile.environments else "development")
        )
        launch = prepare_launch(
            profile,
            repo_id=repo.id,
            repository_path=self._root(repo),
            environment=env,
            port=port,
            confirm_live=confirm_live,
        )
        return {
            "ok": True,
            "profile_id": launch.profile_id,
            "environment": launch.environment,
            "port": launch.port,
            "port_mode": launch.port_mode,
            "command_preview": [launch.executable, *launch.argv_redacted],
            "cwd": str(launch.cwd),
            "env_names": launch.env_names,
            "local_url": launch.local_url,
            "health_url": launch.health_url,
            "live_profile": launch.live_profile,
            "write_capable": launch.write_capable,
        }

    def preview_run(
        self,
        repo: Repository,
        *,
        profile_id: str,
        environment: str,
        port: int | None = None,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        launch = self._prepare(repo, profile_id, environment, port, confirm_live)
        return {
            "profile_id": launch.profile_id,
            "environment": launch.environment,
            "port": launch.port,
            "port_mode": launch.port_mode,
            "command_preview": [launch.executable, *launch.argv_redacted],
            "cwd": str(launch.cwd),
            "env_names": launch.env_names,
            "local_url": launch.local_url,
            "health_url": launch.health_url,
            "live_profile": launch.live_profile,
            "write_capable": launch.write_capable,
        }

    def _prepare(
        self,
        repo: Repository,
        profile_id: str,
        environment: str,
        port: int | None,
        confirm_live: bool,
    ) -> PreparedLaunch:
        root = self._root(repo)
        profiles = {
            p.id: p
            for p in merged_profiles_for_repository(repo.id, store=self.profile_store)
        }
        profile = profiles.get(profile_id)
        if profile is None or not profile.applies_to(repo.id):
            raise WorkspaceSecurityError(
                "Unknown or disallowed run profile.", code="not_found"
            )
        if not profile.enabled or not profile.approved:
            raise WorkspaceSecurityError(
                "Profile is disabled or not yet approved.", code="forbidden"
            )
        return prepare_launch(
            profile,
            repo_id=repo.id,
            repository_path=root,
            environment=environment,
            port=port,
            confirm_live=confirm_live,
        )

    def find_port(self, preferred: int) -> dict[str, Any]:
        port = self.processes.find_port(int(preferred))
        return {
            "preferred": int(preferred),
            "port": port,
            "available": port is not None and port == int(preferred),
        }

    def start_run(
        self,
        repo: Repository,
        *,
        profile_id: str,
        environment: str,
        port: int | None = None,
        confirm_live: bool = False,
        bypass_process_conflicts: bool = False,
    ) -> dict[str, Any]:
        launch = self._prepare(repo, profile_id, environment, port, confirm_live)
        if not bypass_process_conflicts:
            profiles = {
                p.id: p
                for p in merged_profiles_for_repository(
                    repo.id,
                    store=self.profile_store,
                    include_disabled=True,
                    include_unapproved=True,
                )
            }
            profile = profiles.get(profile_id)
            if profile is not None:
                conflict = find_start_conflicts(
                    repo,
                    process_manager=self.processes,
                    profile=profile,
                    resolved_port=launch.port,
                )
                if conflict.get("blocked"):
                    raise WorkspaceSecurityError(
                        conflict.get("message")
                        or "Conflicting repository process detected.",
                        code="process_conflict",
                    )
        run = self.processes.start(repo_id=repo.id, launch=launch)
        from hub.repository_workspace.run_status import reconcile_run

        return reconcile_run(run, check_port=False).to_public()

    def scan_processes(self, repo: Repository) -> dict[str, Any]:
        rows = detect_repository_processes(repo, process_manager=self.processes)
        from hub.repository_workspace.run_status import pick_active_run

        active_run = pick_active_run(self.processes.list_runs(repo_id=repo.id))
        active_id = active_run.run_id if active_run else None
        processes = []
        for row in rows:
            public = row.to_public()
            public["process_kind"] = process_kind_label(public)
            public["linked_to_active"] = bool(
                public.get("managed_by_hub")
                and public.get("run_id")
                and public.get("run_id") == active_id
            )
            processes.append(public)
        return {
            "repo_id": repo.id,
            "count": len(processes),
            "processes": processes,
            "active_run_id": active_id,
        }

    def stop_detected_process(
        self,
        repo: Repository,
        *,
        pid: int,
        identity_token: str,
        force: bool = False,
        confirm: bool = False,
        typed_confirm: str | None = None,
        run_id: str | None = None,
        port: int | None = None,
        managed_by_hub: bool | None = None,
        confidence: str | None = None,
    ) -> dict[str, Any]:
        scan = detect_repository_processes(repo, process_manager=self.processes)
        match = next((r for r in scan if r.pid == int(pid)), None)
        if match is None:
            raise WorkspaceSecurityError(
                "Process not found in the latest repository scan.",
                code="not_found",
            )
        if match.identity_token != identity_token:
            raise WorkspaceSecurityError(
                "Process identity changed since scan (PID reuse protection).",
                code="pid_reuse",
            )
        if match.view_only or match.confidence == "Low":
            raise WorkspaceSecurityError(
                "Low-confidence processes are view-only and cannot be stopped.",
                code="view_only",
            )
        if not confirm:
            raise WorkspaceSecurityError(
                "Explicit confirmation is required before stopping a process.",
                code="confirm_required",
            )
        if match.requires_typed_confirm:
            expected = match.typed_confirm_phrase or f"STOP PROCESS {match.pid}"
            if (typed_confirm or "").strip() != expected:
                raise WorkspaceSecurityError(
                    f'Type "{expected}" to stop this medium-confidence process.',
                    code="typed_confirm_required",
                )

        if match.managed_by_hub and match.run_id:
            from hub.repository_workspace.ports import port_available

            result = self.processes.stop(
                match.run_id,
                reason=(
                    "force stop from Repository Processes"
                    if force
                    else "graceful stop from Repository Processes"
                ),
            )
            port_val = int(result.port) if result.port else (port or match.port)
            ended = result.status == "stopped" or (
                "still appears alive" not in (result.error or "")
                and result.status in {"stopped", "failed"}
            )
            if "still appears alive" in (result.error or ""):
                ended = False
            elif result.status == "stopped":
                ended = True
            port_released = port_available(int(port_val)) if port_val else None
            return {
                "ok": True,
                "pid": match.pid,
                "managed_by_hub": True,
                "run": result.to_public(),
                "ended": ended,
                "port": port_val,
                "port_released": port_released,
                "force": bool(force),
            }

        stopped = stop_external_process(
            pid=match.pid,
            identity_token=identity_token,
            force=force,
            port=port or match.port,
        )
        return {"ok": True, "managed_by_hub": False, **stopped}

    def summarize_local_processes(self, repositories: list) -> list[dict[str, Any]]:
        return summarize_all_repositories(
            repositories, process_manager=self.processes
        )

    def stop_run(self, repo: Repository, run_id: str) -> dict[str, Any]:
        run = self.processes.get(run_id)
        if run is None or run.repo_id != repo.id:
            raise WorkspaceSecurityError(
                "Run not found for this repository.", code="not_found"
            )
        return self.processes.stop(run_id).to_public()


    def restart_run(
        self,
        repo: Repository,
        run_id: str,
        *,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        existing = self.processes.get(run_id)
        if existing is None or existing.repo_id != repo.id:
            raise WorkspaceSecurityError(
                "Run not found for this repository.", code="not_found"
            )

        def _again(old):
            port = old.port if old.port and old.port > 0 else None
            return self._prepare(
                repo,
                old.profile_id,
                old.environment,
                port,
                confirm_live or old.environment == "live",
            )

        return self.processes.restart(run_id, _again).to_public()

    def list_runs(self, repo: Repository) -> list[dict[str, Any]]:
        return [
            r.to_public()
            for r in self.processes.list_runs(repo_id=repo.id, refresh=True)
        ]

    def run_dashboard(
        self,
        repo: Repository,
        *,
        preferred_profile_id: str = "",
        preferred_environment: str = "development",
        preferred_port: int | None = None,
        check_ports: bool = True,
    ) -> dict[str, Any]:
        """Active Application + history with process/health reconciliation."""
        runs = self.processes.list_runs(repo_id=repo.id, refresh=True)
        return build_run_dashboard(
            runs,
            repo_id=repo.id,
            preferred_profile_id=preferred_profile_id,
            preferred_environment=preferred_environment,
            preferred_port=preferred_port,
            check_ports=check_ports,
        )

    def get_run(self, repo: Repository, run_id: str) -> dict[str, Any]:
        run = self.processes.get(run_id)
        if run is None or run.repo_id != repo.id:
            raise WorkspaceSecurityError(
                "Run not found for this repository.", code="not_found"
            )
        from hub.repository_workspace.run_status import reconcile_run

        return reconcile_run(run).to_public()

    def read_logs(
        self, repo: Repository, run_id: str, *, offset: int = 0, limit: int = 300
    ) -> dict[str, Any]:
        run = self.processes.get(run_id)
        if run is None or run.repo_id != repo.id:
            raise WorkspaceSecurityError(
                "Run not found for this repository.", code="not_found"
            )
        payload = self.logs.read(run_id, offset=offset, limit=limit)
        payload["run"] = run.to_public()
        return payload

