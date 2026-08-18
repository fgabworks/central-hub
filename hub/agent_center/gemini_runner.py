"""Cancellable background runner for read-only Gemini chat."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from hub.agent_center.gemini_client import (
    GeminiClient,
    GeminiClientError,
    response_text,
    response_usage,
)
from hub.agent_center.gemini_settings import GeminiSettings
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore


AuditFn = Callable[..., None]

AIRIX_SYSTEM_INSTRUCTION = (
    "You are AiriX, CLIMATE's permanent assistant identity. Gemini is "
    "the selected provider, not the assistant identity. This v1 session "
    "is read-only: do not edit files, apply patches, execute commands, "
    "or claim actions were performed. Use only the prompt and bounded "
    "repository context supplied by CLIMATE, and clearly separate "
    "verified evidence from inference."
)

DIRECT_SYSTEM_INSTRUCTION = (
    "You are chatting in CLIMATE Direct mode. Gemini is the selected model. "
    "Answer the user's question normally using general knowledge and any "
    "attached user-supplied context. This session is read-only: do not edit "
    "files, apply patches, execute commands, or claim actions were performed."
)


def gemini_system_instruction(*, direct_provider_chat: bool = False) -> str:
    return DIRECT_SYSTEM_INSTRUCTION if direct_provider_chat else AIRIX_SYSTEM_INSTRUCTION


class GeminiRunner:
    def __init__(
        self,
        store: AgentCenterStore,
        *,
        settings: GeminiSettings,
        client: GeminiClient | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.client = client or GeminiClient(settings)
        self.audit = audit
        self._threads: dict[str, threading.Thread] = {}
        self._streams: dict[str, Any] = {}
        self._lock = threading.Lock()

    def reload_runtime(
        self,
        settings: GeminiSettings,
        client: GeminiClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or GeminiClient(settings)

    def start(
        self,
        *,
        run_id: str,
        model: str,
        packed_prompt: str,
        timeout_seconds: float | None = None,
        conversation_id: str = "",
        agent_id: str = "gemini",
        direct_provider_chat: bool = False,
        **_: Any,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "run_id": run_id,
                "model": model,
                "packed_prompt": packed_prompt,
                "timeout_seconds": (
                    timeout_seconds or self.settings.timeout_seconds
                ),
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "direct_provider_chat": bool(direct_provider_chat),
            },
            daemon=True,
            name=f"gemini-run-{run_id[:8]}",
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def cancel(self, run_id: str) -> dict[str, Any] | None:
        updated = self.store.request_cancel(run_id)
        with self._lock:
            stream = self._streams.get(run_id)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        return updated

    def _run(
        self,
        *,
        run_id: str,
        model: str,
        packed_prompt: str,
        timeout_seconds: float,
        conversation_id: str,
        agent_id: str,
        direct_provider_chat: bool = False,
    ) -> None:
        started = datetime.now(timezone.utc).isoformat()
        self.store.update_run(
            run_id, status="running", started_at=started, model=model
        )
        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        try:
            contents = self._conversation_contents(
                run_id, conversation_id, agent_id, model
            )
            history_reused = bool(contents)
            contents.append(
                {"role": "user", "parts": [{"text": packed_prompt}]}
            )
            system = gemini_system_instruction(
                direct_provider_chat=direct_provider_chat
            )
            for event in self.client.stream_generate_content(
                model=model,
                contents=contents,
                system_instruction=system,
                timeout=timeout_seconds,
                should_cancel=lambda: self._cancelled(run_id),
                on_response=lambda response: self._set_stream(run_id, response),
            ):
                if self._cancelled(run_id):
                    self._finish_cancelled(run_id, answer_parts, usage)
                    return
                chunk = response_text(event)
                if chunk:
                    answer_parts.append(chunk)
                    self.store.append_log(run_id, chunk)
                event_usage = response_usage(event)
                if event_usage:
                    usage.update(event_usage)
            answer = redact_text("".join(answer_parts))
            if not answer.strip():
                self._fail(
                    run_id,
                    answer_parts,
                    usage,
                    "Gemini completed without a text answer",
                    code="empty_answer",
                )
                return
            self.store.update_run(
                run_id,
                status="completed",
                answer=answer,
                finished_at=datetime.now(timezone.utc).isoformat(),
                usage={
                    **usage,
                    "provider": "gemini",
                    "model": model,
                    "session_reused": history_reused,
                },
            )
            if self.audit:
                self.audit(
                    action="AGENT_RUN_COMPLETED",
                    detail={
                        "run_id": run_id,
                        "provider": "gemini",
                        "model": model,
                        "usage": usage,
                    },
                )
        except GeminiClientError as exc:
            self._fail(
                run_id,
                answer_parts,
                usage,
                str(exc),
                code=exc.code,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(
                run_id,
                answer_parts,
                usage,
                redact_text(str(exc), limit=500),
                code="error",
            )
        finally:
            with self._lock:
                self._threads.pop(run_id, None)
                self._streams.pop(run_id, None)

    def _set_stream(self, run_id: str, response: Any) -> None:
        with self._lock:
            self._streams[run_id] = response

    def _conversation_contents(
        self,
        run_id: str,
        conversation_id: str,
        agent_id: str,
        model: str,
    ) -> list[dict[str, Any]]:
        current = self.store.get_run(run_id) or {}
        profile_id = str(current.get("profile_id") or "okarun")
        rows = (
            self.store.list_conversation_runs(
                conversation_id, profile_id=profile_id, limit=12
            )
            if conversation_id
            else []
        )
        contents: list[dict[str, Any]] = []
        char_budget = 80_000
        for row in rows:
            if row.get("id") == run_id or row.get("status") != "completed":
                continue
            if row.get("agent_id") != agent_id or row.get("model") != model:
                continue
            prompt = str(row.get("prompt") or "").strip()
            answer = str(row.get("answer") or "").strip()
            if not prompt or not answer:
                continue
            pair_chars = len(prompt) + len(answer)
            if pair_chars > char_budget:
                continue
            contents.extend(
                [
                    {"role": "user", "parts": [{"text": prompt}]},
                    {"role": "model", "parts": [{"text": answer}]},
                ]
            )
            char_budget -= pair_chars
        return contents

    def _cancelled(self, run_id: str) -> bool:
        run = self.store.get_run(run_id)
        return bool(run and run.get("cancel_requested"))

    def _finish_cancelled(
        self,
        run_id: str,
        answer_parts: list[str],
        usage: dict[str, Any],
    ) -> None:
        self.store.update_run(
            run_id,
            status="cancelled",
            answer=redact_text("".join(answer_parts)),
            finished_at=datetime.now(timezone.utc).isoformat(),
            usage=usage,
        )

    def _fail(
        self,
        run_id: str,
        answer_parts: list[str],
        usage: dict[str, Any],
        error: str,
        *,
        code: str,
    ) -> None:
        self.store.append_log(run_id, f"\n[error:{code}] {error}\n")
        self.store.update_run(
            run_id,
            status="failed",
            error=error,
            answer=redact_text("".join(answer_parts)),
            finished_at=datetime.now(timezone.utc).isoformat(),
            usage=usage,
        )
        if self.audit:
            self.audit(
                action="AGENT_RUN_FAILED",
                detail={
                    "run_id": run_id,
                    "provider": "gemini",
                    "code": code,
                    "error": error[:300],
                },
            )
