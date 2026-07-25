"""Credential and sensitive-string redaction helpers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_PASSWORD_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
}


def redact_url(url: str | None) -> str | None:
    """Strip userinfo from a URL if present."""
    if not url:
        return url
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def redact_text(text: str | None, secrets: list[str] | None = None) -> str:
    """Remove known secret substrings from free text."""
    if not text:
        return ""
    redacted = text
    for secret in secrets or []:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "***")
    # Also strip URL userinfo patterns like https://user:pass@host
    redacted = re.sub(r"(://[^:/@\s]+):([^@/\s]+)@", r"\1:***@", redacted)
    return redacted


def redact_mapping(value: Any, secrets: list[str] | None = None) -> Any:
    """Recursively redact password-like keys and secret substrings."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _PASSWORD_KEYS:
                out[key] = "***"
            else:
                out[key] = redact_mapping(item, secrets)
        return out
    if isinstance(value, list):
        return [redact_mapping(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value


def public_dhis2_config(
    *,
    base_url: str | None,
    username: str | None,
    password: str | None,
    timeout_seconds: float,
    allow_writes: bool,
    enabled: bool,
    configured: bool,
    probe_timeout_seconds: float | None = None,
    retry_max: int | None = None,
    retry_backoff_seconds: float | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
    http_pool_maxsize: int | None = None,
    mode: str = "readonly",
    environment: str = "canonical",
    credential_fields: dict[str, str] | None = None,
    configuration_errors: list[str] | tuple[str, ...] | None = None,
    missing_fields: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Safe config snapshot for UI/API (never includes password or username values)."""
    fields = dict(credential_fields or {})
    # Never expose credential values — only set/missing for named env vars.
    safe_fields = {
        str(name): ("set" if status == "set" else "missing") for name, status in fields.items()
    }
    return {
        "base_url": redact_url(base_url),
        "username_set": bool(username),
        "password_set": bool(password),
        "timeout_seconds": timeout_seconds,
        "probe_timeout_seconds": probe_timeout_seconds
        if probe_timeout_seconds is not None
        else min(5.0, timeout_seconds),
        "retry_max": 0 if retry_max is None else retry_max,
        "retry_backoff_seconds": 0.5 if retry_backoff_seconds is None else retry_backoff_seconds,
        "page_size": 100 if page_size is None else page_size,
        "max_pages": 10 if max_pages is None else max_pages,
        "http_pool_maxsize": 10 if http_pool_maxsize is None else http_pool_maxsize,
        "allow_writes": allow_writes,
        "enabled": enabled,
        "configured": configured,
        "mode": mode,
        "environment": environment or "canonical",
        "credential_fields": safe_fields,
        "configuration_errors": list(configuration_errors or ()),
        "missing_fields": list(missing_fields or ()),
    }
