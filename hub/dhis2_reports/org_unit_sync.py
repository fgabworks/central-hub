"""Background / on-demand DHIS2 → SQLite organisation-unit sync (GET-only)."""

from __future__ import annotations

import threading
from typing import Any, Callable

from hub.dhis2.client import Dhis2Error
from hub.dhis2_reports.org_unit_store import OrgUnitStore, utcnow_iso

ClientFactory = Callable[[str], Any]
FetchChildren = Callable[..., list[dict[str, Any]]]
FetchByLevel = Callable[..., list[dict[str, Any]]]
FetchSearch = Callable[..., list[dict[str, Any]]]


class OrgUnitSyncManager:
    """Deduped sync jobs per environment+scope. Never crosses Stage/Live."""

    def __init__(self, store: OrgUnitStore | None = None) -> None:
        self.store = store or OrgUnitStore()
        self._lock = threading.RLock()
        self._inflight: dict[str, float] = {}

    def _job_key(self, environment: str, scope_key: str) -> str:
        return f"{(environment or '').strip().lower()}|{scope_key}"

    def is_inflight(self, environment: str, scope_key: str) -> bool:
        with self._lock:
            return self._job_key(environment, scope_key) in self._inflight

    def try_begin(self, environment: str, scope_key: str) -> bool:
        key = self._job_key(environment, scope_key)
        with self._lock:
            if key in self._inflight:
                return False
            self._inflight[key] = threading.get_ident()
            return True

    def end(self, environment: str, scope_key: str) -> None:
        key = self._job_key(environment, scope_key)
        with self._lock:
            self._inflight.pop(key, None)

    def enrich_path_labels(
        self,
        environment: str,
        rows: list[dict[str, Any]],
        *,
        parent_uid: str = "",
    ) -> list[dict[str, Any]]:
        parent = None
        if parent_uid:
            parent = self.store.get(environment, parent_uid)
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            name = item.get("name") or item.get("id") or ""
            if parent and parent.get("path_label"):
                item["path_label"] = f"{parent['path_label']} › {name}"
            elif parent and parent.get("name"):
                item["path_label"] = f"{parent['name']} › {name}"
            else:
                item["path_label"] = item.get("path_label") or item.get("path") or name
            if parent_uid and not item.get("parent_uid"):
                item["parent_uid"] = parent_uid
            out.append(item)
        return out

    def persist_rows(
        self,
        environment: str,
        rows: list[dict[str, Any]],
        *,
        scope_key: str,
        parent_uid: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        enriched = self.enrich_path_labels(environment, rows, parent_uid=parent_uid)
        stamp = self.store.upsert_rows(
            environment, enriched, parent_uid=parent_uid, synced_at=utcnow_iso()
        )
        self.store.mark_scope(
            environment, scope_key, unit_count=len(enriched), synced_at=stamp
        )
        return enriched, stamp

    def schedule(
        self,
        environment: str,
        scope_key: str,
        worker: Callable[[], None],
    ) -> bool:
        """Start a daemon worker once per env+scope. Returns False if already running."""
        if not self.try_begin(environment, scope_key):
            return False

        def _run() -> None:
            try:
                worker()
            finally:
                self.end(environment, scope_key)

        threading.Thread(
            target=_run,
            name=f"ou-sync-{environment}-{scope_key[:24]}",
            daemon=True,
        ).start()
        return True

    def sync_scope_blocking(
        self,
        environment: str,
        *,
        scope_key: str,
        parent_uid: str = "",
        level: int | None = None,
        q: str = "",
        limit: int = 100,
        fetch_children: FetchChildren,
        fetch_by_level: FetchByLevel,
        fetch_search: FetchSearch,
        client: Any,
        timeout: float,
    ) -> list[dict[str, Any]]:
        """Fetch from DHIS2 and persist. Caller must hold try_begin or accept nesting."""
        self.store.set_env_sync_state(environment, status="running", started=True)
        try:
            if parent_uid:
                rows = fetch_children(client, parent_uid, limit=limit, timeout=timeout)
            elif level is not None and not (q or "").strip():
                rows = fetch_by_level(client, level=level, limit=limit, timeout=timeout)
            else:
                rows = fetch_search(client, needle=q, limit=limit, timeout=timeout)
            enriched, _stamp = self.persist_rows(
                environment,
                rows,
                scope_key=scope_key,
                parent_uid=parent_uid,
            )
            self.store.set_env_sync_state(environment, status="ok")
            return enriched
        except Dhis2Error as exc:
            self.store.set_env_sync_state(
                environment, status="error", error=str(exc.message or exc)
            )
            raise
        except Exception as exc:  # noqa: BLE001 — persist sync failure status
            self.store.set_env_sync_state(environment, status="error", error=str(exc))
            raise
