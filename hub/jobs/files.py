"""Upload / result file helpers (Phase 5)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
_ALLOWED_SUFFIXES = frozenset({".txt", ".csv", ".json", ".log", ".md"})


class FileSafetyError(ValueError):
    pass


def save_upload(job_input_dir: Path, upload: FileStorage) -> dict[str, Any]:
    if not upload or not upload.filename:
        raise FileSafetyError("No file uploaded")
    name = secure_filename(upload.filename)
    if not name or not _SAFE_NAME.match(name):
        raise FileSafetyError("Unsafe filename")
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise FileSafetyError(f"File type not allowed: {suffix or '(none)'}")
    job_input_dir.mkdir(parents=True, exist_ok=True)
    dest = (job_input_dir / name).resolve()
    try:
        dest.relative_to(job_input_dir.resolve())
    except ValueError as exc:
        raise FileSafetyError("Upload path escape blocked") from exc

    # Stream with size cap
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
                raise FileSafetyError(f"Upload exceeds {_MAX_UPLOAD_BYTES} bytes")
            handle.write(chunk)
    return {"filename": name, "path": str(dest), "size": size}


def list_artifacts(result_dir: Path) -> list[dict[str, Any]]:
    if not result_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(result_dir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(result_dir))
            items.append({"name": rel, "size": path.stat().st_size, "path": str(path)})
    return items


def resolve_download(result_dir: Path, relative_name: str) -> Path:
    name = relative_name.replace("\\", "/").lstrip("/")
    if ".." in Path(name).parts or not name:
        raise FileSafetyError("Invalid artifact name")
    target = (result_dir / name).resolve()
    try:
        target.relative_to(result_dir.resolve())
    except ValueError as exc:
        raise FileSafetyError("Artifact path escape blocked") from exc
    if not target.is_file():
        raise FileSafetyError("Artifact not found")
    return target


def collect_results(result_dir: Path, archive_name: str = "results") -> Path | None:
    """Optional zip of results directory; returns zip path or None if empty."""
    artifacts = list_artifacts(result_dir)
    if not artifacts:
        return None
    base = result_dir.parent / archive_name
    zip_path = Path(shutil.make_archive(str(base), "zip", root_dir=result_dir))
    return zip_path
