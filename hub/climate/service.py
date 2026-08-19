"""CLIMATE workspace facade with workspace/repository isolation."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from hub.agent_center.repository_context import explicit_repository_id
from hub.agent_center.redact import redact_text
from hub.climate.file_view import as_read_only_file
from hub.climate.context_registry import (
    ClimateContextResolver,
    ContextRequest,
    ContextResolution,
    build_default_context_resolver,
)
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError, classify_task_mode
from hub.climate.codex_limits import get_codex_rate_limits_service
from hub.climate.investigation_metrics import summarize_tool_activity
from hub.climate.context_scope import (
    ALL,
    GENERAL,
    REPOSITORY,
    resolve_chat_scope,
)
from hub.climate.execution_mode import (
    CLIMATE_ASSISTED,
    DIRECT,
    MODE_LABELS,
    MODE_TOOLTIPS,
    EXECUTION_MODES,
    assistant_label,
    format_execution_summary,
    is_direct_mode,
    normalize_execution_mode,
    normalize_path_list,
    provider_display_label,
)
from hub.climate.preflight import make_blocked_run, resolve_climate_context
from hub.climate.proposal_store import CodingProposalStore
from hub.climate.retrieval_policy import is_logs_history_query, is_noisy_artifact
from hub.climate.token_efficiency import TokenEfficiencyService
from hub.climate.test_execution import CodingTestExecutionService, CodingTestRunStore
from hub.registry.models import Registry, Repository
from hub.repository_workspace.ports import port_listeners
from hub.repository_workspace.process_manager import ACTIVE_STATUSES
from hub.repository_workspace.security import WorkspaceSecurityError, should_skip_dir
from hub.repository_workspace.service import RepositoryWorkspaceService

MAX_CLIMATE_PORTS = 80
MAX_DEBUG_LOG_LINES = 200


def _proposal_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_PROPOSAL_FILES = _proposal_limit("CODING_AGENT_MAX_PROPOSAL_FILES", 6, 1, 20)
MAX_PROPOSAL_PATCH_CHARS = _proposal_limit(
    "CODING_AGENT_MAX_PATCH_CHARS", 60_000, 1_000, 500_000
)


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
    id: str
    run_id: str
    workspace: str
    repository_id: str
    state: str = "pending"
    edits: list[dict[str, Any]] = field(default_factory=list)
    conversation_id: str = ""
    requested_change: str = ""
    plan: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    inspected_files: list[str] = field(default_factory=list)
    decision: str = ""
    provider: str = ""
    model: str = ""
    execution_mode: str = ""
    context_scope: str = REPOSITORY
    evidence_provenance: dict[str, Any] = field(default_factory=dict)
    rollback_snapshot: list[dict[str, Any]] = field(default_factory=list)
    files_changed: list[dict[str, Any]] = field(default_factory=list)
    resulting_state: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    decided_at: str | None = None
    applied_at: str | None = None
    parent_proposal_id: str = ""
    source_test_run_id: str = ""


class ClimateService:
    def __init__(
        self,
        registry: Registry,
        repository_workspace: RepositoryWorkspaceService,
        coding: ClimateCodingAdapter,
        notebook_store: Any | None = None,
        sql_workspace_store: Any | None = None,
        context_resolver: ClimateContextResolver | None = None,
        email_service: Any | None = None,
        calendar_service: Any | None = None,
        drive_service: Any | None = None,
        dhis2_client: Any | None = None,
        uid_index: Any | None = None,
        enrichment_store: Any | None = None,
        dhis2_reports: Any | None = None,
        job_store: Any | None = None,
        audit_store: Any | None = None,
        dhis2_instance: str = "",
        proposal_store: CodingProposalStore | None = None,
        test_execution: CodingTestExecutionService | None = None,
    ) -> None:
        self.registry = registry
        self.repository_workspace = repository_workspace
        self.coding = coding
        self._run_scope: dict[str, tuple[str, str]] = {}
        self._run_meta: dict[str, dict[str, Any]] = {}
        self._proposals: dict[str, Proposal] = {}
        self._local_runs: dict[str, dict[str, Any]] = {}
        self.audit_store = audit_store
        self.proposal_store = proposal_store or self._default_proposal_store()
        self.test_execution = test_execution or self._default_test_execution()
        self.token_efficiency = TokenEfficiencyService()
        self.context_resolver = context_resolver or build_default_context_resolver(
            registry=registry,
            repository_workspace=repository_workspace,
            notebook_store=notebook_store,
            sql_workspace_store=sql_workspace_store,
            intelligence_loader=lambda: self._repository_intelligence(),
            repobrain_loader=lambda: self._repobrain(),
            # Resolve through this module so existing tests and callers can
            # replace the established repository resolver seam.
            context_loader=lambda **kwargs: resolve_climate_context(**kwargs),
            email_service=email_service,
            calendar_service=calendar_service,
            drive_service=drive_service,
            dhis2_client=dhis2_client,
            uid_index=uid_index,
            enrichment_store=enrichment_store,
            dhis2_reports=dhis2_reports,
            job_store=job_store,
            audit_store=audit_store,
            dhis2_instance=dhis2_instance,
        )

    def _default_proposal_store(self) -> CodingProposalStore | None:
        agent_center = getattr(self.coding, "agent_center", None)
        backing = getattr(agent_center, "store", None) if agent_center else None
        db = getattr(backing, "db", None) if backing else None
        return CodingProposalStore(db) if db is not None else None

    def _default_test_execution(self) -> CodingTestExecutionService | None:
        db = getattr(self.proposal_store, "db", None) if self.proposal_store else None
        return CodingTestExecutionService(CodingTestRunStore(db)) if db is not None else None

    def _repository_intelligence(self) -> Any | None:
        agent_center = getattr(self.coding, "agent_center", None)
        return getattr(agent_center, "repository_intelligence", None) if agent_center else None

    def _repobrain(self) -> Any | None:
        agent_center = getattr(self.coding, "agent_center", None)
        # Read only an actually configured service. Dynamic proxy/mock attributes
        # must not make the optional source appear available.
        values = getattr(agent_center, "__dict__", {}) if agent_center else {}
        return values.get("repobrain") if isinstance(values, dict) else None

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

    def hub_registry_facts(self, workspace: str) -> str:
        """Compact CLIMATE-known registry facts — not repository file contents."""
        ws = normalize_workspace(workspace)
        lines = ["CLIMATE connected repositories (registry/config, not repository contents):"]
        count = 0
        for repo in self.registry.enabled_repositories():
            if repo_workspace(repo) != ws:
                continue
            count += 1
            kind = str(repo.type or "unknown")
            desc = str(getattr(repo, "description", "") or "").replace("\n", " ").strip()
            extra = f" — {desc[:160]}" if desc else ""
            lines.append(f"- {repo.name} ({repo.id}), type={kind}{extra}")
        if count == 0:
            lines.append("- None connected in this workspace.")
        return "\n".join(lines)[:4_000]

    def _bounded_all_repository_bundle(self, workspace: str, prompt: str) -> tuple[str, list[str]]:
        """Search all connected command repos; keep only relevant bounded hits."""
        ws = normalize_workspace(workspace)
        repo_ids = [
            row["id"] for row in self.repositories(ws)
            if row.get("available") and row.get("id")
        ]
        chunks = [self.hub_registry_facts(ws)]
        paths: list[str] = []
        intelligence = self._repository_intelligence() if ws == "work" else None
        if intelligence is not None and repo_ids and hasattr(intelligence, "retrieve"):
            knowledge = intelligence.retrieve(
                repo_ids,
                prompt,
                limit=6,
                max_repositories=max(1, len(repo_ids)),
                include_empty_fallback=False,
            )
            items = [
                item for item in list((knowledge or {}).get("items") or [])
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ][:6]
            if items:
                lines = ["Bounded relevant repository hits (not full repositories):"]
                for item in items:
                    summary = str(item.get("summary") or "").replace("\n", " ").strip()[:500]
                    rid = str(item.get("repository_id") or "").strip()
                    path = str(item.get("path") or "").replace("\\", "/").strip().lstrip("/")
                    label = f"{rid}:{path}" if rid and path else path
                    if label:
                        paths.append(label)
                    lines.append(f"- {rid}:{item.get('path')}: {summary}")
                chunks.append("\n".join(lines))
        text = "\n\n".join(chunk for chunk in chunks if str(chunk).strip()).strip()
        return text[:12_000], normalize_path_list(paths, limit=24)

    def _bounded_all_repository_context(self, workspace: str, prompt: str) -> str:
        text, _paths = self._bounded_all_repository_bundle(workspace, prompt)
        return text

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

    def view_file(self, workspace: str, repository_id: str, path: str) -> dict[str, Any]:
        """Read-only file view for the selected approved repository."""
        repo = self.require_repo(workspace, repository_id)
        return as_read_only_file(self.repository_workspace.preview(repo, path))

    def bootstrap(self, workspace: str, repository_id: str = "", *, surface: str = "") -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        repos = self.repositories(ws)
        requested = explicit_repository_id(repository_id)
        if str(surface or "").strip().lower() in {"chat", "general"}:
            active = requested
        else:
            active = requested or next((row["id"] for row in repos if row["available"]), "")
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
            "execution_modes": [
                {
                    "id": mode,
                    "label": MODE_LABELS[mode],
                    "tooltip": MODE_TOOLTIPS[mode],
                }
                for mode in EXECUTION_MODES
            ],
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

    def conversations(
        self,
        workspace: str,
        *,
        repository_id: str = "",
        surface: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        ws = normalize_workspace(workspace)
        if repository_id:
            self.require_repo(ws, repository_id)
        return self.coding.conversations(
            workspace=ws, repository_id=repository_id, surface=surface, limit=limit
        )

    def conversation(
        self,
        workspace: str,
        conversation_id: str,
        *,
        repository_id: str = "",
        surface: str = "",
    ) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        if repository_id:
            self.require_repo(ws, repository_id)
        payload = self.coding.conversation(
            conversation_id,
            workspace=ws,
            repository_id=repository_id,
            surface=surface,
        )
        for run in list(payload.get("runs") or []):
            if isinstance(run, dict):
                self._apply_execution_identity(run)
        return payload

    def rename_conversation(
        self,
        workspace: str,
        conversation_id: str,
        *,
        title: str,
        repository_id: str = "",
        surface: str = "",
    ) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        if repository_id:
            self.require_repo(ws, repository_id)
        return self.coding.rename_conversation(
            conversation_id,
            workspace=ws,
            title=title,
            repository_id=repository_id,
            surface=surface,
        )

    def _attach_selected_file_bodies(
        self,
        repo: Repository,
        *,
        current_file: str,
        selected_files: list[str],
        selection: str,
    ) -> str:
        """Pack only user-selected files for providers that cannot inspect the repo."""
        chunks = [selection] if str(selection or "").strip() else []
        for path in list(dict.fromkeys(([current_file] if current_file else []) + selected_files))[:4]:
            data = self.repository_workspace.preview(repo, path)
            if data.get("binary") or data.get("error"):
                continue
            chunks.append(
                f"Selected file {path}:\n{str(data.get('content') or '')[:12_000]}"
            )
        return "\n\n".join(chunks).strip()[:40_000]

    def _parse_attached_files(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw in list(payload.get("attached_files") or []):
            if isinstance(raw, str):
                path = raw.replace("\\", "/").strip()
                if path:
                    items.append({"repository_id": "", "path": path, "start_line": 0, "end_line": 0})
                continue
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            try:
                start = int(raw.get("start_line") or raw.get("startLine") or 0)
            except (TypeError, ValueError):
                start = 0
            try:
                end = int(raw.get("end_line") or raw.get("endLine") or 0)
            except (TypeError, ValueError):
                end = 0
            items.append({
                "repository_id": str(raw.get("repository_id") or raw.get("repositoryId") or "").strip(),
                "path": path,
                "start_line": start if start > 0 else 0,
                "end_line": end if end > 0 else 0,
            })
        return items[:12]

    def _validate_attached_files(
        self,
        ws: str,
        scope: str,
        scoped_repo_id: str,
        items: list[dict[str, Any]],
        default_repo_id: str = "",
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        scoped = explicit_repository_id(scoped_repo_id)
        default = explicit_repository_id(default_repo_id)
        for item in items:
            rid = explicit_repository_id(item.get("repository_id")) or scoped or default
            if scope == REPOSITORY:
                if not scoped:
                    raise ClimateCodingError("Repository not found", code="not_found")
                if rid and rid != scoped:
                    raise ClimateCodingError(
                        "Attached file is outside the selected repository",
                        code="workspace_isolation",
                    )
                rid = scoped
            elif not rid:
                raise ClimateCodingError("Attached file is missing a repository", code="invalid_request")
            repo = self.require_repo(ws, rid)
            out.append({**item, "repository_id": rid, "repo": repo})
        return out

    def _attach_explicit_file_bodies(
        self,
        items: list[dict[str, Any]],
        *,
        selection: str = "",
    ) -> str:
        """Pack user-selected files as high-priority bounded context — never whole repos."""
        chunks = [selection] if str(selection or "").strip() else []
        if items:
            chunks.append("Explicit attached file context (user-selected, high-priority):")
        for item in items[:8]:
            repo = item.get("repo")
            path = str(item.get("path") or "").strip()
            if repo is None or not path:
                continue
            data = self.repository_workspace.preview(repo, path)
            if data.get("binary") or data.get("error"):
                continue
            content = str(data.get("content") or "")
            start = int(item.get("start_line") or 0)
            end = int(item.get("end_line") or 0)
            if start > 0:
                lines = content.splitlines()
                last = end if end >= start else start
                sliced = lines[start - 1:last][:400]
                content = "\n".join(sliced)
                label = f"{item.get('repository_id')}:{path} L{start}-{start + len(sliced) - 1}"
            else:
                label = f"{item.get('repository_id')}:{path}"
            chunks.append(f"Attached file {label}:\n{content[:12_000]}")
        return "\n\n".join(chunk for chunk in chunks if str(chunk).strip()).strip()[:40_000]

    def execute_chat(self, workspace: str, **payload: Any) -> dict[str, Any]:
        """General AiriX chat — scope is explicit; VANTA repo is never inherited."""
        ws = normalize_workspace(workspace)
        prompt = str(payload.get("prompt") or "")
        display_prompt = str(payload.get("display_prompt") or prompt)
        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        direct = is_direct_mode(execution_mode)
        scope, repo_id = resolve_chat_scope(payload)
        selected_files = [
            str(path).replace("\\", "/")
            for path in list(payload.get("selected_files") or [])
            if str(path).strip()
        ]
        current_file = str(payload.get("current_file") or "").replace("\\", "/")
        selection = str(payload.get("selection") or "")
        extra_selection = selection
        packed_prompt = prompt
        chat_sources: list[str] = []
        attached_labels: list[str] = []
        retrieved_files: list[str] = []
        context_resolution = ContextResolution("", [], [], [], [], [])
        attached_items = self._parse_attached_files(payload)
        if scope == REPOSITORY:
            repo = self.require_repo(ws, repo_id)
            attached_items = self._validate_attached_files(
                ws, scope, repo_id, attached_items, repo_id
            )
            attached_labels = normalize_path_list(
                list(attached_items)
                + selected_files
                + ([current_file] if current_file else [])
            )
            if attached_items:
                extra_selection = self._attach_explicit_file_bodies(
                    attached_items, selection=extra_selection
                )
            if direct:
                extra_selection = self._attach_selected_file_bodies(
                    repo,
                    current_file=current_file,
                    selected_files=selected_files,
                    selection=extra_selection,
                )
                chat_sources = list(attached_labels)
        if not direct:
            context_resolution = self.context_resolver.resolve(ContextRequest(
                query=prompt,
                workspace=ws,
                scope=scope,
                repository_id=repo_id if scope == REPOSITORY else "",
                provider=provider,
                model=model,
                current_file=current_file if scope == REPOSITORY else "",
                selected_files=tuple(selected_files if scope == REPOSITORY else []),
                selection=selection if scope == REPOSITORY else "",
            ))
            if context_resolution.packet:
                extra_selection = "\n\n".join(filter(None, [
                    extra_selection if attached_items else "",
                    context_resolution.packet,
                ]))[:40_000]
            retrieved_labels: list[Any] = []
            for ref in context_resolution.evidence_references:
                metadata = dict(ref.get("metadata") or {})
                paths = list(metadata.get("paths") or [])
                if metadata.get("path"):
                    paths.append(metadata.get("path"))
                for path in paths:
                    if not str(path).strip():
                        continue
                    if scope == REPOSITORY:
                        retrieved_labels.append(str(path))
                    else:
                        retrieved_labels.append({
                            "repository_id": metadata.get("repository_id", ""),
                            "path": path,
                        })
            retrieved_files = normalize_path_list(retrieved_labels)
            chat_sources = normalize_path_list(list(attached_labels) + list(retrieved_files))
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo_id if scope == REPOSITORY else "",
            provider=provider,
            model=model,
            prompt=packed_prompt,
            selected_files=[],
            current_file="",
            selection=extra_selection,
            include_repo_context=False,
            task_mode="ask",
            reuse_session=bool(payload.get("reuse_session", True))
            and not bool(payload.get("handoff")),
            handoff=bool(payload.get("handoff")),
            conversation_id=str(payload.get("conversation_id") or ""),
            repository_investigation=False,
            execution_mode=execution_mode,
            display_prompt=display_prompt,
            surface="chat",
            context_scope=scope,
            attached_files=attached_labels,
            retrieved_files=retrieved_files,
            repository_name=self._repository_display_name(repo_id) if scope == REPOSITORY else "",
            sources_considered=context_resolution.sources_considered,
            sources_queried=context_resolution.sources_queried,
            sources_used=context_resolution.sources_used,
            evidence_references=context_resolution.evidence_references,
            context_source_failures=context_resolution.failures,
            repository_evidence_origin=context_resolution.repository_evidence_origin,
            repository_evidence_origins=context_resolution.repository_evidence_origins,
        )
        result["task_mode"] = "ask"
        result["provider_invoked"] = True
        return self._record_execution(
            result,
            ws=ws,
            surface="chat",
            execution_mode=execution_mode,
            scope=scope,
            repository_id=repo_id if scope == REPOSITORY else "",
            provider=provider,
            model=model,
            attached_files=attached_labels,
            retrieved_files=retrieved_files,
            selected_files=selected_files if scope == REPOSITORY else [],
            current_file=current_file if scope == REPOSITORY else "",
            sources=chat_sources,
            extra={
                "user_prompt": display_prompt or prompt,
                "sources_considered": context_resolution.sources_considered,
                "sources_queried": context_resolution.sources_queried,
                "sources_used": context_resolution.sources_used,
                "evidence_references": context_resolution.evidence_references,
                "context_source_failures": context_resolution.failures,
                "repository_evidence_origin": context_resolution.repository_evidence_origin,
                "repository_evidence_origins": context_resolution.repository_evidence_origins,
            },
        )

    def execute(self, workspace: str, repository_id: str, **payload: Any) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        scope, scoped_repo = resolve_chat_scope({
            "context_scope": payload.get("context_scope"),
            "repository_id": payload.get("repository_id") or repository_id,
        })
        if scope != REPOSITORY:
            return self._execute_workspace_open_scope(ws, scope, str(repository_id or ""), payload)
        repo = self.require_repo(ws, scoped_repo or repository_id)
        if not self.repository_workspace.availability(repo).get("available"):
            raise ClimateCodingError("Local repository unavailable", code="repository_unavailable")
        current_file = str(payload.get("current_file") or "").replace("\\", "/")
        selected_files = list(dict.fromkeys(
            str(path).replace("\\", "/")
            for path in list(payload.get("selected_files") or [])
            if str(path).strip()
        ))
        attached = self._validate_attached_files(
            ws, scope, repo.id, self._parse_attached_files(payload), repo.id
        )
        attached_paths = [str(item["path"]) for item in attached]
        selected_files = list(dict.fromkeys(selected_files + attached_paths))
        # ARCTIC deliberately does not enter the Work/AiriX repository profile.
        # Pack only the explicitly selected personal files into the user-owned
        # prompt so the existing provider runner remains isolated.
        selection = str(payload.get("selection") or "")
        if attached:
            selection = self._attach_explicit_file_bodies(attached, selection=selection)
        if ws == "personal":
            personal_context = []
            for path in list(dict.fromkeys(([current_file] if current_file else []) + selected_files))[:12]:
                if path in attached_paths:
                    continue
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
        if provider == "gemini" and ws == "work" and not attached:
            selection = self._attach_selected_file_bodies(
                repo,
                current_file=current_file,
                selected_files=selected_files,
                selection=selection,
            )
        task_mode = classify_task_mode(prompt, str(payload.get("task_mode") or "") or None)
        handoff = bool(payload.get("handoff"))
        include_repo_context = bool(payload.get("include_repo_context"))
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        direct = is_direct_mode(execution_mode)
        can_investigate = bool(
            self.coding.can_investigate_repository(provider)
            if hasattr(self.coding, "can_investigate_repository")
            else provider == "codex"
        )

        if direct:
            return self._execute_direct(
                ws=ws,
                repo=repo,
                prompt=prompt,
                provider=provider,
                model=model,
                task_mode=task_mode,
                current_file=current_file,
                selected_files=selected_files,
                selection=selection,
                handoff=handoff,
                can_investigate=can_investigate,
                payload=payload,
            )

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
            repository_agent=can_investigate,
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
            blocked["execution_mode"] = CLIMATE_ASSISTED
            blocked["task_mode"] = preflight.task_mode
            self._record_execution(
                blocked,
                ws=ws,
                surface=str(payload.get("surface") or "workspace"),
                execution_mode=CLIMATE_ASSISTED,
                scope=REPOSITORY,
                repository_id=repo.id,
                provider=provider,
                model=model,
                attached_files=list(dict.fromkeys(attached_paths + selected_files + ([current_file] if current_file else []))),
                retrieved_files=list(preflight.source_files),
                selected_files=selected_files,
                current_file=current_file,
                sources=list(preflight.source_files),
                extra={
                    "user_prompt": prompt,
                    "provider_invoked": False,
                    "preflight": blocked.get("preflight"),
                },
            )
            return blocked

        # Bounded packet replaces blind full-history / full-instruction dumps.
        # Keep a short task banner; packet already contains task + evidence.
        augmented_prompt = preflight.packet
        clean_sources = [
            path
            for path in list(preflight.source_files)
            if not is_noisy_artifact(path) or is_logs_history_query(prompt)
        ]
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo.id,
            provider=provider,
            model=model,
            prompt=augmented_prompt,
            selected_files=list(dict.fromkeys(selected_files + clean_sources))[:16],
            current_file=current_file,
            selection=selection if (attached or ws == "personal" or provider == "gemini") else "",
            include_repo_context=False,  # packet already carries ranked evidence
            task_mode=preflight.task_mode,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            handoff=handoff,
            preflight_log=preflight.activity_log(),
            evidence_packet={
                "repository_ids": [repo.id],
                "tool_results": [],
                "hits": [
                    {
                        "source": "climate_context_resolver",
                        "repository_id": repo.id,
                        "path": str(item.get("path") or item.get("file") or ""),
                        "functions": list(item.get("functions") or item.get("symbols") or []),
                        "score": int(item.get("score") or 0),
                    }
                    for item in list(preflight.diagnostics.get("qualification") or [])
                    if item.get("accepted")
                    and (
                        not is_noisy_artifact(str(item.get("path") or item.get("file") or ""))
                        or is_logs_history_query(prompt)
                    )
                ],
                "sources": [
                    f"repository:{repo.id}:{path}"
                    for path in list(preflight.diagnostics.get("authoritative_sources") or [])
                ],
                "usable": bool(preflight.diagnostics.get("authoritative_sources")),
                "errors": [],
                "summary": (
                    f"CLIMATE selected {len(preflight.diagnostics.get('authoritative_sources') or [])} "
                    "authoritative implementation source(s) locally."
                ),
            },
            conversation_id=str(payload.get("conversation_id") or "").strip(),
            repository_investigation=can_investigate,
            execution_mode=CLIMATE_ASSISTED,
            display_prompt=str(payload.get("display_prompt") or prompt),
            surface=str(payload.get("surface") or "workspace"),
            context_scope=REPOSITORY,
            attached_files=list(dict.fromkeys(attached_paths + selected_files + ([current_file] if current_file else []))),
            retrieved_files=list(preflight.source_files),
            repository_name=self._repository_display_name(repo.id),
        )
        result["provider_invoked"] = True
        result["execution_mode"] = CLIMATE_ASSISTED
        result["preflight"] = {
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
        }
        self._record_execution(
            result,
            ws=ws,
            surface=str(payload.get("surface") or "workspace"),
            execution_mode=CLIMATE_ASSISTED,
            scope=REPOSITORY,
            repository_id=repo.id,
            provider=provider,
            model=model,
            attached_files=list(dict.fromkeys(attached_paths + selected_files + ([current_file] if current_file else []))),
            retrieved_files=list(preflight.source_files),
            selected_files=selected_files,
            current_file=current_file,
            sources=list(dict.fromkeys(
                list(preflight.source_files) + selected_files + ([current_file] if current_file else [])
            )),
            extra={
                "user_prompt": prompt,
                "preflight": result["preflight"],
            },
        )
        self._capture_token_efficiency_snapshot(
            str(result["id"]),
            ws=ws,
            repo=repo,
            user_prompt=prompt,
            provider=provider,
            model=model,
            preflight=preflight,
            run=result,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            execution_mode=CLIMATE_ASSISTED,
        )
        if provider == "codex":
            result["token_efficiency"] = self.token_efficiency.public(
                self.token_efficiency.load(str(result["id"]))
            )
        return result

    def _execute_workspace_open_scope(
        self,
        ws: str,
        scope: str,
        explorer_repo_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """General / All Repositories in Code Workspace — no implied repo inheritance."""
        del explorer_repo_id
        prompt = str(payload.get("prompt") or "")
        display_prompt = str(payload.get("display_prompt") or prompt)
        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        direct = is_direct_mode(execution_mode)
        attached = self._validate_attached_files(
            ws, scope, "", self._parse_attached_files(payload), ""
        )
        extra = ""
        retrieved_files: list[str] = []
        context_resolution = ContextResolution("", [], [], [], [], [])
        if not direct:
            context_resolution = self.context_resolver.resolve(ContextRequest(
                query=prompt,
                workspace=ws,
                scope=scope,
                provider=provider,
                model=model,
            ))
            extra = context_resolution.packet
            retrieved_labels: list[Any] = []
            for ref in context_resolution.evidence_references:
                metadata = dict(ref.get("metadata") or {})
                paths = list(metadata.get("paths") or [])
                if metadata.get("path"):
                    paths.append(metadata.get("path"))
                for path in paths:
                    retrieved_labels.append({
                        "repository_id": metadata.get("repository_id", ""),
                        "path": path,
                    })
            retrieved_files = normalize_path_list(retrieved_labels)
        file_block = self._attach_explicit_file_bodies(attached)
        selection_parts = [extra, file_block, str(payload.get("selection") or "")]
        extra_selection = "\n\n".join(
            part.strip() for part in selection_parts if str(part).strip()
        ).strip()
        attached_labels = normalize_path_list(attached)
        result = self.coding.execute(
            workspace=ws,
            repository_id="",
            provider=provider,
            model=model,
            prompt=prompt,
            selected_files=[],
            current_file="",
            selection=extra_selection,
            include_repo_context=False,
            task_mode="ask",
            reuse_session=bool(payload.get("reuse_session", True))
            and not bool(payload.get("handoff")),
            handoff=bool(payload.get("handoff")),
            conversation_id=str(payload.get("conversation_id") or "").strip(),
            repository_investigation=False,
            execution_mode=execution_mode,
            display_prompt=display_prompt,
            surface="workspace",
            context_scope=scope,
            attached_files=attached_labels,
            retrieved_files=retrieved_files,
            repository_name="",
            sources_considered=context_resolution.sources_considered,
            sources_queried=context_resolution.sources_queried,
            sources_used=context_resolution.sources_used,
            evidence_references=context_resolution.evidence_references,
            context_source_failures=context_resolution.failures,
            repository_evidence_origin=context_resolution.repository_evidence_origin,
            repository_evidence_origins=context_resolution.repository_evidence_origins,
        )
        result["task_mode"] = "ask"
        result["provider_invoked"] = True
        return self._record_execution(
            result,
            ws=ws,
            surface="workspace",
            execution_mode=execution_mode,
            scope=scope,
            repository_id="",
            provider=provider,
            model=model,
            attached_files=attached_labels,
            retrieved_files=retrieved_files,
            selected_files=[str(item.get("path") or "") for item in attached],
            current_file="",
            sources=normalize_path_list(list(attached_labels) + list(retrieved_files)),
            extra={
                "user_prompt": display_prompt or prompt,
                "sources_considered": context_resolution.sources_considered,
                "sources_queried": context_resolution.sources_queried,
                "sources_used": context_resolution.sources_used,
                "evidence_references": context_resolution.evidence_references,
                "context_source_failures": context_resolution.failures,
                "repository_evidence_origin": context_resolution.repository_evidence_origin,
                "repository_evidence_origins": context_resolution.repository_evidence_origins,
            },
        )

    def _execute_direct(
        self,
        *,
        ws: str,
        repo: Repository,
        prompt: str,
        provider: str,
        model: str,
        task_mode: str,
        current_file: str,
        selected_files: list[str],
        selection: str,
        handoff: bool,
        can_investigate: bool,  # retained for call-site compatibility; Direct never scans
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Raw prompt + explicit attached files only. Skip Context Resolver and repo scan."""
        del can_investigate
        attached_labels = normalize_path_list(
            list(selected_files) + ([current_file] if current_file else [])
        )
        packed_selection = selection
        if current_file or selected_files:
            packed_selection = self._attach_selected_file_bodies(
                repo,
                current_file=current_file,
                selected_files=selected_files,
                selection=selection,
            )
        safety_log = (
            "[climate_execution_mode]\n"
            "Mode=direct\n"
            "Context Resolver skipped\n"
            "Safety=approved repo boundary, ASK read-only sandbox, controlled EDIT\n"
            "Context=explicit attached files only"
        )
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo.id,
            provider=provider,
            model=model,
            prompt=prompt,
            selected_files=list(selected_files)[:16],
            current_file=current_file,
            selection=packed_selection,
            include_repo_context=False,
            task_mode=task_mode,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            handoff=handoff,
            preflight_log=safety_log,
            evidence_packet=None,
            conversation_id=str(payload.get("conversation_id") or "").strip(),
            repository_investigation=False,
            execution_mode=DIRECT,
            display_prompt=str(payload.get("display_prompt") or prompt),
            surface=str(payload.get("surface") or "workspace"),
            context_scope=REPOSITORY,
            attached_files=attached_labels,
            retrieved_files=[],
            repository_name=self._repository_display_name(repo.id),
        )
        preflight = {
            "ok": True,
            "activity": [
                "Direct Provider",
                "Context Resolver skipped",
                "Explicit attached files only",
                "Approved repository boundary preserved",
            ],
            "instruction_files": [],
            "skills_used": [],
            "source_files": [],
            "context_chars": 0,
            "context_tokens_est": 0,
            "confidence": "n/a",
            "provider_invoked": True,
            "diagnostics": {
                "execution_mode": DIRECT,
                "resolver_skipped": True,
                "provider_invoked": True,
                "candidates_found": 0,
                "authoritative_sources": [],
                "qualification": [],
            },
        }
        result["provider_invoked"] = True
        result["execution_mode"] = DIRECT
        result["preflight"] = preflight
        self._record_execution(
            result,
            ws=ws,
            surface=str(payload.get("surface") or "workspace"),
            execution_mode=DIRECT,
            scope=REPOSITORY,
            repository_id=repo.id,
            provider=provider,
            model=model,
            attached_files=attached_labels,
            retrieved_files=[],
            selected_files=selected_files,
            current_file=current_file,
            sources=attached_labels,
            extra={"user_prompt": prompt, "preflight": preflight},
        )
        self._capture_token_efficiency_snapshot(
            str(result["id"]),
            ws=ws,
            repo=repo,
            user_prompt=prompt,
            provider=provider,
            model=model,
            preflight=None,
            run=result,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            execution_mode=DIRECT,
        )
        if provider == "codex":
            result["token_efficiency"] = self.token_efficiency.public(
                self.token_efficiency.load(str(result["id"]))
            )
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
            self._apply_execution_identity(
                local,
                execution_mode=meta.get("execution_mode") or local.get("execution_mode"),
                context_scope=meta.get("context_scope") or local.get("context_scope"),
                repository_id=meta.get("repository_id"),
                repository_name=meta.get("repository_name") or local.get("repository_name"),
                provider=local.get("provider"),
                model=local.get("model"),
                surface=meta.get("surface") or local.get("surface"),
                attached_files=meta.get("attached_files") or local.get("attached_files"),
                retrieved_files=meta.get("retrieved_files") or local.get("retrieved_files"),
                inspected_files=local.get("inspected_files"),
            )
            return local
        result = self.coding.result(run_id, workspace=ws)
        scope = self._run_scope.get(run_id)
        meta = self._run_meta.get(run_id) or {}
        task_mode = str(meta.get("task_mode") or result.get("task_mode") or "ask")
        result["task_mode"] = task_mode
        if meta.get("execution_mode") or result.get("execution_mode"):
            result["execution_mode"] = meta.get("execution_mode") or result.get("execution_mode")
        if scope and not (meta.get("context_scope") or result.get("context_scope")):
            result["repository_id"] = scope[1]
        raw_answer = str(result.get("answer") or "")
        display, raw_diag = self.coding.humanize_answer(
            raw_answer,
            task_mode=task_mode,
            prompt=str(meta.get("user_prompt") or ""),
        )
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
                header = (
                    "[climate_execution_mode]"
                    if meta.get("execution_mode") == DIRECT
                    else "[climate_context_resolver]"
                )
                pref_log = header + "\n" + "\n".join(activity)
                diag_lines = [
                    "[climate_context_resolver_diagnostics]",
                    f"instruction_files={','.join(preflight_meta.get('instruction_files') or []) or '(none)'}",
                    f"skills_used={','.join(preflight_meta.get('skills_used') or []) or '(none)'}",
                    f"source_files={','.join(preflight_meta.get('source_files') or []) or '(none)'}",
                    f"candidates_found={(preflight_meta.get('diagnostics') or {}).get('candidates_found') or 0}",
                    "authoritative_sources=" + (
                        ",".join((preflight_meta.get("diagnostics") or {}).get("authoritative_sources") or [])
                        or "(none)"
                    ),
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
                for item in list((preflight_meta.get("diagnostics") or {}).get("qualification") or []):
                    functions = ",".join(item.get("functions") or item.get("symbols") or []) or "(none)"
                    diag_lines.append(
                        "evidence "
                        f"file={item.get('path') or item.get('file')} "
                        f"function/symbol={functions} score={item.get('score', 0)} "
                        f"accepted={'Yes' if item.get('accepted') else 'No'} "
                        f"reason={item.get('reason') or 'n/a'}"
                    )
                pref_log = pref_log + "\n" + "\n".join(diag_lines)
            logs = str(result.get("logs") or "")
            if pref_log and "[climate_context_resolver]" not in logs and "[climate_preflight]" not in logs:
                result["logs"] = (pref_log + ("\n\n" if logs else "") + logs).strip()
        proposal = self._load_proposal(run_id)
        if result["status"] == "completed" and proposal is None:
            if task_mode == "edit" and meta.get("provider_invoked", True):
                change = (
                    self.coding.proposed_change(raw_answer)
                    if hasattr(self.coding, "proposed_change")
                    else {"plan": [], "edits": self.coding.proposed_edits(raw_answer)}
                )
                edits = list(change.get("edits") or [])
                context_scope = str(meta.get("context_scope") or "")
                if edits and scope and context_scope == REPOSITORY:
                    inspected_files = normalize_path_list(
                        list(result.get("inspected_files") or [])
                        + list(meta.get("retrieved_files") or [])
                        + list(meta.get("selected_files") or [])
                        + ([meta.get("current_file")] if meta.get("current_file") else [])
                    )
                    proposal = self.stage_proposal(
                        run_id,
                        ws,
                        scope[1],
                        edits,
                        plan=list(change.get("plan") or []),
                        requested_change=str(meta.get("user_prompt") or ""),
                        conversation_id=str(meta.get("conversation_id") or result.get("conversation_id") or ""),
                        inspected_files=inspected_files,
                        provider=str(result.get("provider") or ""),
                        model=str(result.get("model") or ""),
                        execution_mode=str(meta.get("execution_mode") or result.get("execution_mode") or ""),
                        context_scope=context_scope,
                        evidence_provenance={
                            "repobrain": "repobrain" in list(meta.get("repository_evidence_origins") or []),
                            "live_repository": bool(meta.get("retrieved_files") or inspected_files),
                            "repository_evidence_origin": meta.get("repository_evidence_origin") or "none",
                            "repository_evidence_origins": list(meta.get("repository_evidence_origins") or []),
                            "evidence_references": list(meta.get("evidence_references") or []),
                        },
                        parent_proposal_id=str(meta.get("parent_proposal_id") or ""),
                        source_test_run_id=str(meta.get("source_test_run_id") or ""),
                    )
                    if proposal.source_test_run_id and self.test_execution is not None:
                        self.test_execution.store.update(
                            proposal.source_test_run_id,
                            follow_up_proposal_id=proposal.id,
                        )
        result["proposal"] = self._public_proposal(proposal) if proposal else None
        sources = list(meta.get("sources") or result.get("sources") or [])
        if not sources:
            for path in list(meta.get("selected_files") or []) + ([meta.get("current_file")] if meta.get("current_file") else []):
                p = str(path or "").replace("\\", "/").lstrip("/")
                if p and p not in sources:
                    sources.append(p)
        result["sources"] = sources[:24]
        investigation = investigation_diagnostics(result, meta)
        result["investigation"] = investigation
        result["files_inspected"] = investigation.get("files_inspected")
        result["search_matched_files"] = investigation.get("search_matched_files")
        result["tool_calls"] = investigation.get("tool_calls")
        inspected = normalize_path_list(
            result.get("inspected_files") or investigation.get("inspected_paths") or []
        )
        attached = normalize_path_list(meta.get("attached_files") or result.get("attached_files") or [])
        retrieved = normalize_path_list(meta.get("retrieved_files") or result.get("retrieved_files") or [])
        if inspected:
            result["inspected_files"] = inspected
        inv_log = investigation.get("log") or ""
        logs = str(result.get("logs") or "")
        if inv_log and "[climate_investigation]" not in logs:
            result["logs"] = (logs + ("\n\n" if logs else "") + inv_log).strip()
        if str(result.get("provider") or result.get("agent_id") or "") == "codex":
            result["token_efficiency"] = self._token_efficiency_payload(ws, run_id, result)
        self._apply_execution_identity(
            result,
            execution_mode=meta.get("execution_mode") or result.get("execution_mode"),
            context_scope=meta.get("context_scope") or result.get("context_scope"),
            repository_id=(
                meta.get("repository_id")
                if meta.get("context_scope") or meta.get("repository_id") is not None
                else result.get("repository_id")
            ),
            repository_name=meta.get("repository_name") or result.get("repository_name"),
            provider=result.get("provider"),
            model=result.get("model"),
            surface=meta.get("surface") or result.get("surface"),
            attached_files=attached,
            retrieved_files=retrieved,
            inspected_files=inspected,
        )
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

    def token_efficiency_status(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        return self._token_efficiency_payload(ws, run_id)

    def evaluate_token_efficiency(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        if run_id in self._local_runs:
            raise ClimateCodingError("Blocked preflight runs have no Direct Codex comparison.", code="unavailable")
        adapter = self._codex_adapter()
        if adapter is None:
            raise ClimateCodingError("Codex adapter is unavailable.", code="unavailable")
        record = self._ensure_token_efficiency_record(ws, run_id)
        snapshot = dict(record.get("snapshot") or {})
        mode = normalize_execution_mode(snapshot.get("execution_mode"))
        if record.get("status") == "Measured":
            if mode == DIRECT and record.get("assisted"):
                return self.token_efficiency.public(record)
            if mode != DIRECT and record.get("direct"):
                return self.token_efficiency.public(record)
        repo_id = str(snapshot.get("repository_id") or (self._run_scope.get(run_id) or ("", ""))[1] or "")
        if not repo_id:
            raise ClimateCodingError("Repository for this run is unknown.", code="not_found")
        repo = self.require_repo(ws, repo_id)
        if mode == DIRECT:
            user_prompt = str(snapshot.get("user_prompt") or "")
            preflight = resolve_climate_context(
                workspace=ws,
                repo=repo,
                repository_workspace=self.repository_workspace,
                prompt=user_prompt,
                provider=str(snapshot.get("provider") or "codex"),
                model=str(snapshot.get("model") or ""),
                task_mode="ask",
                include_repo_context=False,
                repository_intelligence=self._repository_intelligence() if ws == "work" else None,
                repository_agent=True,
            )
            packed = str(preflight.packet or user_prompt)
            record = self.token_efficiency.start_direct(
                run_id,
                adapter=adapter,
                repository_id=repo.id,
                repository_path=str(getattr(repo, "local_path", "") or getattr(repo, "working_directory", "") or ""),
                comparison_prompt=packed,
                comparison_side="assisted",
            )
        else:
            record = self.token_efficiency.start_direct(
                run_id,
                adapter=adapter,
                repository_id=repo.id,
                repository_path=str(getattr(repo, "local_path", "") or getattr(repo, "working_directory", "") or ""),
            )
        return self.token_efficiency.public(record)

    def cancel_token_efficiency(self, workspace: str, run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        self._require_run_scope(ws, run_id)
        record = self.token_efficiency.cancel(run_id)
        return self.token_efficiency.public(record)

    def _capture_token_efficiency_snapshot(
        self,
        run_id: str,
        *,
        ws: str,
        repo: Any,
        user_prompt: str,
        provider: str,
        model: str,
        preflight: Any,
        run: dict[str, Any],
        reuse_session: bool,
        execution_mode: str = CLIMATE_ASSISTED,
    ) -> None:
        if provider != "codex":
            return
        adapter = self._codex_adapter()
        exe = ""
        version = ""
        if adapter is not None:
            try:
                exe = adapter.resolve_executable() or ""
                if exe and hasattr(adapter, "_detect_version"):
                    version = adapter._detect_version(exe) or ""
            except Exception:
                exe = ""
        diagnostics = dict(getattr(preflight, "diagnostics", None) or {})
        if not diagnostics and isinstance(preflight, dict):
            diagnostics = dict(preflight.get("diagnostics") or {})
        candidates = diagnostics.get("candidates_found")
        try:
            candidates_i = int(candidates) if candidates is not None else None
        except (TypeError, ValueError):
            candidates_i = None
        packet_chars = int(getattr(preflight, "context_chars", 0) or 0) if preflight is not None else 0
        if not packet_chars and isinstance(preflight, dict):
            packet_chars = int(preflight.get("context_chars") or 0)
        token_est = int(getattr(preflight, "context_tokens_est", 0) or 0) if preflight is not None else 0
        if not token_est and isinstance(preflight, dict):
            token_est = int(preflight.get("context_tokens_est") or 0)
        if is_direct_mode(execution_mode):
            packet_chars = 0
            token_est = 0
            candidates_i = 0
        self.token_efficiency.capture_snapshot(
            run_id=run_id,
            user_prompt=user_prompt,
            repository_id=repo.id,
            repository_path=str(getattr(repo, "local_path", "") or getattr(repo, "working_directory", "") or ""),
            provider=provider,
            model=model,
            read_only=True,
            session_reused=None,
            context_packet_chars=packet_chars or None,
            context_tokens_est=token_est or None,
            source_candidates=candidates_i,
            execution_mode=normalize_execution_mode(execution_mode),
            codex_executable=exe,
            codex_version=version,
            reasoning_config={
                "sandbox": "read-only",
                "json": True,
                "model": model,
                "reuse_session": bool(reuse_session),
            },
            persist=(self.token_efficiency.persist_root / run_id).is_dir(),
        )
        meta = self._run_meta.get(run_id) or {}
        meta["user_prompt"] = user_prompt
        self._run_meta[run_id] = meta

    def _token_efficiency_payload(
        self, workspace: str, run_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = self._ensure_token_efficiency_record(workspace, run_id, result=result)
        return self.token_efficiency.public(record)

    def _ensure_token_efficiency_record(
        self, workspace: str, run_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        record = self.token_efficiency.load(run_id)
        result = result if isinstance(result, dict) else None
        if result is None and run_id not in self._local_runs:
            try:
                result = self.coding.result(run_id, workspace=workspace)
            except ClimateCodingError:
                result = None
        if result and str(result.get("provider") or result.get("agent_id") or "") == "codex":
            if str(result.get("status") or "") in {"completed", "failed", "cancelled"}:
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                self.token_efficiency.update_climate_metrics(
                    run_id,
                    usage=usage,
                    started_at=result.get("started_at") or result.get("created_at"),
                    finished_at=result.get("finished_at"),
                    tool_activity=list(result.get("tool_activity") or []),
                    logs=str(result.get("logs") or ""),
                    session_reused=usage.get("session_reused") if "session_reused" in usage else None,
                    persist=(self.token_efficiency.persist_root / run_id).is_dir(),
                )
                record = self.token_efficiency.load(run_id)
        if record and record.get("snapshot"):
            return record
        agent_run = self._agent_center_run(run_id, workspace)
        if agent_run:
            repo_id = (agent_run.get("repository_ids") or [None])[0]
            path = ""
            if repo_id:
                try:
                    repo = self.require_repo(workspace, str(repo_id))
                    path = str(getattr(repo, "local_path", "") or "")
                except ClimateCodingError:
                    path = ""
            return self.token_efficiency.reconstruct_from_agent_run(agent_run, repository_path=path)
        return record or {"status": "Benchmark unavailable", "reason": "Run not found.", "snapshot": None}

    def _agent_center_run(self, run_id: str, workspace: str) -> dict[str, Any] | None:
        agent_center = getattr(self.coding, "agent_center", None)
        if agent_center is None or not hasattr(agent_center, "store"):
            return None
        profile = "okarun" if workspace == "work" else "aira"
        try:
            return agent_center.store.get_run(run_id, profile_id=profile)
        except Exception:
            return None

    def _codex_adapter(self) -> Any:
        agent_center = getattr(self.coding, "agent_center", None)
        if agent_center is None:
            return None
        registry = getattr(agent_center, "connections", None)
        adapters = getattr(registry, "adapters", None) or {}
        return adapters.get("codex")

    def stage_proposal(
        self,
        run_id: str,
        workspace: str,
        repository_id: str,
        edits: list[dict[str, str]],
        *,
        plan: list[str] | None = None,
        requested_change: str = "",
        conversation_id: str = "",
        inspected_files: list[str] | None = None,
        provider: str = "",
        model: str = "",
        execution_mode: str = "",
        context_scope: str = REPOSITORY,
        evidence_provenance: dict[str, Any] | None = None,
        parent_proposal_id: str = "",
        source_test_run_id: str = "",
    ) -> Proposal:
        ws = normalize_workspace(workspace)
        if context_scope != REPOSITORY or not repository_id:
            raise ClimateCodingError(
                "Edit proposals require a Specific Repository scope.", code="repository_scope_required"
            )
        if not edits:
            raise ClimateCodingError("Proposal contains no file edits.", code="proposal_invalid")
        if len(edits) > MAX_PROPOSAL_FILES:
            raise ClimateCodingError(
                f"Proposal exceeds the {MAX_PROPOSAL_FILES}-file limit.", code="proposal_too_large"
            )
        repo = self.require_repo(ws, repository_id)
        staged: list[dict[str, Any]] = []
        rollback: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_patch_chars = 0
        for item in edits:
            path = self._validate_proposal_path(str(item.get("path") or ""))
            content = item.get("content")
            if not path or not isinstance(content, str):
                raise ClimateCodingError("Proposal contains an invalid file edit.", code="proposal_invalid")
            if path.casefold() in seen:
                raise ClimateCodingError(f"Proposal repeats file: {path}", code="proposal_invalid")
            seen.add(path.casefold())
            current = self.repository_workspace.edit_state(repo, path)
            before = str(current.get("content") or "")
            preview = self.repository_workspace.preview_save(repo, path, content)
            diff = str(preview.get("diff") or "")
            if "diff truncated" in diff.lower():
                raise ClimateCodingError("Proposal diff exceeds the review limit.", code="proposal_too_large")
            total_patch_chars += len(diff)
            if total_patch_chars > MAX_PROPOSAL_PATCH_CHARS:
                raise ClimateCodingError(
                    f"Proposal exceeds the {MAX_PROPOSAL_PATCH_CHARS}-character patch limit.",
                    code="proposal_too_large",
                )
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
                "base_sha256": str(current.get("sha256") or ""),
                "line_plus": plus,
                "line_minus": minus,
                "large_diff": large_diff,
                "requires_review": True,
            })
            rollback.append({
                "path": path,
                "content": before,
                "sha256": str(current.get("sha256") or ""),
                "bytes": int(current.get("size") or 0),
                "modified_at": current.get("modified_at"),
                "encoding": str(current.get("encoding") or "utf-8"),
                "newline": str(current.get("newline") or "lf"),
            })
        staged = [item for item in staged if item.get("changed")]
        rollback = [item for item in rollback if any(e["path"] == item["path"] for e in staged)]
        if not staged:
            raise ClimateCodingError("Proposal would not change any files.", code="proposal_invalid")
        now = self._utc_now()
        paths = [item["path"] for item in staged]
        clean_plan = [str(item).strip()[:240] for item in list(plan or [])[:8] if str(item).strip()]
        if not clean_plan:
            clean_plan = [f"Update {path}." for path in paths[:3]]
        proposal = Proposal(
            id=str(uuid.uuid4()),
            run_id=run_id,
            workspace=ws,
            repository_id=repo.id,
            edits=staged,
            conversation_id=conversation_id,
            requested_change=requested_change[:8000],
            plan=clean_plan,
            affected_files=paths,
            inspected_files=normalize_path_list(inspected_files or [], limit=32),
            provider=provider,
            model=model,
            execution_mode=execution_mode,
            context_scope=context_scope,
            evidence_provenance=dict(evidence_provenance or {}),
            rollback_snapshot=rollback,
            created_at=now,
            updated_at=now,
            parent_proposal_id=parent_proposal_id,
            source_test_run_id=source_test_run_id,
        )
        self._proposals[run_id] = proposal
        self._run_scope.setdefault(run_id, (ws, repo.id))
        self._persist_proposal(proposal)
        self._audit_proposal("coding_proposal_created", proposal, ok=True)
        return proposal

    def accept(self, workspace: str, run_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "pending":
            raise ClimateCodingError("Proposal is no longer pending", code="proposal_closed")
        if proposal.context_scope != REPOSITORY or not proposal.repository_id:
            raise ClimateCodingError(
                "Edit proposals require a Specific Repository scope.", code="repository_scope_required"
            )
        repo = self.require_repo(proposal.workspace, proposal.repository_id)
        for edit in proposal.edits:
            path = self._validate_proposal_path(str(edit.get("path") or ""))
            current = self.repository_workspace.edit_state(repo, path)
            digest = str(current.get("sha256") or "")
            if digest != edit["base_sha256"]:
                proposal.state = "conflict"
                proposal.decision = "conflict"
                proposal.error = f"File changed since proposal: {edit['path']}"
                proposal.decided_at = self._utc_now()
                proposal.updated_at = proposal.decided_at
                self._persist_proposal(proposal)
                self._audit_proposal("coding_proposal_conflict", proposal, ok=False)
                raise ClimateCodingError(
                    f"File changed since proposal: {edit['path']}", code="proposal_conflict"
                )
            self.repository_workspace.preview_save(repo, path, edit["content"])
        applied = []
        for edit in proposal.edits:
            if edit["changed"]:
                applied.append(self.repository_workspace.save(
                    repo, edit["path"], edit["content"], confirm=True
                ))
        proposal.state = "accepted"
        proposal.decision = "accepted"
        proposal.files_changed = [dict(item) for item in applied]
        proposal.resulting_state = [
            {
                "path": edit["path"],
                "sha256": self.repository_workspace.edit_state(repo, edit["path"])["sha256"],
                "bytes": self.repository_workspace.edit_state(repo, edit["path"])["size"],
            }
            for edit in proposal.edits if edit.get("changed")
        ]
        proposal.applied_at = self._utc_now()
        proposal.decided_at = proposal.applied_at
        proposal.updated_at = proposal.applied_at
        self._persist_proposal(proposal)
        self._audit_proposal("coding_proposal_accepted", proposal, ok=True)
        profiles = self.test_profiles(proposal.workspace, proposal.run_id)
        return {
            "ok": True,
            "state": proposal.state,
            "proposal_id": proposal.id,
            "applied": applied,
            "test_profiles": profiles,
            "tests_available": bool(profiles),
        }

    def reject(self, workspace: str, run_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "pending":
            raise ClimateCodingError("Proposal is no longer pending", code="proposal_closed")
        proposal.state = "rejected"
        proposal.decision = "rejected"
        proposal.decided_at = self._utc_now()
        proposal.updated_at = proposal.decided_at
        self._persist_proposal(proposal)
        self._audit_proposal("coding_proposal_rejected", proposal, ok=True)
        return {"ok": True, "state": proposal.state, "applied": []}

    def test_profiles(self, workspace: str, run_id: str) -> list[dict[str, Any]]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "accepted":
            raise ClimateCodingError("Tests are available only after Accept.", code="proposal_not_applied")
        if self.test_execution is None:
            return []
        repo = self.require_repo(proposal.workspace, proposal.repository_id)
        root = self._repository_root(repo)
        return [
            profile.public()
            for profile in self.test_execution.discover(
                repo, root, proposal.affected_files, self.repository_workspace.profile_store
            )
        ]

    def run_tests(self, workspace: str, run_id: str, profile_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "accepted":
            raise ClimateCodingError("Tests require an accepted proposal.", code="proposal_not_applied")
        if self.test_execution is None:
            raise ClimateCodingError("Test execution is unavailable.", code="unavailable")
        repo = self.require_repo(proposal.workspace, proposal.repository_id)
        record = self.test_execution.start(
            proposal=proposal,
            repo=repo,
            root=self._repository_root(repo),
            profile_id=profile_id,
            profile_store=self.repository_workspace.profile_store,
        )
        self._audit_test("coding_tests_started", record, ok=True)
        return record

    def skip_tests(self, workspace: str, run_id: str) -> dict[str, Any]:
        proposal = self._require_proposal(workspace, run_id)
        if proposal.state != "accepted":
            raise ClimateCodingError("Tests require an accepted proposal.", code="proposal_not_applied")
        if self.test_execution is None:
            raise ClimateCodingError("Test execution is unavailable.", code="unavailable")
        record = self.test_execution.skip(proposal)
        self._audit_test("coding_tests_skipped", record, ok=True)
        return record

    def test_result(self, workspace: str, test_run_id: str) -> dict[str, Any]:
        ws = normalize_workspace(workspace)
        if self.test_execution is None:
            raise ClimateCodingError("Test execution is unavailable.", code="unavailable")
        record = self.test_execution.store.get(test_run_id)
        if record is None:
            raise ClimateCodingError("Test run not found.", code="not_found")
        if record.get("workspace") != ws:
            raise ClimateCodingError("Test run belongs to another workspace.", code="workspace_isolation")
        return record

    def cancel_tests(self, workspace: str, test_run_id: str) -> dict[str, Any]:
        record = self.test_result(workspace, test_run_id)
        assert self.test_execution is not None
        cancelled = self.test_execution.cancel(test_run_id)
        self._audit_test("coding_tests_cancel_requested", cancelled, ok=True)
        return cancelled

    def follow_up_test_failure(self, workspace: str, test_run_id: str) -> dict[str, Any]:
        record = self.test_result(workspace, test_run_id)
        if record.get("status") != "failed":
            raise ClimateCodingError("A follow-up proposal requires a failed test run.", code="test_not_failed")
        if record.get("follow_up_run_id"):
            raise ClimateCodingError("A follow-up run already exists.", code="follow_up_exists")
        parent = self._require_proposal(workspace, str(record.get("proposal_run_id") or ""))
        failure = "\n".join(filter(None, [str(record.get("stdout") or ""), str(record.get("stderr") or "")]))[:12_000]
        failed = ", ".join(record.get("failed_tests") or []) or "See bounded output"
        prompt = (
            "Propose a minimal fix for the explicitly run tests that failed after the accepted change. "
            "Do not apply changes or run commands. Verify exact current files and return a new review-gated proposal.\n\n"
            f"Original request: {parent.requested_change[:2000]}\n"
            f"Changed files: {', '.join(parent.affected_files)}\n"
            f"Test profile: {record.get('profile_name')}\n"
            f"Failed tests: {failed}\n"
            f"Bounded test evidence:\n{failure}"
        )
        run = self.execute(
            parent.workspace,
            parent.repository_id,
            prompt=prompt,
            display_prompt="Propose a fix for the failed approved tests.",
            provider=parent.provider,
            model=parent.model,
            task_mode="edit",
            execution_mode=parent.execution_mode or CLIMATE_ASSISTED,
            context_scope=REPOSITORY,
            selected_files=list(parent.affected_files)[:MAX_PROPOSAL_FILES],
            current_file=(parent.affected_files[0] if parent.affected_files else ""),
            conversation_id=parent.conversation_id,
            surface="workspace",
        )
        follow_run_id = str(run.get("id") or "")
        if follow_run_id:
            meta = self._run_meta.get(follow_run_id) or {}
            meta["parent_proposal_id"] = parent.id
            meta["source_test_run_id"] = test_run_id
            self._run_meta[follow_run_id] = meta
            assert self.test_execution is not None
            self.test_execution.store.update(test_run_id, follow_up_run_id=follow_run_id)
        self._audit_test("coding_test_follow_up_started", {**record, "follow_up_run_id": follow_run_id}, ok=True)
        return run

    def _repository_root(self, repo: Repository) -> Path:
        root = self.repository_workspace.availability(repo).get("root")
        if not root:
            raise ClimateCodingError("Local repository unavailable", code="repository_unavailable")
        return Path(str(root)).resolve()

    def _audit_test(self, action: str, record: dict[str, Any], *, ok: bool) -> None:
        if self.audit_store is None:
            return
        self.audit_store.append(
            action=action,
            target=str(record.get("id") or ""),
            detail=f"{record.get('repository_id')}: {record.get('status')}",
            ok=ok,
            metadata={
                "proposal_id": record.get("proposal_id"),
                "profile_id": record.get("profile_id"),
                "command": list(record.get("command") or []),
                "exit_code": record.get("exit_code"),
                "timed_out": bool(record.get("timed_out")),
                "cancel_requested": bool(record.get("cancel_requested")),
            },
        )

    def ports(self, workspace: str, repository_id: str) -> dict[str, Any]:
        """Read-only local listener discovery for the selected CLIMATE repository."""
        repo = self.require_repo(workspace, repository_id)
        rows = self.repository_workspace.summarize_local_processes([repo])
        ports: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()

        def append_port(
            *,
            port: int,
            pid: int,
            process: str,
            source: str,
            repository_id_value: str = "",
            repository_name: str = "",
            run_id: str = "",
            session: str = "",
            managed_by_hub: bool = False,
        ) -> None:
            key = (int(port), int(pid or 0))
            if key in seen or int(port) <= 0:
                return
            seen.add(key)
            ports.append(
                {
                    "port": int(port),
                    "pid": int(pid or 0) or None,
                    "process": redact_text(str(process or ""), limit=200),
                    "source": source,
                    "session": session,
                    "repository_id": repository_id_value,
                    "repository_name": repository_name,
                    "run_id": run_id,
                    "managed_by_hub": bool(managed_by_hub),
                    "open_url": f"http://127.0.0.1:{int(port)}",
                }
            )

        for row in rows:
            port = row.get("port")
            if not port:
                continue
            source = "run-profile" if (row.get("managed_by_hub") or row.get("run_id")) else "repository"
            append_port(
                port=int(port),
                pid=int(row.get("pid") or 0),
                process=str(row.get("command_redacted") or row.get("executable") or ""),
                source=source,
                repository_id_value=str(row.get("repo_id") or repo.id),
                repository_name=str(row.get("repository_name") or repo.name),
                run_id=str(row.get("run_id") or ""),
                session=str(row.get("profile_id") or row.get("run_id") or ""),
                managed_by_hub=bool(row.get("managed_by_hub")),
            )

        hub_by_pid: dict[int, Any] = {}
        for run in self.repository_workspace.processes.list_runs(repo_id=repo.id, refresh=False):
            if run.status not in ACTIVE_STATUSES or not run.pid:
                continue
            hub_by_pid[int(run.pid)] = run
            if run.port:
                append_port(
                    port=int(run.port),
                    pid=int(run.pid),
                    process=" ".join(str(part) for part in (run.argv_redacted or [])[:6])
                    or (run.executable_path or "hub run"),
                    source="run-profile",
                    repository_id_value=repo.id,
                    repository_name=repo.name,
                    run_id=str(run.run_id or ""),
                    session=str(run.profile_id or run.run_id or ""),
                    managed_by_hub=True,
                )

        pid_rows = {int(row.get("pid") or 0): row for row in rows if row.get("pid")}
        extra = 0
        try:
            listeners = port_listeners()
        except Exception:  # noqa: BLE001
            listeners = {}
        for port, pids in sorted(listeners.items()):
            if int(port) < 1024:
                continue
            for pid in pids:
                key = (int(port), int(pid or 0))
                if key in seen:
                    continue
                match = pid_rows.get(int(pid))
                run = hub_by_pid.get(int(pid))
                if match:
                    source = "run-profile" if (match.get("managed_by_hub") or match.get("run_id")) else "repository"
                    append_port(
                        port=int(port),
                        pid=int(pid),
                        process=str(match.get("command_redacted") or match.get("executable") or ""),
                        source=source,
                        repository_id_value=str(match.get("repo_id") or repo.id),
                        repository_name=str(match.get("repository_name") or repo.name),
                        run_id=str(match.get("run_id") or ""),
                        session=str(match.get("profile_id") or match.get("run_id") or ""),
                        managed_by_hub=bool(match.get("managed_by_hub")),
                    )
                elif run is not None:
                    append_port(
                        port=int(port),
                        pid=int(pid),
                        process=" ".join(str(part) for part in (run.argv_redacted or [])[:6])
                        or (run.executable_path or "hub run"),
                        source="run-profile",
                        repository_id_value=repo.id,
                        repository_name=repo.name,
                        run_id=str(run.run_id or ""),
                        session=str(run.profile_id or run.run_id or ""),
                        managed_by_hub=True,
                    )
                else:
                    extra += 1
                    if extra > 60:
                        continue
                    append_port(
                        port=int(port),
                        pid=int(pid),
                        process=f"pid {pid}",
                        source="local",
                    )

        ports = ports[:MAX_CLIMATE_PORTS]
        return {
            "ok": True,
            "count": len(ports),
            "ports": ports,
            "discovery": "read-only",
            "forwarding": False,
        }

    def debug(self, workspace: str, repository_id: str) -> dict[str, Any]:
        """Active hub-managed run-profile console — not a debugger and not a PTY."""
        repo = self.require_repo(workspace, repository_id)
        runs = self.repository_workspace.processes.list_runs(repo_id=repo.id, refresh=True)
        active = [run for run in runs if run.status in ACTIVE_STATUSES]
        if not active:
            return {
                "ok": True,
                "active": False,
                "message": "No active debug session",
                "session": None,
                "logs": [],
                "evaluate": False,
            }
        active.sort(key=lambda run: str(run.started_at or ""), reverse=True)
        run = active[0]
        public = run.to_public() if hasattr(run, "to_public") else {}
        logs: list[dict[str, str]] = []
        try:
            chunk = self.repository_workspace.read_logs(repo, run.run_id, offset=0, limit=MAX_DEBUG_LOG_LINES)
            for line in chunk.get("lines") or []:
                text = str(line)
                stream = "stderr" if text.startswith("[ERR]") else "stdout"
                logs.append({"stream": stream, "text": redact_text(text, limit=2000)})
        except Exception as exc:  # noqa: BLE001
            logs.append({"stream": "stderr", "text": redact_text(str(exc), limit=400)})
        return {
            "ok": True,
            "active": True,
            "message": "",
            "evaluate": False,
            "session": {
                "run_id": public.get("run_id") or run.run_id,
                "profile_id": public.get("profile_id") or run.profile_id,
                "status": public.get("status") or run.status,
                "pid": public.get("pid") or run.pid,
                "port": public.get("port") or run.port,
                "local_url": public.get("local_url") or "",
                "started_at": public.get("started_at") or run.started_at,
                "error": public.get("error") or run.error or "",
                "cwd": public.get("cwd") or getattr(run, "cwd", "") or "",
            },
            "logs": logs,
        }

    def _repository_display_name(self, repo_id: str) -> str:
        rid = str(repo_id or "").strip()
        if not rid:
            return ""
        repo = self.registry.get(rid)
        return str(getattr(repo, "name", "") or rid)

    def _record_execution(
        self,
        result: dict[str, Any],
        *,
        ws: str,
        surface: str,
        execution_mode: str,
        scope: str,
        repository_id: str = "",
        provider: str = "",
        model: str = "",
        attached_files: list[str] | None = None,
        retrieved_files: list[str] | None = None,
        selected_files: list[str] | None = None,
        current_file: str = "",
        sources: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist executed configuration in memory and stamp the public result."""
        scoped = repository_id if scope == REPOSITORY else ""
        attached = normalize_path_list(attached_files)
        retrieved = normalize_path_list(retrieved_files)
        source_list = normalize_path_list(
            sources if sources is not None else (list(attached) + list(retrieved)),
            limit=24,
        )
        repo_name = self._repository_display_name(scoped) if scoped else ""
        run_id = str(result.get("id") or "")
        extra_meta = dict(extra or {})
        if run_id:
            self._run_scope[run_id] = (ws, scoped)
            meta = {
                "task_mode": result.get("task_mode") or extra_meta.get("task_mode") or "ask",
                "selected_files": list(selected_files or []),
                "current_file": current_file,
                "provider_invoked": extra_meta.get(
                    "provider_invoked",
                    bool(result.get("provider_invoked", True)),
                ),
                "sources": source_list,
                "attached_files": attached,
                "retrieved_files": retrieved,
                "user_prompt": extra_meta.get("user_prompt") or "",
                "execution_mode": execution_mode,
                "context_scope": scope,
                "repository_id": scoped,
                "repository_name": repo_name,
                "surface": surface,
                "conversation_id": result.get("conversation_id") or "",
                "sources_considered": list(extra_meta.get("sources_considered") or []),
                "sources_queried": list(extra_meta.get("sources_queried") or []),
                "sources_used": list(extra_meta.get("sources_used") or []),
                "evidence_references": list(extra_meta.get("evidence_references") or []),
                "context_source_failures": list(extra_meta.get("context_source_failures") or []),
                "repository_evidence_origin": str(extra_meta.get("repository_evidence_origin") or "none"),
                "repository_evidence_origins": list(extra_meta.get("repository_evidence_origins") or []),
            }
            if extra_meta.get("preflight") is not None:
                meta["preflight"] = extra_meta.get("preflight")
            self._run_meta[run_id] = meta
        result["sources"] = source_list
        for key in (
            "sources_considered",
            "sources_queried",
            "sources_used",
            "evidence_references",
            "context_source_failures",
            "repository_evidence_origin",
            "repository_evidence_origins",
        ):
            if key in extra_meta:
                result[key] = (
                    str(extra_meta.get(key) or "none")
                    if key == "repository_evidence_origin"
                    else list(extra_meta.get(key) or [])
                )
        return self._apply_execution_identity(
            result,
            execution_mode=execution_mode,
            context_scope=scope,
            repository_id=scoped,
            repository_name=repo_name,
            provider=provider or result.get("provider"),
            model=model or result.get("model"),
            surface=surface,
            attached_files=attached,
            retrieved_files=retrieved,
            inspected_files=list(result.get("inspected_files") or []),
        )

    def _apply_execution_identity(
        self,
        result: dict[str, Any],
        *,
        execution_mode: str | None = None,
        context_scope: str | None = None,
        repository_id: str | None = None,
        repository_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        surface: str | None = None,
        attached_files: list[str] | None = None,
        retrieved_files: list[str] | None = None,
        inspected_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Stamp speaker label + compact Details from persisted run metadata."""
        raw_mode = execution_mode or result.get("execution_mode")
        if raw_mode:
            mode = normalize_execution_mode(raw_mode)
            result["execution_mode"] = mode
        else:
            mode = ""
        scope = str(context_scope or result.get("context_scope") or "").strip()
        if scope:
            result["context_scope"] = scope
        repo_id = str(
            repository_id if repository_id is not None else result.get("repository_id") or ""
        ).strip()
        name = str(
            repository_name if repository_name is not None else result.get("repository_name") or ""
        ).strip()
        if scope == REPOSITORY:
            result["repository_id"] = repo_id
            result["repository_name"] = name or self._repository_display_name(repo_id)
        elif scope in {GENERAL, ALL}:
            result["repository_id"] = ""
            result["repository_name"] = ""
            repo_id = ""
        else:
            if repo_id:
                result["repository_id"] = repo_id
            if name or repo_id:
                result["repository_name"] = name or self._repository_display_name(repo_id)
        if surface is not None or result.get("surface"):
            result["surface"] = str(surface if surface is not None else result.get("surface") or "")
        if attached_files is not None:
            result["attached_files"] = normalize_path_list(attached_files)
        elif "attached_files" not in result:
            result["attached_files"] = []
        if retrieved_files is not None:
            result["retrieved_files"] = normalize_path_list(retrieved_files)
        elif "retrieved_files" not in result:
            result["retrieved_files"] = []
        if inspected_files is not None:
            result["inspected_files"] = normalize_path_list(inspected_files)
        elif "inspected_files" not in result:
            result["inspected_files"] = []
        provider_id = str(provider or result.get("provider") or "")
        provider_label = provider_display_label(
            provider_id, str(result.get("provider_label") or "")
        )
        result["provider_label"] = provider_label
        if mode:
            result["assistant_label"] = assistant_label(mode, provider_label)
            result["execution_summary"] = format_execution_summary(
                execution_mode=mode,
                provider_label=provider_label,
                model=str(model or result.get("model") or ""),
                context_scope=scope,
                repository_label=str(result.get("repository_name") or ""),
            )
        return result

    def _require_run_scope(self, workspace: str, run_id: str) -> None:
        scope = self._run_scope.get(run_id)
        if scope is not None and scope[0] != workspace:
            raise ClimateCodingError("Run belongs to another workspace", code="workspace_isolation")

    def _require_proposal(self, workspace: str, run_id: str) -> Proposal:
        ws = normalize_workspace(workspace)
        proposal = self._load_proposal(run_id)
        if proposal is None:
            raise ClimateCodingError("Proposal not found", code="not_found")
        if proposal.workspace != ws:
            raise ClimateCodingError("Proposal belongs to another workspace", code="workspace_isolation")
        return proposal

    def _load_proposal(self, run_id: str) -> Proposal | None:
        proposal = self._proposals.get(run_id)
        if proposal is not None or self.proposal_store is None:
            return proposal
        record = self.proposal_store.get(run_id)
        if record is None:
            return None
        fields = Proposal.__dataclass_fields__
        proposal = Proposal(**{key: record.get(key) for key in fields if key in record})
        self._proposals[run_id] = proposal
        self._run_scope.setdefault(run_id, (proposal.workspace, proposal.repository_id))
        return proposal

    def _persist_proposal(self, proposal: Proposal) -> None:
        if self.proposal_store is not None:
            self.proposal_store.save(dict(vars(proposal)))

    def _audit_proposal(self, action: str, proposal: Proposal, *, ok: bool) -> None:
        if self.audit_store is None:
            return
        self.audit_store.append(
            action=action,
            target=proposal.run_id,
            detail=f"{proposal.repository_id}: {proposal.state}",
            ok=ok,
            metadata={
                "proposal_id": proposal.id,
                "conversation_id": proposal.conversation_id,
                "repository_id": proposal.repository_id,
                "files": list(proposal.affected_files),
                "provider": proposal.provider,
                "model": proposal.model,
                "execution_mode": proposal.execution_mode,
                "context_scope": proposal.context_scope,
            },
        )

    @staticmethod
    def _validate_proposal_path(value: str) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if (
            not raw
            or raw.startswith("/")
            or re.match(r"^[A-Za-z]:", raw)
            or "\x00" in raw
        ):
            raise ClimateCodingError("Proposal path must be repository-relative.", code="path_invalid")
        parts = PurePosixPath(raw).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ClimateCodingError("Proposal path traversal is not allowed.", code="path_invalid")
        excluded = {"vendor", "generated", "target", "out"}
        if any(should_skip_dir(part) or part.lower() in excluded for part in parts[:-1]):
            raise ClimateCodingError("Proposal targets an excluded directory.", code="path_excluded")
        return "/".join(parts)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

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
            "id": proposal.id,
            "run_id": proposal.run_id,
            "workspace": proposal.workspace,
            "repository_id": proposal.repository_id,
            "state": proposal.state,
            "plan": list(proposal.plan),
            "affected_files": list(proposal.affected_files),
            "inspected_files": list(proposal.inspected_files),
            "edits": edits,
            "large_diff": large,
            "requires_review": True,
            "line_plus": total_plus,
            "line_minus": total_minus,
            "warning": (
                "Large or destructive replacement detected. Review the diff before Accept."
                if large
                else ""
            ),
            "evidence_provenance": dict(proposal.evidence_provenance),
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


def investigation_diagnostics(result: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact ASK investigation stats. Raw command output stays in tool_activity/logs only."""
    meta = meta or {}
    preflight = dict(meta.get("preflight") or result.get("preflight") or {})
    diagnostics = dict(preflight.get("diagnostics") or {})
    summary = summarize_tool_activity(
        list(result.get("tool_activity") or []),
        str(result.get("logs") or ""),
    )
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    started = result.get("started_at") or result.get("created_at")
    finished = result.get("finished_at")
    elapsed = None
    try:
        if started is not None and finished is not None:
            elapsed = max(0, int(finished) - int(started))
            if elapsed > 10_000_000:
                elapsed = elapsed // 1000
    except (TypeError, ValueError):
        elapsed = None
    payload = {
        "candidate_sources": len(list(meta.get("sources") or result.get("sources") or [])),
        "authoritative_candidates": list(diagnostics.get("authoritative_sources") or [])[:16],
        "resolver_queries": list(diagnostics.get("resolver_queries") or [])[:16],
        "domain_terms": dict(diagnostics.get("domain_terms") or {}),
        "search_commands": summary.public().get("search_commands") or [],
        "successful_searches": summary.successful_searches,
        "failed_searches": summary.failed_searches,
        "invalid_windows_globs": summary.invalid_windows_globs,
        "search_matched_files": summary.search_matched_files,
        "files_inspected": summary.files_inspected,
        "inspected_paths": list(summary.inspected_paths[:16]),
        "tool_calls": summary.tool_calls,
        "provider_tokens": usage.get("total_tokens"),
        "elapsed_ms": elapsed if elapsed is not None else result.get("runtime_ms"),
    }
    payload["log"] = "\n".join([
        "[climate_investigation]",
        f"candidate_sources={payload['candidate_sources']}",
        f"authoritative_candidates={','.join(payload['authoritative_candidates']) or '(none)'}",
        f"resolver_queries={','.join(str(q) for q in payload['resolver_queries']) or '(none)'}",
        f"successful_searches={payload['successful_searches']}",
        f"failed_searches={payload['failed_searches']}",
        f"invalid_windows_globs={payload['invalid_windows_globs']}",
        f"search_matched_files={payload['search_matched_files'] if payload['search_matched_files'] is not None else 'n/a'}",
        f"files_inspected={payload['files_inspected'] if payload['files_inspected'] is not None else 'n/a'}",
        f"tool_calls={payload['tool_calls'] if payload['tool_calls'] is not None else 'n/a'}",
        f"provider_tokens={payload['provider_tokens'] if payload['provider_tokens'] is not None else 'n/a'}",
        f"elapsed_ms={payload['elapsed_ms'] if payload['elapsed_ms'] is not None else 'n/a'}",
    ])
    return payload
