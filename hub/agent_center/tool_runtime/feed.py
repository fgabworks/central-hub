"""Transient live step feed for Tool Runtime (not persisted as a primary store)."""

from __future__ import annotations

import threading
import time
from typing import Any

from hub.agent_center.tool_runtime.results import ToolStepRecord


class ToolRuntimeFeed:
    """In-memory compact step feed keyed by execution/run id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._feeds: dict[str, list[dict[str, Any]]] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    def reset(self, run_id: str) -> None:
        key = str(run_id or "").strip()
        if not key:
            return
        with self._lock:
            self._feeds[key] = []
            self._meta[key] = {"updated_at": time.time(), "status": "running"}

    def append(self, run_id: str, step: ToolStepRecord | dict[str, Any]) -> dict[str, Any]:
        key = str(run_id or "").strip()
        if not key:
            return {}
        payload = step.public() if isinstance(step, ToolStepRecord) else dict(step)
        # Compact public feed row.
        row = {
            "step": payload.get("step"),
            "provider": payload.get("provider") or "",
            "model": payload.get("model") or "",
            "tool": payload.get("tool") or "",
            "ok": bool(payload.get("ok")),
            "result": payload.get("result") or ("ok" if payload.get("ok") else "error"),
            "duration_ms": payload.get("duration_ms") or 0,
            "summary": str(payload.get("summary") or "")[:160],
            "context_chars": payload.get("context_chars") or 0,
            "tokens": payload.get("total_tokens"),
            "error": str(payload.get("error") or "")[:160],
        }
        with self._lock:
            self._feeds.setdefault(key, []).append(row)
            # Cap feed length to avoid unbounded growth.
            if len(self._feeds[key]) > 40:
                self._feeds[key] = self._feeds[key][-40:]
            self._meta[key] = {"updated_at": time.time(), "status": "running"}
        return row

    def finish(self, run_id: str, *, status: str = "completed") -> None:
        key = str(run_id or "").strip()
        if not key:
            return
        with self._lock:
            self._meta[key] = {"updated_at": time.time(), "status": status}

    def snapshot(self, run_id: str) -> dict[str, Any]:
        key = str(run_id or "").strip()
        with self._lock:
            steps = list(self._feeds.get(key) or [])
            meta = dict(self._meta.get(key) or {})
        return {
            "run_id": key,
            "status": meta.get("status") or ("running" if steps else "idle"),
            "updated_at": meta.get("updated_at"),
            "steps": steps,
            "step_count": len(steps),
        }

    def clear(self, run_id: str) -> None:
        key = str(run_id or "").strip()
        with self._lock:
            self._feeds.pop(key, None)
            self._meta.pop(key, None)


# Process-wide feed used by routing/status polling.
GLOBAL_TOOL_RUNTIME_FEED = ToolRuntimeFeed()
