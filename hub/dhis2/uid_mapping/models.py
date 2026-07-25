"""Normalized UID mapping record model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


INDEX_FIELDS: tuple[str, ...] = (
    "uid",
    "name",
    "code",
    "object_type",
    "value_type",
    "domain_type",
    "source_repository",
    "source_file",
    "source_environment",
    "program_uid",
    "program_stage_uid",
    "option_set_uid",
    "category_combo_uid",
    "last_synced",
    "checksum",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def checksum_for(payload: dict[str, Any]) -> str:
    """Stable checksum over identity + mapping fields (excludes last_synced/checksum)."""
    keys = [
        "uid",
        "name",
        "code",
        "object_type",
        "value_type",
        "domain_type",
        "source_repository",
        "source_file",
        "source_environment",
        "program_uid",
        "program_stage_uid",
        "option_set_uid",
        "category_combo_uid",
    ]
    material = {k: (payload.get(k) or "") for k in keys}
    blob = json.dumps(material, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class NormalizedUidRecord:
    uid: str
    name: str = ""
    code: str = ""
    object_type: str = ""
    value_type: str = ""
    domain_type: str = ""
    source_repository: str = ""
    source_file: str = ""
    source_environment: str = ""
    program_uid: str = ""
    program_stage_uid: str = ""
    option_set_uid: str = ""
    category_combo_uid: str = ""
    last_synced: str = ""
    checksum: str = ""
    # Non-indexed extras kept for raw JSON view / import round-trip
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("last_synced"):
            data["last_synced"] = _now_iso()
        if not data.get("checksum"):
            data["checksum"] = checksum_for(data)
        return data

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NormalizedUidRecord":
        known = {k: (raw.get(k) or "") for k in INDEX_FIELDS if k != "checksum" and k != "last_synced"}
        known["uid"] = str(raw.get("uid") or "").strip()
        extras = {
            k: v
            for k, v in raw.items()
            if k not in INDEX_FIELDS and k != "extras" and v not in (None, "")
        }
        if isinstance(raw.get("extras"), dict):
            extras.update(raw["extras"])
        record = cls(
            uid=known["uid"],
            name=str(known.get("name") or ""),
            code=str(known.get("code") or ""),
            object_type=str(known.get("object_type") or ""),
            value_type=str(known.get("value_type") or ""),
            domain_type=str(known.get("domain_type") or ""),
            source_repository=str(known.get("source_repository") or ""),
            source_file=str(known.get("source_file") or ""),
            source_environment=str(known.get("source_environment") or ""),
            program_uid=str(known.get("program_uid") or ""),
            program_stage_uid=str(known.get("program_stage_uid") or ""),
            option_set_uid=str(known.get("option_set_uid") or ""),
            category_combo_uid=str(known.get("category_combo_uid") or ""),
            last_synced=str(raw.get("last_synced") or _now_iso()),
            checksum="",
            extras=extras,
        )
        data = record.to_dict()
        record.checksum = data["checksum"]
        record.last_synced = data["last_synced"]
        return record
