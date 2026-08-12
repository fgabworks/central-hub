"""Deterministic subject detection for Official References (no LLM)."""

from __future__ import annotations

import re
from typing import Any

_EXPLICIT_LINE = re.compile(
    r"^\s*(?:SUBJECT|Subject|RE|Re)\s*[:\-–—]\s*(.+?)\s*$"
)
_EXPLICIT_LABEL_ONLY = re.compile(r"^\s*(?:SUBJECT|Subject|RE|Re)\s*[:\-–—]?\s*$")
_NOISE = re.compile(
    r"^(?:page\s+\d+|confidential|republic of|department of|office of|"
    r"memorandum|advisory|guideline|to\b|from\b|date\b|through\b)\b",
    re.IGNORECASE,
)
_MAX_SUBJECT_LEN = 200
_SCAN_LINES = 80


def detect_subject_from_text(text: str) -> dict[str, Any]:
    """Detect an explicit SUBJECT/RE line, else suggest from early headings.

    Returns:
      subject: str | None
      subject_source: '' | 'detected' | 'suggested'
      confidence: 'high' | 'medium' | 'none'
    """
    raw = str(text or "").replace("\r\n", "\n")
    if not raw.strip():
        return {"subject": None, "subject_source": "", "confidence": "none"}

    lines = [ln.strip() for ln in raw.split("\n")]
    # Explicit subject / RE lines first.
    for idx, line in enumerate(lines[:_SCAN_LINES]):
        if not line:
            continue
        match = _EXPLICIT_LINE.match(line)
        if match:
            subject = _clean_subject(match.group(1))
            if subject:
                return {
                    "subject": subject,
                    "subject_source": "detected",
                    "confidence": "high",
                }
        if _EXPLICIT_LABEL_ONLY.match(line):
            # SUBJECT: on its own line — take the next non-empty line.
            for nxt in lines[idx + 1 : idx + 4]:
                candidate = _clean_subject(nxt)
                if candidate and not _EXPLICIT_LINE.match(nxt):
                    return {
                        "subject": candidate,
                        "subject_source": "detected",
                        "confidence": "high",
                    }
            break

    suggestion = _suggest_from_heading(lines)
    if suggestion:
        return {
            "subject": suggestion,
            "subject_source": "suggested",
            "confidence": "medium",
        }
    return {"subject": None, "subject_source": "", "confidence": "none"}


def normalize_subject(value: str | None) -> str | None:
    text = _clean_subject(value)
    return text or None


def _clean_subject(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t-:–—")
    if len(text) > _MAX_SUBJECT_LEN:
        text = text[:_MAX_SUBJECT_LEN].rstrip(" ,;:-")
    return text


def _suggest_from_heading(lines: list[str]) -> str | None:
    """Pick a short deterministic subject from early substantive lines."""
    candidates: list[str] = []
    for line in lines[:_SCAN_LINES]:
        if not line or len(line) < 8:
            continue
        if _NOISE.match(line):
            continue
        if _EXPLICIT_LINE.match(line) or _EXPLICIT_LABEL_ONLY.match(line):
            continue
        # Skip lines that look like addresses / all-caps department banners longer than useful.
        letters = sum(ch.isalpha() for ch in line)
        if letters < 6:
            continue
        if line.isupper() and len(line) > 60:
            continue
        cleaned = _clean_subject(line)
        if cleaned:
            candidates.append(cleaned)
        if len(candidates) >= 3:
            break
    if not candidates:
        return None
    # Prefer a mid-length informative line over a tiny fragment.
    ranked = sorted(
        candidates,
        key=lambda s: (20 <= len(s) <= 120, len(s) <= 160, -abs(len(s) - 72)),
        reverse=True,
    )
    return ranked[0]
