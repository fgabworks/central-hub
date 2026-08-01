"""In-memory TTL caches for DHIS2 Reports (catalog, org-units, rendered HTML)."""

from __future__ import annotations

import threading
import time
from typing import Any


class TtlCache:
    """Simple process-local TTL cache with max entries."""

    def __init__(self, *, ttl_seconds: float, max_entries: int = 256) -> None:
        self.ttl = max(1.0, float(ttl_seconds))
        self.max_entries = max(8, int(max_entries))
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            row = self._data.get(key)
            if not row:
                return None
            expires, value = row
            if expires < now:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Drop expired first, then oldest insertion order approx.
                now = time.monotonic()
                expired = [k for k, (exp, _) in self._data.items() if exp < now]
                for k in expired:
                    self._data.pop(k, None)
                while len(self._data) >= self.max_entries:
                    self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic() + self.ttl, value)

    def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Module-level caches shared across requests (per process).
CATALOG_CACHE = TtlCache(ttl_seconds=120, max_entries=32)
ORG_UNIT_CACHE = TtlCache(ttl_seconds=90, max_entries=128)
RESULT_CACHE = TtlCache(ttl_seconds=45, max_entries=64)
CAPABILITY_CACHE = TtlCache(ttl_seconds=300, max_entries=8)


def result_cache_key(
    *,
    environment: str,
    report_id: str,
    period: str,
    org_unit: str,
    output_format: str = "html",
) -> str:
    return "|".join(
        [
            "result",
            (environment or "").strip().lower(),
            (report_id or "").strip(),
            (period or "").strip(),
            (org_unit or "").strip(),
            (output_format or "html").strip().lower(),
        ]
    )
