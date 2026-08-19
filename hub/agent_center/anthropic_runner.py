"""Cancellable background runner for read-only Anthropic chat."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from hub.agent_center.api_chat import api_chat_system_instruction
from hub.agent_center.anthropic_client import (
    AnthropicClient,
    AnthropicClientError,
    stream_text,
    stream_usage,
)
from hub.agent_center.anthropic_settings import AnthropicSettings
from hub.agent_center.conversation_history import prior_completed_turns
from hub.agent_center.redact import redact_text
from hub.agent_center.store import AgentCenterStore


AuditFn = Callable[..., None]


class AnthropicRunner:
    def __init__(
        self,
        store: AgentCenterStore,
        *,
        settings: AnthropicSettings,
        client: AnthropicClient | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.client = client or AnthropicClient(settings)
        self.audit = audit
        self._threads: dict[str, threading.Thread] = {}
        self._streams: dict[str, Any] = {}
        self._lock = threading.Lock()

    def reload_runtime(
        self,
        settings: AnthropicSettings,
        client: AnthropicClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or AnthropicClient(settings)

    def start(
        self,
        *,
        run_id: str,
        model: str,
        packed_prompt: str,
        timeout_seconds: float | None = None,
        conversation_id: str = "",
        agent_id: str = "anthropic-api",
        direct_provider_chat: bool = False,
        **_: Any,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            kwargs={
                "run_id": run_id,
                "model": model,
                "packed_prompt": packed_prompt,
                "timeout_seconds": timeout_seconds or self.settings.timeout_seconds,
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "direct_provider_chat": bool(direct_provider_chat),
            },
            daemon=True,
            name=f"anthropic-run-{run_id[:8]}",
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
        self.store.update_run(run_id, status="running", started_at=started, model=model)
        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        try:
            messages = self._conversation_messages(run_id, conversation_id, agent_id, model)
            history_reused = bool(messages)
            messages.append({"role": "user", "content": packed_prompt})
            system = api_chat_system_instruction(direct_provider_chat=direct_provider_chat)
            for event in self.client.stream_messages(
                model=model,
                messages=messages,
                system=system,
                timeout=timeout_seconds,
                should_cancel=lambda: self._cancelled(run_id),
                on_response=lambda response: self._set_stream(run_id, response),
            ):
                if event.get("type") == "error":
                    err = event.get("error") or event
                    raise AnthropicClientError(str(err), code="stream_error")
                if self._cancelled(run_id):
                    self._finish_cancelled(run_id, answer_parts, usage)
                    return
                chunk = stream_text(event)
                if chunk:
                    answer_parts.append(chunk)
                    self.store.append_log(run_id, chunk)
                event_usage = stream_usage(event)
                if event_usage:
                    usage.update(event_usage)
            answer = redact_text("".join(answer_parts))
            if not answer.strip():
                self._fail(
                    run_id,
                    answer_parts,
                    usage,
                    "Anthropic completed without a text answer",
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
                    "provider": agent_id,
                    "model": model,
                    "session_reused": history_reused,
                },
            )
            if self.audit:
                self.audit(
                    action="AGENT_RUN_COMPLETED",
                    detail={
                        "run_id": run_id,
                        "provider": agent_id,
                        "model": model,
                        "usage": usage,
                    },
                )
        except AnthropicClientError as exc:
            self._fail(run_id, answer_parts, usage, str(exc), code=exc.code)
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

    def _conversation_messages(
        self,
        run_id: str,
        conversation_id: str,
        agent_id: str,
        model: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for prompt, answer in prior_completed_turns(
            self.store,
            run_id=run_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            model=model,
        ):
            messages.append({"role": "user", "content": prompt})
            messages.append({"role": "assistant", "content": answer})
        return messages

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
                    "provider": "anthropic-api",
                    "code": code,
                    "error": error[:300],
                },
            )
