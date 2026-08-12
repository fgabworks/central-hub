"""ARCTIC constants and normalizers."""

from __future__ import annotations

from typing import Any

# CLIMATE naming (documentation / UI labels — not separate codebases).
CLIMATE_SYSTEM = "CLIMATE"
CLIMATE_WORK = "VANTA"
CLIMATE_PERSONAL = "ARCTIC"
CLIMATE_AI = "AiriX"
CLIMATE_RESERVED = "ECLIPSE"

ARCTIC_WORKSPACE = "personal"  # Always Personal; never Work/VANTA.

PRIMARY_ROLES = (
    "cv",
    "profile_photo",
    "signature",
    "cover_letter",
    "portfolio",
    "diploma",
    "transcript",
    "employment_certificate",
)

PRIMARY_ROLE_LABELS = {
    "cv": "CV / Resume",
    "profile_photo": "Profile photo",
    "signature": "Signature",
    "cover_letter": "Cover letter",
    "portfolio": "Portfolio",
    "diploma": "Diploma",
    "transcript": "Transcript",
    "employment_certificate": "Employment certificate",
}

# Career Pack is a logical view over registry entries (not a folder).
CAREER_PACK_ROLES = frozenset(
    {
        "cv",
        "cover_letter",
        "portfolio",
        "diploma",
        "transcript",
        "employment_certificate",
        "profile_photo",
        "signature",
    }
)

SOURCE_TYPES = ("local", "google_drive")

SOURCE_TYPE_LABELS = {
    "local": "Local",
    "google_drive": "Google Drive",
}

SOURCE_STATUS = ("ready", "deferred", "unavailable", "error")

# Sensitive kinds never stored in ARCTIC registry.
BLOCKED_SENSITIVE_KINDS = frozenset(
    {
        "password",
        "passwords",
        "otp",
        "2fa",
        "totp",
        "banking",
        "bank",
        "credential",
        "credentials",
        "auth_secret",
        "private_key",
        "ssh_key",
        "api_key",
        "token",
    }
)

SMART_COLLECTIONS = (
    {
        "id": "career_pack",
        "label": "Career Pack",
        "description": "Primary CV, letters, credentials, and portfolio (logical view).",
        "roles": list(CAREER_PACK_ROLES),
    },
    {
        "id": "identity",
        "label": "Identity",
        "description": "Photo, signature, and ID-style documents.",
        "roles": ["profile_photo", "signature"],
        "tags": ["id", "identity", "passport", "license"],
    },
    {
        "id": "certificates",
        "label": "Certificates",
        "description": "Diplomas, transcripts, and employment certificates.",
        "roles": ["diploma", "transcript", "employment_certificate"],
        "tags": ["certificate", "cert"],
    },
    {
        "id": "applications",
        "label": "Applications",
        "description": "CVs and cover letters for applications.",
        "roles": ["cv", "cover_letter"],
        "tags": ["application", "job"],
    },
)

ATTENTION_REASONS = (
    "missing_primary_cv",
    "missing_profile_photo",
    "source_unavailable",
    "stale_reference",
    "duplicate_blocked",
)


def normalize_primary_role(value: str | None) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "resume": "cv",
        "curriculum_vitae": "cv",
        "photo": "profile_photo",
        "headshot": "profile_photo",
        "sig": "signature",
        "cover": "cover_letter",
        "letter": "cover_letter",
        "degree": "diploma",
        "employment": "employment_certificate",
        "employment_cert": "employment_certificate",
        "work_certificate": "employment_certificate",
    }
    key = aliases.get(key, key)
    return key if key in PRIMARY_ROLES else ""


def normalize_source_type(value: str | None) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"drive": "google_drive", "gdrive": "google_drive", "file": "local"}
    key = aliases.get(key, key)
    return key if key in SOURCE_TYPES else ""


def is_blocked_sensitive(kind_or_tag: str | None) -> bool:
    key = str(kind_or_tag or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not key:
        return False
    if key in BLOCKED_SENSITIVE_KINDS:
        return True
    return any(tok in key for tok in ("password", "otp", "banking", "credential", "private_key"))


def normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
        return list(dict.fromkeys(p for p in parts if p and not is_blocked_sensitive(p)))
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            tag = str(item or "").strip().lower()
            if tag and not is_blocked_sensitive(tag) and tag not in out:
                out.append(tag)
        return out
    return []
