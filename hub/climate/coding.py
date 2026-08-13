"""Provider-neutral coding adapter backed by the existing Agent Center runtime."""

from __future__ import annotations

import json
import re
from typing import Any

from hub.agent_center.service import AgentCenterError, AgentCenterService


CODING_PROVIDERS = ("codex", "claude-code", "cursor-agent")


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

        files = list(dict.fromkeys(
            str(path).replace("\\", "/").lstrip("/")
            for path in ([current_file] + list(selected_files or []))
            if str(path).strip()
        ))
        context_note = [
            "CLIMATE coding request. Stay read-only and propose edits only.",
            "Return proposed file replacements in a fenced JSON object using this schema: ",
            '{"edits":[{"path":"relative/path","content":"complete replacement content"}]}.',
            "Do not apply edits or execute commands.",
        ]
        if selection:
            context_note.append("Current editor selection:\n" + selection[:20_000])
        context_note.append("Repository context: " + ("enabled" if include_repo_context else "selected files only"))
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
            "files": {repository_id: files} if repository_id and workspace == "work" else {},
            "repository_ids": [repository_id] if repository_id and workspace == "work" else [],
            "active_repository_id": repository_id if workspace == "work" else None,
            "selected_repository_id": repository_id if workspace == "work" else None,
            "tool_runtime_lean_context": not include_repo_context,
        }
        try:
            run = self.agent_center.start_run(payload)
        except AgentCenterError as exc:
            raise ClimateCodingError(str(exc), code=exc.code) from exc
        return self._public_run(run, workspace=workspace, repository_id=repository_id)

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
        candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", answer or "", re.S | re.I)
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
            "cancel_requested": bool(run.get("cancel_requested")),
            "created_at": run.get("created_at"),
            "finished_at": run.get("finished_at"),
        }
