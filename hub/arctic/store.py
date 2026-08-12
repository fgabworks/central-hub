"""ARCTIC store — Personal Profile + Document Registry (metadata only)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hub.arctic.db import ArcticDatabase, utcnow
from hub.arctic.models import (
    CAREER_PACK_ROLES,
    PRIMARY_ROLE_LABELS,
    SMART_COLLECTIONS,
    is_blocked_sensitive,
    normalize_primary_role,
    normalize_source_type,
    normalize_tags,
)
from hub.arctic.sources import get_source, list_source_descriptors


class ArcticError(Exception):
    def __init__(self, message: str, *, code: str = "arctic_error") -> None:
        super().__init__(message)
        self.code = code


class ArcticStore:
    def __init__(self, db: ArcticDatabase | None = None) -> None:
        self.db = db or ArcticDatabase()

    # --- Profile ---

    def get_profile(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM arctic_profile WHERE id = 'personal'"
            ).fetchone()
        if not row:
            return self._empty_profile()
        return self._profile_public(row)

    def update_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_profile()
        display_name = str(payload.get("display_name", current["display_name"]) or "").strip()
        headline = str(payload.get("headline", current["headline"]) or "").strip()
        email = str(payload.get("email", current["email"]) or "").strip()
        phone = str(payload.get("phone", current["phone"]) or "").strip()
        location = str(payload.get("location", current["location"]) or "").strip()
        summary = str(payload.get("summary", current["summary"]) or "").strip()
        links = payload.get("links", current.get("links") or [])
        skills = payload.get("skills", current.get("skills") or [])
        if not isinstance(links, list):
            links = []
        if not isinstance(skills, list):
            skills = []
        # Never store auth secrets in profile fields.
        for field in (display_name, headline, email, phone, location, summary):
            if is_blocked_sensitive(field):
                raise ArcticError(
                    "Profile fields cannot contain passwords, OTPs, or banking credentials",
                    code="sensitive_blocked",
                )
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO arctic_profile
                    (id, display_name, headline, email, phone, location, summary,
                     links_json, skills_json, updated_at)
                VALUES ('personal', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    headline = excluded.headline,
                    email = excluded.email,
                    phone = excluded.phone,
                    location = excluded.location,
                    summary = excluded.summary,
                    links_json = excluded.links_json,
                    skills_json = excluded.skills_json,
                    updated_at = excluded.updated_at
                """,
                (
                    display_name,
                    headline,
                    email,
                    phone,
                    location,
                    summary,
                    json.dumps(links, ensure_ascii=False),
                    json.dumps([str(s).strip() for s in skills if str(s).strip()], ensure_ascii=False),
                    now,
                ),
            )
        return self.get_profile()

    # --- Sources ---

    def list_sources(self) -> list[dict[str, Any]]:
        descriptors = {d.source_type: d.public() for d in list_source_descriptors()}
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM arctic_sources ORDER BY source_type"
            ).fetchall()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            st = str(row["source_type"])
            seen.add(st)
            base = descriptors.get(st, {})
            out.append(
                {
                    **base,
                    "id": row["id"],
                    "label": row["label"] or base.get("label") or st,
                    "status": row["status"] or base.get("status") or "deferred",
                    "detail": row["detail"] or base.get("detail") or "",
                    "root_path": row["root_path"] or "",
                    "last_checked_at": row["last_checked_at"],
                    "updated_at": row["updated_at"],
                }
            )
        for st, desc in descriptors.items():
            if st not in seen:
                out.append(desc)
        return out

    def refresh_sources(self) -> list[dict[str, Any]]:
        now = utcnow()
        for desc in list_source_descriptors():
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO arctic_sources
                        (id, source_type, label, status, detail, root_path,
                         last_checked_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        label = excluded.label,
                        status = excluded.status,
                        detail = excluded.detail,
                        last_checked_at = excluded.last_checked_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        desc.id,
                        desc.source_type,
                        desc.label,
                        desc.status,
                        desc.detail,
                        desc.root_path,
                        now,
                        now,
                    ),
                )
        return self.list_sources()

    # --- Documents ---

    def list_documents(
        self,
        *,
        primary_role: str | None = None,
        source_type: str | None = None,
        tag: str | None = None,
        favorite_only: bool = False,
        collection: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        role = normalize_primary_role(primary_role) if primary_role else ""
        src = normalize_source_type(source_type) if source_type else ""
        tag_n = str(tag or "").strip().lower()
        coll = str(collection or "").strip().lower()
        query = str(q or "").strip().lower()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM arctic_documents
                ORDER BY
                    CASE WHEN last_accessed_at IS NULL OR last_accessed_at = '' THEN 1 ELSE 0 END,
                    last_accessed_at DESC,
                    updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        docs = [self._document_public(r) for r in rows]
        if role:
            docs = [d for d in docs if d.get("primary_role") == role]
        if src:
            docs = [d for d in docs if d.get("source_type") == src]
        if favorite_only:
            docs = [d for d in docs if d.get("is_favorite")]
        if tag_n:
            docs = [d for d in docs if tag_n in (d.get("tags") or [])]
        if coll == "career_pack":
            docs = [d for d in docs if d.get("primary_role") in CAREER_PACK_ROLES]
        elif coll:
            meta = next((c for c in SMART_COLLECTIONS if c["id"] == coll), None)
            if meta:
                roles = set(meta.get("roles") or [])
                tags = set(meta.get("tags") or [])
                docs = [
                    d
                    for d in docs
                    if d.get("primary_role") in roles
                    or any(t in tags for t in (d.get("tags") or []))
                ]
        if query:
            docs = [
                d
                for d in docs
                if query in str(d.get("title") or "").lower()
                or query in str(d.get("source_ref") or "").lower()
                or query in str(d.get("notes") or "").lower()
                or any(query in t for t in (d.get("tags") or []))
            ]
        return docs

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        key = str(doc_id or "").strip()
        if not key:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM arctic_documents WHERE id = ?", (key,)
            ).fetchone()
        return self._document_public(row) if row else None

    def find_by_source(self, source_type: str, source_ref: str) -> dict[str, Any] | None:
        src = normalize_source_type(source_type)
        if not src:
            return None
        source = get_source(src)
        ref = source.normalize_ref(source_ref)
        if not ref:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM arctic_documents
                WHERE source_type = ? AND source_ref = ?
                """,
                (src, ref),
            ).fetchone()
        return self._document_public(row) if row else None

    def register_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        src = normalize_source_type(payload.get("source_type"))
        if not src:
            raise ArcticError("source_type must be local or google_drive", code="invalid_source")
        source = get_source(src)
        ref = source.normalize_ref(str(payload.get("source_ref") or ""))
        ok, detail = source.validate_ref(ref)
        if not ok and src == "local":
            # Allow registering a planned path that does not yet exist, but mark attention.
            if not ref:
                raise ArcticError(detail or "Invalid local reference", code="invalid_ref")
            needs_attention = True
            attention_reason = "stale_reference"
        elif not ok:
            raise ArcticError(detail or "Invalid source reference", code="invalid_ref")
        else:
            needs_attention = bool(payload.get("needs_attention"))
            attention_reason = str(payload.get("attention_reason") or "").strip()

        existing = self.find_by_source(src, ref)
        if existing:
            raise ArcticError(
                "Document already registered for this source reference (no duplication)",
                code="duplicate",
            )

        role = normalize_primary_role(payload.get("primary_role"))
        tags = normalize_tags(payload.get("tags"))
        for tag in tags:
            if is_blocked_sensitive(tag):
                raise ArcticError(
                    "Sensitive credential tags are not allowed",
                    code="sensitive_blocked",
                )
        title = str(payload.get("title") or "").strip() or Path_name(ref)
        mime = str(payload.get("mime_type") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        if is_blocked_sensitive(notes) or is_blocked_sensitive(title):
            raise ArcticError(
                "Passwords, OTPs, and banking credentials cannot be stored",
                code="sensitive_blocked",
            )

        doc_id = uuid.uuid4().hex
        now = utcnow()
        with self.db.connect() as conn:
            if role:
                # Primary role replacement: only one primary per role.
                conn.execute(
                    """
                    UPDATE arctic_documents
                    SET primary_role = '', updated_at = ?
                    WHERE primary_role = ?
                    """,
                    (now, role),
                )
            conn.execute(
                """
                INSERT INTO arctic_documents (
                    id, title, source_type, source_ref, source_label, mime_type,
                    primary_role, tags_json, is_favorite, needs_attention,
                    attention_reason, notes, last_accessed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    doc_id,
                    title,
                    src,
                    ref,
                    str(payload.get("source_label") or source.descriptor().label),
                    mime,
                    role,
                    json.dumps(tags, ensure_ascii=False),
                    1 if payload.get("is_favorite") else 0,
                    1 if needs_attention else 0,
                    attention_reason,
                    notes,
                    now,
                    now,
                ),
            )
        doc = self.get_document(doc_id)
        assert doc is not None
        return doc

    def update_document(self, doc_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_document(doc_id)
        if not current:
            raise ArcticError("Document not found", code="not_found")
        title = str(payload.get("title", current["title"]) or "").strip()
        notes = str(payload.get("notes", current.get("notes") or "") or "").strip()
        mime = str(payload.get("mime_type", current.get("mime_type") or "") or "").strip()
        tags = normalize_tags(payload["tags"] if "tags" in payload else current.get("tags"))
        role_in = payload.get("primary_role", current.get("primary_role"))
        role = normalize_primary_role(role_in) if role_in else ""
        if "primary_role" in payload and not str(payload.get("primary_role") or "").strip():
            role = ""
        favorite = current.get("is_favorite")
        if "is_favorite" in payload:
            favorite = bool(payload.get("is_favorite"))
        if is_blocked_sensitive(title) or is_blocked_sensitive(notes):
            raise ArcticError(
                "Passwords, OTPs, and banking credentials cannot be stored",
                code="sensitive_blocked",
            )
        now = utcnow()
        with self.db.connect() as conn:
            if role and role != current.get("primary_role"):
                conn.execute(
                    """
                    UPDATE arctic_documents
                    SET primary_role = '', updated_at = ?
                    WHERE primary_role = ? AND id != ?
                    """,
                    (now, role, current["id"]),
                )
            conn.execute(
                """
                UPDATE arctic_documents SET
                    title = ?, mime_type = ?, primary_role = ?, tags_json = ?,
                    is_favorite = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    mime,
                    role,
                    json.dumps(tags, ensure_ascii=False),
                    1 if favorite else 0,
                    notes,
                    now,
                    current["id"],
                ),
            )
        updated = self.get_document(current["id"])
        assert updated is not None
        return updated

    def set_primary_role(self, doc_id: str, role: str) -> dict[str, Any]:
        role_n = normalize_primary_role(role)
        if not role_n:
            raise ArcticError("Invalid primary role", code="invalid_role")
        return self.update_document(doc_id, {"primary_role": role_n})

    def touch_accessed(self, doc_id: str) -> dict[str, Any] | None:
        key = str(doc_id or "").strip()
        if not key:
            return None
        now = utcnow()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE arctic_documents
                SET last_accessed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, key),
            )
        return self.get_document(key)

    def delete_document(self, doc_id: str) -> bool:
        key = str(doc_id or "").strip()
        if not key:
            return False
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM arctic_documents WHERE id = ?", (key,))
            return cur.rowcount > 0

    def get_primary(self, role: str) -> dict[str, Any] | None:
        role_n = normalize_primary_role(role)
        if not role_n:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM arctic_documents
                WHERE primary_role = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (role_n,),
            ).fetchone()
        return self._document_public(row) if row else None

    def latest_cv(self) -> dict[str, Any] | None:
        """Resolve 'latest CV' to the Primary CV registry entry."""
        return self.get_primary("cv")

    def career_pack(self) -> dict[str, Any]:
        docs = self.list_documents(collection="career_pack")
        primaries = {
            role: self.get_primary(role)
            for role in sorted(CAREER_PACK_ROLES)
        }
        return {
            "id": "career_pack",
            "label": "Career Pack",
            "logical_view": True,
            "note": "Career Pack uses existing registry entries — not a separate folder.",
            "primaries": {k: v for k, v in primaries.items() if v},
            "documents": docs,
            "count": len(docs),
        }

    def favorites(self, *, limit: int = 12) -> list[dict[str, Any]]:
        return self.list_documents(favorite_only=True, limit=limit)

    def recent(self, *, limit: int = 12) -> list[dict[str, Any]]:
        docs = self.list_documents(limit=max(limit * 2, 24))
        accessed = [d for d in docs if d.get("last_accessed_at")]
        accessed.sort(key=lambda d: str(d.get("last_accessed_at") or ""), reverse=True)
        return accessed[:limit]

    def needs_attention(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.latest_cv():
            items.append(
                {
                    "reason": "missing_primary_cv",
                    "label": "Primary CV missing",
                    "detail": "Set a Primary CV so “latest CV” resolves correctly.",
                }
            )
        if not self.get_primary("profile_photo"):
            items.append(
                {
                    "reason": "missing_profile_photo",
                    "label": "Profile photo missing",
                    "detail": "Register and mark a primary profile photo.",
                }
            )
        for doc in self.list_documents(limit=200):
            if doc.get("needs_attention"):
                items.append(
                    {
                        "reason": doc.get("attention_reason") or "needs_attention",
                        "label": doc.get("title") or doc.get("id"),
                        "detail": doc.get("attention_reason") or "Needs review",
                        "document_id": doc.get("id"),
                    }
                )
            elif doc.get("source_type") == "local":
                source = get_source("local")
                if not source.exists(str(doc.get("source_ref") or "")):
                    items.append(
                        {
                            "reason": "stale_reference",
                            "label": doc.get("title") or doc.get("id"),
                            "detail": "Local path no longer exists",
                            "document_id": doc.get("id"),
                        }
                    )
        for src in self.list_sources():
            if src.get("status") in {"unavailable", "error"}:
                items.append(
                    {
                        "reason": "source_unavailable",
                        "label": src.get("label") or src.get("id"),
                        "detail": src.get("detail") or "Source unavailable",
                    }
                )
        # De-dupe by reason+label
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in items:
            key = f"{item.get('reason')}:{item.get('label')}:{item.get('document_id') or ''}"
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def dashboard(self) -> dict[str, Any]:
        profile = self.get_profile()
        primaries = {
            role: self.get_primary(role)
            for role in (
                "cv",
                "profile_photo",
                "signature",
                "cover_letter",
                "portfolio",
            )
        }
        return {
            "climate": {
                "system": "CLIMATE",
                "section": "ARCTIC",
                "work": "VANTA",
                "ai": "AiriX",
                "reserved": "ECLIPSE",
            },
            "profile": profile,
            "quick_access": {k: v for k, v in primaries.items() if v},
            "latest_cv": self.latest_cv(),
            "sources": self.list_sources(),
            "collections": SMART_COLLECTIONS,
            "recent": self.recent(limit=8),
            "favorites": self.favorites(limit=8),
            "needs_attention": self.needs_attention(),
            "career_pack": self.career_pack(),
            "role_labels": PRIMARY_ROLE_LABELS,
            "document_count": len(self.list_documents(limit=500)),
        }

    def _empty_profile(self) -> dict[str, Any]:
        return {
            "id": "personal",
            "display_name": "",
            "headline": "",
            "email": "",
            "phone": "",
            "location": "",
            "summary": "",
            "links": [],
            "skills": [],
            "updated_at": "",
            "workspace": "personal",
            "climate_section": "ARCTIC",
        }

    def _profile_public(self, row: Any) -> dict[str, Any]:
        links = _json_list(row["links_json"])
        skills = _json_list(row["skills_json"])
        return {
            "id": "personal",
            "display_name": row["display_name"] or "",
            "headline": row["headline"] or "",
            "email": row["email"] or "",
            "phone": row["phone"] or "",
            "location": row["location"] or "",
            "summary": row["summary"] or "",
            "links": links,
            "skills": skills,
            "updated_at": row["updated_at"] or "",
            "workspace": "personal",
            "climate_section": "ARCTIC",
        }

    def _document_public(self, row: Any) -> dict[str, Any]:
        role = row["primary_role"] or ""
        return {
            "id": row["id"],
            "title": row["title"] or "",
            "source_type": row["source_type"],
            "source_ref": row["source_ref"],
            "source_label": row["source_label"] or "",
            "mime_type": row["mime_type"] or "",
            "primary_role": role,
            "primary_role_label": PRIMARY_ROLE_LABELS.get(role, role),
            "tags": _json_list(row["tags_json"]),
            "is_favorite": bool(row["is_favorite"]),
            "needs_attention": bool(row["needs_attention"]),
            "attention_reason": row["attention_reason"] or "",
            "notes": row["notes"] or "",
            "last_accessed_at": row["last_accessed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "workspace": "personal",
            "climate_section": "ARCTIC",
            # Content is never embedded — reference only.
            "content_embedded": False,
        }


def _json_list(raw: Any) -> list[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def Path_name(ref: str) -> str:
    text = str(ref or "").strip().replace("\\", "/")
    if not text:
        return "Untitled"
    return text.rstrip("/").split("/")[-1] or "Untitled"
