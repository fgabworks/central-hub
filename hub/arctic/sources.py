"""Document source abstraction for ARCTIC (Local ready; Google Drive deferred)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hub.arctic.models import SOURCE_TYPE_LABELS, normalize_source_type


@dataclass(frozen=True)
class SourceDescriptor:
    id: str
    source_type: str
    label: str
    status: str
    detail: str
    root_path: str = ""
    sync_ready: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "label": self.label or SOURCE_TYPE_LABELS.get(self.source_type, self.source_type),
            "status": self.status,
            "detail": self.detail,
            "root_path": self.root_path,
            "sync_ready": self.sync_ready,
        }


class DocumentSource(Protocol):
    source_type: str

    def descriptor(self) -> SourceDescriptor: ...

    def normalize_ref(self, ref: str) -> str: ...

    def validate_ref(self, ref: str) -> tuple[bool, str]: ...

    def exists(self, ref: str) -> bool: ...


class LocalDocumentSource:
    """References files on disk — never copies content into ARCTIC."""

    source_type = "local"

    def __init__(self, root_path: str = "") -> None:
        self.root_path = (root_path or "").strip()

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            id="local",
            source_type="local",
            label="Local files",
            status="ready",
            detail="References local paths; files stay on disk.",
            root_path=self.root_path,
            sync_ready=True,
        )

    def normalize_ref(self, ref: str) -> str:
        raw = (ref or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve())
        except OSError:
            return str(Path(raw).expanduser())

    def validate_ref(self, ref: str) -> tuple[bool, str]:
        path = self.normalize_ref(ref)
        if not path:
            return False, "Local path is required"
        p = Path(path)
        if not p.exists():
            return False, f"Local path not found: {path}"
        if not p.is_file():
            return False, "Local reference must be a file (not a folder)"
        return True, ""

    def exists(self, ref: str) -> bool:
        ok, _ = self.validate_ref(ref)
        return ok


class GoogleDriveDocumentSource:
    """Placeholder source — full Drive sync deferred until OAuth scopes exist."""

    source_type = "google_drive"

    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            id="google_drive",
            source_type="google_drive",
            label="Google Drive",
            status="deferred",
            detail=(
                "Google Drive sync is deferred. You may register Drive file IDs as "
                "references now; full browse/sync will land when Drive OAuth is ready."
            ),
            root_path="",
            sync_ready=False,
        )

    def normalize_ref(self, ref: str) -> str:
        return str(ref or "").strip()

    def validate_ref(self, ref: str) -> tuple[bool, str]:
        key = self.normalize_ref(ref)
        if not key:
            return False, "Google Drive file id or URL is required"
        if len(key) < 3:
            return False, "Google Drive reference looks invalid"
        return True, ""

    def exists(self, ref: str) -> bool:
        # Deferred: accept well-formed refs without live API checks.
        ok, _ = self.validate_ref(ref)
        return ok


def get_source(source_type: str, *, root_path: str = "") -> DocumentSource:
    kind = normalize_source_type(source_type)
    if kind == "local":
        return LocalDocumentSource(root_path=root_path)
    if kind == "google_drive":
        return GoogleDriveDocumentSource()
    raise ValueError(f"Unknown ARCTIC source type: {source_type}")


def list_source_descriptors() -> list[SourceDescriptor]:
    return [
        LocalDocumentSource().descriptor(),
        GoogleDriveDocumentSource().descriptor(),
    ]
