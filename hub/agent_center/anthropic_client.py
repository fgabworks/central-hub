"""Native Anthropic REST client for model discovery and SSE message streaming."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator

import requests

from hub.agent_center.anthropic_settings import AnthropicSettings
from hub.agent_center.redact import redact_text


class AnthropicClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "anthropic_error",
        status: int | None = None,
    ) -> None:
        super().__init__(redact_text(message, limit=2000))
        self.code = code
        self.status = status


class AnthropicClient:
    def __init__(
        self,
        settings: AnthropicSettings,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._cache_lock = threading.Lock()
        self._cached_models: list[dict[str, Any]] | None = None
        self._cached_at = 0.0

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise AnthropicClientError(
                "ANTHROPIC_API_KEY is not configured",
                code="not_configured",
            )
        return {
            "x-api-key": self.settings.api_key,
            "anthropic-version": self.settings.api_version,
            "Content-Type": "application/json",
        }

    def list_models(self, *, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        if not self.settings.is_configured:
            return [], "none"
        now = time.monotonic()
        ttl = self.settings.model_cache_ttl_seconds
        with self._cache_lock:
            if (
                not force_refresh
                and self._cached_models is not None
                and ttl > 0
                and now - self._cached_at < ttl
            ):
                return [dict(row) for row in self._cached_models], "cache:discovered"

        rows: list[dict[str, Any]] = []
        after_id = ""
        while True:
            params: dict[str, Any] = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            try:
                response = self._session.get(
                    f"{self.settings.base_url}/v1/models",
                    headers=self._headers(),
                    params=params,
                    timeout=min(30.0, self.settings.timeout_seconds),
                )
            except requests.Timeout as exc:
                raise AnthropicClientError("Anthropic model list timed out", code="timeout") from exc
            except requests.RequestException as exc:
                raise AnthropicClientError(
                    f"Anthropic model list failed: {exc}", code="network"
                ) from exc
            payload = self._checked_json(response, operation="models")
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id or not _is_chat_model(model_id, item):
                    continue
                rows.append(
                    {
                        "id": model_id,
                        "display_name": str(item.get("display_name") or model_id),
                        "availability": "available",
                    }
                )
            if not payload.get("has_more"):
                break
            after_id = str(payload.get("last_id") or "").strip()
            if not after_id:
                break

        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            unique[row["id"]] = row
        ordered = [unique[key] for key in sorted(unique)]
        with self._cache_lock:
            self._cached_models = [dict(row) for row in ordered]
            self._cached_at = time.monotonic()
        return [dict(row) for row in ordered], "discovered" if ordered else "discovered_empty"

    def stream_messages(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str = "",
        timeout: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_response: Callable[[Any], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not model.strip():
            raise AnthropicClientError("Model is required", code="model_required")
        body: dict[str, Any] = {
            "model": model.strip(),
            "max_tokens": self.settings.max_output_tokens,
            "stream": True,
            "messages": messages,
        }
        if system.strip():
            body["system"] = system.strip()
        try:
            response = self._session.post(
                f"{self.settings.base_url}/v1/messages",
                headers=self._headers(),
                json=body,
                stream=True,
                timeout=timeout or self.settings.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise AnthropicClientError("Anthropic request timed out", code="timeout") from exc
        except requests.RequestException as exc:
            raise AnthropicClientError(f"Anthropic request failed: {exc}", code="network") from exc
        if response.status_code >= 400:
            self._raise_http(response, operation="messages")
        if on_response:
            on_response(response)
        try:
            for raw in response.iter_lines(decode_unicode=True):
                if should_cancel and should_cancel():
                    break
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            response.close()

    def _checked_json(self, response: requests.Response, *, operation: str) -> dict[str, Any]:
        if response.status_code >= 400:
            self._raise_http(response, operation=operation)
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise AnthropicClientError(
                f"Anthropic {operation} returned invalid JSON",
                code="invalid_response",
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _raise_http(self, response: requests.Response, *, operation: str) -> None:
        status = response.status_code
        text = redact_text(response.text[:500])
        lowered = text.lower()
        if status in {401, 403}:
            code = "auth"
        elif status == 429:
            code = "quota" if "quota" in lowered else "rate_limit"
        elif status == 404:
            code = "model_unavailable"
        elif status == 400:
            if "model" in lowered:
                code = "model_unavailable"
            else:
                code = "invalid_request"
        else:
            code = "http_error"
        response.close()
        raise AnthropicClientError(
            f"Anthropic {operation} HTTP {status}: {text}",
            code=code,
            status=status,
        )


def stream_text(payload: dict[str, Any]) -> str:
    if payload.get("type") != "content_block_delta":
        return ""
    delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
    if delta.get("type") == "text_delta":
        return str(delta.get("text") or "")
    return ""


def stream_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if not raw and isinstance(message.get("usage"), dict):
        raw = message["usage"]
    if payload.get("type") == "message_start" and isinstance(message.get("usage"), dict):
        raw = message["usage"]
    if "input_tokens" in raw and raw["input_tokens"] is not None:
        usage["input_tokens"] = raw["input_tokens"]
    if "output_tokens" in raw and raw["output_tokens"] is not None:
        usage["output_tokens"] = raw["output_tokens"]
    if usage.get("input_tokens") is not None or usage.get("output_tokens") is not None:
        usage["total_tokens"] = int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0
        )
        usage["usage_source"] = "exact"
    return usage


def _is_chat_model(model_id: str, row: dict[str, Any]) -> bool:
    kind = str(row.get("type") or "model").strip().lower()
    if kind and kind not in {"model", "claude"}:
        return False
    mid = model_id.lower()
    blocked = ("embedding", "rerank", "classifier")
    return not any(token in mid for token in blocked)
