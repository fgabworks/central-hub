"""HTTP API adapter — Phase 1 health probe only."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests

from hub.registry.models import Repository


class ApiAdapter:
    """Talks to a connected repository over HTTP."""

    def __init__(self, repository: Repository, default_timeout: float = 5.0) -> None:
        self.repository = repository
        self.default_timeout = default_timeout

    def health_check(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        repo = self.repository
        config = repo.health_check

        if not repo.base_url:
            return _result(
                ok=False,
                status="misconfigured",
                detail="API repository is missing base_url",
                latency_ms=0,
                checked_at=checked_at,
            )

        if config is None:
            # Fallback: probe base URL root.
            url = repo.base_url.rstrip("/") + "/"
            method = "GET"
            timeout = self.default_timeout
        elif config.type != "http":
            return _result(
                ok=False,
                status="misconfigured",
                detail=f"API repository health_check.type must be 'http', got {config.type!r}",
                latency_ms=0,
                checked_at=checked_at,
            )
        else:
            url = urljoin(repo.base_url.rstrip("/") + "/", config.path.lstrip("/"))
            method = config.method or "GET"
            timeout = config.timeout_seconds or self.default_timeout

        started = time.perf_counter()
        try:
            response = requests.request(method, url, timeout=timeout)
            latency_ms = int((time.perf_counter() - started) * 1000)
            ok = 200 <= response.status_code < 300
            return _result(
                ok=ok,
                status="healthy" if ok else "unhealthy",
                detail=f"HTTP {response.status_code} from {url}",
                latency_ms=latency_ms,
                checked_at=checked_at,
            )
        except requests.RequestException as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return _result(
                ok=False,
                status="unreachable",
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
                checked_at=checked_at,
            )


def _result(
    *,
    ok: bool,
    status: str,
    detail: str,
    latency_ms: int,
    checked_at: str,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "detail": detail,
        "latency_ms": latency_ms,
        "checked_at": checked_at,
    }
