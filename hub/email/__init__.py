"""Email Center — shared read-only Gmail integration (Personal / Work)."""

from __future__ import annotations

from hub.email.service import EmailService
from hub.email.settings_gmail import GmailOAuthSettings, load_gmail_oauth_settings

__all__ = [
    "EmailService",
    "GmailOAuthSettings",
    "load_gmail_oauth_settings",
]
