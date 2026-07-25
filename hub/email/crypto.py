"""Encrypt Gmail OAuth tokens at rest (Fernet derived from hub secret)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a url-safe 32-byte Fernet key from CENTRAL_HUB_SECRET_KEY."""
    digest = hashlib.sha256((secret_key or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_fernet(secret_key: str) -> Fernet:
    return Fernet(_derive_fernet_key(secret_key))


def encrypt_token_blob(secret_key: str, payload: dict[str, Any]) -> str:
    """Serialize and encrypt a token payload. Never log the result."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return get_fernet(secret_key).encrypt(raw).decode("ascii")


def decrypt_token_blob(secret_key: str, blob: str) -> dict[str, Any]:
    """Decrypt a token payload. Raises ValueError on failure."""
    if not blob:
        raise ValueError("empty token blob")
    try:
        raw = get_fernet(secret_key).decrypt(blob.encode("ascii"))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("token decrypt failed") from exc
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid token payload")
    return data


def redact_account_public(account: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe for templates/API/audit (no tokens)."""
    blocked = {
        "token_encrypted",
        "refresh_token",
        "access_token",
        "token",
        "id_token",
        "client_secret",
    }
    out: dict[str, Any] = {}
    for key, value in account.items():
        if key.lower() in blocked or "token" in key.lower():
            continue
        out[key] = value
    out["token_stored"] = bool(account.get("token_encrypted") or account.get("token_stored"))
    return out
