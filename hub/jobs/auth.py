"""Minimal local owner role (Phase 6)."""

from __future__ import annotations

import hmac
import os
from functools import wraps
from typing import Any, Callable

from flask import Request, abort, request, session


def load_owner_token() -> str:
    """Owner token from env; empty means open local mode (single-user default)."""
    return (os.getenv("CENTRAL_HUB_OWNER_TOKEN") or "").strip()


def current_actor(req: Request | None = None) -> str:
    req = req or request
    token = load_owner_token()
    if not token:
        return "owner"
    provided = (
        session.get("owner_token")
        or req.headers.get("X-Central-Hub-Owner")
        or req.values.get("owner_token")
        or ""
    )
    if hmac.compare_digest(str(provided), token):
        return "owner"
    return "anonymous"


def require_owner(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if current_actor() != "owner":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def login_owner(token: str) -> bool:
    expected = load_owner_token()
    if not expected:
        session["owner_token"] = ""
        return True
    if hmac.compare_digest(token.strip(), expected):
        session["owner_token"] = expected
        return True
    return False
