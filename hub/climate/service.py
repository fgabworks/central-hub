"""CLIMATE workspace facade with workspace/repository isolation."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from typing import Any

from hub.agent_center.repository_context import explicit_repository_id
from hub.agent_center.redact import redact_text
from hub.climate.file_view import as_read_only_file
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError, classify_task_mode
from hub.climate.codex_limits import get_codex_rate_limits_service
from hub.climate.investigation_metrics import summarize_tool_activity
from hub.climate.execution_mode import (
    CLIMATE_ASSISTED,
    DIRECT,
    MODE_LABELS,
    MODE_TOOLTIPS,
    EXECUTION_MODES,
    is_direct_mode,
    normalize_execution_mode,
)
from hub.climate.preflight import make_blocked_run, resolve_climate_context
from hub.climate.retrieval_policy import is_logs_history_query, is_noisy_artifact
from hub.climate.token_efficiency import TokenEfficiencyService
from hub.registry.models import Registry, Repository
from hub.repository_workspace.ports import port_listeners
from hub.repository_workspace.process_manager import ACTIVE_STATUSES
from hub.repository_workspace.security import WorkspaceSecurityError
from hub.repository_workspace.service import RepositoryWorkspaceService

MAX_CLIMATE_PORTS = 80
MAX_DEBUG_LOG_LINES = 200


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
        self.token_efficiency = TokenEfficiencyService()

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
        return self.coding.conversation(
            conversation_id,
            workspace=ws,
            repository_id=repository_id,
            surface=surface,
        )

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

    def execute_chat(self, workspace: str, **payload: Any) -> dict[str, Any]:
        """General AiriX chat — no implied repository scan or editor context."""
        ws = normalize_workspace(workspace)
        prompt = str(payload.get("prompt") or "")
        display_prompt = str(payload.get("display_prompt") or prompt)
        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        direct = is_direct_mode(execution_mode)
        repo_id = explicit_repository_id(payload.get("repository_id"))
        selected_files = [
            str(path).replace("\\", "/")
            for path in list(payload.get("selected_files") or [])
            if str(path).strip()
        ]
        current_file = str(payload.get("current_file") or "").replace("\\", "/")
        selection = str(payload.get("selection") or "")
        if repo_id:
            self.require_repo(ws, repo_id)
        include_repo_context = bool(payload.get("include_repo_context")) and bool(repo_id)
        extra_selection = selection
        packed_prompt = prompt
        if include_repo_context:
            repo = self.require_repo(ws, repo_id)
            if direct:
                extra_selection = self._attach_selected_file_bodies(
                    repo,
                    current_file=current_file,
                    selected_files=selected_files,
                    selection=selection,
                )
                if not extra_selection:
                    extra_selection = (
                        f"Explicit repository context selected: {repo.name} ({repo.id}). "
                        "No files were attached."
                    )
            else:
                preflight = resolve_climate_context(
                    workspace=ws,
                    repo=repo,
                    repository_workspace=self.repository_workspace,
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    task_mode="ask",
                    current_file=current_file,
                    selected_files=selected_files,
                    selection=selection,
                    include_repo_context=True,
                    repository_intelligence=self._repository_intelligence() if ws == "work" else None,
                    handoff=bool(payload.get("handoff")),
                    repository_agent=False,
                )
                if preflight.ok and preflight.packet:
                    packed_prompt = preflight.packet
                extra_selection = selection
        result = self.coding.execute(
            workspace=ws,
            repository_id="",
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
        )
        run_id = str(result.get("id") or "")
        if run_id:
            self._run_scope[run_id] = (ws, "")
            self._run_meta[run_id] = {
                "task_mode": "ask",
                "selected_files": selected_files if include_repo_context else [],
                "current_file": current_file if include_repo_context else "",
                "provider_invoked": True,
                "sources": [],
                "user_prompt": display_prompt or prompt,
                "execution_mode": execution_mode,
                "surface": "chat",
                "conversation_id": result.get("conversation_id") or "",
            }
        result["task_mode"] = "ask"
        result["execution_mode"] = execution_mode
        result["sources"] = []
        result["provider_invoked"] = True
        return result

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
        if provider == "gemini" and ws == "work":
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
            selection=selection if (ws == "personal" or provider == "gemini") else "",
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
        )
        self._run_scope[str(result["id"])] = (ws, repo.id)
        self._run_meta[str(result["id"])] = {
            "task_mode": result.get("task_mode") or preflight.task_mode,
            "selected_files": selected_files,
            "current_file": current_file,
            "provider_invoked": True,
            "conversation_id": result.get("conversation_id") or "",
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
            "user_prompt": prompt,
            "execution_mode": CLIMATE_ASSISTED,
        }
        result["provider_invoked"] = True
        result["execution_mode"] = CLIMATE_ASSISTED
        result["preflight"] = self._run_meta[str(result["id"])]["preflight"]
        result["sources"] = list(self._run_meta[str(result["id"])]["sources"])[:24]
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
        can_investigate: bool,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Raw prompt + approved cwd. Skip Context Resolver; keep ASK/EDIT safety."""
        safety_log = (
            "[climate_execution_mode]\n"
            "Mode=direct\n"
            "Context Resolver skipped\n"
            "Safety=approved repo boundary, ASK read-only sandbox, controlled EDIT"
        )
        result = self.coding.execute(
            workspace=ws,
            repository_id=repo.id,
            provider=provider,
            model=model,
            prompt=prompt,
            selected_files=list(selected_files)[:16],
            current_file=current_file,
            selection=selection if (ws == "personal" or provider == "gemini") else "",
            include_repo_context=False,
            task_mode=task_mode,
            reuse_session=bool(payload.get("reuse_session", True)) and not handoff,
            handoff=handoff,
            preflight_log=safety_log,
            evidence_packet=None,
            conversation_id=str(payload.get("conversation_id") or "").strip(),
            repository_investigation=can_investigate,
            execution_mode=DIRECT,
            display_prompt=str(payload.get("display_prompt") or prompt),
        )
        preflight = {
            "ok": True,
            "activity": [
                "Direct Provider",
                "Context Resolver skipped",
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
        self._run_scope[str(result["id"])] = (ws, repo.id)
        self._run_meta[str(result["id"])] = {
            "task_mode": result.get("task_mode") or task_mode,
            "selected_files": selected_files,
            "current_file": current_file,
            "provider_invoked": True,
            "conversation_id": result.get("conversation_id") or "",
            "preflight": preflight,
            "sources": list(dict.fromkeys(selected_files + ([current_file] if current_file else []))),
            "user_prompt": prompt,
            "execution_mode": DIRECT,
        }
        result["provider_invoked"] = True
        result["execution_mode"] = DIRECT
        result["preflight"] = preflight
        result["sources"] = list(self._run_meta[str(result["id"])]["sources"])[:24]
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
            return local
        result = self.coding.result(run_id, workspace=ws)
        scope = self._run_scope.get(run_id)
        meta = self._run_meta.get(run_id) or {}
        task_mode = str(meta.get("task_mode") or result.get("task_mode") or "ask")
        result["task_mode"] = task_mode
        result["execution_mode"] = meta.get("execution_mode") or result.get("execution_mode") or CLIMATE_ASSISTED
        if scope:
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
        investigation = investigation_diagnostics(result, meta)
        result["investigation"] = investigation
        result["files_inspected"] = investigation.get("files_inspected")
        result["search_matched_files"] = investigation.get("search_matched_files")
        result["tool_calls"] = investigation.get("tool_calls")
        inv_log = investigation.get("log") or ""
        logs = str(result.get("logs") or "")
        if inv_log and "[climate_investigation]" not in logs:
            result["logs"] = (logs + ("\n\n" if logs else "") + inv_log).strip()
        if str(result.get("provider") or result.get("agent_id") or "") == "codex":
            result["token_efficiency"] = self._token_efficiency_payload(ws, run_id, result)
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
