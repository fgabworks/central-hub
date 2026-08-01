"""Lightweight request timing, Server-Timing headers, and TTL caches."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

from flask import Flask, g, has_request_context, request

logger = logging.getLogger("hub.perf")

T = TypeVar("T")

_SLOW_MS = float(os.getenv("CENTRAL_HUB_SLOW_OP_MS", "200"))
_DEV_LOG = (os.getenv("CENTRAL_HUB_ENV", "dev") or "dev").strip().lower() in {
    "dev",
    "development",
    "local",
} or os.getenv("CENTRAL_HUB_PERF_LOG", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class RequestTimer:
    started: float = field(default_factory=time.perf_counter)
    marks: list[tuple[str, float, dict[str, Any]]] = field(default_factory=list)
    sqlite_queries: int = 0
    sqlite_ms: float = 0.0
    external_calls: int = 0
    external_ms: float = 0.0

    def mark(self, name: str, duration_ms: float, **meta: Any) -> None:
        self.marks.append((name, float(duration_ms), meta))
        if _DEV_LOG and duration_ms >= _SLOW_MS:
            path = request.path if has_request_context() else "?"
            extra = " ".join(f"{k}={v}" for k, v in meta.items())
            logger.warning(
                "slow_op name=%s duration_ms=%.1f path=%s %s",
                name,
                duration_ms,
                path,
                extra,
            )

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def server_timing_header(self) -> str:
        parts: list[str] = [f"app;dur={self.elapsed_ms():.1f}"]
        for name, dur, _meta in self.marks[:24]:
            safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)[:40]
            parts.append(f"{safe};dur={dur:.1f}")
        if self.sqlite_queries:
            parts.append(f"sqlite;dur={self.sqlite_ms:.1f};desc=\"q={self.sqlite_queries}\"")
        if self.external_calls:
            parts.append(
                f"external;dur={self.external_ms:.1f};desc=\"n={self.external_calls}\""
            )
        return ", ".join(parts)


def current_timer() -> RequestTimer | None:
    if not has_request_context():
        return None
    return getattr(g, "_hub_timer", None)


@contextmanager
def timed(name: str, **meta: Any) -> Iterator[None]:
    timer = current_timer()
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000.0
        if timer is not None:
            timer.mark(name, duration_ms, **meta)


def record_sqlite(duration_ms: float, *, queries: int = 1) -> None:
    timer = current_timer()
    if timer is None:
        return
    timer.sqlite_queries += max(0, int(queries))
    timer.sqlite_ms += float(duration_ms)


def record_external(duration_ms: float, *, name: str = "external") -> None:
    timer = current_timer()
    if timer is None:
        return
    timer.external_calls += 1
    timer.external_ms += float(duration_ms)
    timer.mark(name, duration_ms, kind="external")


class TtlCache:
    """Simple thread-safe TTL cache for stable metadata."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self.ttl_seconds > 0 and time.monotonic() >= expires_at:
                return None
            return value

    def peek(self, key: str) -> tuple[Any | None, bool]:
        """Return (value_or_None, fresh). Stale values are still returned."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None, False
            expires_at, value = item
            fresh = self.ttl_seconds <= 0 or time.monotonic() < expires_at
            return value, fresh

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            ttl = self.ttl_seconds if self.ttl_seconds > 0 else 365 * 24 * 3600.0
            self._items[key] = (time.monotonic() + ttl, value)

    def invalidate(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._items.clear()
            else:
                self._items.pop(key, None)


_inflight: dict[str, dict[str, Any]] = {}
_inflight_lock = threading.Lock()


def coalesce(key: str, fn: Callable[[], T]) -> T:
    """Deduplicate overlapping identical work within the process."""
    with _inflight_lock:
        box = _inflight.get(key)
        if box is None:
            box = {"event": threading.Event(), "result": None, "error": None}
            _inflight[key] = box
            owner = True
        else:
            owner = False
    if owner:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            box["event"].set()
            with _inflight_lock:
                _inflight.pop(key, None)
        if box["error"] is not None:
            raise box["error"]
        return box["result"]
    box["event"].wait(timeout=60)
    if box["error"] is not None:
        raise box["error"]
    if box["result"] is not None:
        return box["result"]
    return fn()

def register_perf_middleware(app: Flask) -> None:
    @app.before_request
    def _perf_start() -> None:
        g._hub_timer = RequestTimer()

    @app.after_request
    def _perf_finish(response):  # type: ignore[no-untyped-def]
        timer = current_timer()
        if timer is None:
            return response
        # Template render approximate: remaining time after marked segments.
        response.headers["Server-Timing"] = timer.server_timing_header()
        if _DEV_LOG and timer.elapsed_ms() >= _SLOW_MS:
            logger.info(
                "request path=%s status=%s duration_ms=%.1f sqlite_q=%s external_n=%s",
                request.path,
                response.status_code,
                timer.elapsed_ms(),
                timer.sqlite_queries,
                timer.external_calls,
            )
        return response
