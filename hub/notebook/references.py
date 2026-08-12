"""Official References — Work Notebook library for memoranda, advisories, guidelines.

Files are stored under data/work-notebook/references/{year}/.
Metadata lives in notebook.db. Work scope only.
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from hub.notebook.db import NotebookDatabase, utcnow
from hub.notebook.subject_detect import detect_subject_from_text, normalize_subject
from hub.notebook.text_extract import extract_text_from_bytes, extract_text_from_path
from hub.settings import ROOT_DIR

REFERENCE_TYPES = (
    "department_memorandum",
    "advisory",
    "guideline",
    "other",
)

REFERENCE_TYPE_LABELS = {
    "department_memorandum": "Department Memoranda",
    "advisory": "Advisories",
    "guideline": "Guidelines",
    "other": "Other References",
}

REFERENCE_TYPE_ORDER = {key: idx for idx, key in enumerate(REFERENCE_TYPES)}

STORAGE_FILE = "file"
STORAGE_LINK = "link"
STORAGE_BOTH = "file_and_link"

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB — official PDFs
_ALLOWED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})")


class ReferenceError(ValueError):
    def __init__(self, message: str, *, code: str = "reference_error") -> None:
        super().__init__(message)
        self.code = code


def default_references_root() -> Path:
    return ROOT_DIR / "data" / "work-notebook" / "references"


def normalize_reference_type(value: str | None) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "memorandum": "department_memorandum",
        "memo": "department_memorandum",
        "dm": "department_memorandum",
        "department_memoranda": "department_memorandum",
        "advisories": "advisory",
        "guidelines": "guideline",
        "guide": "guideline",
        "other_references": "other",
        "misc": "other",
    }
    key = aliases.get(key, key)
    return key if key in REFERENCE_TYPES else ""


def infer_year_from_text(*parts: str) -> int | None:
    """Prefer the last 19xx/20xx token found in filename/title text."""
    blob = " ".join(str(p or "") for p in parts)
    matches = _YEAR_RE.findall(blob)
    if not matches:
        return None
    try:
        year = int(matches[-1])
    except ValueError:
        return None
    current = datetime.now(timezone.utc).year + 1
    if 1990 <= year <= current:
        return year
    return None


def infer_type_from_text(*parts: str) -> str:
    blob = " ".join(str(p or "") for p in parts).lower()
    if any(tok in blob for tok in ("memorandum", "memo", " dm ", "dm-", "dm_")):
        return "department_memorandum"
    if "advisor" in blob:
        return "advisory"
    if any(tok in blob for tok in ("guideline", "guidelines", "guide")):
        return "guideline"
    return ""


def title_from_filename(filename: str) -> str:
    name = Path(str(filename or "").strip()).stem
    name = name.replace("_", " ").replace("-", " ").strip()
    return name or "Untitled reference"


def normalize_year(value: Any, *, fallback: int | None = None) -> int:
    try:
        year = int(str(value or "").strip())
    except (TypeError, ValueError):
        year = 0
    current = datetime.now(timezone.utc).year + 1
    if 1990 <= year <= current:
        return year
    if fallback and 1990 <= int(fallback) <= current:
        return int(fallback)
    return datetime.now(timezone.utc).year


class OfficialReferencesStore:
    def __init__(
        self,
        db: NotebookDatabase,
        *,
        root: Path | None = None,
    ) -> None:
        self.db = db
        self.root = root or default_references_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def year_dir(self, year: int) -> Path:
        path = (self.root / str(int(year))).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ReferenceError("Invalid references year path", code="path_escape") from exc
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list(
        self,
        *,
        year: int | None = None,
        ref_type: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        type_n = normalize_reference_type(ref_type) if ref_type else ""
        query = str(q or "").strip().lower()
        clauses = ["1=1"]
        params: list[Any] = []
        if year is not None:
            clauses.append("year = ?")
            params.append(int(year))
        if type_n:
            clauses.append("ref_type = ?")
            params.append(type_n)
        sql = f"""
            SELECT * FROM official_references
            WHERE {' AND '.join(clauses)}
            ORDER BY year DESC, ref_type ASC, title COLLATE NOCASE ASC, created_at DESC
        """
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        items = [self._public(row) for row in rows]
        if query:
            items = [
                item
                for item in items
                if query in str(item.get("title") or "").lower()
                or query in str(item.get("subject") or "").lower()
                or query in str(item.get("short_note") or "").lower()
                or query in str(item.get("original_filename") or "").lower()
                or query in str(item.get("external_url") or "").lower()
            ]
        return items

    def grouped(
        self,
        *,
        year: int | None = None,
        ref_type: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        items = self.list(year=year, ref_type=ref_type, q=q)
        by_year: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for item in items:
            y = int(item["year"])
            t = str(item["ref_type"])
            by_year.setdefault(y, {key: [] for key in REFERENCE_TYPES})
            by_year[y].setdefault(t, [])
            by_year[y][t].append(item)
        years = sorted(by_year.keys(), reverse=True)
        out: list[dict[str, Any]] = []
        for y in years:
            type_groups = []
            for type_key in REFERENCE_TYPES:
                refs = by_year[y].get(type_key) or []
                if not refs:
                    continue
                type_groups.append(
                    {
                        "type": type_key,
                        "label": REFERENCE_TYPE_LABELS[type_key],
                        "references": refs,
                        "count": len(refs),
                    }
                )
            if type_groups:
                out.append(
                    {
                        "year": y,
                        "types": type_groups,
                        "count": sum(g["count"] for g in type_groups),
                    }
                )
        return out

    def get(self, ref_id: str) -> dict[str, Any] | None:
        key = str(ref_id or "").strip()
        if not key:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM official_references WHERE id = ?", (key,)
            ).fetchone()
        return self._public(row) if row else None

    def create(
        self,
        *,
        title: str,
        ref_type: str,
        year: int | None = None,
        short_note: str = "",
        source_url: str = "",
        external_url: str = "",
        subject: str | None = None,
        subject_source: str | None = None,
        upload: FileStorage | None = None,
        actor: str = "owner",
    ) -> dict[str, Any]:
        type_n = normalize_reference_type(ref_type)
        if not type_n:
            raise ReferenceError("Type is required", code="type_required")

        filename = (upload.filename if upload and upload.filename else "") or ""
        inferred_year = infer_year_from_text(filename, title)
        year_n = normalize_year(year, fallback=inferred_year)

        ext_url = str(external_url or "").strip()
        src_url = str(source_url or "").strip()
        note = str(short_note or "").strip()[:500]
        title_n = str(title or "").strip() or title_from_filename(filename) or "Untitled reference"

        has_file = bool(upload and upload.filename)
        if not has_file and not ext_url:
            raise ReferenceError(
                "Provide a local file, an external link, or both",
                code="file_or_link_required",
            )

        storage = STORAGE_BOTH if has_file and ext_url else (STORAGE_FILE if has_file else STORAGE_LINK)
        ref_id = uuid.uuid4().hex
        now = utcnow()
        stored_name = ""
        original_name = ""
        size_bytes = 0
        mime = ""
        rel_path = ""
        subject_provided = subject is not None or subject_source is not None
        subject_n: str | None = normalize_subject(subject) if subject is not None else None
        subject_src = str(subject_source or "").strip().lower()
        if subject_src not in {"detected", "suggested", "manual"}:
            subject_src = "manual" if (subject_provided and subject_n) else ""

        if has_file:
            assert upload is not None
            saved = self._save_upload(upload, year=year_n, ref_id=ref_id)
            stored_name = saved["stored_name"]
            original_name = saved["original_filename"]
            size_bytes = int(saved["size_bytes"])
            mime = saved["mime_type"]
            rel_path = saved["relative_path"]
            if not str(title or "").strip():
                title_n = title_from_filename(original_name)
            # Auto-detect when caller did not supply a subject value.
            if not subject_provided:
                detected = self._detect_subject_from_saved(rel_path, original_name)
                if detected.get("subject"):
                    subject_n = detected["subject"]
                    subject_src = str(detected.get("subject_source") or "")
            elif subject_n and not subject_source:
                subject_src = "manual"

        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO official_references (
                    id, title, ref_type, year, short_note, source_url, external_url,
                    storage_kind, original_filename, stored_filename, relative_path,
                    mime_type, size_bytes, created_at, updated_at, created_by,
                    subject, subject_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    title_n,
                    type_n,
                    year_n,
                    note,
                    src_url,
                    ext_url,
                    storage,
                    original_name,
                    stored_name,
                    rel_path,
                    mime,
                    size_bytes,
                    now,
                    now,
                    (actor or "owner").strip() or "owner",
                    subject_n,
                    subject_src if subject_n else "",
                ),
            )
        item = self.get(ref_id)
        assert item is not None
        return item

    def update(self, ref_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get(ref_id)
        if not current:
            raise ReferenceError("Reference not found", code="not_found")

        title = str(payload.get("title", current["title"]) or "").strip() or current["title"]
        type_n = normalize_reference_type(payload.get("ref_type", current["ref_type"]))
        if not type_n:
            raise ReferenceError("Type is required", code="type_required")
        year_n = normalize_year(payload.get("year", current["year"]))
        note = str(payload.get("short_note", current.get("short_note") or "") or "").strip()[:500]
        src_url = str(payload.get("source_url", current.get("source_url") or "") or "").strip()
        ext_url = str(
            payload.get("external_url", current.get("external_url") or "") or ""
        ).strip()

        upload = payload.get("upload")
        has_new_file = isinstance(upload, FileStorage) and bool(upload.filename)
        rel_path = current.get("relative_path") or ""
        stored_name = current.get("stored_filename") or ""
        original_name = current.get("original_filename") or ""
        size_bytes = int(current.get("size_bytes") or 0)
        mime = current.get("mime_type") or ""

        subject_n = current.get("subject")
        subject_src = str(current.get("subject_source") or "")
        if "subject" in payload:
            subject_n = normalize_subject(payload.get("subject"))
            if subject_n is None:
                subject_src = ""
            elif payload.get("subject_source") in {"detected", "suggested", "manual"}:
                subject_src = str(payload.get("subject_source"))
            elif subject_n != current.get("subject"):
                subject_src = "manual"
            else:
                subject_src = subject_src or "manual"

        # Move file if year changed and an existing file is present.
        if rel_path and year_n != int(current["year"]) and not has_new_file:
            rel_path = self._relocate_file(current, year_n)

        if has_new_file:
            assert isinstance(upload, FileStorage)
            # Remove previous file if any.
            self._delete_file(current)
            saved = self._save_upload(upload, year=year_n, ref_id=current["id"])
            stored_name = saved["stored_name"]
            original_name = saved["original_filename"]
            size_bytes = int(saved["size_bytes"])
            mime = saved["mime_type"]
            rel_path = saved["relative_path"]
            if "subject" not in payload:
                detected = self._detect_subject_from_saved(rel_path, original_name)
                subject_n = detected.get("subject")
                subject_src = str(detected.get("subject_source") or "")

        has_file = bool(rel_path)
        if not has_file and not ext_url:
            raise ReferenceError(
                "Provide a local file, an external link, or both",
                code="file_or_link_required",
            )
        storage = STORAGE_BOTH if has_file and ext_url else (STORAGE_FILE if has_file else STORAGE_LINK)
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE official_references SET
                    title = ?, ref_type = ?, year = ?, short_note = ?,
                    source_url = ?, external_url = ?, storage_kind = ?,
                    original_filename = ?, stored_filename = ?, relative_path = ?,
                    mime_type = ?, size_bytes = ?, updated_at = ?,
                    subject = ?, subject_source = ?
                WHERE id = ?
                """,
                (
                    title,
                    type_n,
                    year_n,
                    note,
                    src_url,
                    ext_url,
                    storage,
                    original_name,
                    stored_name,
                    rel_path,
                    mime,
                    size_bytes,
                    now,
                    subject_n,
                    subject_src if subject_n else "",
                    current["id"],
                ),
            )
        updated = self.get(current["id"])
        assert updated is not None
        return updated

    def delete(self, ref_id: str) -> bool:
        current = self.get(ref_id)
        if not current:
            return False
        self._delete_file(current)
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM official_references WHERE id = ?", (current["id"],)
            )
            return cur.rowcount > 0

    def resolve_file(self, ref_id: str) -> Path:
        current = self.get(ref_id)
        if not current or not current.get("relative_path"):
            raise ReferenceError("File not found", code="file_not_found")
        rel = str(current["relative_path"]).replace("\\", "/").lstrip("/")
        if ".." in Path(rel).parts or not rel:
            raise ReferenceError("Invalid file path", code="path_escape")
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ReferenceError("File path escape blocked", code="path_escape") from exc
        if not target.is_file():
            raise ReferenceError("File missing on disk", code="file_missing")
        return target

    def available_years(self) -> list[int]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT year FROM official_references ORDER BY year DESC"
            ).fetchall()
        return [int(r["year"]) for r in rows]

    def suggest_from_upload(self, upload: FileStorage | None) -> dict[str, Any]:
        filename = (upload.filename if upload and upload.filename else "") or ""
        meta = {
            "title": title_from_filename(filename),
            "year": infer_year_from_text(filename),
            "ref_type": infer_type_from_text(filename),
            "original_filename": filename,
            "subject": None,
            "subject_source": "",
            "confidence": "none",
            "extract_reason": "",
        }
        if not upload or not upload.filename:
            return meta
        detected = self.detect_subject_from_upload(upload)
        meta.update(
            {
                "subject": detected.get("subject"),
                "subject_source": detected.get("subject_source") or "",
                "confidence": detected.get("confidence") or "none",
                "extract_reason": detected.get("extract_reason") or "",
            }
        )
        return meta

    def detect_subject_from_upload(self, upload: FileStorage) -> dict[str, Any]:
        """Bounded extract + subject detect for Quick Add / preview (rewinds stream)."""
        filename = (upload.filename or "").strip()
        data = upload.stream.read(256 * 1024)
        try:
            upload.stream.seek(0)
        except Exception:  # noqa: BLE001
            pass
        extracted = extract_text_from_bytes(data, filename=filename)
        if not extracted.get("ok"):
            return {
                "subject": None,
                "subject_source": "",
                "confidence": "none",
                "extract_reason": extracted.get("reason") or "empty_or_scanned",
            }
        detected = detect_subject_from_text(str(extracted.get("text") or ""))
        detected["extract_reason"] = ""
        return detected

    def _detect_subject_from_saved(self, relative_path: str, original_filename: str) -> dict[str, Any]:
        rel = str(relative_path or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return {"subject": None, "subject_source": "", "confidence": "none"}
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return {"subject": None, "subject_source": "", "confidence": "none"}
        extracted = extract_text_from_path(path, filename=original_filename)
        if not extracted.get("ok"):
            return {
                "subject": None,
                "subject_source": "",
                "confidence": "none",
                "extract_reason": extracted.get("reason") or "",
            }
        return detect_subject_from_text(str(extracted.get("text") or ""))

    def _save_upload(self, upload: FileStorage, *, year: int, ref_id: str) -> dict[str, Any]:
        if not upload or not upload.filename:
            raise ReferenceError("No file uploaded", code="no_file")
        original = upload.filename
        name = secure_filename(original)
        if not name or not _SAFE_NAME.match(name):
            # Fall back to a safe generated name while keeping extension when possible.
            suffix = Path(original).suffix.lower()
            if suffix not in _ALLOWED_SUFFIXES:
                suffix = ".bin"
            name = f"document{suffix}"
        suffix = Path(name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ReferenceError(f"File type not allowed: {suffix or '(none)'}", code="bad_type")

        year_path = self.year_dir(year)
        stored = f"{ref_id}_{name}"
        dest = (year_path / stored).resolve()
        try:
            dest.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ReferenceError("Upload path escape blocked", code="path_escape") from exc

        size = 0
        with dest.open("wb") as handle:
            while True:
                chunk = upload.stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    handle.close()
                    dest.unlink(missing_ok=True)
                    raise ReferenceError(
                        f"Upload exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
                        code="too_large",
                    )
                handle.write(chunk)

        rel = str(Path(str(year)) / stored).replace("\\", "/")
        mime = (upload.mimetype or "").strip()
        return {
            "stored_name": stored,
            "original_filename": Path(original).name,
            "relative_path": rel,
            "size_bytes": size,
            "mime_type": mime,
        }

    def _delete_file(self, item: dict[str, Any]) -> None:
        rel = str(item.get("relative_path") or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return
        if target.is_file():
            target.unlink(missing_ok=True)

    def _relocate_file(self, item: dict[str, Any], new_year: int) -> str:
        rel = str(item.get("relative_path") or "").replace("\\", "/").lstrip("/")
        if not rel:
            return ""
        src = (self.root / rel).resolve()
        try:
            src.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ReferenceError("File path escape blocked", code="path_escape") from exc
        if not src.is_file():
            return rel
        stored = src.name
        dest_dir = self.year_dir(new_year)
        dest = (dest_dir / stored).resolve()
        if dest != src:
            shutil.move(str(src), str(dest))
        return str(Path(str(new_year)) / stored).replace("\\", "/")

    def _public(self, row: Any) -> dict[str, Any]:
        ref_type = str(row["ref_type"] or "other")
        storage = str(row["storage_kind"] or "")
        source_url = str(row["source_url"] or "").strip()
        external_url = str(row["external_url"] or "").strip()
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        subject = None
        subject_source = ""
        if "subject" in keys:
            raw = row["subject"]
            subject = None if raw is None else (str(raw).strip() or None)
        if "subject_source" in keys:
            subject_source = str(row["subject_source"] or "").strip()
        return {
            "id": row["id"],
            "title": row["title"] or "",
            "subject": subject,
            "subject_source": subject_source if subject else "",
            "ref_type": ref_type,
            "type_label": REFERENCE_TYPE_LABELS.get(ref_type, ref_type),
            "year": int(row["year"] or 0),
            "short_note": row["short_note"] or "",
            "source_url": source_url,
            "external_url": external_url,
            "storage_kind": storage,
            "original_filename": row["original_filename"] or "",
            "stored_filename": row["stored_filename"] or "",
            "relative_path": row["relative_path"] or "",
            "mime_type": row["mime_type"] or "",
            "size_bytes": int(row["size_bytes"] or 0),
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
            "created_by": row["created_by"] or "",
            "has_file": bool(row["relative_path"]),
            "has_source": bool(source_url),
            "open_url": external_url if storage == STORAGE_LINK else "",
            "workspace": "work",
        }
