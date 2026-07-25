"""Lightweight Markdown → HTML for note preview (no external deps)."""

from __future__ import annotations

import html
import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def render_markdown(text: str) -> str:
    """Render a safe subset of Markdown to HTML."""
    raw = text or ""
    if not raw.strip():
        return '<p class="muted">Nothing to preview.</p>'

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.strip().startswith("```"):
            fence = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                fence.append(html.escape(lines[i]))
                i += 1
            if i < len(lines):
                i += 1
            blocks.append("<pre><code>" + "\n".join(fence) + "</code></pre>")
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"^[-*]\s+\[([ xX])\]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+\[([ xX])\]\s+", lines[i]):
                m = re.match(r"^[-*]\s+\[([ xX])\]\s+(.*)$", lines[i])
                assert m
                checked = m.group(1).lower() == "x"
                box = "☑" if checked else "☐"
                items.append(f"<li>{box} {_inline(m.group(2))}</li>")
                i += 1
            blocks.append('<ul class="md-checklist">' + "".join(items) + "</ul>")
            continue
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^[-*]\\s+', '', lines[i]))}</li>")
                i += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\\d+\\.\\s+', '', lines[i]))}</li>")
                i += 1
            blocks.append("<ol>" + "".join(items) + "</ol>")
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#") and not re.match(
            r"^[-*]\s+", lines[i]
        ) and not re.match(r"^\d+\.\s+", lines[i]) and not lines[i].strip().startswith("```"):
            para.append(lines[i])
            i += 1
        blocks.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
    return "\n".join(blocks)


def _inline(text: str) -> str:
    escaped = html.escape(text)

    def link_sub(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
            return html.escape(f"[{label}]({url})")
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>'

    # Work on original then escape carefully: escape first, then apply patterns on escaped text
    # Re-do: escape, then bold/italic/code on escaped content; links need raw parse first.
    raw = text
    placeholders: list[str] = []

    def stash(html_bit: str) -> str:
        placeholders.append(html_bit)
        return f"\x00PH{len(placeholders) - 1}\x00"

    def link_raw(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = match.group(2).strip()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
            return html.escape(match.group(0))
        return stash(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>'
        )

    raw = _LINK_RE.sub(link_raw, raw)

    def code_raw(match: re.Match[str]) -> str:
        return stash(f"<code>{html.escape(match.group(1))}</code>")

    raw = _CODE_RE.sub(code_raw, raw)

    def bold_raw(match: re.Match[str]) -> str:
        return stash(f"<strong>{html.escape(match.group(1))}</strong>")

    raw = _BOLD_RE.sub(bold_raw, raw)

    def italic_raw(match: re.Match[str]) -> str:
        return stash(f"<em>{html.escape(match.group(1))}</em>")

    raw = _ITALIC_RE.sub(italic_raw, raw)

    out = html.escape(raw)
    for idx, bit in enumerate(placeholders):
        out = out.replace(html.escape(f"\x00PH{idx}\x00"), bit)
        out = out.replace(f"\x00PH{idx}\x00", bit)
    return out
