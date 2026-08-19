"""Provider-neutral coding adapter backed by the existing Agent Center runtime."""

from __future__ import annotations

import json
import re
from typing import Any

from hub.agent_center.service import AgentCenterError, AgentCenterService
from hub.climate.logic_format import (
    format_logic_explanation,
    is_logic_explanation_prompt,
    logic_explanation_instructions,
)
from hub.climate.retrieval_policy import ASK_INVESTIGATION_CONSTRAINTS
from hub.agent_center.repository_context import explicit_repository_id
from hub.climate.execution_mode import (
    assistant_label,
    climate_execution_record,
    format_execution_summary,
    is_direct_mode,
    normalize_path_list,
    provider_display_label,
    normalize_execution_mode,
)
from hub.climate.context_scope import ALL, CONTEXT_SCOPES, GENERAL, REPOSITORY, normalize_context_scope
from hub.climate.investigation_metrics import summarize_tool_activity
from hub.agent_center.connections import API_CHAT_PROVIDER_IDS, CODING_CLI_PROVIDER_IDS


CODING_PROVIDERS = CODING_CLI_PROVIDER_IDS

_ASK_RE = re.compile(
    r"\b("
    r"explain|describe|summarize|summary|clarify|overview|"
    r"what(?:'s|\s+is|\s+are|\s+does|\s+do)|"
    r"how(?:\s+does|\s+do|\s+is|\s+are|\s+can|\s+should)?|"
    r"why(?:\s+does|\s+do|\s+is|\s+are)?|"
    r"where(?:\s+is|\s+are|\s+does)?|"
    r"which|who|when|tell\s+me|show\s+me|list|find|search|look\s+up|"
    r"compare|difference|derived|derivation|meaning\s+of"
    r")\b",
    re.I,
)
_EDIT_RE = re.compile(
    r"\b("
    r"edit|fix|change|modify|update|refactor|implement|patch|apply|"
    r"add|remove|delete|rename|replace|write|create|insert|migrate|"
    r"generate\s+code|make\s+changes?|update\s+the\s+file|open\s+a\s+pr"
    r")\b",
    re.I,
)
_EDITS_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
    re.I,
)


def classify_task_mode(prompt: str, explicit: str | None = None) -> str:
    """Classify a coding prompt as ask (read-only) or edit (proposal allowed)."""
    mode = str(explicit or "").strip().lower()
    if mode in {"ask", "edit"}:
        return mode
    text = (prompt or "").strip()
    if not text:
        return "ask"
    lower = text.lower()
    if re.search(r"\b(?:do\s+not|don't|dont|never)\s+(?:edit|modify|change|write)\b|\bno\s+(?:file\s+)?edits?\b", lower):
        return "ask"
    ask_score = 0
    edit_score = 0
    if "?" in text:
        ask_score += 2
    if _ASK_RE.search(text):
        ask_score += 2
    if _EDIT_RE.search(text):
        edit_score += 2
    if re.match(r"^(please\s+)?(explain|describe|summarize|what|how|why|where|which|who|when)\b", lower):
        ask_score += 3
    if re.match(
        r"^(please\s+)?(fix|edit|change|update|implement|add|remove|delete|refactor|create|write|patch)\b",
        lower,
    ):
        edit_score += 3
    if ask_score > edit_score:
        return "ask"
    if edit_score > ask_score:
        return "edit"
    return "ask"


def _is_general_chat_surface(surface: str) -> bool:
    return str(surface or "").strip().lower() in {"chat", "general"}


def _is_workspace_surface(surface: str) -> bool:
    return str(surface or "").strip().lower() == "workspace"


def _is_general_chat_conversation(runs: list[dict[str, Any]]) -> bool:
    return all(not list(run.get("repository_ids") or []) for run in runs)


def _run_surface(run: dict[str, Any]) -> str:
    ctx = run.get("context") if isinstance(run.get("context"), dict) else {}
    exec_meta = ctx.get("climate_execution") if isinstance(ctx.get("climate_execution"), dict) else {}
    return str(exec_meta.get("surface") or "").strip().lower()


def _is_workspace_conversation(runs: list[dict[str, Any]]) -> bool:
    if not runs:
        return False
    surfaces = [_run_surface(run) for run in runs]
    if any(item == "workspace" for item in surfaces):
        return True
    if any(_is_general_chat_surface(item) for item in surfaces):
        return False
    return any(list(run.get("repository_ids") or []) for run in runs)


def _is_chat_conversation(runs: list[dict[str, Any]]) -> bool:
    if _is_workspace_conversation(runs):
        return False
    if not runs:
        return True
    return _is_general_chat_conversation(runs)


def _workspace_open_scope(runs: list[dict[str, Any]]) -> bool:
    return bool(runs) and all(not list(run.get("repository_ids") or []) for run in runs)


class ClimateCodingError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class ClimateCodingAdapter:
    """One CLIMATE contract over existing authenticated provider adapters.

    This class intentionally does not know provider argv, auth, credentials, or
    subprocess details. Those stay owned by Agent Center.
    """

    def __init__(self, agent_center: AgentCenterService) -> None:
        self.agent_center = agent_center

    def availability(self, provider: str | None = None, *, refresh: bool = False) -> Any:
        rows = self.agent_center.connections.list_coding_clis(refresh=refresh, probe=True)
        public = [self._public_connection(row) for row in rows]
        if provider is None:
            return public
        return next((row for row in public if row["id"] == provider), None)

    def coding_defaults(self) -> dict[str, Any]:
        return self.agent_center.connections.coding_defaults()

    def conversations(
        self,
        *,
        workspace: str,
        repository_id: str = "",
        surface: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        profile_id = "okarun" if workspace == "work" else "aira"
        rows = self.agent_center.store.list_conversations(profile_id=profile_id, limit=limit)
        general = _is_general_chat_surface(surface)
        workspace_surface = _is_workspace_surface(surface)
        if not repository_id and not general and not workspace_surface:
            return rows
        filtered = []
        for row in rows:
            runs = self.agent_center.store.list_conversation_runs(
                str(row.get("id") or ""), profile_id=profile_id, limit=100
            )
            if general:
                if _is_chat_conversation(runs):
                    filtered.append(row)
                continue
            if workspace_surface:
                if not _is_workspace_conversation(runs):
                    continue
                if repository_id:
                    repo_match = any(
                        repository_id in list(run.get("repository_ids") or []) for run in runs
                    )
                    if not repo_match and not _workspace_open_scope(runs):
                        continue
                filtered.append(row)
                continue
            if any(repository_id in list(run.get("repository_ids") or []) for run in runs):
                filtered.append(row)
        return filtered

    def conversation(
        self,
        conversation_id: str,
        *,
        workspace: str,
        repository_id: str = "",
        surface: str = "",
    ) -> dict[str, Any]:
        profile_id = "okarun" if workspace == "work" else "aira"
        conversation = self.agent_center.store.get_conversation(
            conversation_id, profile_id=profile_id
        )
        if not conversation:
            raise ClimateCodingError("Conversation not found", code="not_found")
        runs = self.agent_center.store.list_conversation_runs(
            conversation_id, profile_id=profile_id, limit=200
        )
        if _is_general_chat_surface(surface) and not _is_chat_conversation(runs):
            raise ClimateCodingError("Conversation not found", code="not_found")
        if _is_workspace_surface(surface):
            if not _is_workspace_conversation(runs):
                raise ClimateCodingError("Conversation not found", code="not_found")
            if repository_id:
                repo_match = any(
                    repository_id in list(run.get("repository_ids") or []) for run in runs
                )
                if not repo_match and not _workspace_open_scope(runs):
                    raise ClimateCodingError("Conversation not found", code="not_found")
        elif repository_id and not any(
            repository_id in list(run.get("repository_ids") or []) for run in runs
        ):
            raise ClimateCodingError("Conversation not found", code="not_found")
        public_runs = []
        for run in runs:
            public = self._public_run(run, workspace=workspace)
            public["prompt"] = run.get("prompt") or ""
            public["mode"] = run.get("mode") or "ask"
            public_runs.append(public)
        return {**conversation, "runs": public_runs}

    def rename_conversation(
        self,
        conversation_id: str,
        *,
        workspace: str,
        title: str,
        repository_id: str = "",
        surface: str = "",
    ) -> dict[str, Any]:
        profile_id = "okarun" if workspace == "work" else "aira"
        if repository_id or _is_general_chat_surface(surface) or _is_workspace_surface(surface):
            self.conversation(
                conversation_id,
                workspace=workspace,
                repository_id=repository_id,
                surface=surface,
            )
        conversation = self.agent_center.store.rename_conversation(
            conversation_id, profile_id=profile_id, title=title
        )
        if not conversation:
            raise ClimateCodingError("Conversation not found", code="not_found")
        return conversation

    def models(self, provider: str, *, refresh: bool = False) -> dict[str, Any]:
        self._require_provider(provider)
        details = self.agent_center.connections.models(provider, mode="ask", refresh=refresh)
        return {
            "provider": provider,
            "models": list(details.get("models") or []),
            "model_details": list(details.get("model_details") or []),
            "recommended_model": details.get("recommended_model"),
            "models_source": details.get("models_source") or "none",
            "error": str(details.get("error") or ""),
        }

    def can_investigate_repository(self, provider: str) -> bool:
        provider = str(provider or "").strip()
        if provider == "codex":
            return True
        connection = self.availability(provider)
        capabilities = dict((connection or {}).get("capabilities") or {})
        return bool(capabilities.get("native_repository_investigation"))

    def execute(
        self,
        *,
        workspace: str,
        repository_id: str,
        provider: str,
        model: str,
        prompt: str,
        selected_files: list[str] | None = None,
        current_file: str = "",
        selection: str = "",
        include_repo_context: bool = False,
        task_mode: str | None = None,
        reuse_session: bool = True,
        handoff: bool = False,
        preflight_log: str = "",
        evidence_packet: dict[str, Any] | None = None,
        conversation_id: str = "",
        repository_investigation: bool = False,
        execution_mode: str = "",
        display_prompt: str = "",
        surface: str = "",
        context_scope: str = "",
        attached_files: list[str] | None = None,
        retrieved_files: list[str] | None = None,
        repository_name: str = "",
        provider_label: str = "",
    ) -> dict[str, Any]:
        self._require_provider(provider)
        if workspace not in {"work", "personal"}:
            raise ClimateCodingError("Unknown CLIMATE workspace", code="workspace_invalid")
        if not prompt.strip():
            raise ClimateCodingError("Prompt is required", code="prompt_required")
        connection = self.availability(provider)
        if not connection or connection["state"] != "connected":
            detail = (connection or {}).get("detail") or "Provider unavailable"
            raise ClimateCodingError(detail, code=(connection or {}).get("state") or "unavailable")
        if not model.strip():
            raise ClimateCodingError("Select an exact model before running", code="model_required")

        mode = classify_task_mode(prompt, task_mode)
        orchestration = normalize_execution_mode(execution_mode)
        direct = is_direct_mode(orchestration)
        # Prefer task_mode already applied in the context packet when present.
        if not direct:
            if "CLIMATE context packet (EDIT)" in prompt or "CLIMATE preflight context packet (EDIT)" in prompt:
                mode = "edit"
            elif "CLIMATE context packet (ASK)" in prompt or "CLIMATE preflight context packet (ASK)" in prompt:
                mode = "ask"
        if provider in API_CHAT_PROVIDER_IDS and mode != "ask":
            if provider == "gemini":
                raise ClimateCodingError(
                    "Gemini v1 is read-only chat. Ask for an explanation or analysis; editing and agent actions are not enabled.",
                    code="mode_unsupported",
                )
            label = provider_display_label(provider)
            raise ClimateCodingError(
                f"{label} is read-only chat in CLIMATE. Ask for an explanation or analysis; editing and agent actions are not enabled.",
                code="mode_unsupported",
            )
        general_chat = _is_general_chat_surface(surface)
        raw_scope = str(context_scope or "").strip().lower()
        chat_scope = raw_scope if raw_scope in CONTEXT_SCOPES else normalize_context_scope(context_scope)
        if (
            not general_chat
            and str(surface or "").strip().lower() == "workspace"
            and chat_scope in {GENERAL, ALL}
        ):
            general_chat = True
        scoped_repo_id = explicit_repository_id(repository_id) if chat_scope == REPOSITORY else ""
        explicit_files = bool(
            str(current_file or "").strip()
            or any(str(path or "").strip() for path in (selected_files or []))
            or str(selection or "").strip()
        )
        if general_chat:
            # Chat never implies a repository. Keep ASK-only and omit repo/cwd
            # unless the caller supplied explicit bounded context in `selection`.
            repository_investigation = False
            include_repo_context = False
            selected_files = []
            current_file = ""
            repository_id = ""
            if not explicit_files:
                selection = ""
        repository_investigation = bool(repository_investigation and mode == "ask")
        files = list(dict.fromkeys(
            str(path).replace("\\", "/").lstrip("/")
            for path in ([current_file] + list(selected_files or []))
            if str(path).strip()
        ))
        if general_chat:
            has_packet = (not direct) and (
                "CLIMATE context packet" in prompt
                or "CLIMATE preflight context packet" in prompt
            )
            if direct:
                packed_parts = []
                if selection:
                    packed_parts.append("Attached context:\n" + selection[:20_000])
                packed_parts.append(prompt.strip())
                packed_prompt = "\n\n".join(packed_parts)
            else:
                context_note = [
                    "AiriX · CLIMATE Chat (ASK).",
                    "Answer in clear human-readable prose (markdown allowed).",
                    "Use only the user prompt and any supplied bounded context.",
                    "Do not propose file edits, diffs, patches, or command execution.",
                    "Do not assume repository access unless bounded file context is supplied.",
                ]
                if selection:
                    context_note.append("Bounded selected context:\n" + selection[:20_000])
                if reuse_session and not handoff:
                    context_note.append(
                        "Same-provider session: reuse prior provider context when supported."
                    )
                packed_prompt = "\n\n".join(
                    context_note + ([prompt.strip()] if has_packet else ["User prompt:", prompt.strip()])
                )
        else:
            if mode == "edit":
                context_note = [
                    "CLIMATE coding request (EDIT mode).",
                    "Stay read-only at runtime; propose file replacements only.",
                    "Return proposed file replacements in a fenced JSON object using this schema: ",
                    '{"edits":[{"path":"relative/path","content":"complete replacement content"}]}.',
                    "Do not apply edits or execute commands.",
                    (
                        "Do not assume full chat history."
                        if direct
                        else "Use only the bounded context packet; do not assume full chat history."
                    ),
                ]
            elif direct:
                context_note = [
                    "CLIMATE coding request (ASK / EXPLAIN mode).",
                    "Execution mode: Direct Provider.",
                    "Answer in clear human-readable prose (markdown allowed).",
                    (
                        "Investigate the approved repository directly from the user prompt. "
                        "There is no CLIMATE Context Resolver packet and no candidate-source list. "
                        "Use the provider's normal project/repository instructions."
                        if repository_investigation
                        else "Answer from the user prompt. There is no CLIMATE Context Resolver packet."
                    ),
                    "Do NOT propose file edits, diffs, patches, or JSON {\"edits\":[...]} payloads.",
                    (
                        "Cite the concrete implementation paths/functions you verify in the repository."
                        if repository_investigation
                        else "Cite concrete paths/functions when you use them."
                    ),
                    (
                        "Safe read-only search, file/symbol/reference/import/test/git inspection commands "
                        "are allowed. Do not modify files or repository state and do not run destructive commands. "
                        + ASK_INVESTIGATION_CONSTRAINTS
                        if repository_investigation
                        else "Do not apply edits or execute commands."
                    ),
                ]
            else:
                context_note = [
                    "CLIMATE coding request (ASK / EXPLAIN mode).",
                    "Answer in clear human-readable prose (markdown allowed).",
                    (
                        "Use the compact context packet as starting guidance, then independently search, "
                        "read, and trace the approved repository as needed."
                        if repository_investigation
                        else "Use the bounded context packet (read-only)."
                    ),
                    "Do NOT propose file edits, diffs, patches, or JSON {\"edits\":[...]} payloads.",
                    (
                        "Cite the concrete implementation paths/functions you verify in the repository."
                        if repository_investigation
                        else "Cite the concrete paths/functions from the packet."
                    ),
                    (
                        "Safe read-only search, file/symbol/reference/import/test/git inspection commands "
                        "are allowed. Do not modify files or repository state and do not run destructive commands. "
                        + ASK_INVESTIGATION_CONSTRAINTS
                        if repository_investigation
                        else "Do not apply edits or execute commands."
                    ),
                ]
            if mode != "edit" and is_logic_explanation_prompt(prompt):
                context_note.append(logic_explanation_instructions())
            if selection:
                context_note.append("Current editor selection:\n" + selection[:20_000])
            if handoff:
                context_note.append("Cross-provider compact handoff — do not replay full CLIMATE history.")
            if reuse_session and not handoff:
                context_note.append("Same-provider session: reuse prior provider context when supported.")
            has_packet = (not direct) and (
                "CLIMATE context packet" in prompt
                or "CLIMATE preflight context packet" in prompt
            )
            if has_packet:
                packed_prompt = "\n\n".join(context_note + [prompt.strip()])
            elif direct:
                packed_prompt = "\n\n".join(context_note + ["User prompt:", prompt.strip()])
            else:
                context_note.append(
                    "Repository context: " + ("enabled" if include_repo_context else "selected files only")
                )
                packed_prompt = "\n\n".join(context_note + [prompt.strip()])

        profile_id = "okarun" if workspace == "work" else "aira"
        if workspace == "personal" and provider == "codex":
            raise ClimateCodingError(
                "Codex is not enabled for the isolated ARCTIC profile.",
                code="profile_unsupported",
            )
        payload: dict[str, Any] = {
            "profile_id": profile_id,
            "mode": "ask",
            "agent_id": provider,
            "model": model,
            "prompt": packed_prompt,
            "display_prompt": display_prompt.strip() or prompt.strip(),
            "tool_ids": [],
            "files": (
                {}
                if repository_investigation
                else ({repository_id: files} if repository_id and workspace == "work" else {})
            ),
            "repository_ids": [repository_id] if repository_id and workspace == "work" else [],
            "active_repository_id": repository_id if workspace == "work" else None,
            "selected_repository_id": repository_id if workspace == "work" else None,
            "tool_runtime_lean_context": False if direct else (True if has_packet else (not include_repo_context)),
            "bounded_evidence_only": False if direct else bool(has_packet),
            "reuse_provider_session": bool(reuse_session) and not handoff,
            "repository_investigation": bool(repository_investigation),
            "climate_execution": climate_execution_record(
                execution_mode=orchestration,
                context_scope=chat_scope,
                repository_id=scoped_repo_id,
                repository_name=repository_name or scoped_repo_id,
                surface=str(surface or ""),
                provider=provider,
                model=model,
                provider_label=provider_label,
                attached_files=attached_files,
                retrieved_files=retrieved_files,
                current_file=current_file,
            ),
        }
        if general_chat:
            payload["inherit_repository_scope"] = False
            payload["active_repository_id"] = None
            payload["selected_repository_id"] = None
            payload["repository_ids"] = []
            if direct:
                payload["direct_provider_chat"] = True
                payload["allow_general_knowledge"] = True
                payload["tool_runtime"] = False
                payload["bounded_evidence_only"] = False
            elif chat_scope in {GENERAL, ALL}:
                payload["allow_general_knowledge"] = True
                payload["bounded_evidence_only"] = False
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if provider in API_CHAT_PROVIDER_IDS:
            payload["tool_runtime_lean_context"] = True
            if not payload.get("direct_provider_chat") and not payload.get("allow_general_knowledge"):
                payload["bounded_evidence_only"] = True
            payload["repository_investigation"] = False
            payload["files"] = {}
            payload["tool_runtime"] = False
            payload["api_chat"] = True
        elif isinstance(evidence_packet, dict):
            payload["evidence_packet"] = evidence_packet
        try:
            run = self.agent_center.start_run(payload)
        except AgentCenterError as exc:
            raise ClimateCodingError(str(exc), code=exc.code) from exc
        public = self._public_run(run, workspace=workspace, repository_id=scoped_repo_id or repository_id)
        public["task_mode"] = mode
        public["provider_invoked"] = True
        public["execution_mode"] = orchestration
        public["context_scope"] = chat_scope
        public["surface"] = str(surface or public.get("surface") or "")
        public["provider"] = provider
        public["model"] = model
        if scoped_repo_id:
            public["repository_id"] = scoped_repo_id
            public["repository_name"] = str(repository_name or public.get("repository_name") or scoped_repo_id)
        elif chat_scope in {GENERAL, ALL}:
            public["repository_id"] = ""
            public["repository_name"] = ""
        display_provider = provider_display_label(
            provider, str(provider_label or run.get("agent_label") or "")
        )
        public["provider_label"] = display_provider
        public["assistant_label"] = assistant_label(orchestration, display_provider)
        public["attached_files"] = normalize_path_list(attached_files)
        public["retrieved_files"] = normalize_path_list(retrieved_files)
        public["inspected_files"] = list(public.get("inspected_files") or [])
        public["sources"] = normalize_path_list(
            list(public.get("attached_files") or [])
            + list(public.get("retrieved_files") or [])
            + list(public.get("inspected_files") or [])
        )
        public["execution_summary"] = format_execution_summary(
            execution_mode=orchestration,
            provider_label=display_provider,
            model=model,
            context_scope=chat_scope,
            repository_label=str(public.get("repository_name") or ""),
        )
        if preflight_log:
            logs = str(public.get("logs") or "")
            public["logs"] = (preflight_log + ("\n\n" if logs else "") + logs).strip()
        return public

    def cancel(self, run_id: str, *, workspace: str) -> dict[str, Any]:
        profile = "okarun" if workspace == "work" else "aira"
        try:
            run = self.agent_center.cancel_run(run_id, profile_id=profile)
        except AgentCenterError as exc:
            raise ClimateCodingError(str(exc), code=exc.code) from exc
        return self._public_run(run, workspace=workspace)

    def result(self, run_id: str, *, workspace: str) -> dict[str, Any]:
        profile = "okarun" if workspace == "work" else "aira"
        try:
            run = self.agent_center.get_run(run_id, profile_id=profile)
        except AgentCenterError as exc:
            raise ClimateCodingError(str(exc), code=exc.code) from exc
        return self._public_run(run, workspace=workspace)

    def usage(self, run_id: str, *, workspace: str) -> dict[str, Any]:
        result = self.result(run_id, workspace=workspace)
        return dict(result.get("usage") or {})

    @staticmethod
    def proposed_edits(answer: str) -> list[dict[str, str]]:
        candidates = _EDITS_FENCE_RE.findall(answer or "")
        if not candidates and (answer or "").lstrip().startswith("{"):
            candidates = [answer]
        for raw in reversed(candidates):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            edits = payload.get("edits") if isinstance(payload, dict) else None
            if not isinstance(edits, list):
                continue
            clean = []
            for item in edits:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
                content = item.get("content")
                if path and isinstance(content, str):
                    clean.append({"path": path, "content": content})
            if clean:
                return clean
        return []

    @staticmethod
    def humanize_answer(answer: str, *, task_mode: str = "ask", prompt: str = "") -> tuple[str, str]:
        """Return (display_text, raw_payload_for_diagnostics).

        Never leave provider edit-protocol JSON as the normal chat body.
        Logic-explanation formatting is presentation-only and does not invent facts.
        """
        raw = str(answer or "")
        if not raw.strip():
            return "", ""
        edits = ClimateCodingAdapter.proposed_edits(raw)
        stripped = _EDITS_FENCE_RE.sub("", raw)
        stripped = re.sub(
            r"\{\s*\"edits\"\s*:\s*\[[\s\S]*\]\s*\}",
            "",
            stripped,
        ).strip()
        raw_diag = ""
        display = stripped or raw.strip()
        if not stripped and edits:
            parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
            if task_mode == "ask" and parts:
                display, raw_diag = "\n\n".join(parts), raw
            else:
                return ("Proposed changes are ready for review." if task_mode == "edit" else ""), raw
        elif stripped and '"edits"' in stripped and stripped.lstrip().startswith("{"):
            try:
                payload = json.loads(stripped)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("edits"), list):
                edits = ClimateCodingAdapter.proposed_edits(stripped) or edits
                parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
                if task_mode == "ask" and parts:
                    display, raw_diag = "\n\n".join(parts), raw
                else:
                    return ("Proposed changes are ready for review." if task_mode == "edit" else ""), raw
        elif edits and display == raw.strip():
            parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
            if task_mode == "ask" and parts:
                display, raw_diag = "\n\n".join(parts), raw
            elif task_mode == "edit":
                return "Proposed changes are ready for review.", raw
        elif edits and display != raw.strip():
            raw_diag = raw
        if task_mode == "ask" and is_logic_explanation_prompt(prompt):
            display = format_logic_explanation(display)
        return display, raw_diag

    def _require_provider(self, provider: str) -> None:
        if provider not in CODING_PROVIDERS:
            raise ClimateCodingError("Unknown coding provider", code="provider_invalid")

    @staticmethod
    def _public_connection(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "label": row.get("label"),
            "state": row.get("state"),
            "status": row.get("status"),
            "detail": row.get("detail"),
            "installed": bool(row.get("installed")),
            "authenticated": bool(row.get("authenticated")),
            "available": bool(row.get("available")),
            "executable_path": row.get("executable_path") or "",
            "runtime_health": row.get("runtime_health") or "",
            "runtime_complete": bool(row.get("runtime_complete")),
            "discovery_source": row.get("discovery_source") or "",
            "host_path": row.get("host_path") or "",
            "account_label": row.get("account_label") or "",
            "capabilities": dict(row.get("capabilities") or {}),
            "logo": str(row.get("logo") or ""),
        }

    @staticmethod
    def _public_run(run: dict[str, Any], *, workspace: str, repository_id: str = "") -> dict[str, Any]:
        ctx = run.get("context") if isinstance(run.get("context"), dict) else {}
        exec_meta = ctx.get("climate_execution") if isinstance(ctx.get("climate_execution"), dict) else {}
        execution_mode = str(exec_meta.get("execution_mode") or "")
        context_scope = str(exec_meta.get("context_scope") or "")
        if "repository_id" in exec_meta:
            scoped_repo = str(exec_meta.get("repository_id") or "")
        else:
            scoped_repo = str(repository_id or "")
            if not scoped_repo:
                scoped_repo = str(((run.get("repository_ids") or [""])[0]) or "")
        provider_id = str(run.get("agent_id") or exec_meta.get("provider") or "")
        provider_label = provider_display_label(
            provider_id, str(run.get("agent_label") or exec_meta.get("provider_label") or "")
        )
        model = str(exec_meta.get("model") or run.get("model") or "")
        repo_name = str(exec_meta.get("repository_name") or "")
        attached = normalize_path_list(exec_meta.get("attached_files"))
        retrieved = normalize_path_list(exec_meta.get("retrieved_files"))
        persisted_inspected = normalize_path_list(exec_meta.get("inspected_files"))
        activity = summarize_tool_activity(
            run.get("tool_activity"),
            str(run.get("logs") or run.get("log") or ""),
        )
        inspected = persisted_inspected or normalize_path_list(list(activity.inspected_paths or []))
        sources = normalize_path_list(list(attached) + list(retrieved) + list(inspected), limit=24)
        return {
            "id": run.get("id"),
            "workspace": workspace,
            "repository_id": scoped_repo,
            "repository_name": repo_name,
            "status": run.get("status"),
            "provider": provider_id,
            "provider_label": provider_label,
            "model": model,
            "surface": str(exec_meta.get("surface") or ""),
            "execution_mode": execution_mode,
            "context_scope": context_scope,
            "attached_files": attached,
            "retrieved_files": retrieved,
            "inspected_files": inspected,
            "sources": sources,
            "assistant_label": assistant_label(execution_mode, provider_label) if execution_mode else "",
            "execution_summary": format_execution_summary(
                execution_mode=execution_mode,
                provider_label=provider_label,
                model=model,
                context_scope=context_scope,
                repository_label=repo_name or scoped_repo,
            ) if execution_mode else "",
            "answer": run.get("answer") or "",
            "error": run.get("error") or "",
            "logs": run.get("logs") or run.get("log") or "",
            "usage": dict(run.get("usage") or {}),
            "tool_activity": list(run.get("tool_activity") or []),
            "cancel_requested": bool(run.get("cancel_requested")),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "conversation_id": run.get("conversation_id") or "",
        }
