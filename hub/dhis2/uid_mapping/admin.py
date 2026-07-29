"""Controlled UID index updates — adapted from Live Processing's uid_index_admin flow.

Hub owns only ``data/dhis2/uid_index/`` (JSON). Never writes DHIS2 or LP's
``AI_UID_INDEX.csv``. Apply/restore require an exact typed confirmation phrase.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.dhis2.uid_mapping.models import NormalizedUidRecord
from hub.dhis2.uid_mapping.store import MappingIndexStore, _conflict_fields

CONFIRM_APPLY = "APPLY HUB UID INDEX"
CONFIRM_RESTORE = "RESTORE UID INDEX VERSION"


def confirm_phrase_for_apply() -> str:
    return CONFIRM_APPLY


def confirm_phrase_for_restore() -> str:
    return CONFIRM_RESTORE


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _as_dict(item: NormalizedUidRecord | dict[str, Any]) -> dict[str, Any]:
    return item.to_dict() if isinstance(item, NormalizedUidRecord) else dict(item)


def _sample_rows(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        out.append(
            {
                "uid": row.get("uid"),
                "name": row.get("name"),
                "code": row.get("code"),
                "object_type": row.get("object_type"),
                "source_repository": row.get("source_repository"),
            }
        )
    return out


def enrich_controlled_preview(
    preview: dict[str, Any],
    *,
    existing: list[dict[str, Any]],
    incoming: list[NormalizedUidRecord] | list[dict[str, Any]],
    store: MappingIndexStore,
) -> dict[str, Any]:
    """Attach LP-style change cards, samples, blocking reasons, and file paths."""
    counts = dict(preview.get("counts") or {})
    changed_name = 0
    changed_type = 0
    name_samples: list[dict[str, Any]] = []
    type_samples: list[dict[str, Any]] = []
    for item in preview.get("changed") or []:
        fields = list(item.get("fields") or [])
        incoming_row = item.get("incoming") or {}
        existing_row = item.get("existing") or {}
        sample = {
            "uid": incoming_row.get("uid"),
            "name_old": existing_row.get("name"),
            "name_new": incoming_row.get("name"),
            "object_type_old": existing_row.get("object_type"),
            "object_type_new": incoming_row.get("object_type"),
            "fields": fields,
        }
        if "name" in fields or "code" in fields:
            changed_name += 1
            if len(name_samples) < 40:
                name_samples.append(sample)
        if "object_type" in fields:
            changed_type += 1
            if len(type_samples) < 40:
                type_samples.append(sample)

    incoming_dicts = [_as_dict(item) for item in incoming]
    incoming_uids = {str(r.get("uid") or "") for r in incoming_dicts if r.get("uid")}
    incoming_repos = {
        str(r.get("source_repository") or "")
        for r in incoming_dicts
        if r.get("source_repository")
    }
    missing: list[dict[str, Any]] = []
    if incoming_repos:
        for row in existing:
            uid = str(row.get("uid") or "")
            repo = str(row.get("source_repository") or "")
            if repo in incoming_repos and uid and uid not in incoming_uids:
                missing.append(row)

    blank_uid = sum(1 for r in incoming_dicts if not str(r.get("uid") or "").strip())
    reasons: list[dict[str, Any]] = []
    conflicting = int(counts.get("conflicting") or 0)
    if conflicting:
        reasons.append(
            {
                "type": "CONFLICT",
                "count": conflicting,
                "detail": "Conflicting mappings will be skipped unless you explicitly include them.",
            }
        )
    if blank_uid:
        reasons.append(
            {
                "type": "BLANK_UID",
                "count": blank_uid,
                "detail": "Blank UID rows found in the scan/import.",
            }
        )
    # Type changes are reviewable but not hard-blocked (hub merge keeps prior until apply).
    if changed_type:
        reasons.append(
            {
                "type": "TYPE_CHANGE_REVIEW",
                "count": changed_type,
                "detail": "Object type changed for existing UID(s). Review samples before apply.",
            }
        )

    stamp = _now_stamp()
    archive = store.archive_dir
    enriched = dict(preview)
    enriched.update(
        {
            "controlled": True,
            "read_only_dry_run": True,
            "confirm_phrase": CONFIRM_APPLY,
            "change_counts": {
                "NEW_UID": int(counts.get("added") or 0),
                "CHANGED_NAME": changed_name,
                "CHANGED_TYPE": changed_type,
                "MISSING_FROM_SOURCE": len(missing),
                "UNCHANGED": int(counts.get("unchanged") or 0),
                "CONFLICTING": conflicting,
            },
            "blocking": {
                # Only blank UIDs hard-block; conflicts skip by default on apply.
                "blocked": blank_uid > 0,
                "reasons": reasons,
            },
            "samples": {
                "new": _sample_rows(list(preview.get("added") or [])),
                "changed_name": name_samples,
                "changed_type": type_samples,
                "missing_from_source": _sample_rows(missing),
                "conflicting": [
                    {
                        "uid": (item.get("incoming") or {}).get("uid"),
                        "reason": item.get("reason"),
                        "fields": item.get("fields")
                        or [
                            f
                            for peer in (item.get("peers") or [])
                            for f in (peer.get("fields") or [])
                        ],
                    }
                    for item in (preview.get("conflicting") or [])[:40]
                ],
            },
            "missing_from_source": missing,
            "files_to_change": {
                "active_index": str(store.latest_path),
                "backup_to_create": str(archive / f"hub_uid_index_backup_v{stamp}.json"),
                "versioned_copy": str(archive / f"hub_uid_index_updated_v{stamp}.json"),
                "change_log": str(archive / f"hub_uid_index_change_log_v{stamp}.md"),
            },
            "notes": [
                "Dry-run only until you type the confirmation phrase and apply.",
                "Missing-from-source rows are informational; apply does not delete them.",
                "Never writes DHIS2 metadata or Live Processing's AI_UID_INDEX.csv.",
            ],
        }
    )
    return enriched


def list_versions(store: MappingIndexStore) -> dict[str, Any]:
    archive = store.archive_dir
    candidates: list[Path] = []
    if archive.is_dir():
        candidates.extend(sorted(archive.glob("hub_uid_index_updated_v*.json"), reverse=True))
        candidates.extend(sorted(archive.glob("hub_uid_index_backup_v*.json"), reverse=True))
    candidates.extend(sorted(store.root.glob("index_*.json"), reverse=True))

    versions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        name = path.name
        if "updated_v" in name or "backup_v" in name:
            ts = path.stem.split("_v", 1)[-1]
        else:
            ts = path.stem.replace("index_", "", 1)
        if ts in seen:
            continue
        # Prefer updated over backup for the same stamp.
        preferred = archive / f"hub_uid_index_updated_v{ts}.json"
        restore_target = preferred if preferred.is_file() else path
        seen.add(ts)
        try:
            data = json.loads(restore_target.read_text(encoding="utf-8"))
            rows = len(data.get("records") or []) if isinstance(data, dict) else 0
        except (OSError, json.JSONDecodeError):
            rows = 0
        versions.append(
            {
                "version": ts,
                "created_display": _format_stamp(ts),
                "restore_target": str(restore_target),
                "rows": rows,
                "sha256": _sha256(restore_target),
                "is_current": False,
            }
        )

    current = store.load_latest()
    return {
        "current": {
            "version": "current",
            "index_path": str(store.latest_path),
            "rows": len((current or {}).get("records") or []) if current else 0,
            "sha256": _sha256(store.latest_path) if store.latest_path.is_file() else "",
            "updated_at": (current or {}).get("updated_at"),
        },
        "versions": versions,
        "count": len(versions),
        "restore_confirm_phrase": CONFIRM_RESTORE,
        "apply_confirm_phrase": CONFIRM_APPLY,
    }


def _format_stamp(ts: str) -> str:
    for fmt in ("%Y%m%d_%H%M%S", "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(ts, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ts


def _resolve_version_file(store: MappingIndexStore, version: str) -> Path | None:
    if version == "current":
        return store.latest_path if store.latest_path.is_file() else None
    archive = store.archive_dir
    for candidate in (
        archive / f"hub_uid_index_updated_v{version}.json",
        archive / f"hub_uid_index_backup_v{version}.json",
        store.root / f"index_{version}.json",
    ):
        if candidate.is_file():
            return candidate
    # Fuzzy match legacy ISO stamps.
    matches = list(store.root.glob(f"index_{version}*.json"))
    return matches[0] if matches else None


def compare_versions(
    store: MappingIndexStore, version_a: str, version_b: str
) -> dict[str, Any]:
    file_a = _resolve_version_file(store, version_a)
    file_b = _resolve_version_file(store, version_b)
    if file_a is None:
        return {"ok": False, "error": f"Version not found: {version_a}"}
    if file_b is None:
        return {"ok": False, "error": f"Version not found: {version_b}"}

    def _index(path: Path) -> dict[str, dict[str, Any]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("records") or [] if isinstance(data, dict) else []
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("uid"):
                out[str(row["uid"])] = row
        return out

    a = _index(file_a)
    b = _index(file_b)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed: list[dict[str, Any]] = []
    for uid in sorted(set(a) & set(b)):
        diffs = _conflict_fields(a[uid], b[uid])
        if diffs:
            changed.append(
                {
                    "uid": uid,
                    "changes": [
                        {"field": f, "old": a[uid].get(f), "new": b[uid].get(f)}
                        for f in diffs
                    ],
                }
            )
    return {
        "ok": True,
        "version_a": version_a,
        "version_b": version_b,
        "file_a": str(file_a),
        "file_b": str(file_b),
        "rows_a": len(a),
        "rows_b": len(b),
        "added": added[:100],
        "removed": removed[:100],
        "changed": changed[:100],
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
    }


def apply_with_confirmation(
    store: MappingIndexStore,
    preview: dict[str, Any],
    confirmation: str,
    *,
    include_conflicts: bool = False,
    confirm_phrase: str | None = None,
) -> dict[str, Any]:
    expected = confirm_phrase or CONFIRM_APPLY
    if (confirmation or "") != expected:
        return {
            "ok": False,
            "error": "Confirmation phrase did not match.",
            "expected_phrase": expected,
            "writes": 0,
        }
    if preview.get("blocking", {}).get("blocked"):
        return {
            "ok": False,
            "error": "Apply blocked by unresolved issues.",
            "blocking": preview.get("blocking"),
            "writes": 0,
        }

    from hub.dhis2.uid_mapping.store import apply_merge

    stamp = _now_stamp()
    store.archive_dir.mkdir(parents=True, exist_ok=True)
    backup_path = store.archive_dir / f"hub_uid_index_backup_v{stamp}.json"
    if store.latest_path.is_file():
        shutil.copy2(store.latest_path, backup_path)

    index = apply_merge(store, preview, include_conflicts=include_conflicts)
    # apply_merge already saved; also keep archive updated copy + change log.
    updated_path = store.archive_dir / f"hub_uid_index_updated_v{stamp}.json"
    if store.latest_path.is_file():
        shutil.copy2(store.latest_path, updated_path)
    change_counts = preview.get("change_counts") or preview.get("counts") or {}
    log_path = store.archive_dir / f"hub_uid_index_change_log_v{stamp}.md"
    log_path.write_text(
        "\n".join(
            [
                f"# Hub UID index change log v{stamp}",
                "",
                f"- Applied at: {datetime.now(timezone.utc).isoformat()}",
                f"- Active index: `{store.latest_path}`",
                f"- Backup: `{backup_path if backup_path.exists() else 'none'}`",
                f"- Versioned copy: `{updated_path}`",
                f"- Record count after: {index.get('record_count')}",
                f"- Change counts: `{json.dumps(change_counts)}`",
                "",
                "Local hub index only. No DHIS2 writes. No LP CSV writes.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "applied": True,
        "version": stamp,
        "index": index,
        "backup_path": str(backup_path) if backup_path.exists() else None,
        "versioned_copy": str(updated_path),
        "change_log": str(log_path),
        "writes": 1,
        "dhis2_writes": 0,
    }


def restore_with_confirmation(
    store: MappingIndexStore,
    version: str,
    confirmation: str,
) -> dict[str, Any]:
    if (confirmation or "") != CONFIRM_RESTORE:
        return {
            "ok": False,
            "error": "Confirmation phrase did not match.",
            "expected_phrase": CONFIRM_RESTORE,
            "writes": 0,
        }
    source = _resolve_version_file(store, version)
    if source is None:
        return {"ok": False, "error": f"Version not found: {version}", "writes": 0}
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Cannot read version: {exc}", "writes": 0}
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return {"ok": False, "error": "Version file is not a valid hub UID index.", "writes": 0}

    stamp = _now_stamp()
    store.archive_dir.mkdir(parents=True, exist_ok=True)
    if store.latest_path.is_file():
        shutil.copy2(
            store.latest_path,
            store.archive_dir / f"hub_uid_index_backup_v{stamp}.json",
        )
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["notes"] = list(data.get("notes") or []) + [
        f"Restored from version {version} at {data['updated_at']}"
    ]
    path = store.save(data)
    shutil.copy2(store.latest_path, store.archive_dir / f"hub_uid_index_updated_v{stamp}.json")
    return {
        "ok": True,
        "restored": True,
        "version": version,
        "new_version": stamp,
        "index_path": str(path),
        "record_count": len(data.get("records") or []),
        "writes": 1,
        "dhis2_writes": 0,
    }


__all__ = [
    "CONFIRM_APPLY",
    "CONFIRM_RESTORE",
    "apply_with_confirmation",
    "compare_versions",
    "confirm_phrase_for_apply",
    "confirm_phrase_for_restore",
    "enrich_controlled_preview",
    "list_versions",
    "restore_with_confirmation",
]
