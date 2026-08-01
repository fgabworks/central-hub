"""Append-only JSONL audit store."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.agent_center.redact import redact_text


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value, limit=4000)
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class AuditStore:
    """Persists operator actions to a local JSONL file (no secrets)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(
        self,
        *,
        action: str,
        actor: str = "local-owner",
        target: str | None = None,
        detail: str | None = None,
        ok: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "target": _redact(target),
            "detail": _redact(detail),
            "ok": ok,
            "metadata": _redact(metadata or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        return event

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in reversed(lines):
            text = line.strip()
            if not text:
                continue
            try:
                events.append(json.loads(text))
            except json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break
        return events
