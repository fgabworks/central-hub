"""Native Gemini REST client for model discovery and SSE text streaming."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterator
from urllib.parse import quote

import requests

from hub.agent_center.gemini_settings import GeminiSettings
from hub.agent_center.redact import redact_text


class GeminiClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "gemini_error",
        status: int | None = None,
    ) -> None:
        super().__init__(redact_text(message, limit=2000))
        self.code = code
        self.status = status


class GeminiClient:
    def __init__(
        self,
        settings: GeminiSettings,
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
            raise GeminiClientError(
                "GEMINI_API_KEY or GOOGLE_API_KEY is not configured",
                code="not_configured",
            )
        return {
            "x-goog-api-key": self.settings.api_key,
            "Content-Type": "application/json",
        }

    def list_models(
        self, *, force_refresh: bool = False
    ) -> tuple[list[dict[str, Any]], str]:
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
        page_token = ""
        while True:
            params: dict[str, Any] = {"pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self._session.get(
                    f"{self.settings.base_url}/models",
                    headers=self._headers(),
                    params=params,
                    timeout=min(30.0, self.settings.timeout_seconds),
                )
            except requests.Timeout as exc:
                raise GeminiClientError(
                    "Gemini model list timed out", code="timeout"
                ) from exc
            except requests.RequestException as exc:
                raise GeminiClientError(
                    f"Gemini model list failed: {exc}", code="network"
                ) from exc
            payload = self._checked_json(response, operation="model list")
            for item in payload.get("models") or []:
                if not isinstance(item, dict):
                    continue
                methods = set(
                    item.get("supportedGenerationMethods")
                    or item.get("supportedActions")
                    or []
                )
                if methods and "generateContent" not in methods:
                    continue
                model_id = str(
                    item.get("baseModelId") or item.get("name") or ""
                ).removeprefix("models/").strip()
                if not model_id or not model_id.lower().startswith("gemini"):
                    continue
                rows.append(
                    {
                        "id": model_id,
                        "display_name": str(item.get("displayName") or model_id),
                        "description": str(item.get("description") or ""),
                        "input_token_limit": item.get("inputTokenLimit"),
                        "output_token_limit": item.get("outputTokenLimit"),
                        "availability": "available",
                    }
                )
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                break

        deduped = {str(row["id"]): row for row in rows}
        discovered = [deduped[key] for key in sorted(deduped)]
        with self._cache_lock:
            self._cached_models = [dict(row) for row in discovered]
            self._cached_at = time.monotonic()
        return discovered, "discovered"

    def stream_generate_content(
        self,
        *,
        model: str,
        contents: list[dict[str, Any]],
        system_instruction: str,
        timeout: float | None = None,
    ) -> Iterator[dict[str, Any]]:
        safe_model = quote(model.removeprefix("models/"), safe="-._")
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self.settings.max_output_tokens
            },
        }
        if system_instruction.strip():
            body["systemInstruction"] = {
                "parts": [{"text": system_instruction.strip()}]
            }
        try:
            response = self._session.post(
                f"{self.settings.base_url}/models/{safe_model}:streamGenerateContent?alt=sse",
                headers=self._headers(),
                json=body,
                stream=True,
                timeout=timeout or self.settings.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise GeminiClientError(
                "Gemini request timed out", code="timeout"
            ) from exc
        except requests.RequestException as exc:
            raise GeminiClientError(
                f"Gemini request failed: {exc}", code="network"
            ) from exc
        if response.status_code >= 400:
            self._raise_http(response, operation="generation")
        try:
            for raw in response.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            response.close()

    def _checked_json(
        self, response: requests.Response, *, operation: str
    ) -> dict[str, Any]:
        if response.status_code >= 400:
            self._raise_http(response, operation=operation)
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise GeminiClientError(
                f"Gemini {operation} returned invalid JSON",
                code="invalid_response",
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def _raise_http(self, response: requests.Response, *, operation: str) -> None:
        status = response.status_code
        text = redact_text(response.text[:500])
        if status in {401, 403}:
            code = "auth"
        elif status == 429:
            code = "quota" if "quota" in text.lower() else "rate_limit"
        elif status == 404:
            code = "model_unavailable"
        elif status == 400:
            code = "invalid_request"
        else:
            code = "http_error"
        response.close()
        raise GeminiClientError(
            f"Gemini {operation} HTTP {status}: {text}",
            code=code,
            status=status,
        )


def response_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        for part in (content or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
    return "".join(parts)


def response_usage(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("usageMetadata") or {}
    if not isinstance(raw, dict):
        return {}
    usage = {
        "input_tokens": raw.get("promptTokenCount"),
        "output_tokens": raw.get("candidatesTokenCount"),
        "total_tokens": raw.get("totalTokenCount"),
        "cached_tokens": raw.get("cachedContentTokenCount"),
        "usage_source": "exact",
    }
    return {key: value for key, value in usage.items() if value is not None}
