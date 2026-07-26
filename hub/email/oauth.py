"""Google OAuth 2.0 web-server flow for Gmail readonly."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from hub.email.models import GMAIL_SCOPES, OAUTH_STATE_TTL_SECONDS, with_identity_scopes
from hub.email.settings_gmail import GmailOAuthSettings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

HttpPost = Callable[..., Any]


class OAuthError(Exception):
    """OAuth configuration or exchange failure (message is safe to show)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def state_expiry_iso(*, ttl_seconds: int = OAUTH_STATE_TTL_SECONDS) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def build_authorization_url(
    settings: GmailOAuthSettings,
    *,
    state: str,
    login_hint: str | None = None,
    prompt: str = "consent",
    scopes: tuple[str, ...] | list[str] | None = None,
) -> str:
    if not settings.is_configured:
        raise OAuthError("Gmail OAuth is not configured (set GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET)")
    assert settings.client_id is not None
    scope_list = with_identity_scopes(scopes if scopes is not None else GMAIL_SCOPES)
    if not scope_list:
        raise OAuthError("OAuth requires at least one scope")
    params: dict[str, str] = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(scope_list),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": state,
        "prompt": prompt,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(
    settings: GmailOAuthSettings,
    code: str,
    *,
    http_post: HttpPost | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Exchange authorization code for tokens. Returns token dict (caller encrypts)."""
    if not settings.is_configured:
        raise OAuthError("Gmail OAuth is not configured")
    post = http_post or requests.post
    try:
        resp = post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "redirect_uri": settings.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OAuthError("Token exchange network error") from exc
    data = _json_or_empty(resp)
    if resp.status_code >= 400:
        raise OAuthError(_safe_oauth_error(data, "Token exchange failed"), status_code=resp.status_code)
    if "refresh_token" not in data and "access_token" not in data:
        raise OAuthError("Token exchange returned no tokens")
    return data


def refresh_access_token(
    settings: GmailOAuthSettings,
    refresh_token: str,
    *,
    http_post: HttpPost | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    if not settings.is_configured:
        raise OAuthError("Gmail OAuth is not configured")
    if not refresh_token:
        raise OAuthError("Missing refresh token — reconnect the account")
    post = http_post or requests.post
    try:
        resp = post(
            TOKEN_URL,
            data={
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OAuthError("Token refresh network error") from exc
    data = _json_or_empty(resp)
    if resp.status_code >= 400:
        err = str(data.get("error") or "")
        if err in {"invalid_grant", "unauthorized_client"}:
            raise OAuthError("Refresh token revoked or expired — reconnect", status_code=resp.status_code)
        raise OAuthError(_safe_oauth_error(data, "Token refresh failed"), status_code=resp.status_code)
    # Preserve refresh_token if Google omits it on refresh.
    if "refresh_token" not in data:
        data["refresh_token"] = refresh_token
    return data


def revoke_token(
    token: str,
    *,
    http_post: HttpPost | None = None,
    timeout: float = 15.0,
) -> bool:
    """Revoke access or refresh token at Google. Returns True if revoked/already invalid."""
    if not token:
        return False
    post = http_post or requests.post
    try:
        resp = post(REVOKE_URL, params={"token": token}, timeout=timeout)
    except requests.RequestException:
        return False
    return resp.status_code in {200, 400}  # 400 often means already revoked


def fetch_userinfo(
    access_token: str,
    *,
    http_get: Callable[..., Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    get = http_get or requests.get
    try:
        resp = get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OAuthError("Userinfo network error") from exc
    data = _json_or_empty(resp)
    if resp.status_code >= 400:
        raise OAuthError("Could not load Google account profile", status_code=resp.status_code)
    return data


def fetch_gmail_profile(
    access_token: str,
    *,
    http_get: Callable[..., Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fallback profile via Gmail API (works with gmail.readonly)."""
    get = http_get or requests.get
    try:
        resp = get(
            GMAIL_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OAuthError("Gmail profile network error") from exc
    data = _json_or_empty(resp)
    if resp.status_code >= 400:
        raise OAuthError("Could not load Gmail profile", status_code=resp.status_code)
    email = str(data.get("emailAddress") or "").strip()
    if not email:
        raise OAuthError("Gmail profile missing email")
    return {"email": email, "sub": email}


def resolve_google_profile(
    access_token: str,
    *,
    http_get: Callable[..., Any] | None = None,
    fallback_email: str = "",
    fallback_sub: str = "",
) -> dict[str, Any]:
    """Resolve email/sub from userinfo, Gmail profile, or reconnect fallbacks."""
    try:
        data = fetch_userinfo(access_token, http_get=http_get)
        email = str(data.get("email") or "").strip()
        sub = str(data.get("sub") or "").strip()
        if email:
            return {"email": email, "sub": sub or email}
    except OAuthError:
        pass
    try:
        return fetch_gmail_profile(access_token, http_get=http_get)
    except OAuthError:
        pass
    email = (fallback_email or "").strip()
    sub = (fallback_sub or "").strip()
    if email:
        return {"email": email, "sub": sub or email}
    raise OAuthError(
        "Could not load Google account profile — reconnect and approve email access"
    )


def access_expires_at_iso(token_response: dict[str, Any]) -> str | None:
    expires_in = token_response.get("expires_in")
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds - 60))).isoformat()


def is_expired(expires_at: str | None, *, skew_seconds: int = 0) -> bool:
    if not expires_at:
        return True
    try:
        when = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= when - timedelta(seconds=skew_seconds)


def _json_or_empty(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _safe_oauth_error(data: dict[str, Any], fallback: str) -> str:
    err = str(data.get("error") or "").strip()
    desc = str(data.get("error_description") or "").strip()
    # Never echo raw tokens that might appear in odd error bodies.
    for key in ("access_token", "refresh_token", "id_token"):
        if key in desc:
            return fallback
    if err and desc:
        return f"{fallback}: {err}"
    if err:
        return f"{fallback}: {err}"
    return fallback


def monotonic_now() -> float:
    return time.monotonic()
