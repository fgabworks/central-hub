"""Persist and merge the local UID mapping index (never silent conflict overwrite)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.dhis2.uid_mapping.models import NormalizedUidRecord, checksum_for
from hub.dhis2.uid_mapping.scan import load_sources_config
from hub.settings import ROOT_DIR


def _identity_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            str(record.get("uid") or ""),
            str(record.get("source_repository") or ""),
            str(record.get("source_file") or ""),
            str(record.get("source_environment") or ""),
        ]
    )


def _conflict_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    fields = ("name", "code", "object_type", "value_type", "domain_type", "program_uid", "option_set_uid", "category_combo_uid")
    return [f for f in fields if str(a.get(f) or "") != str(b.get(f) or "")]


class MappingIndexStore:
    """Load/save normalized UID index under data/dhis2/uid_index/."""

    def __init__(self, root: Path | None = None) -> None:
        if root is not None:
            self.root = root
            self.latest_path = root / "latest.json"
        else:
            cfg = load_sources_config()
            rel = (cfg.get("defaults") or {}).get("index_path") or "data/dhis2/uid_index/latest.json"
            self.latest_path = ROOT_DIR / rel if not Path(rel).is_absolute() else Path(rel)
            self.root = self.latest_path.parent

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    def save(self, index: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        text = json.dumps(index, indent=2, ensure_ascii=True)
        # Versioned archive copy (LP-style) plus latest pointer.
        stamped = self.archive_dir / f"hub_uid_index_updated_v{stamp}.json"
        stamped.write_text(text, encoding="utf-8")
        self.latest_path.write_text(text, encoding="utf-8")
        return stamped

    def load_latest(self) -> dict[str, Any] | None:
        if not self.latest_path.is_file():
            return None
        try:
            data = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def records(self) -> list[dict[str, Any]]:
        index = self.load_latest() or {}
        rows = index.get("records") or []
        return [r for r in rows if isinstance(r, dict)]

    def get_by_uid(self, uid: str) -> list[dict[str, Any]]:
        uid = (uid or "").strip()
        return [r for r in self.records() if str(r.get("uid")) == uid]


def merge_preview(
    existing: list[dict[str, Any]],
    incoming: list[NormalizedUidRecord] | list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Preview merge of incoming records into existing index.

    Conflicts (same uid+source identity with differing mapping fields, or same uid
    with different object_type/name/code across sources) are never auto-applied.
    """
    existing_by_key = {_identity_key(r): r for r in existing}
    existing_by_uid: dict[str, list[dict[str, Any]]] = {}
    for r in existing:
        existing_by_uid.setdefault(str(r.get("uid")), []).append(r)

    added: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for item in incoming:
        rec = item.to_dict() if isinstance(item, NormalizedUidRecord) else dict(item)
        if not rec.get("checksum"):
            rec["checksum"] = checksum_for(rec)
        key = _identity_key(rec)
        uid = str(rec.get("uid") or "")
        prev = existing_by_key.get(key)

        # Cross-source conflict: same UID, different critical fields already in index
        peers = existing_by_uid.get(uid, [])
        peer_conflicts = []
        for peer in peers:
            if _identity_key(peer) == key:
                continue
            diffs = _conflict_fields(peer, rec)
            if diffs:
                peer_conflicts.append({"peer": peer, "fields": diffs})

        if peer_conflicts and not prev:
            conflicts.append(
                {
                    "status": "conflicting",
                    "reason": "Same UID already indexed with different mapping fields",
                    "incoming": rec,
                    "peers": peer_conflicts,
                }
            )
            continue

        if prev is None:
            added.append(rec)
            continue

        diffs = _conflict_fields(prev, rec)
        if not diffs and str(prev.get("checksum")) == str(rec.get("checksum")):
            unchanged.append(rec)
        elif diffs:
            # Same source identity with field updates → changed (preview + explicit save)
            changed.append({"existing": prev, "incoming": rec, "fields": diffs})
        else:
            changed.append({"existing": prev, "incoming": rec, "fields": []})

    return {
        "added": added,
        "changed": changed,
        "unchanged": unchanged,
        "conflicting": conflicts,
        "counts": {
            "added": len(added),
            "changed": len(changed),
            "unchanged": len(unchanged),
            "conflicting": len(conflicts),
            "incoming": len(incoming),
            "existing": len(existing),
        },
    }


def apply_merge(
    store: MappingIndexStore,
    preview: dict[str, Any],
    *,
    include_conflicts: bool = False,
    conflict_resolutions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Apply a merge preview. Conflicts are skipped unless include_conflicts=True
    and an explicit resolution is provided per UID (keep_existing | take_incoming).
    """
    conflict_resolutions = conflict_resolutions or {}
    existing = { _identity_key(r): r for r in store.records() }

    for rec in preview.get("unchanged") or []:
        existing[_identity_key(rec)] = rec
    for item in preview.get("changed") or []:
        incoming = item.get("incoming") if isinstance(item, dict) else item
        if isinstance(incoming, dict):
            existing[_identity_key(incoming)] = incoming
    for rec in preview.get("added") or []:
        existing[_identity_key(rec)] = rec

    applied_conflicts = 0
    skipped_conflicts = 0
    if include_conflicts:
        for item in preview.get("conflicting") or []:
            incoming = item.get("incoming") or {}
            uid = str(incoming.get("uid") or "")
            resolution = conflict_resolutions.get(uid) or conflict_resolutions.get(_identity_key(incoming))
            if resolution == "take_incoming" and incoming:
                existing[_identity_key(incoming)] = incoming
                applied_conflicts += 1
            else:
                skipped_conflicts += 1
    else:
        skipped_conflicts = len(preview.get("conflicting") or [])

    records = sorted(existing.values(), key=lambda r: (str(r.get("object_type") or ""), str(r.get("name") or ""), str(r.get("uid") or "")))
    index = {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": records,
        "notes": [
            "Local UID mapping index. Not a DHIS2 write.",
            "Conflicts are never overwritten silently.",
        ],
        "last_merge": {
            "applied_conflicts": applied_conflicts,
            "skipped_conflicts": skipped_conflicts,
            "counts": preview.get("counts"),
        },
    }
    path = store.save(index)
    index["index_path"] = str(path)
    return index
