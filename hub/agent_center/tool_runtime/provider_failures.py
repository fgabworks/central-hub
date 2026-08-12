"""Classify provider failures and track short-lived Tool Runtime provider health.

Used by Smart/Auto to avoid immediately re-selecting a hard-failed provider and to
continue the same execution on another compatible Tool Runtime API provider when
available. Manual provider/model selection never silently substitutes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable

# Failure categories (stable telemetry vocabulary).
CATEGORY_QUOTA = "quota"
CATEGORY_AUTH = "auth"
CATEGORY_RATE_LIMIT = "rate_limit"
CATEGORY_UNAVAILABLE = "unavailable"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_RUNTIME = "runtime"

HARD_CATEGORIES = frozenset({CATEGORY_QUOTA, CATEGORY_AUTH})
TRANSIENT_CATEGORIES = frozenset({CATEGORY_RATE_LIMIT, CATEGORY_TIMEOUT})

# API adapters that implement the Unified Tool Runtime contract (OpenAIRunner loop).
TOOL_RUNTIME_API_PROVIDERS = ("openai-api", "grok")

_DEFAULT_HEALTH_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class ProviderFailureInfo:
    category: str
    hard: bool
    retryable: bool
    message: str
    code: str
    provider: str = ""
    model: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _blob(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, dict):
            for key in (
                "code",
                "type",
                "error",
                "message",
                "error_code",
                "status",
                "param",
            ):
                if part.get(key) is not None:
                    chunks.append(str(part.get(key)))
            chunks.append(str(part))
        else:
            chunks.append(str(part))
    return " ".join(chunks).strip().lower()


def classify_provider_failure(
    *,
    error: Any = None,
    error_code: str | None = None,
    status: str | None = None,
    http_status: int | None = None,
    provider: str = "",
    model: str = "",
) -> ProviderFailureInfo:
    """Map provider/client errors into a stable failure category."""
    code = str(error_code or "").strip().lower()
    text = _blob(error, error_code, status)
    http = int(http_status) if http_status is not None else None

    # Prefer explicit client codes when present.
    if code in {"auth", "unauthorized"} or http in {401, 403}:
        category = CATEGORY_AUTH
    elif code in {"quota", "insufficient_quota", "billing_hard_limit", "credit_balance_exhausted"}:
        category = CATEGORY_QUOTA
    elif any(
        token in text
        for token in (
            "insufficient_quota",
            "credit_balance_exhausted",
            "billing_hard_limit",
            "exceeded your current quota",
            "quota exceeded",
            "no credits",
            "payment required",
        )
    ) or http == 402:
        category = CATEGORY_QUOTA
    elif code in {"rate_limit", "ratelimit"} or http == 429:
        # 429 without quota wording is rate limit; quota wording already matched above.
        category = CATEGORY_RATE_LIMIT
    elif code in {"timeout", "timed_out"} or "timed out" in text or "timeout" in text:
        category = CATEGORY_TIMEOUT
    elif code in {
        "unavailable",
        "model_unavailable",
        "model_deprecated",
        "model_invalid",
        "not_configured",
        "network",
    } or any(
        token in text
        for token in ("unavailable", "not configured", "connection refused", "dns")
    ):
        category = CATEGORY_UNAVAILABLE
    elif status and str(status).strip().lower() in {"unavailable", "timeout", "timed_out"}:
        category = (
            CATEGORY_TIMEOUT
            if str(status).strip().lower() in {"timeout", "timed_out"}
            else CATEGORY_UNAVAILABLE
        )
    else:
        category = CATEGORY_RUNTIME

    hard = category in HARD_CATEGORIES
    retryable = category in TRANSIENT_CATEGORIES and not hard
    message = str(error or error_code or status or "provider failure").strip()
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error") or message)
    return ProviderFailureInfo(
        category=category,
        hard=hard,
        retryable=retryable,
        message=message[:2000],
        code=code or category,
        provider=str(provider or "").strip(),
        model=str(model or "").strip(),
    )


def classify_from_run(run: dict[str, Any] | None) -> ProviderFailureInfo | None:
    """Classify a terminal child agent run, or None when the run succeeded."""
    if not isinstance(run, dict):
        return None
    status = str(run.get("status") or "").strip().lower()
    answer = str(run.get("answer") or "").strip()
    failed = status in {
        "failed",
        "cancelled",
        "timed_out",
        "unavailable",
        "timeout",
        "error",
    }
    if not failed and answer:
        return None
    if not failed and not answer and status in {"succeeded", "completed"}:
        # Empty completed answer is still a runtime failure for Tool Runtime.
        failed = True
    if not failed and status in {"queued", "running"}:
        return None
    usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
    prior = usage.get("provider_failure") if isinstance(usage.get("provider_failure"), dict) else {}
    err = run.get("error")
    return classify_provider_failure(
        error=err or prior.get("message"),
        error_code=str(run.get("error_code") or prior.get("code") or ""),
        status=status,
        provider=str(run.get("agent_id") or prior.get("provider") or ""),
        model=str(run.get("model") or prior.get("model") or ""),
    )


def next_action_for_failure(
    info: ProviderFailureInfo,
    *,
    manual_override: bool,
) -> str:
    """Short operator next-action text (manual never implies silent substitute)."""
    if manual_override:
        if info.category == CATEGORY_QUOTA:
            return (
                f"Selected provider {info.provider or '(unknown)'} failed with a quota/billing error. "
                "Add credits or choose another provider/model explicitly — no automatic fallback was used."
            )
        if info.category == CATEGORY_AUTH:
            return (
                f"Selected provider {info.provider or '(unknown)'} failed authentication. "
                "Reconnect or update credentials, then retry — no automatic fallback was used."
            )
        return (
            f"Selected provider {info.provider or '(unknown)'} failed ({info.category}). "
            "Fix the provider issue or choose another agent explicitly — no automatic fallback was used."
        )
    if info.category == CATEGORY_QUOTA:
        return "Provider quota exhausted. Configure credits or select another Tool Runtime provider."
    if info.category == CATEGORY_AUTH:
        return "Provider authentication failed. Reconnect the provider or select another."
    return f"Provider failed ({info.category}). Retry or select another Tool Runtime provider."


def is_tool_runtime_api_provider(provider_id: str | None) -> bool:
    return str(provider_id or "").strip() in TOOL_RUNTIME_API_PROVIDERS


def list_compatible_tool_runtime_providers(
    *,
    configured: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    prefer: str | None = None,
) -> list[str]:
    """Ordered list of Tool Runtime–capable API providers that remain eligible."""
    configured_set = {
        str(p).strip() for p in (configured or TOOL_RUNTIME_API_PROVIDERS) if str(p).strip()
    }
    excluded = {str(p).strip() for p in (exclude or []) if str(p).strip()}
    order = list(TOOL_RUNTIME_API_PROVIDERS)
    pref = str(prefer or "").strip()
    if pref and pref in order:
        order = [pref] + [p for p in order if p != pref]
    out: list[str] = []
    for pid in order:
        if pid not in configured_set:
            continue
        if pid in excluded:
            continue
        out.append(pid)
    return out


class ProviderHealthCache:
    """Process-local short-lived hard-failure marks for Smart selection."""

    def __init__(self, *, ttl_seconds: float = _DEFAULT_HEALTH_TTL_SECONDS) -> None:
        self._ttl = max(5.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def _key(self, provider: str, model: str = "") -> str:
        return f"{str(provider or '').strip()}::{str(model or '').strip()}"

    def mark_failure(
        self,
        provider: str,
        *,
        model: str = "",
        category: str = CATEGORY_RUNTIME,
        message: str = "",
        ttl_seconds: float | None = None,
    ) -> None:
        provider = str(provider or "").strip()
        if not provider:
            return
        ttl = self._ttl if ttl_seconds is None else max(5.0, float(ttl_seconds))
        with self._lock:
            self._entries[self._key(provider, model)] = {
                "provider": provider,
                "model": str(model or "").strip(),
                "category": str(category or CATEGORY_RUNTIME),
                "message": str(message or "")[:500],
                "hard": str(category or "") in HARD_CATEGORIES,
                "expires_at": time.monotonic() + ttl,
            }
            # Also mark provider-wide (any model) for hard failures so Smart avoids it.
            if str(category or "") in HARD_CATEGORIES:
                self._entries[self._key(provider, "")] = {
                    "provider": provider,
                    "model": "",
                    "category": str(category or CATEGORY_RUNTIME),
                    "message": str(message or "")[:500],
                    "hard": True,
                    "expires_at": time.monotonic() + ttl,
                }

    def clear(self, provider: str = "", model: str = "") -> None:
        with self._lock:
            if not provider:
                self._entries.clear()
                return
            self._entries.pop(self._key(provider, model), None)
            if model:
                self._entries.pop(self._key(provider, ""), None)

    def _purge_locked(self) -> None:
        now = time.monotonic()
        dead = [k for k, v in self._entries.items() if float(v.get("expires_at") or 0) <= now]
        for key in dead:
            self._entries.pop(key, None)

    def is_healthy(self, provider: str, model: str = "") -> bool:
        provider = str(provider or "").strip()
        if not provider:
            return False
        with self._lock:
            self._purge_locked()
            if self._key(provider, model) in self._entries:
                return False
            if model and self._key(provider, "") in self._entries:
                entry = self._entries.get(self._key(provider, ""))
                if entry and entry.get("hard"):
                    return False
            return True

    def recent_failure(self, provider: str, model: str = "") -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            row = self._entries.get(self._key(provider, model))
            if row:
                return dict(row)
            if model:
                row = self._entries.get(self._key(provider, ""))
                return dict(row) if row else None
            return None

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            return [dict(v) for v in self._entries.values()]


GLOBAL_PROVIDER_HEALTH = ProviderHealthCache()


def pick_fallback_tool_runtime_provider(
    *,
    failed_provider: str,
    tried: Iterable[str] | None = None,
    configured: Iterable[str] | None = None,
    health: ProviderHealthCache | None = None,
    availability: Any | None = None,
) -> str | None:
    """Next compatible Tool Runtime API provider that is healthy and configured."""
    cache = health or GLOBAL_PROVIDER_HEALTH
    excluded = {str(p).strip() for p in (tried or []) if str(p).strip()}
    failed = str(failed_provider or "").strip()
    if failed:
        excluded.add(failed)
    candidates = list_compatible_tool_runtime_providers(
        configured=configured,
        exclude=excluded,
    )
    for pid in candidates:
        if not cache.is_healthy(pid):
            continue
        if callable(availability):
            ok, _detail = availability(pid)
            if not ok:
                continue
        return pid
    return None


def build_provider_failure_telemetry(
    *,
    selected_provider: str = "",
    selected_model: str = "",
    resolved_provider: str = "",
    resolved_model: str = "",
    failure: ProviderFailureInfo | None = None,
    retry_attempted: bool = False,
    fallback_attempted: bool = False,
    fallback_provider: str = "",
    fallback_model: str = "",
    context_preserved: bool = False,
    manual_override: bool = False,
) -> dict[str, Any]:
    """Telemetry fields for provider failure / Smart continue paths."""
    out: dict[str, Any] = {
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "resolved_provider": resolved_provider or selected_provider,
        "resolved_model": resolved_model or selected_model,
        "retry_attempted": bool(retry_attempted),
        "fallback_attempted": bool(fallback_attempted),
        "fallback_provider": fallback_provider or "",
        "fallback_model": fallback_model or "",
        "context_preserved": bool(context_preserved),
        "manual_override": bool(manual_override),
    }
    if failure is not None:
        out["failure_category"] = failure.category
        out["failed_provider"] = failure.provider or resolved_provider or selected_provider
        out["failed_model"] = failure.model or resolved_model or selected_model
        out["failure_hard"] = failure.hard
        out["failure_retryable"] = failure.retryable
        out["failure_code"] = failure.code
        out["failure_message"] = failure.message[:500]
    return out
