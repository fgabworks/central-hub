"""Codex account rate-limit snapshots for CLIMATE (app-server backed).

Never estimates capacity from chat/session tokens. Returns unavailable when
Codex is missing, unauthenticated, or not ChatGPT-backed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from hub.agent_center.adapters.codex_app_server import (
    CodexAppServerError,
    ReusedCodexAppServer,
)
from hub.agent_center.codex_safety import discover_codex_executable

CACHE_TTL_SECONDS = 45.0
REQUEST_TIMEOUT = 10.0
UNAVAILABLE_MESSAGE = "Codex limit unavailable"

_AUTH_MARKERS = (
    "chatgpt authentication required",
    "codex account authentication required",
    "authentication required",
    "not logged in",
    "login required",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_float(value: Any) -> float | None:
    if value is None or value is False:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _plan_type(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("type", "planType", "plan_type", "name"):
            found = _as_str(value.get(key))
            if found:
                return found
        return None
    return _as_str(value)


def normalize_credits(raw: Any) -> dict[str, Any] | None:
    data = _as_dict(raw)
    if not data:
        return None
    has_credits = data.get("hasCredits")
    if has_credits is None:
        has_credits = data.get("has_credits")
    unlimited = data.get("unlimited")
    if has_credits is None and unlimited is None and data.get("balance") is None:
        return None
    balance = data.get("balance")
    return {
        "hasCredits": bool(has_credits) if has_credits is not None else False,
        "unlimited": bool(unlimited) if unlimited is not None else False,
        "balance": None if balance is None else str(balance),
    }


def _normalize_window_bucket(
    *,
    limit_id: str,
    limit_name: str | None,
    window_name: str,
    window: dict[str, Any],
    plan_type: str | None,
    credits: dict[str, Any] | None,
) -> dict[str, Any] | None:
    used = _as_float(window.get("usedPercent"))
    if used is None:
        used = _as_float(window.get("used_percent"))
    if used is None:
        return None
    used = max(0.0, min(100.0, used))
    remaining = max(0.0, min(100.0, 100.0 - used))
    duration = _as_int(window.get("windowDurationMins"))
    if duration is None:
        duration = _as_int(window.get("window_duration_mins"))
    resets_at = _as_int(window.get("resetsAt"))
    if resets_at is None:
        resets_at = _as_int(window.get("resets_at"))
    display_name = limit_name or limit_id
    if window_name and window_name not in {"primary", ""}:
        label = f"{display_name} ({window_name})"
    else:
        label = display_name
    return {
        "limitId": limit_id,
        "limitName": label,
        "window": window_name or "primary",
        "usedPercent": used,
        "remainingPercent": remaining,
        "windowDurationMins": duration,
        "resetsAt": resets_at,
        "planType": plan_type,
        "credits": credits,
    }


def normalize_snapshot(snapshot: Any, *, fallback_id: str = "codex") -> list[dict[str, Any]]:
    data = _as_dict(snapshot)
    if not data:
        return []
    limit_id = _as_str(data.get("limitId")) or _as_str(data.get("limit_id")) or fallback_id
    limit_name = _as_str(data.get("limitName")) or _as_str(data.get("limit_name"))
    plan_type = _plan_type(data.get("planType") if "planType" in data else data.get("plan_type"))
    credits = normalize_credits(data.get("credits"))
    buckets: list[dict[str, Any]] = []
    for window_key, window_name in (
        ("primary", "primary"),
        ("secondary", "secondary"),
    ):
        window = _as_dict(data.get(window_key))
        bucket = _normalize_window_bucket(
            limit_id=limit_id,
            limit_name=limit_name,
            window_name=window_name,
            window=window,
            plan_type=plan_type,
            credits=credits,
        )
        if bucket:
            buckets.append(bucket)
    individual = _as_dict(data.get("individualLimit") or data.get("individual_limit"))
    if individual:
        # Spend-control limits already expose remainingPercent; still derive used.
        remaining = _as_float(individual.get("remainingPercent"))
        if remaining is None:
            remaining = _as_float(individual.get("remaining_percent"))
        used = _as_float(individual.get("usedPercent"))
        if used is None:
            used = _as_float(individual.get("used_percent"))
        if used is None and remaining is not None:
            used = max(0.0, min(100.0, 100.0 - remaining))
        if used is not None:
            used = max(0.0, min(100.0, used))
            remaining = max(0.0, min(100.0, 100.0 - used))
            resets_at = _as_int(individual.get("resetsAt"))
            if resets_at is None:
                resets_at = _as_int(individual.get("resets_at"))
            buckets.append({
                "limitId": limit_id,
                "limitName": f"{limit_name or limit_id} (individual)",
                "window": "individual",
                "usedPercent": used,
                "remainingPercent": remaining,
                "windowDurationMins": None,
                "resetsAt": resets_at,
                "planType": plan_type,
                "credits": credits,
            })
    return buckets


def normalize_rate_limits_response(raw: Any) -> dict[str, Any]:
    """Normalize ``account/rateLimits/read`` (or notification) payload."""
    data = _as_dict(raw)
    by_id = data.get("rateLimitsByLimitId")
    if by_id is None:
        by_id = data.get("rate_limits_by_limit_id")
    buckets: list[dict[str, Any]] = []
    plan_type: str | None = None
    credits: dict[str, Any] | None = None

    if isinstance(by_id, dict) and by_id:
        for key, snapshot in by_id.items():
            limit_key = _as_str(key) or "codex"
            for bucket in normalize_snapshot(snapshot, fallback_id=limit_key):
                buckets.append(bucket)
                plan_type = plan_type or bucket.get("planType")
                if credits is None and bucket.get("credits"):
                    credits = bucket.get("credits")
    else:
        primary = data.get("rateLimits")
        if primary is None:
            primary = data.get("rate_limits")
        # Notification form: { rateLimits: Snapshot }
        if isinstance(primary, dict) and (
            "primary" in primary
            or "secondary" in primary
            or "limitId" in primary
            or "limit_id" in primary
        ):
            for bucket in normalize_snapshot(primary):
                buckets.append(bucket)
                plan_type = plan_type or bucket.get("planType")
                if credits is None and bucket.get("credits"):
                    credits = bucket.get("credits")
        elif isinstance(primary, list):
            for index, snapshot in enumerate(primary):
                for bucket in normalize_snapshot(snapshot, fallback_id=f"limit-{index}"):
                    buckets.append(bucket)
                    plan_type = plan_type or bucket.get("planType")
                    if credits is None and bucket.get("credits"):
                        credits = bucket.get("credits")

    # Prefer primary windows when choosing header remaining %.
    remaining_candidates = [
        float(bucket["remainingPercent"])
        for bucket in buckets
        if bucket.get("window") == "primary" and bucket.get("remainingPercent") is not None
    ]
    if not remaining_candidates:
        remaining_candidates = [
            float(bucket["remainingPercent"])
            for bucket in buckets
            if bucket.get("remainingPercent") is not None
        ]
    remaining = min(remaining_candidates) if remaining_candidates else None

    if not buckets:
        return unavailable_payload(detail="no rate-limit windows returned")

    return {
        "ok": True,
        "available": True,
        "message": None,
        "detail": None,
        "planType": plan_type,
        "remainingPercent": remaining,
        "buckets": buckets,
        "credits": credits,
        "source": "account/rateLimits/read",
        "fetchedAt": int(time.time()),
    }


def unavailable_payload(*, detail: str | None = None, auth_required: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "message": UNAVAILABLE_MESSAGE,
        "detail": detail,
        "authRequired": auth_required,
        "planType": None,
        "remainingPercent": None,
        "buckets": [],
        "credits": None,
        "source": None,
        "fetchedAt": int(time.time()),
    }


def _is_auth_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(marker in lower for marker in _AUTH_MARKERS)


class CodexRateLimitsService:
    """Fetch/cache Codex account rate limits through a reused app-server."""

    def __init__(
        self,
        *,
        discover_executable: Callable[[], str | None] | None = None,
        session_factory: Callable[[str, Callable[[str, dict[str, Any]], None]], Any] | None = None,
        cache_ttl: float = CACHE_TTL_SECONDS,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._discover = discover_executable or (lambda: discover_codex_executable("codex"))
        self._session_factory = session_factory or (
            lambda exe, on_note: ReusedCodexAppServer(exe, on_notification=on_note)
        )
        self._cache_ttl = cache_ttl
        self._request_timeout = request_timeout
        self._lock = threading.RLock()
        self._session: Any | None = None
        self._cache: dict[str, Any] | None = None
        self._cache_expires = 0.0

    def get(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if (
                not refresh
                and self._cache is not None
                and now < self._cache_expires
            ):
                cached = dict(self._cache)
                cached["cached"] = True
                return cached

            executable = self._discover()
            if not executable:
                payload = unavailable_payload(detail="Codex executable not found")
                self._store_cache(payload)
                return dict(payload)

            try:
                session = self._ensure_session(executable)
                raw = session.request(
                    "account/rateLimits/read",
                    {},
                    timeout=self._request_timeout,
                )
                payload = normalize_rate_limits_response(raw)
            except CodexAppServerError as exc:
                message = str(exc)
                payload = unavailable_payload(
                    detail=message,
                    auth_required=_is_auth_error(message),
                )
            except Exception as exc:  # pragma: no cover - defensive
                payload = unavailable_payload(detail=str(exc))

            self._store_cache(payload)
            out = dict(payload)
            out["cached"] = False
            return out

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                close = getattr(self._session, "close", None)
                if callable(close):
                    close()
                self._session = None

    def _ensure_session(self, executable: str) -> Any:
        if self._session is None:
            self._session = self._session_factory(executable, self._on_notification)
        return self._session

    def _store_cache(self, payload: dict[str, Any]) -> None:
        self._cache = dict(payload)
        self._cache_expires = time.monotonic() + self._cache_ttl

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method != "account/rateLimits/updated":
            return
        with self._lock:
            # Sparse update: merge notified snapshot into cached buckets when possible.
            sparse = normalize_rate_limits_response(params)
            if not sparse.get("available"):
                return
            if self._cache and self._cache.get("available"):
                existing = {
                    (row.get("limitId"), row.get("window")): row
                    for row in list(self._cache.get("buckets") or [])
                }
                for row in list(sparse.get("buckets") or []):
                    existing[(row.get("limitId"), row.get("window"))] = row
                merged_buckets = list(existing.values())
                remaining_candidates = [
                    float(bucket["remainingPercent"])
                    for bucket in merged_buckets
                    if bucket.get("window") == "primary"
                    and bucket.get("remainingPercent") is not None
                ]
                if not remaining_candidates:
                    remaining_candidates = [
                        float(bucket["remainingPercent"])
                        for bucket in merged_buckets
                        if bucket.get("remainingPercent") is not None
                    ]
                updated = dict(self._cache)
                updated["buckets"] = merged_buckets
                updated["remainingPercent"] = (
                    min(remaining_candidates) if remaining_candidates else None
                )
                if sparse.get("credits") is not None:
                    updated["credits"] = sparse.get("credits")
                if sparse.get("planType"):
                    updated["planType"] = sparse.get("planType")
                updated["fetchedAt"] = int(time.time())
                updated["source"] = "account/rateLimits/updated"
                self._store_cache(updated)
            else:
                sparse["source"] = "account/rateLimits/updated"
                self._store_cache(sparse)


_SERVICE: CodexRateLimitsService | None = None
_SERVICE_LOCK = threading.Lock()


def get_codex_rate_limits_service() -> CodexRateLimitsService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = CodexRateLimitsService()
        return _SERVICE


def reset_codex_rate_limits_service_for_tests() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None:
            _SERVICE.close()
        _SERVICE = None
