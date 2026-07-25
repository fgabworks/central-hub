"""Enrichment workflow: fetch → preview → typed confirm → versioned snapshot."""

from __future__ import annotations

import json
import threading
from typing import Any

from hub.dhis2.client import Dhis2Client
from hub.dhis2.enrichment.fetch import EnrichmentFetcher
from hub.dhis2.enrichment.models import CONFIRM_APPLY
from hub.dhis2.enrichment.store import EnrichmentStore
from hub.dhis2.uid_mapping.store import MappingIndexStore


class EnrichmentWorkflow:
    def __init__(
        self,
        client: Dhis2Client,
        *,
        mapping_store: MappingIndexStore | None = None,
        enrichment_store: EnrichmentStore | None = None,
    ) -> None:
        self.client = client
        self.mapping_store = mapping_store or MappingIndexStore()
        self.store = enrichment_store or EnrichmentStore()
        self._lock = threading.Lock()
        self._preview: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    @property
    def preview(self) -> dict[str, Any] | None:
        return self._preview

    def start_fetch(self, *, environment: str = "") -> str:
        if not self.client.public_config().get("configured"):
            raise RuntimeError("DHIS2 is not configured. Set DHIS2_* credentials first.")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("An enrichment fetch is already running.")
            run_id = self.store.create_run(environment=environment or "default")
            self._preview = None

            def _worker() -> None:
                try:
                    self.store.update_run(
                        run_id, status="running", phase="fetch", message="Starting", percent=1
                    )

                    def on_progress(phase: str, pct: float, message: str) -> None:
                        self.store.update_run(
                            run_id,
                            status="running",
                            phase=phase,
                            percent=pct,
                            message=message,
                        )

                    previous = self.store.checksum_map()
                    fetcher = EnrichmentFetcher(self.client)
                    result = fetcher.fetch_all(
                        self.mapping_store.records(),
                        environment=environment or "default",
                        include_raw=False,
                        previous_checksums=previous,
                        on_progress=on_progress,
                        should_cancel=lambda: self.store.is_cancel_requested(run_id),
                    )
                    if result.get("cancelled"):
                        from hub.dhis2.enrichment.db import utcnow

                        self.store.update_run(
                            run_id,
                            status="cancelled",
                            phase="cancelled",
                            message="Cancelled",
                            percent=100,
                            finished_at=utcnow(),
                        )
                        return

                    objects = list(result.get("objects") or [])
                    conflict_labels = {
                        "Name Mismatch",
                        "Object Type Mismatch",
                        "Value Type Mismatch",
                        "Domain Type Mismatch",
                        "Option Set Mismatch",
                        "Program Stage Mismatch",
                        "Duplicate Mapping",
                        "Broken Reference",
                    }
                    added = sum(1 for o in objects if o.get("uid") not in previous)
                    changed = sum(
                        1
                        for o in objects
                        if "Changed Since Last Scan" in (o.get("audit_statuses") or [])
                    )
                    conflicting = sum(
                        1
                        for o in objects
                        if conflict_labels.intersection(o.get("audit_statuses") or [])
                    )
                    sample = [
                        {
                            "uid": o.get("uid"),
                            "name": o.get("name"),
                            "object_type": o.get("object_type"),
                            "answer_type": o.get("answer_type"),
                            "audit_statuses": o.get("audit_statuses") or [],
                        }
                        for o in objects[:40]
                    ]
                    preview = {
                        "environment": environment or "default",
                        "stats": result.get("stats") or {},
                        "objects": objects,
                        "relationships": result.get("relationships") or [],
                        "options": result.get("options") or [],
                        "confirm_phrase": CONFIRM_APPLY,
                        "sample_objects": sample,
                        "counts": {
                            "added_objects": added,
                            "changed_objects": changed,
                            "conflicting_objects": conflicting,
                            "total_objects": len(objects),
                            "relationships": len(result.get("relationships") or []),
                            "options": len(result.get("options") or []),
                            "missing_in_dhis2": (result.get("stats") or {}).get(
                                "missing_in_dhis2", 0
                            ),
                            "unresolved_references": (result.get("stats") or {}).get(
                                "unresolved_references", 0
                            ),
                        },
                    }
                    with self._lock:
                        self._preview = preview
                    from hub.dhis2.enrichment.db import utcnow

                    self.store.update_run(
                        run_id,
                        status="preview_ready",
                        phase="preview",
                        percent=100,
                        message="Preview ready — confirm to save snapshot",
                        preview_json=json.dumps(
                            {
                                "stats": preview["stats"],
                                "counts": preview["counts"],
                                "confirm_phrase": CONFIRM_APPLY,
                            },
                            ensure_ascii=True,
                        ),
                        finished_at=utcnow(),
                    )
                except Exception as exc:  # noqa: BLE001
                    from hub.dhis2.enrichment.db import utcnow

                    self.store.update_run(
                        run_id,
                        status="failed",
                        phase="error",
                        message=str(exc),
                        error=str(exc),
                        finished_at=utcnow(),
                    )

            self._thread = threading.Thread(target=_worker, name=f"enrich-{run_id[:8]}", daemon=True)
            self._thread.start()
            return run_id

    def cancel(self, run_id: str) -> None:
        self.store.request_cancel(run_id)

    def apply_preview(self, confirmation: str) -> dict[str, Any]:
        if (confirmation or "") != CONFIRM_APPLY:
            return {
                "ok": False,
                "error": "Confirmation phrase did not match.",
                "expected_phrase": CONFIRM_APPLY,
                "writes": 0,
            }
        with self._lock:
            preview = self._preview
        if not preview:
            return {"ok": False, "error": "No enrichment preview to apply.", "writes": 0}

        snap_id = self.store.save_snapshot(
            environment=str(preview.get("environment") or "default"),
            objects=list(preview.get("objects") or []),
            relationships=list(preview.get("relationships") or []),
            options=list(preview.get("options") or []),
            stats=dict(preview.get("stats") or {}),
            notes="DHIS2 metadata enrichment snapshot (local only; no DHIS2 writes).",
        )
        with self._lock:
            self._preview = None
        return {
            "ok": True,
            "snapshot_id": snap_id,
            "writes": 1,
            "dhis2_writes": 0,
            "stats": preview.get("stats"),
        }

    def discard_preview(self) -> None:
        with self._lock:
            self._preview = None
