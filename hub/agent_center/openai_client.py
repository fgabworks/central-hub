"""OpenAI HTTP client — Responses API + model list (never logs API keys)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator

import requests

from hub.agent_center.openai_settings import OpenAISettings
from hub.agent_center.redact import redact_text


class OpenAIClientError(Exception):
    def __init__(self, message: str, *, code: str = "openai_error", status: int | None = None) -> None:
        super().__init__(redact_text(message, limit=2000))
        self.code = code
        self.status = status


class OpenAIClient:
    def __init__(self, settings: OpenAISettings, *, session: requests.Session | None = None) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._cache_lock = threading.Lock()
        self._cached_ids: list[str] | None = None
        self._cached_at: float = 0.0
        self._cached_source: str = "none"

    def clear_model_cache(self) -> None:
        with self._cache_lock:
            self._cached_ids = None
            self._cached_at = 0.0
            self._cached_source = "none"

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise OpenAIClientError("OPENAI_API_KEY is not configured", code="not_configured")
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    def list_model_ids(self, *, force_refresh: bool = False) -> tuple[list[str], str]:
        """Return model ids visible to this API key (cached)."""
        if not self.settings.is_configured:
            return [], "none"

        ttl = max(0.0, float(self.settings.model_cache_ttl_seconds))
        now = time.monotonic()
        with self._cache_lock:
            if (
                not force_refresh
                and self._cached_ids is not None
                and ttl > 0
                and (now - self._cached_at) < ttl
            ):
                return list(self._cached_ids), f"cache:{self._cached_source}"

        ids, source = self._fetch_model_ids()
        with self._cache_lock:
            self._cached_ids = list(ids)
            self._cached_at = time.monotonic()
            self._cached_source = source
        return list(ids), source

    def _fetch_model_ids(self) -> tuple[list[str], str]:
        try:
            resp = self._session.get(
                f"{self.settings.base_url}/models",
                headers=self._headers(),
                timeout=min(30.0, self.settings.timeout_seconds),
            )
        except requests.Timeout as exc:
            raise OpenAIClientError("OpenAI model list timed out", code="timeout") from exc
        except requests.RequestException as exc:
            raise OpenAIClientError(f"OpenAI model list failed: {exc}", code="network") from exc

        if resp.status_code == 401:
            raise OpenAIClientError("OpenAI authentication failed", code="auth", status=401)
        if resp.status_code == 403:
            raise OpenAIClientError("OpenAI authorization failed", code="unauthorized", status=403)
        if resp.status_code == 429:
            raise OpenAIClientError("OpenAI rate limit while listing models", code="rate_limit", status=429)
        if resp.status_code >= 400:
            raise OpenAIClientError(
                f"OpenAI models HTTP {resp.status_code}: {resp.text[:300]}",
                code="http_error",
                status=resp.status_code,
            )

        payload = resp.json() if resp.content else {}
        rows = payload.get("data") or []
        ids: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "").strip()
            if mid and _is_text_model_candidate(mid):
                ids.append(mid)
        ids = sorted(set(ids))
        if ids:
            return ids, "discovered"
        return [], "discovered_empty"

    def create_response_stream(
        self,
        body: dict[str, Any],
        *,
        timeout: float | None = None,
        should_cancel: Callable[..., bool] | None = None,
        on_response: Callable[[Any], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield parsed SSE event payloads from POST /responses?stream=true."""
        payload = dict(body)
        payload["stream"] = True
        try:
            resp = self._session.post(
                f"{self.settings.base_url}/responses",
                headers=self._headers(),
                json=payload,
                stream=True,
                timeout=timeout or self.settings.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise OpenAIClientError("OpenAI Responses request timed out", code="timeout") from exc
        except requests.RequestException as exc:
            raise OpenAIClientError(f"OpenAI Responses request failed: {exc}", code="network") from exc

        if resp.status_code == 401:
            resp.close()
            raise OpenAIClientError("OpenAI authentication failed", code="auth", status=401)
        if resp.status_code == 403:
            resp.close()
            raise OpenAIClientError(
                "OpenAI refused this model or request (unauthorized)",
                code="unauthorized",
                status=403,
            )
        if resp.status_code == 429:
            text = redact_text(resp.text[:500])
            resp.close()
            lowered = text.lower()
            # Quota/billing exhaustion often arrives as HTTP 429 with quota wording.
            if any(
                token in lowered
                for token in (
                    "insufficient_quota",
                    "credit_balance_exhausted",
                    "billing_hard_limit",
                    "exceeded your current quota",
                    "quota",
                )
            ):
                raise OpenAIClientError(
                    f"OpenAI quota exhausted: {text}" if text else "OpenAI quota exhausted",
                    code="quota",
                    status=429,
                )
            raise OpenAIClientError(
                f"OpenAI rate limit: {text}" if text else "OpenAI rate limit",
                code="rate_limit",
                status=429,
            )
        if resp.status_code == 402:
            text = redact_text(resp.text[:500])
            resp.close()
            raise OpenAIClientError(
                f"OpenAI payment required: {text}" if text else "OpenAI payment required",
                code="quota",
                status=402,
            )
        if resp.status_code == 404:
            resp.close()
            raise OpenAIClientError(
                "OpenAI model unavailable or deprecated (not found)",
                code="model_unavailable",
                status=404,
            )
        if resp.status_code == 400:
            text = redact_text(resp.text[:500])
            resp.close()
            lowered = text.lower()
            if "deprecated" in lowered:
                code = "model_deprecated"
            elif "invalid" in lowered and "model" in lowered:
                code = "model_invalid"
            elif "model" in lowered:
                code = "model_unavailable"
            elif any(
                token in lowered
                for token in ("insufficient_quota", "credit_balance_exhausted", "quota")
            ):
                code = "quota"
            else:
                code = "invalid_request"
            raise OpenAIClientError(f"OpenAI request rejected: {text}", code=code, status=400)
        if resp.status_code >= 400:
            text = redact_text(resp.text[:500])
            resp.close()
            lowered = text.lower()
            code = "http_error"
            if any(
                token in lowered
                for token in ("insufficient_quota", "credit_balance_exhausted", "quota")
            ):
                code = "quota"
            raise OpenAIClientError(
                f"OpenAI Responses HTTP {resp.status_code}: {text}",
                code=code,
                status=resp.status_code,
            )

        if on_response:
            on_response(resp)
        try:
            for raw in resp.iter_lines(decode_unicode=True):
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
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
        finally:
            resp.close()


def _is_text_model_candidate(model_id: str) -> bool:
    """Exclude non-chat / legacy completion / non-text modalities.

    Does not hard-code a preferred chat model — only filters families that are
    incompatible with the Responses-style adapters used by Agent Center.
    """
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    # Legacy completion engines (e.g. babbage-002) sort first alphabetically and
    # must never become a silent default when the UI sends no/empty model.
    for legacy in ("babbage", "davinci", "curie", "ada"):
        if mid == legacy or mid.startswith(legacy + "-"):
            return False
    if mid.startswith("text-") or mid.startswith("code-"):
        return False
    blocked = (
        "embedding",
        "whisper",
        "tts",
        "audio",
        "realtime",
        "dall-e",
        "moderation",
        "transcribe",
        "image",
        "sora",
    )
    return not any(b in mid for b in blocked)
