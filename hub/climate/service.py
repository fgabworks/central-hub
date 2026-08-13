"""CLIMATE workspace facade with workspace/repository isolation."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from typing import Any

from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.registry.models import Registry, Repository
from hub.repository_workspace.security import WorkspaceSecurityError
from hub.repository_workspace.service import RepositoryWorkspaceService


def normalize_workspace(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"vanta", "work"}:
        return "work"
    if value in {"arctic", "personal"}:
        return "personal"
    raise ClimateCodingError("Unknown CLIMATE workspace", code="workspace_invalid")


def repo_workspace(repo: Repository) -> str:
    tags = {str(tag).strip().lower() for tag in repo.tags}
    return "personal" if tags & {"personal", "arctic"} else "work"


@dataclass
class Proposal:
    run_id: str
    workspace: str
    repository_id: str
    state: str = "pending"
    edits: list[dict[str, Any]] = field(default_factory=list)


class ClimateService:
    def __init__(
        self,
        registry: Registry,
        repository_workspace: RepositoryWorkspaceService,
        coding: ClimateCodingAdapter,
    ) -> None:
        self.registry = registry
        self.repository_workspace = repository_workspace
        self.coding = coding
        self._run_scope: dict[str, tuple[str, str]] = {}
        self._proposals: dict[str, Proposal] = {}

    def repositories(self, workspace: str) -> list[dict[str, Any]]:
        ws = normalize_workspace(workspace)
        rows = []
        for repo in self.registry.enabled_repositories():
            if repo_workspace(repo) != ws or repo.type != "command":
                continue
            availability = self.repository_workspace.availability(repo)
            rows.append({
                "id": repo.id,
                "name": repo.name,
                "workspace": ws,
                "available": bool(availability.get("available")),
                "branch": self._branch(repo) if availability.get("available") else None,
            })
        return rows

    def require_repo(self, workspace: str, repository_id: str) -> Repository:
        ws = normalize_workspace(workspace)
        repo = self.registry.get(repository_id)
        if repo is None or not repo.enabled:
            raise ClimateCodingError("Repository not found", code="not_found")
        if repo_workspace(repo) != ws:
            raise ClimateCodingError("Repository belongs to another workspace", code="workspace_isolation")
        if repo.type != "command":
            raise ClimateCodingError("Repository has no local file workspace", code="repository_unavailable")
        return repo

    def bootstrap(self, workspace: str, repository_id: str = "") -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        repos = self.repositories(ws)
        active = repository_id or next((row["id"] for row in repos if row["available"]), "")
        if active:
            self.require_repo(ws, active)
        active_repo = next((row for row in repos if row["id"] == active), None)
        active_name = str((active_repo or {}).get("name") or "").strip()
        if ws == "work":
            context_label = "VANTA / DOH"
        else:
            context_label = "ARCTIC"
        if active_name:
            context_label = f"{context_label} / {active_name}"
        providers = self.coding.availability()
        if ws == "personal":
            providers = [dict(row) for row in providers]
            for row in providers:
                if row.get("id") == "codex":
                    row.update({
                        "state": "workspace_unsupported",
                        "status": "VANTA only",
                        "available": False,
                        "detail": "Codex is not enabled for the isolated ARCTIC profile.",
                    })
        return {
            "ok": True,
            "workspace": ws,
            "workspace_label": "VANTA" if ws == "work" else "ARCTIC",
            "context_label": context_label,
            "repositories": repos,
            "active_repository_id": active,
            "providers": providers,
            "coding_defaults": self.coding.coding_defaults(),
            "safety": {
                "safe_file_api": True,
                "proposal_confirmation": True,
                "unrestricted_shell": False,
                "workspace_isolation": True,
            },
        }

    def execute(self, workspace: str, repository_id: str, **payload: Any) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        repo = self.require_repo(ws, repository_id)
        if not self.repository_workspace.availability(repo).get("available"):
            raise ClimateCodingError("Local repository unavailable", code="repository_unavailable")
        current_file = str(payload.get("current_file") or "").replace("\\", "/")
        selected_files = list(dict.fromkeys(
            str(path).replace("\\", "/")
            for path in list(payload.get("selected_files") or [])
            if str(path).strip()
        ))
        # ARCTIC deliberately does not enter the Work/AiriX repository profile.
        # Pack only the explicitly selected personal files into the user-owned
        # prompt so the existing provider runner remains isolated.
        selection = str(payload.get("selection") or "")
        if ws == "personal":
            personal_context = []
            for path in list(dict.fromkeys(([current_file] if current_file else []) + selected_files))[:12]:
                data = self.repository_workspace.preview(repo, path)
                if data.get("binary") or data.get("error"):
                    continue
                personal_context.append(
                    f"ARCTIC selected file {path}:\n{str(data.get('content') or '')[:20_000]}"
                )
            if personal_context:
                selection = "\n\n".join([selection] + personal_context).strip()[:60_000]
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo.id,
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            prompt=str(payload.get("prompt") or ""),
            selected_files=selected_files,
            current_file=current_file,
            selection=selection,
            include_repo_context=bool(payload.get("include_repo_context")),
        )
        self._run_scope[str(result["id"])] = (ws, repo.id)
        return result

    def result(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        result = self.coding.result(run_id, workspace=ws)
        scope = self._run_scope.get(run_id)
        if scope:
            result["repository_id"] = scope[1]
        if result["status"] == "completed" and run_id not in self._proposals:
            edits = self.coding.proposed_edits(result.get("answer") or "")
            if edits and scope:
                self.stage_proposal(run_id, ws, scope[1], edits)
        proposal = self._proposals.get(run_id)
        result["proposal"] = self._public_proposal(proposal) if proposal else None
        return result

    def cancel(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        return self.coding.cancel(run_id, workspace=ws)

    def stage_proposal(
        self, run_id: str, workspace: str, repository_id: str, edits: list[dict[str, str]]
    ) -> Proposal:
        ws = normalize_workspace(workspace)
        repo = self.require_repo(ws, repository_id)
        staged = []
        for item in edits:
            path = str(item.get("path") or "")
            content = item.get("content")
            if not path or not isinstance(content, str):
                continue
            current = self.repository_workspace.preview(repo, path)
            before = str(current.get("content") or "")
            preview = self.repository_workspace.preview_save(repo, path, content)
            staged.append({
                "path": path,
                "content": content,
                "diff": preview.get("diff") or "",
                "changed": bool(preview.get("changed")),
                "base_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
            })
        proposal = Proposal(run_id=run_id, workspace=ws, repository_id=repo.id, edits=staged)
        self._proposals[run_id] = proposal
        self._run_scope.setdefault(run_id, (ws, repo.id))
        return proposal

    def accept(self, workspace: str, run_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "pending":
            raise ClimateCodingError("Proposal is no longer pending", code="proposal_closed")
        repo = self.require_repo(proposal.workspace, proposal.repository_id)
        for edit in proposal.edits:
            current = self.repository_workspace.preview(repo, edit["path"])
            digest = hashlib.sha256(str(current.get("content") or "").encode("utf-8")).hexdigest()
            if digest != edit["base_sha256"]:
                raise ClimateCodingError(
                    f"File changed since proposal: {edit['path']}", code="proposal_conflict"
                )
            self.repository_workspace.preview_save(repo, edit["path"], edit["content"])
        applied = []
        for edit in proposal.edits:
            if edit["changed"]:
                applied.append(self.repository_workspace.save(
                    repo, edit["path"], edit["content"], confirm=True
                ))
        proposal.state = "accepted"
        return {"ok": True, "state": proposal.state, "applied": applied}

    def reject(self, workspace: str, run_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "pending":
            raise ClimateCodingError("Proposal is no longer pending", code="proposal_closed")
        proposal.state = "rejected"
        return {"ok": True, "state": proposal.state, "applied": []}

    def _require_run_scope(self, workspace: str, run_id: str) -> None:
        scope = self._run_scope.get(run_id)
        if scope is not None and scope[0] != workspace:
            raise ClimateCodingError("Run belongs to another workspace", code="workspace_isolation")

    def _require_proposal(self, workspace: str, run_id: str) -> Proposal:
        ws = normalize_workspace(workspace)
        proposal = self._proposals.get(run_id)
        if proposal is None:
            raise ClimateCodingError("Proposal not found", code="not_found")
        if proposal.workspace != ws:
            raise ClimateCodingError("Proposal belongs to another workspace", code="workspace_isolation")
        return proposal

    @staticmethod
    def _public_proposal(proposal: Proposal) -> dict[str, Any]:
        return {
            "run_id": proposal.run_id,
            "workspace": proposal.workspace,
            "repository_id": proposal.repository_id,
            "state": proposal.state,
            "edits": [
                {k: v for k, v in edit.items() if k != "content"}
                for edit in proposal.edits
            ],
        }

    def _branch(self, repo: Repository) -> str | None:
        info = self.repository_workspace.availability(repo)
        root = info.get("root")
        if not root:
            return None
        try:
            proc = subprocess.run(
                ["git", "branch", "--show-current"], cwd=root, shell=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.repository_workspace.settings.git_timeout_seconds, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() or "HEAD"
