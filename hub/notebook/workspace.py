"""Personal / Work workspace preference (cookie + hub_prefs)."""

from __future__ import annotations

from typing import Any

from flask import Request, Response

from hub.notebook.db import NotebookDatabase, utcnow
from hub.notebook.models import DEFAULT_WORKSPACE, normalize_workspace

COOKIE_NAME = "hub_workspace"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
PREF_KEY = "workspace"


def get_pref(db: NotebookDatabase, key: str, default: str = "") -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM hub_prefs WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return default
    return str(row["value"] or default)


def set_pref(db: NotebookDatabase, key: str, value: str) -> None:
    now = utcnow()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO hub_prefs (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def read_workspace(
    request: Request,
    db: NotebookDatabase | None = None,
    *,
    default: str = DEFAULT_WORKSPACE,
) -> str:
    """Resolve workspace: cookie → persisted pref → default."""
    cookie = normalize_workspace(request.cookies.get(COOKIE_NAME), default="")
    if cookie:
        return cookie
    if db is not None:
        stored = normalize_workspace(get_pref(db, PREF_KEY, ""), default="")
        if stored:
            return stored
    return normalize_workspace(default)


def persist_workspace(db: NotebookDatabase, workspace: str) -> str:
    value = normalize_workspace(workspace)
    set_pref(db, PREF_KEY, value)
    return value


def apply_workspace_cookie(response: Response, workspace: str) -> Response:
    response.set_cookie(
        COOKIE_NAME,
        normalize_workspace(workspace),
        max_age=COOKIE_MAX_AGE,
        samesite="Lax",
        httponly=False,
        path="/",
    )
    return response


def dashboard_endpoint(workspace: str) -> str:
    return (
        "personal_dashboard"
        if normalize_workspace(workspace) == "personal"
        else "work_dashboard"
    )


def notebook_endpoint(workspace: str) -> str:
    return (
        "personal_notebook"
        if normalize_workspace(workspace) == "personal"
        else "work_notebook"
    )


def scope_for_workspace(workspace: str) -> str:
    return normalize_workspace(workspace)
