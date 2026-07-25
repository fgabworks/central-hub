"""Local draft storage for DHIS2 Metadata Builder (preview-only)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.settings import ROOT_DIR

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class DraftStore:
    """Save/load builder drafts under data/dhis2/drafts/ (gitignored)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (ROOT_DIR / "data" / "dhis2" / "drafts")

    def save(self, draft: dict[str, Any], *, draft_id: str | None = None) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        draft_id = draft_id or f"draft_{uuid.uuid4().hex[:12]}"
        if not _SAFE_ID.match(draft_id):
            raise ValueError("Invalid draft id.")
        payload = {
            **draft,
            "id": draft_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "mode": "preview_only",
        }
        path = self.root / f"{draft_id}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return payload

    def load(self, draft_id: str) -> dict[str, Any] | None:
        if not _SAFE_ID.match(draft_id):
            return None
        path = self.root / f"{draft_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        files = sorted(self.root.glob("draft_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items: list[dict[str, Any]] = []
        for path in files[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            items.append(
                {
                    "id": data.get("id") or path.stem,
                    "saved_at": data.get("saved_at"),
                    "metadata_type": data.get("metadata_type"),
                    "operation": data.get("operation"),
                    "name": (data.get("form") or {}).get("name"),
                }
            )
        return items
