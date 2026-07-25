"""DHIS2 Stage/Live instance profile helpers (credentials never exposed)."""

from __future__ import annotations

import os
from typing import Any, Callable

from hub.settings import (
    _DHIS2_ENV_ALIASES,
    _as_bool,
    _as_float,
    _as_int,
    _clean_password,
    _clean_url,
    _clean_user,
    _field_status,
    Dhis2CredentialResolution,
    Dhis2Settings,
)

_EnvGet = Callable[[str], str | None]

INSTANCE_PROFILES = ("stage", "live")
INSTANCE_LABELS = {"stage": "Stage", "live": "Live"}


def list_dhis2_instance_profiles(getenv: _EnvGet | None = None) -> list[dict[str, Any]]:
    """Return Stage/Live profile availability with set/missing field names only."""
    get = getenv or os.getenv
    out: list[dict[str, Any]] = []
    for profile in INSTANCE_PROFILES:
        names = _DHIS2_ENV_ALIASES[profile]
        values = {
            "url": _clean_url(get(names["url"])),
            "username": _clean_user(get(names["username"])),
            "password": _clean_password(get(names["password"])),
        }
        fields = {
            names["url"]: _field_status(values["url"]),
            names["username"]: _field_status(values["username"]),
            names["password"]: _field_status(values["password"]),
        }
        missing = tuple(name for name, status in fields.items() if status == "missing")
        configured = not missing
        out.append(
            {
                "id": profile,
                "label": INSTANCE_LABELS[profile],
                "configured": configured,
                "available": configured,
                "credential_fields": fields,
                "missing_fields": missing,
            }
        )
    return out


def resolve_dhis2_instance(
    profile: str,
    getenv: _EnvGet | None = None,
) -> Dhis2CredentialResolution:
    """Resolve credentials for an explicit Stage/Live instance profile."""
    get = getenv or os.getenv
    key = (profile or "").strip().lower()
    if key not in _DHIS2_ENV_ALIASES:
        return Dhis2CredentialResolution(
            base_url=None,
            username=None,
            password=None,
            environment="canonical",
            credential_fields={},
            missing_fields=(),
            configuration_errors=(
                f"Unknown DHIS2 instance profile {profile!r}. Expected 'stage' or 'live'.",
            ),
        )

    names = _DHIS2_ENV_ALIASES[key]
    values = {
        "url": _clean_url(get(names["url"])),
        "username": _clean_user(get(names["username"])),
        "password": _clean_password(get(names["password"])),
    }
    fields = {
        names["url"]: _field_status(values["url"]),
        names["username"]: _field_status(values["username"]),
        names["password"]: _field_status(values["password"]),
    }
    missing = tuple(name for name, status in fields.items() if status == "missing")
    errors: list[str] = []
    if missing:
        errors.append(
            f"Incomplete {key} DHIS2 credentials. Missing: " + ", ".join(missing) + "."
        )
    return Dhis2CredentialResolution(
        base_url=values["url"],
        username=values["username"],
        password=values["password"],
        environment=key,
        credential_fields=fields,
        missing_fields=missing,
        configuration_errors=tuple(errors),
    )


def build_dhis2_settings_for_instance(
    profile: str | None,
    getenv: _EnvGet | None = None,
) -> Dhis2Settings:
    """Build Dhis2Settings for a selected instance (always writes-disabled)."""
    get = getenv or os.getenv
    timeout = _as_float(get("DHIS2_TIMEOUT_SECONDS"), 10.0)
    probe_default = min(5.0, timeout) if timeout > 0 else 5.0
    enabled = _as_bool(get("DHIS2_ENABLED"), default=True)

    if not profile:
        return Dhis2Settings(
            base_url=None,
            username=None,
            password=None,
            timeout_seconds=timeout,
            allow_writes=False,
            enabled=enabled,
            probe_timeout_seconds=_as_float(get("DHIS2_PROBE_TIMEOUT_SECONDS"), probe_default),
            retry_max=_as_int(get("DHIS2_RETRY_MAX"), 2, minimum=0, maximum=5),
            retry_backoff_seconds=_as_float(get("DHIS2_RETRY_BACKOFF_SECONDS"), 0.5),
            page_size=_as_int(get("DHIS2_PAGE_SIZE"), 100, minimum=1, maximum=300),
            max_pages=_as_int(get("DHIS2_MAX_PAGES"), 10, minimum=1, maximum=100),
            http_pool_maxsize=_as_int(get("DHIS2_HTTP_POOL_MAXSIZE"), 10, minimum=1, maximum=50),
            environment="canonical",
            missing_fields=(),
            configuration_errors=(
                "No DHIS2 instance selected. Choose Stage or Live on the DHIS2 Overview.",
            ),
            credential_fields={},
        )

    resolved = resolve_dhis2_instance(profile, get)
    return Dhis2Settings(
        base_url=resolved.base_url,
        username=resolved.username,
        password=resolved.password,
        timeout_seconds=timeout,
        allow_writes=False,
        enabled=enabled,
        probe_timeout_seconds=_as_float(get("DHIS2_PROBE_TIMEOUT_SECONDS"), probe_default),
        retry_max=_as_int(get("DHIS2_RETRY_MAX"), 2, minimum=0, maximum=5),
        retry_backoff_seconds=_as_float(get("DHIS2_RETRY_BACKOFF_SECONDS"), 0.5),
        page_size=_as_int(get("DHIS2_PAGE_SIZE"), 100, minimum=1, maximum=300),
        max_pages=_as_int(get("DHIS2_MAX_PAGES"), 10, minimum=1, maximum=100),
        http_pool_maxsize=_as_int(get("DHIS2_HTTP_POOL_MAXSIZE"), 10, minimum=1, maximum=50),
        environment=resolved.environment,
        missing_fields=resolved.missing_fields,
        configuration_errors=resolved.configuration_errors,
        credential_fields=dict(resolved.credential_fields),
    )


def default_instance_selection(
    *,
    available_ids: list[str],
    persisted: str | None,
    env_default: str | None,
) -> str | None:
    """
    Choose initial instance:
    1) persisted selection when still available
    2) DHIS2_ENVIRONMENT when stage|live and available
    3) otherwise None (user must select)
    """
    avail = {a.lower() for a in available_ids}
    if persisted and persisted.lower() in avail:
        return persisted.lower()
    env = (env_default or "").strip().lower()
    if env in avail:
        return env
    return None
