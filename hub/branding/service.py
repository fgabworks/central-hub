"""Store a CLIMATE logo on disk and keep display/fit prefs in JSON (not base64)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hub.settings import ROOT_DIR

DISPLAYS = ("icon", "wordmark", "full")
FITS = ("contain", "cover")
DEFAULT_DISPLAY = "wordmark"
DEFAULT_FIT = "contain"
DEFAULT_ICON = "img/climate-mark.png"
DEFAULT_FULL = "img/climate-logo.png"
MAX_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_MAGIC = b"WEBP"
_SVG_DENIED = (
    "<script",
    "</script",
    "javascript:",
    "onload=",
    "onerror=",
    "foreignobject",
)


class BrandingError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class BrandingService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or (ROOT_DIR / "data" / "branding")).resolve()
        self.settings_path = self.root / "settings.json"

    def state(self) -> dict[str, Any]:
        data = self._load()
        custom = bool(data.get("filename")) and self._custom_path(str(data.get("filename") or "")).is_file()
        filename = str(data.get("filename") or "") if custom else ""
        version = "default"
        if custom:
            try:
                version = str(int(self._custom_path(filename).stat().st_mtime_ns))
            except OSError:
                version = "custom"
        return {
            "display": self._display(data.get("display")),
            "avatar_display": "icon",
            "fit": self._fit(data.get("fit")),
            "custom": custom,
            "filename": filename,
            "original_name": str(data.get("original_name") or "") if custom else "",
            "content_type": str(data.get("content_type") or "image/png") if custom else "image/png",
            "version": version,
            "default_icon": DEFAULT_ICON,
            "default_full": DEFAULT_FULL,
        }

    def save(
        self,
        *,
        display: str | None = None,
        fit: str | None = None,
        payload: bytes | None = None,
        filename: str = "",
    ) -> dict[str, Any]:
        data = self._load()
        if display is not None:
            data["display"] = self._display(display)
        if fit is not None:
            data["fit"] = self._fit(fit)
        if payload is not None:
            suffix, content_type = self._validate_upload(payload)
            self.root.mkdir(parents=True, exist_ok=True)
            stored = f"logo{suffix}"
            dest = self._custom_path(stored)
            dest.write_bytes(payload)
            self._purge_other_logos(stored)
            data["filename"] = stored
            data["original_name"] = Path(filename or stored).name[:160]
            data["content_type"] = content_type
        self._write(data)
        return self.state()

    def reset(self) -> dict[str, Any]:
        if self.root.is_dir():
            for path in self.root.glob("logo.*"):
                path.unlink(missing_ok=True)
        self._write({"display": DEFAULT_DISPLAY, "fit": DEFAULT_FIT})
        return self.state()

    def logo_file(self) -> tuple[Path, str]:
        data = self.state()
        if not data["custom"]:
            raise BrandingError("No custom logo is stored", code="not_found")
        path = self._custom_path(str(data["filename"]))
        if not path.is_file():
            raise BrandingError("No custom logo is stored", code="not_found")
        return path, str(data["content_type"] or "application/octet-stream")

    def _load(self) -> dict[str, Any]:
        if not self.settings_path.is_file():
            return {"display": DEFAULT_DISPLAY, "fit": DEFAULT_FIT}
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"display": DEFAULT_DISPLAY, "fit": DEFAULT_FIT}
        if not isinstance(raw, dict):
            return {"display": DEFAULT_DISPLAY, "fit": DEFAULT_FIT}
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "display": self._display(data.get("display")),
            "fit": self._fit(data.get("fit")),
            "filename": str(data.get("filename") or ""),
            "original_name": str(data.get("original_name") or ""),
            "content_type": str(data.get("content_type") or ""),
        }
        self.settings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    def _custom_path(self, filename: str) -> Path:
        name = Path(str(filename or "")).name
        if name not in {f"logo{suffix}" for suffix in ALLOWED_TYPES}:
            return self.root / "__missing__"
        dest = (self.root / name).resolve()
        try:
            dest.relative_to(self.root)
        except ValueError:
            return self.root / "__missing__"
        return dest

    def _purge_other_logos(self, keep: str) -> None:
        for path in self.root.glob("logo.*"):
            if path.name != keep:
                path.unlink(missing_ok=True)

    def _display(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return raw if raw in DISPLAYS else DEFAULT_DISPLAY

    def _fit(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return raw if raw in FITS else DEFAULT_FIT

    def _validate_upload(self, payload: bytes) -> tuple[str, str]:
        if not payload:
            raise BrandingError("Choose a PNG, SVG, or WEBP logo", code="file_required")
        if len(payload) > MAX_BYTES:
            raise BrandingError("Logo must be 2 MB or smaller", code="too_large")
        sniffed = self._sniff(payload)
        if sniffed not in ALLOWED_TYPES:
            raise BrandingError("Logo must be PNG, SVG, or WEBP", code="type_unsupported")
        if sniffed == ".svg":
            self._validate_svg(payload)
        return sniffed, ALLOWED_TYPES[sniffed]

    def _sniff(self, payload: bytes) -> str:
        head = payload[:64]
        if head.startswith(_PNG_MAGIC):
            return ".png"
        if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == _WEBP_MAGIC:
            return ".webp"
        text = payload[:256].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if text.startswith(b"<svg") or text.startswith(b"<?xml"):
            return ".svg"
        return ""

    def _validate_svg(self, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BrandingError("SVG logo must be UTF-8 text", code="type_unsupported") from exc
        lowered = text.lower()
        if "<svg" not in lowered:
            raise BrandingError("SVG logo is not a valid image", code="type_unsupported")
        if any(token in lowered for token in _SVG_DENIED):
            raise BrandingError("SVG logo contains unsupported markup", code="type_unsupported")
