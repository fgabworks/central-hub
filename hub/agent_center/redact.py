"""Redact secrets from agent logs/answers before audit or UI echo."""

from __future__ import annotations

import re

_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
)
_ENV_ASSIGN = re.compile(r"(?i)^[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY)[A-Z0-9_]*\s*=")
_OPENAI_KEY = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")


def redact_text(value: str | None, *, limit: int | None = None) -> str:
    text = value or ""
    text = _OPENAI_KEY.sub("[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    lines = []
    for line in text.splitlines():
        if _ENV_ASSIGN.search(line) or _SECRET_LINE.search(line):
            lines.append("[redacted]")
        else:
            lines.append(line)
    out = "\n".join(lines)
    if limit is not None and len(out) > limit:
        out = out[:limit] + "\n…[truncated]"
    return out
