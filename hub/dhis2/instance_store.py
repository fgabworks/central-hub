"""Persist the selected DHIS2 Stage/Live instance locally (not browser storage)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.settings import ROOT_DIR

_DEFAULT_PATH = ROOT_DIR / "data" / "dhis2" / "active_instance.json"


def default_instance_path() -> Path:
    return _DEFAULT_PATH


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Dhis2InstanceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_instance_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        instance = str(data.get("instance") or "").strip().lower()
        if instance not in {"stage", "live"}:
            return None
        return {"instance": instance, "updated_at": data.get("updated_at")}

    def get_instance(self) -> str | None:
        data = self.load()
        return None if not data else str(data["instance"])

    def save(self, instance: str) -> dict[str, Any]:
        key = (instance or "").strip().lower()
        if key not in {"stage", "live"}:
            raise ValueError("instance must be 'stage' or 'live'")
        payload = {"instance": key, "updated_at": utcnow()}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
