"""Store CLIMATE app logo and AiriX avatar on disk; prefs stay in JSON (not base64)."""

from __future__ import annotations

import json
import re
import struct
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
        logo = self._asset_info(data, prefix="logo", filename_key="filename", original_key="original_name", type_key="content_type")
        avatar = self._asset_info(
            data,
            prefix="avatar",
            filename_key="avatar_filename",
            original_key="avatar_original_name",
            type_key="avatar_content_type",
        )
        return {
            "display": self._display(data.get("display")),
            "avatar_display": "icon",
            "fit": self._fit(data.get("fit")),
            "custom": logo["custom"],
            "custom_logo": logo["custom"],
            "custom_avatar": avatar["custom"],
            "filename": logo["filename"],
            "original_name": logo["original_name"],
            "content_type": logo["content_type"],
            "version": logo["version"],
            "avatar_filename": avatar["filename"],
            "avatar_original_name": avatar["original_name"],
            "avatar_content_type": avatar["content_type"],
            "avatar_version": avatar["version"],
            "logo_width": logo["width"],
            "logo_height": logo["height"],
            "avatar_width": avatar["width"],
            "avatar_height": avatar["height"],
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
        avatar_payload: bytes | None = None,
        avatar_filename: str = "",
        remove_logo: bool = False,
        remove_avatar: bool = False,
    ) -> dict[str, Any]:
        data = self._load()
        if display is not None:
            data["display"] = self._display(display)
        data["fit"] = DEFAULT_FIT
        if payload is not None:
            self._store_asset(data, prefix="logo", payload=payload, filename=filename)
        elif remove_logo:
            self._clear_asset(data, prefix="logo", filename_key="filename", original_key="original_name", type_key="content_type")
        if avatar_payload is not None:
            self._store_asset(
                data,
                prefix="avatar",
                payload=avatar_payload,
                filename=avatar_filename,
                filename_key="avatar_filename",
                original_key="avatar_original_name",
                type_key="avatar_content_type",
            )
        elif remove_avatar:
            self._clear_asset(
                data,
                prefix="avatar",
                filename_key="avatar_filename",
                original_key="avatar_original_name",
                type_key="avatar_content_type",
            )
        self._write(data)
        return self.state()

    def reset(self) -> dict[str, Any]:
        if self.root.is_dir():
            for path in list(self.root.glob("logo.*")) + list(self.root.glob("avatar.*")):
                path.unlink(missing_ok=True)
        self._write({"display": DEFAULT_DISPLAY, "fit": DEFAULT_FIT})
        return self.state()

    def logo_file(self) -> tuple[Path, str]:
        return self._open_asset(self.state(), prefix="logo", filename_key="filename", type_key="content_type", missing="No custom logo is stored")

    def avatar_file(self) -> tuple[Path, str]:
        return self._open_asset(
            self.state(),
            prefix="avatar",
            filename_key="avatar_filename",
            type_key="avatar_content_type",
            missing="No custom AiriX avatar is stored",
        )

    def _asset_info(self, data: dict[str, Any], *, prefix: str, filename_key: str, original_key: str, type_key: str) -> dict[str, Any]:
        filename = str(data.get(filename_key) or "")
        custom = bool(filename) and self._custom_path(filename, prefix=prefix).is_file()
        stored = filename if custom else ""
        version = "default"
        if custom:
            try:
                version = str(int(self._custom_path(stored, prefix=prefix).stat().st_mtime_ns))
            except OSError:
                version = "custom"
        info = {
            "custom": custom,
            "filename": stored,
            "original_name": str(data.get(original_key) or "") if custom else "",
            "content_type": str(data.get(type_key) or "image/png") if custom else "image/png",
            "version": version,
            "width": None,
            "height": None,
        }
        if custom:
            try:
                width, height = image_size(self._custom_path(stored, prefix=prefix).read_bytes())
                info["width"] = width
                info["height"] = height
            except OSError:
                pass
        return info

    def _store_asset(
        self,
        data: dict[str, Any],
        *,
        prefix: str,
        payload: bytes,
        filename: str,
        filename_key: str = "filename",
        original_key: str = "original_name",
        type_key: str = "content_type",
    ) -> None:
        suffix, content_type = self._validate_upload(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        stored = f"{prefix}{suffix}"
        dest = self._custom_path(stored, prefix=prefix)
        dest.write_bytes(payload)
        self._purge_other(prefix, stored)
        data[filename_key] = stored
        data[original_key] = Path(filename or stored).name[:160]
        data[type_key] = content_type

    def _clear_asset(
        self,
        data: dict[str, Any],
        *,
        prefix: str,
        filename_key: str,
        original_key: str,
        type_key: str,
    ) -> None:
        if self.root.is_dir():
            for path in self.root.glob(f"{prefix}.*"):
                path.unlink(missing_ok=True)
        data[filename_key] = ""
        data[original_key] = ""
        data[type_key] = ""

    def _open_asset(
        self,
        state: dict[str, Any],
        *,
        prefix: str,
        filename_key: str,
        type_key: str,
        missing: str,
    ) -> tuple[Path, str]:
        filename = str(state.get(filename_key) or "")
        if not filename:
            raise BrandingError(missing, code="not_found")
        path = self._custom_path(filename, prefix=prefix)
        if not path.is_file():
            raise BrandingError(missing, code="not_found")
        return path, str(state.get(type_key) or "application/octet-stream")

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
            "avatar_filename": str(data.get("avatar_filename") or ""),
            "avatar_original_name": str(data.get("avatar_original_name") or ""),
            "avatar_content_type": str(data.get("avatar_content_type") or ""),
        }
        self.settings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    def _custom_path(self, filename: str, *, prefix: str = "logo") -> Path:
        name = Path(str(filename or "")).name
        allowed = {f"{prefix}{suffix}" for suffix in ALLOWED_TYPES}
        if name not in allowed:
            return self.root / "__missing__"
        dest = (self.root / name).resolve()
        try:
            dest.relative_to(self.root)
        except ValueError:
            return self.root / "__missing__"
        return dest

    def _purge_other(self, prefix: str, keep: str) -> None:
        for path in self.root.glob(f"{prefix}.*"):
            if path.name != keep:
                path.unlink(missing_ok=True)

    def _display(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return raw if raw in DISPLAYS else DEFAULT_DISPLAY

    def _fit(self, value: Any) -> str:
        return DEFAULT_FIT

    def _validate_upload(self, payload: bytes) -> tuple[str, str]:
        if not payload:
            raise BrandingError("Choose a PNG, SVG, or WEBP image", code="file_required")
        if len(payload) > MAX_BYTES:
            raise BrandingError("Image must be 2 MB or smaller", code="too_large")
        sniffed = self._sniff(payload)
        if sniffed not in ALLOWED_TYPES:
            raise BrandingError("Image must be PNG, SVG, or WEBP", code="type_unsupported")
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
            raise BrandingError("SVG image must be UTF-8 text", code="type_unsupported") from exc
        lowered = text.lower()
        if "<svg" not in lowered:
            raise BrandingError("SVG image is not a valid image", code="type_unsupported")
        if any(token in lowered for token in _SVG_DENIED):
            raise BrandingError("SVG image contains unsupported markup", code="type_unsupported")


def image_size(payload: bytes) -> tuple[int | None, int | None]:
    if not payload:
        return None, None
    if payload.startswith(_PNG_MAGIC) and len(payload) >= 24:
        width, height = struct.unpack(">II", payload[16:24])
        return int(width), int(height)
    if len(payload) >= 30 and payload[:4] == b"RIFF" and payload[8:12] == _WEBP_MAGIC:
        kind = payload[12:16]
        if kind == b"VP8X":
            width = 1 + int.from_bytes(payload[24:27], "little")
            height = 1 + int.from_bytes(payload[27:30], "little")
            return width, height
        if kind == b"VP8L" and len(payload) >= 25:
            bits = int.from_bytes(payload[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    try:
        text = payload[:4096].decode("utf-8", errors="ignore")
    except Exception:
        return None, None
    if "<svg" not in text.lower():
        return None, None
    width = _svg_dim(text, "width")
    height = _svg_dim(text, "height")
    if width and height:
        return width, height
    view = re.search(r"viewBox\s*=\s*['\"]\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)", text, re.I)
    if view:
        return int(float(view.group(1))), int(float(view.group(2)))
    return None, None


def _svg_dim(text: str, attr: str) -> int | None:
    match = re.search(rf"\b{attr}\s*=\s*['\"]([\d.]+)(?:px)?['\"]", text, re.I)
    if not match:
        return None
    return int(float(match.group(1)))
