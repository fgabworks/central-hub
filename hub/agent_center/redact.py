"""Redact secrets from agent logs/answers before audit or UI echo."""

from __future__ import annotations

import re
from typing import Any

_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
)
_ENV_ASSIGN = re.compile(r"(?i)^[A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|COOKIE)[A-Z0-9_]*\s*=")
_OPENAI_KEY = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
_GOOGLE_API_KEY = re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,})\b")
_XAI_KEY = re.compile(r"\b(xai-[A-Za-z0-9_\-]{8,})\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
_CMD_SECRET = re.compile(
    r"(?i)((?:(?:^|(?<=\s))--?(?:api[_-]?key|token|password|secret)\s+)|(?:(?:CODEX_API_KEY|OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|XAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*))(\S+)"
)
_ENV_INLINE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|COOKIE)[A-Z0-9_]*)=([^\s]+)"
)


def redact_text(value: str | None, *, limit: int | None = None) -> str:
    text = value or ""
    text = _OPENAI_KEY.sub("[redacted]", text)
    text = _GOOGLE_API_KEY.sub("[redacted]", text)
    text = _XAI_KEY.sub("[redacted]", text)
    text = _JWT.sub("[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _CMD_SECRET.sub(r"\1[redacted]", text)
    text = _ENV_INLINE.sub(r"\1=[redacted]", text)
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


def classify_provider_error(message: str | None) -> dict[str, Any]:
    """Map provider/CLI failures into stable UI categories without leaking secrets."""
    raw = redact_text(message or "", limit=500)
    lower = raw.lower()
    if not raw.strip():
        return {"code": "execution_error", "detail": "Execution failed"}
    if (
        "code-mode-host" in lower
        or "installation incomplete" in lower
    ):
        return {
            "code": "incomplete_cli",
            "detail": "Codex installation incomplete: codex-code-mode-host.exe is missing",
        }
    if (
        ("not found" in lower and ("codex" in lower or "executable" in lower))
        or "no such file" in lower
        or "is not installed" in lower
        or "not discoverable" in lower
    ):
        return {"code": "missing_cli", "detail": "Codex CLI is not installed or not discoverable"}
    if "auth" in lower or "login" in lower or "unauthor" in lower or "not logged" in lower:
        return {"code": "authentication_required", "detail": "Authentication required. Use Connect to run `codex login`."}
    if "timeout" in lower or "timed out" in lower:
        return {"code": "timeout", "detail": "Codex timed out"}
    if "quota" in lower or "rate limit" in lower or "too many requests" in lower or "429" in lower:
        return {"code": "quota", "detail": "Codex quota or rate limit reached"}
    return {"code": "execution_error", "detail": raw or "Execution failed"}
