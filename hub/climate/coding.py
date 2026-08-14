"""Provider-neutral coding adapter backed by the existing Agent Center runtime."""

from __future__ import annotations

import json
import re
from typing import Any

from hub.agent_center.service import AgentCenterError, AgentCenterService


CODING_PROVIDERS = ("codex", "claude-code", "cursor-agent")

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
        # Prefer task_mode already applied in the context packet when present.
        if "CLIMATE context packet (EDIT)" in prompt or "CLIMATE preflight context packet (EDIT)" in prompt:
            mode = "edit"
        elif "CLIMATE context packet (ASK)" in prompt or "CLIMATE preflight context packet (ASK)" in prompt:
            mode = "ask"
        repository_investigation = bool(repository_investigation and mode == "ask")
        files = list(dict.fromkeys(
            str(path).replace("\\", "/").lstrip("/")
            for path in ([current_file] + list(selected_files or []))
            if str(path).strip()
        ))
        if mode == "edit":
            context_note = [
                "CLIMATE coding request (EDIT mode).",
                "Stay read-only at runtime; propose file replacements only.",
                "Return proposed file replacements in a fenced JSON object using this schema: ",
                '{"edits":[{"path":"relative/path","content":"complete replacement content"}]}.',
                "Do not apply edits or execute commands.",
                "Use only the bounded context packet; do not assume full chat history.",
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
                    "are allowed. Do not modify files or repository state and do not run destructive commands."
                    if repository_investigation
                    else "Do not apply edits or execute commands."
                ),
            ]
        if selection:
            context_note.append("Current editor selection:\n" + selection[:20_000])
        if handoff:
            context_note.append("Cross-provider compact handoff — do not replay full CLIMATE history.")
        if reuse_session and not handoff:
            context_note.append("Same-provider session: reuse prior provider context when supported.")
        has_packet = (
            "CLIMATE context packet" in prompt
            or "CLIMATE preflight context packet" in prompt
        )
        if has_packet:
            packed_prompt = "\n\n".join(context_note + [prompt.strip()])
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
            "tool_ids": [],
            "files": (
                {}
                if repository_investigation
                else ({repository_id: files} if repository_id and workspace == "work" else {})
            ),
            "repository_ids": [repository_id] if repository_id and workspace == "work" else [],
            "active_repository_id": repository_id if workspace == "work" else None,
            "selected_repository_id": repository_id if workspace == "work" else None,
            "tool_runtime_lean_context": True if has_packet else (not include_repo_context),
            "bounded_evidence_only": bool(has_packet),
            "reuse_provider_session": bool(reuse_session) and not handoff,
            "repository_investigation": bool(repository_investigation),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if isinstance(evidence_packet, dict):
            payload["evidence_packet"] = evidence_packet
        try:
            run = self.agent_center.start_run(payload)
        except AgentCenterError as exc:
            raise ClimateCodingError(str(exc), code=exc.code) from exc
        public = self._public_run(run, workspace=workspace, repository_id=repository_id)
        public["task_mode"] = mode
        public["provider_invoked"] = True
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
    def humanize_answer(answer: str, *, task_mode: str = "ask") -> tuple[str, str]:
        """Return (display_text, raw_payload_for_diagnostics).

        Never leave provider edit-protocol JSON as the normal chat body.
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
        # Unescape common dumped string literals when the whole answer is one JSON object.
        if not stripped and edits:
            parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
            if task_mode == "ask" and parts:
                return "\n\n".join(parts), raw
            return ("Proposed changes are ready for review." if task_mode == "edit" else ""), raw
        if stripped and '"edits"' in stripped and stripped.lstrip().startswith("{"):
            try:
                payload = json.loads(stripped)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("edits"), list):
                edits = ClimateCodingAdapter.proposed_edits(stripped) or edits
                parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
                if task_mode == "ask" and parts:
                    return "\n\n".join(parts), raw
                return ("Proposed changes are ready for review." if task_mode == "edit" else ""), raw
        display = stripped or raw.strip()
        if edits and display == raw.strip():
            # Raw answer still looks like protocol; prefer extracted content for ask.
            parts = [item["content"].strip() for item in edits if item.get("content", "").strip()]
            if task_mode == "ask" and parts:
                return "\n\n".join(parts), raw
            if task_mode == "edit":
                return "Proposed changes are ready for review.", raw
        if edits and display != raw.strip():
            return display, raw
        return display, ""

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
            "account_label": row.get("account_label") or "",
            "capabilities": dict(row.get("capabilities") or {}),
        }

    @staticmethod
    def _public_run(run: dict[str, Any], *, workspace: str, repository_id: str = "") -> dict[str, Any]:
        return {
            "id": run.get("id"),
            "workspace": workspace,
            "repository_id": repository_id or ((run.get("repository_ids") or [""])[0]),
            "status": run.get("status"),
            "provider": run.get("agent_id"),
            "model": run.get("model"),
            "answer": run.get("answer") or "",
            "error": run.get("error") or "",
            "logs": run.get("logs") or run.get("log") or "",
            "usage": dict(run.get("usage") or {}),
            "tool_activity": list(run.get("tool_activity") or []),
            "cancel_requested": bool(run.get("cancel_requested")),
            "created_at": run.get("created_at"),
            "finished_at": run.get("finished_at"),
            "conversation_id": run.get("conversation_id") or "",
        }
