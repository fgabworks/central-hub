"""CLIMATE workspace facade with workspace/repository isolation."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from typing import Any

from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError, classify_task_mode
from hub.climate.codex_limits import get_codex_rate_limits_service
from hub.climate.preflight import make_blocked_run, resolve_climate_context
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
        self._run_meta: dict[str, dict[str, Any]] = {}
        self._proposals: dict[str, Proposal] = {}
        self._local_runs: dict[str, dict[str, Any]] = {}

    def _repository_intelligence(self) -> Any | None:
        agent_center = getattr(self.coding, "agent_center", None)
        return getattr(agent_center, "repository_intelligence", None) if agent_center else None

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

    def codex_rate_limits(self, workspace: str, *, refresh: bool = False) -> dict[str, Any]:
        """Return actual Codex account rate limits (never estimated from chat tokens)."""
        ws = normalize_workspace(workspace)
        if ws != "work":
            from hub.climate.codex_limits import unavailable_payload

            payload = unavailable_payload(
                detail="Codex rate limits are available only in the VANTA workspace.",
            )
            payload["workspace"] = ws
            return payload
        payload = get_codex_rate_limits_service().get(refresh=refresh)
        payload["workspace"] = ws
        return payload

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
        prompt = str(payload.get("prompt") or "")
        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        task_mode = classify_task_mode(prompt, str(payload.get("task_mode") or "") or None)
        handoff = bool(payload.get("handoff"))
        include_repo_context = bool(payload.get("include_repo_context"))

        preflight = resolve_climate_context(
            workspace=ws,
            repo=repo,
            repository_workspace=self.repository_workspace,
            prompt=prompt,
            provider=provider,
            model=model,
            task_mode=task_mode,
            current_file=current_file,
            selected_files=selected_files,
            selection=selection,
            include_repo_context=include_repo_context,
            repository_intelligence=self._repository_intelligence() if ws == "work" else None,
            handoff=handoff,
        )
        if not preflight.ok:
            blocked = make_blocked_run(
                workspace=ws,
                repository_id=repo.id,
                provider=provider,
                model=model,
                preflight=preflight,
            )
            self._local_runs[str(blocked["id"])] = blocked
            self._run_scope[str(blocked["id"])] = (ws, repo.id)
            self._run_meta[str(blocked["id"])] = {
                "task_mode": preflight.task_mode,
                "selected_files": selected_files,
                "current_file": current_file,
                "provider_invoked": False,
                "preflight": blocked.get("preflight"),
                "sources": list(preflight.source_files),
            }
            return blocked

        # Bounded packet replaces blind full-history / full-instruction dumps.
        # Keep a short task banner; packet already contains task + evidence.
        augmented_prompt = preflight.packet
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo.id,
            provider=provider,
            model=model,
            prompt=augmented_prompt,
            selected_files=list(dict.fromkeys(selected_files + list(preflight.source_files)))[:16],
            current_file=current_file,
            selection=selection if ws == "personal" else "",
            include_repo_context=False,  # packet already carries ranked evidence
            task_mode=preflight.task_mode,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            handoff=handoff,
            preflight_log=preflight.activity_log(),
        )
        self._run_scope[str(result["id"])] = (ws, repo.id)
        self._run_meta[str(result["id"])] = {
            "task_mode": result.get("task_mode") or preflight.task_mode,
            "selected_files": selected_files,
            "current_file": current_file,
            "provider_invoked": True,
            "preflight": {
                "ok": True,
                "activity": list(preflight.activity),
                "instruction_files": list(preflight.instruction_files),
                "skills_used": list(preflight.skills_used),
                "source_files": list(preflight.source_files),
                "context_chars": preflight.context_chars,
                "context_tokens_est": preflight.context_tokens_est,
                "confidence": preflight.confidence,
                "provider_invoked": True,
                "diagnostics": dict(preflight.diagnostics),
            },
            "sources": list(dict.fromkeys(list(preflight.source_files) + selected_files + ([current_file] if current_file else []))),
        }
        result["provider_invoked"] = True
        result["preflight"] = self._run_meta[str(result["id"])]["preflight"]
        result["sources"] = list(self._run_meta[str(result["id"])]["sources"])[:24]
        return result

    def result(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        if run_id in self._local_runs:
            local = dict(self._local_runs[run_id])
            meta = self._run_meta.get(run_id) or {}
            local["task_mode"] = meta.get("task_mode") or local.get("task_mode") or "ask"
            local["proposal"] = None
            local["sources"] = list(meta.get("sources") or local.get("sources") or [])[:24]
            return local
        result = self.coding.result(run_id, workspace=ws)
        scope = self._run_scope.get(run_id)
        meta = self._run_meta.get(run_id) or {}
        task_mode = str(meta.get("task_mode") or result.get("task_mode") or "ask")
        result["task_mode"] = task_mode
        if scope:
            result["repository_id"] = scope[1]
        raw_answer = str(result.get("answer") or "")
        display, raw_diag = self.coding.humanize_answer(raw_answer, task_mode=task_mode)
        if raw_diag:
            result["raw_answer"] = raw_answer
            logs = str(result.get("logs") or "")
            if raw_diag not in logs:
                result["logs"] = (logs + ("\n\n" if logs else "") + "[provider_raw_answer]\n" + raw_diag).strip()
            result["answer"] = display
        elif display != raw_answer:
            result["answer"] = display
        preflight_meta = meta.get("preflight")
        if preflight_meta:
            result["preflight"] = preflight_meta
            result["provider_invoked"] = bool(meta.get("provider_invoked", True))
            pref_log = ""
            activity = list((preflight_meta or {}).get("activity") or [])
            if activity:
                pref_log = "[climate_context_resolver]\n" + "\n".join(activity)
                diag_lines = [
                    "[climate_context_resolver_diagnostics]",
                    f"instruction_files={','.join(preflight_meta.get('instruction_files') or []) or '(none)'}",
                    f"skills_used={','.join(preflight_meta.get('skills_used') or []) or '(none)'}",
                    f"source_files={','.join(preflight_meta.get('source_files') or []) or '(none)'}",
                    f"context_chars={preflight_meta.get('context_chars') or 0}",
                    f"context_tokens_est={preflight_meta.get('context_tokens_est') or 0}",
                    f"confidence={preflight_meta.get('confidence') or (preflight_meta.get('diagnostics') or {}).get('confidence') or 'n/a'}",
                    f"provider_invoked={'Yes' if result.get('provider_invoked') else 'No'}",
                ]
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                total = usage.get("total_tokens")
                if total is None:
                    total = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                diag_lines.append(f"current_run_tokens={total or 0}")
                pref_log = pref_log + "\n" + "\n".join(diag_lines)
            logs = str(result.get("logs") or "")
            if pref_log and "[climate_context_resolver]" not in logs and "[climate_preflight]" not in logs:
                result["logs"] = (pref_log + ("\n\n" if logs else "") + logs).strip()
        if result["status"] == "completed" and run_id not in self._proposals:
            if task_mode == "edit" and meta.get("provider_invoked", True):
                edits = self.coding.proposed_edits(raw_answer)
                if edits and scope:
                    self.stage_proposal(run_id, ws, scope[1], edits)
        proposal = self._proposals.get(run_id)
        result["proposal"] = self._public_proposal(proposal) if proposal else None
        sources = list(meta.get("sources") or [])
        if not sources:
            for path in list(meta.get("selected_files") or []) + ([meta.get("current_file")] if meta.get("current_file") else []):
                p = str(path or "").replace("\\", "/").lstrip("/")
                if p and p not in sources:
                    sources.append(p)
        result["sources"] = sources[:24]
        return result
    def cancel(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        if run_id in self._local_runs:
            local = dict(self._local_runs[run_id])
            local["status"] = "cancelled"
            local["cancel_requested"] = True
            self._local_runs[run_id] = local
            return local
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
            diff = str(preview.get("diff") or "")
            plus, minus = self._diff_line_counts(diff)
            before_lines = len(before.splitlines()) if before else 0
            after_lines = len(content.splitlines())
            large_diff = (
                (plus + minus) >= 120
                or minus >= 80
                or (minus >= 40 and plus <= max(5, minus // 4))
                or (before_lines >= 50 and after_lines <= max(5, int(before_lines * 0.35)) and minus > plus)
            )
            staged.append({
                "path": path,
                "content": content,
                "diff": diff,
                "changed": bool(preview.get("changed")),
                "base_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "line_plus": plus,
                "line_minus": minus,
                "large_diff": large_diff,
                "requires_review": True,
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
    def _diff_line_counts(diff: str) -> tuple[int, int]:
        plus = 0
        minus = 0
        for line in str(diff or "").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                plus += 1
            elif line.startswith("-") and not line.startswith("---"):
                minus += 1
        return plus, minus

    @staticmethod
    def _public_proposal(proposal: Proposal) -> dict[str, Any]:
        edits = [
            {k: v for k, v in edit.items() if k != "content"}
            for edit in proposal.edits
        ]
        large = any(bool(edit.get("large_diff")) for edit in edits)
        total_minus = sum(int(edit.get("line_minus") or 0) for edit in edits)
        total_plus = sum(int(edit.get("line_plus") or 0) for edit in edits)
        return {
            "run_id": proposal.run_id,
            "workspace": proposal.workspace,
            "repository_id": proposal.repository_id,
            "state": proposal.state,
            "edits": edits,
            "large_diff": large,
            "requires_review": True,
            "line_plus": total_plus,
            "line_minus": total_minus,
            "warning": (
                "Large or destructive replacement detected. Review the diff before Keep All."
                if large
                else ""
            ),
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
