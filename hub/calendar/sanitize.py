"""Sanitize Calendar event HTML descriptions for safe display."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


_ALLOWED_TAGS = {
    "a",
    "b",
    "br",
    "em",
    "i",
    "li",
    "ol",
    "p",
    "strong",
    "ul",
}
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
}
_SAFE_HREF = re.compile(r"^(https?:|mailto:)", re.I)


class _SanitizeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l in {"script", "style", "iframe", "object", "embed"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag_l not in _ALLOWED_TAGS:
            return
        if tag_l == "br":
            self._out.append("<br>")
            return
        allowed = _ALLOWED_ATTRS.get(tag_l, set())
        parts = [f"<{tag_l}"]
        for key, value in attrs:
            key_l = (key or "").lower()
            if key_l not in allowed or value is None:
                continue
            if key_l == "href":
                href = value.strip()
                if not _SAFE_HREF.match(href):
                    continue
                parts.append(f' href="{html.escape(href, quote=True)}"')
                parts.append(' rel="noopener noreferrer"')
                parts.append(' target="_blank"')
            else:
                parts.append(f' {key_l}="{html.escape(value, quote=True)}"')
        parts.append(">")
        self._out.append("".join(parts))

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in {"script", "style", "iframe", "object", "embed"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag_l in _ALLOWED_TAGS and tag_l != "br":
            self._out.append(f"</{tag_l}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        # Preserve readable line breaks inside text nodes.
        self._out.append(html.escape(data).replace("\n", "<br>\n"))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f"&#{name};")

    def result(self) -> str:
        return "".join(self._out)


def sanitize_html(raw: str | None) -> str:
    """Return safe HTML for event descriptions (allowlisted tags only)."""
    text = (raw or "").strip()
    if not text:
        return ""
    # Plain text (no tags): escape and preserve line breaks.
    if "<" not in text and ">" not in text:
        return html.escape(text).replace("\n", "<br>\n")
    parser = _SanitizeParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001
        return html.escape(re.sub(r"<[^>]+>", "", text)).replace("\n", "<br>\n")
    return parser.result()


def description_plain(raw: str | None) -> str:
    """Strip tags for list/snippet display."""
    text = (raw or "").strip()
    if not text:
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = html.unescape(plain)
    return re.sub(r"\s+", " ", plain).strip()
