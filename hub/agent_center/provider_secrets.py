"""Gitignored local env-file helpers for AI provider API keys.

Never returns secret values. Only allowlisted keys may be written.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

_SEEN_SECRETS: set[str] = set()
_SECRET_ENV_HINTS = ("API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")

_ROOT = Path(__file__).resolve().parents[2]


def secrets_path() -> Path:
    raw = (os.getenv("CENTRAL_HUB_AI_PROVIDER_SECRETS") or "").strip()
    return Path(raw) if raw else _ROOT / "data" / "ai_provider_secrets.env"


def dotenv_path() -> Path:
    raw = (os.getenv("CENTRAL_HUB_DOTENV") or "").strip()
    return Path(raw) if raw else _ROOT / ".env"


def remember_secret(value: str) -> None:
    text = str(value or "").strip()
    if len(text) >= 8:
        _SEEN_SECRETS.add(text)


def current_secret_values() -> set[str]:
    values: set[str] = set(_SEEN_SECRETS)
    for name, value in os.environ.items():
        upper = name.upper()
        if not any(hint in upper for hint in _SECRET_ENV_HINTS):
            continue
        text = str(value or "").strip()
        if len(text) >= 8:
            values.add(text)
    return values


def redact_known_secrets(text: str | None, *, limit: int | None = None) -> str:
    from hub.agent_center.redact import redact_text

    out = text or ""
    for secret in current_secret_values():
        if secret and secret in out:
            out = out.replace(secret, "[redacted]")
    return redact_text(out, limit=limit)


def env_key_configured(name: str) -> bool:
    if not _KEY_NAME.match(name or ""):
        return False
    return bool((os.getenv(name) or "").strip())


def configured_env_keys(names: list[str] | tuple[str, ...]) -> list[str]:
    return [name for name in names if env_key_configured(name)]


def validate_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("API key is required")
    if any(ch in text for ch in "\n\r\x00"):
        raise ValueError("API key is malformed")
    if len(text) < 8:
        raise ValueError("API key is malformed")
    return text


def set_secret(name: str, value: str, *, allowlist: set[str]) -> None:
    key = _require_allowlisted(name, allowlist)
    secret = validate_secret(value)
    remember_secret(secret)
    _upsert_file(secrets_path(), key, secret)
    os.environ[key] = secret


def set_flag(name: str, enabled: bool, *, allowlist: set[str]) -> None:
    key = _require_allowlisted(name, allowlist)
    text = "true" if enabled else "false"
    _upsert_file(secrets_path(), key, text)
    os.environ[key] = text


def remove_secrets(
    names: list[str] | tuple[str, ...],
    *,
    allowlist: set[str],
    dotenv_keys: list[str] | tuple[str, ...] | None = None,
) -> None:
    keys = [_require_allowlisted(name, allowlist) for name in names if str(name).strip()]
    dotenv_allow = {
        _require_allowlisted(name, allowlist)
        for name in (dotenv_keys if dotenv_keys is not None else keys)
        if str(name).strip()
    }
    for key in keys:
        existing = (os.getenv(key) or "").strip()
        if existing:
            remember_secret(existing)
        _delete_file_key(secrets_path(), key)
        if key in dotenv_allow:
            _delete_file_key(dotenv_path(), key)
        os.environ.pop(key, None)


def load_secrets_into_environ() -> None:
    path = secrets_path()
    if not path.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(path, override=True)


def _require_allowlisted(name: str, allowlist: set[str]) -> str:
    key = str(name or "").strip()
    if not _KEY_NAME.match(key) or key not in allowlist:
        raise ValueError("Unsupported credential key")
    return key


def _format_assignment(key: str, value: str) -> str:
    if _SAFE_VALUE.fullmatch(value):
        return f"{key}={value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def _upsert_file(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    assignment = _format_assignment(key, value)
    replaced = False
    out: list[str] = []
    for line in lines:
        match = _ASSIGN.match(line.strip())
        if match and match.group(1) == key:
            out.append(assignment)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(assignment)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _delete_file_key(path: Path, key: str) -> None:
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    changed = False
    for line in lines:
        match = _ASSIGN.match(line.strip())
        if match and match.group(1) == key:
            changed = True
            continue
        out.append(line)
    if changed:
        path.write_text(("\n".join(out).rstrip() + "\n") if out else "", encoding="utf-8")
