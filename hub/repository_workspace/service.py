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
from hub.repository_workspace.process_manager import ProcessManager
from hub.repository_workspace.run_profiles import (
    PreparedLaunch,
    load_run_profiles,
    prepare_launch,
    profiles_for_repository,
    public_profile,
)
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

    def tree(self, repo: Repository) -> dict[str, Any]:
        files, _, git, _ = self._require(repo)
        tree = files.build_tree()
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

    # ---- Phase 2 runs ----

    def list_profiles(self, repo: Repository) -> list[dict[str, Any]]:
        return [public_profile(p) for p in profiles_for_repository(repo.id)]

    def preview_run(
        self,
        repo: Repository,
        *,
        profile_id: str,
        environment: str,
        port: int,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        launch = self._prepare(repo, profile_id, environment, port, confirm_live)
        return {
            "profile_id": launch.profile_id,
            "environment": launch.environment,
            "port": launch.port,
            "command_preview": [launch.executable, *launch.argv_redacted],
            "cwd": str(launch.cwd),
            "env_names": launch.env_names,
            "local_url": launch.local_url,
            "health_url": launch.health_url,
            "live_profile": launch.live_profile,
        }

    def _prepare(
        self,
        repo: Repository,
        profile_id: str,
        environment: str,
        port: int,
        confirm_live: bool,
    ) -> PreparedLaunch:
        root = self._root(repo)
        profiles = {p.id: p for p in load_run_profiles()}
        profile = profiles.get(profile_id)
        if profile is None or not profile.applies_to(repo.id):
            raise WorkspaceSecurityError(
                "Unknown or disallowed run profile.", code="not_found"
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
        port: int,
        confirm_live: bool = False,
    ) -> dict[str, Any]:
        launch = self._prepare(repo, profile_id, environment, port, confirm_live)
        run = self.processes.start(repo_id=repo.id, launch=launch)
        return run.to_public()

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
            return self._prepare(
                repo,
                old.profile_id,
                old.environment,
                old.port,
                confirm_live or old.environment == "live",
            )

        return self.processes.restart(run_id, _again).to_public()

    def list_runs(self, repo: Repository) -> list[dict[str, Any]]:
        return [r.to_public() for r in self.processes.list_runs(repo_id=repo.id)]

    def get_run(self, repo: Repository, run_id: str) -> dict[str, Any]:
        run = self.processes.get(run_id)
        if run is None or run.repo_id != repo.id:
            raise WorkspaceSecurityError(
                "Run not found for this repository.", code="not_found"
            )
        return run.to_public()

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
