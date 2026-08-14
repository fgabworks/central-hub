"""Read-only CLIMATE file view. Presentation wrapper over repository preview."""

from __future__ import annotations

from typing import Any

PREVIEW_UNAVAILABLE = "Preview unavailable for this file type"


def as_read_only_file(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Return a view payload that never claims the file is editable.

    Readable text ``content`` is preserved. Only binary/unsupported files are
    blanked. A genuine 0-byte file is ``empty`` and distinct from errors.
    """
    out = dict(meta or {})
    out["editable"] = False
    if out.get("binary"):
        out["content"] = ""
        out["content_html"] = ""
        out["error"] = PREVIEW_UNAVAILABLE
        out["empty"] = False
        return out
    content = out.get("content")
    if content is None:
        out["content"] = ""
        content = ""
    else:
        out["content"] = str(content)
        content = out["content"]
    size = out.get("size")
    has_error = bool(out.get("error")) and not content
    out["empty"] = (not content) and not has_error and (size == 0 or size is None)
    return out
