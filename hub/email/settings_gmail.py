"""Gmail OAuth client settings from environment (no secrets in UI)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

_EnvGet = Callable[[str], str | None]


@dataclass(frozen=True)
class GmailOAuthSettings:
    client_id: str | None
    client_secret: str | None
    redirect_uri: str
    enabled: bool

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def public_dict(self) -> dict[str, object]:
        return {
            "configured": self.is_configured,
            "enabled": self.enabled,
            "redirect_uri": self.redirect_uri,
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "scope": "gmail.readonly",
        }


def load_gmail_oauth_settings(getenv: _EnvGet | None = None) -> GmailOAuthSettings:
    get = getenv or os.getenv
    client_id = (get("GMAIL_CLIENT_ID") or "").strip() or None
    client_secret = (get("GMAIL_CLIENT_SECRET") or "").strip() or None
    redirect = (
        get("GMAIL_REDIRECT_URI") or "http://127.0.0.1:8080/email/oauth/callback"
    ).strip()
    enabled_raw = (get("GMAIL_ENABLED") or "true").strip().lower()
    enabled = enabled_raw in {"1", "true", "yes", "on"}
    return GmailOAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect,
        enabled=enabled,
    )
