"""In-memory TTL caches for DHIS2 Reports (catalog, org-units, rendered HTML)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TtlCache:
    """Simple process-local TTL cache with max entries and wall-clock sync times."""

    def __init__(self, *, ttl_seconds: float, max_entries: int = 256) -> None:
        self.ttl = max(1.0, float(ttl_seconds))
        self.max_entries = max(8, int(max_entries))
        # key -> (expires_monotonic, synced_at_iso, value)
        self._data: dict[str, tuple[float, str, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        entry = self.get_entry(key, allow_stale=False)
        return None if entry is None else entry["value"]

    def get_entry(self, key: str, *, allow_stale: bool = False) -> dict[str, Any] | None:
        """Return {value, synced_at, stale} or None when missing / expired (unless allow_stale)."""
        now = time.monotonic()
        with self._lock:
            row = self._data.get(key)
            if not row:
                return None
            expires, synced_at, value = row
            stale = expires < now
            if stale and not allow_stale:
                self._data.pop(key, None)
                return None
            return {"value": value, "synced_at": synced_at, "stale": stale}

    def set(self, key: str, value: Any, *, synced_at: str | None = None) -> str:
        stamp = (synced_at or "").strip() or _utc_now_iso()
        with self._lock:
            if len(self._data) >= self.max_entries:
                now = time.monotonic()
                expired = [k for k, (exp, _, _) in self._data.items() if exp < now]
                for k in expired:
                    self._data.pop(k, None)
                while len(self._data) >= self.max_entries:
                    self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic() + self.ttl, stamp, value)
        return stamp

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

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
# Hierarchy changes rarely; longer TTL keeps cascade selects snappy after first load.
# Stale Stage entries may still be served during maintenance (see search_org_units).
ORG_UNIT_CACHE = TtlCache(ttl_seconds=900, max_entries=512)
RESULT_CACHE = TtlCache(ttl_seconds=120, max_entries=96)
CAPABILITY_CACHE = TtlCache(ttl_seconds=300, max_entries=8)
PERIOD_CACHE = TtlCache(ttl_seconds=600, max_entries=16)
METADATA_CACHE = TtlCache(ttl_seconds=180, max_entries=128)


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
