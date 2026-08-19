"""Google Drive readonly constants for AiriX context (no Drive Center)."""

from __future__ import annotations

from hub.email.models import DRIVE_SCOPES, normalize_workspace

DEFAULT_PAGE_SIZE = 8
MAX_PAGE_SIZE = 12
MAX_EXPORT_CHARS = 2_400

FORBIDDEN_DRIVE_ACTIONS = (
    "create",
    "upload",
    "update",
    "delete",
    "trash",
    "move",
    "copy",
    "share",
    "permission",
    "patch",
    "insert",
)

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"
EXPORTABLE_MIME = {
    GOOGLE_DOC_MIME: "text/plain",
    GOOGLE_SHEET_MIME: "text/csv",
    GOOGLE_SLIDE_MIME: "text/plain",
}

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DRIVE_SCOPES",
    "EXPORTABLE_MIME",
    "FORBIDDEN_DRIVE_ACTIONS",
    "GOOGLE_DOC_MIME",
    "GOOGLE_SHEET_MIME",
    "GOOGLE_SLIDE_MIME",
    "MAX_EXPORT_CHARS",
    "MAX_PAGE_SIZE",
    "normalize_workspace",
]
