"""Environment-driven settings for Central Hub."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

_EnvGet = Callable[[str], str | None]

_DHIS2_ENV_ALIASES = {
    "stage": {
        "url": "STAGE_DHIS2_URL",
        "username": "STAGE_DHIS2_USERNAME",
        "password": "STAGE_DHIS2_PASSWORD",
    },
    "live": {
        "url": "LIVE_DHIS2_URL",
        "username": "LIVE_DHIS2_USERNAME",
        "password": "LIVE_DHIS2_PASSWORD",
    },
}

_CANONICAL_FIELDS = {
    "url": "DHIS2_BASE_URL",
    "username": "DHIS2_USERNAME",
    "password": "DHIS2_PASSWORD",
}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_int(value: str | None, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    if value is None or str(value).strip() == "":
        raw = default
    else:
        try:
            raw = int(value)
        except ValueError:
            raw = default
    raw = max(minimum, raw)
    if maximum is not None:
        raw = min(maximum, raw)
    return raw


def _clean_url(value: str | None) -> str | None:
    cleaned = (value or "").strip().rstrip("/")
    return cleaned or None


def _clean_user(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_password(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip() == "":
        return None
    return value


def _field_status(value: str | None) -> str:
    return "set" if value else "missing"


@dataclass(frozen=True)
class Dhis2CredentialResolution:
    """Resolved DHIS2 credentials with redacted status metadata only."""

    base_url: str | None
    username: str | None
    password: str | None
    environment: str
    credential_fields: Mapping[str, str]
    missing_fields: tuple[str, ...]
    configuration_errors: tuple[str, ...]

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.username and self.password) and not self.missing_fields


def resolve_dhis2_credentials(getenv: _EnvGet | None = None) -> Dhis2CredentialResolution:
    """
    Resolve DHIS2 credentials.

    Precedence:
      1. Canonical DHIS2_BASE_URL / DHIS2_USERNAME / DHIS2_PASSWORD
         (used when any canonical credential field is set)
      2. Selected environment aliases via DHIS2_ENVIRONMENT=stage|live
         (STAGE_DHIS2_* or LIVE_DHIS2_*)
    """
    get = getenv or os.getenv

    canonical = {
        "url": _clean_url(get(_CANONICAL_FIELDS["url"])),
        "username": _clean_user(get(_CANONICAL_FIELDS["username"])),
        "password": _clean_password(get(_CANONICAL_FIELDS["password"])),
    }
    canonical_any = any(canonical.values())

    raw_env = (get("DHIS2_ENVIRONMENT") or "").strip().lower()
    selected_env = raw_env if raw_env in _DHIS2_ENV_ALIASES else ""

    errors: list[str] = []
    if raw_env and raw_env not in _DHIS2_ENV_ALIASES:
        errors.append(
            "Invalid DHIS2_ENVIRONMENT "
            f"(got {raw_env!r}; expected 'stage' or 'live')."
        )

    if canonical_any:
        fields = {
            _CANONICAL_FIELDS["url"]: _field_status(canonical["url"]),
            _CANONICAL_FIELDS["username"]: _field_status(canonical["username"]),
            _CANONICAL_FIELDS["password"]: _field_status(canonical["password"]),
        }
        missing = tuple(name for name, status in fields.items() if status == "missing")
        if missing:
            errors.append(
                "Incomplete canonical DHIS2 credentials. "
                "Missing: " + ", ".join(missing) + "."
            )
        return Dhis2CredentialResolution(
            base_url=canonical["url"],
            username=canonical["username"],
            password=canonical["password"],
            environment="canonical",
            credential_fields=fields,
            missing_fields=missing,
            configuration_errors=tuple(errors),
        )

    if selected_env:
        names = _DHIS2_ENV_ALIASES[selected_env]
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
        if missing:
            errors.append(
                f"Incomplete {selected_env} DHIS2 credentials. "
                "Missing: " + ", ".join(missing) + "."
            )
        return Dhis2CredentialResolution(
            base_url=values["url"],
            username=values["username"],
            password=values["password"],
            environment=selected_env,
            credential_fields=fields,
            missing_fields=missing,
            configuration_errors=tuple(errors),
        )

    # No canonical and no DHIS2_ENVIRONMENT — inspect alias groups.
    available: list[str] = []
    complete: list[str] = []
    for env_name, names in _DHIS2_ENV_ALIASES.items():
        values = {
            "url": _clean_url(get(names["url"])),
            "username": _clean_user(get(names["username"])),
            "password": _clean_password(get(names["password"])),
        }
        if any(values.values()):
            available.append(env_name)
        if all(values.values()):
            complete.append(env_name)

    # Exactly one complete alias group and no selector → use it.
    if len(complete) == 1 and not raw_env:
        only = complete[0]
        names = _DHIS2_ENV_ALIASES[only]
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
        return Dhis2CredentialResolution(
            base_url=values["url"],
            username=values["username"],
            password=values["password"],
            environment=only,
            credential_fields=fields,
            missing_fields=(),
            configuration_errors=tuple(errors),
        )

    fields = {name: "missing" for name in _CANONICAL_FIELDS.values()}
    if len(complete) > 1:
        errors.append(
            "Multiple DHIS2 alias groups are complete ("
            + ", ".join(complete)
            + "). Set DHIS2_ENVIRONMENT=stage or DHIS2_ENVIRONMENT=live to choose one."
        )
    elif available:
        errors.append(
            "DHIS2 alias credentials were found ("
            + ", ".join(available)
            + ") but DHIS2_ENVIRONMENT is unset or incomplete. "
            "Set DHIS2_ENVIRONMENT=stage|live."
        )
    else:
        errors.append(
            "DHIS2 is not configured. Set canonical DHIS2_* credentials, "
            "or set DHIS2_ENVIRONMENT=stage|live with matching STAGE_/LIVE_ aliases."
        )

    return Dhis2CredentialResolution(
        base_url=None,
        username=None,
        password=None,
        environment="canonical",
        credential_fields=fields,
        missing_fields=tuple(_CANONICAL_FIELDS.values()),
        configuration_errors=tuple(errors),
    )


@dataclass(frozen=True)
class Dhis2Settings:
    """DHIS2 connection settings (credentials from env only)."""

    base_url: str | None
    username: str | None
    password: str | None
    timeout_seconds: float
    allow_writes: bool
    enabled: bool
    # Reliability knobs (GET-only client hardening)
    probe_timeout_seconds: float = 5.0
    retry_max: int = 2
    retry_backoff_seconds: float = 0.5
    page_size: int = 100
    max_pages: int = 10
    http_pool_maxsize: int = 10
    environment: str = "canonical"
    missing_fields: tuple[str, ...] = ()
    configuration_errors: tuple[str, ...] = ()
    credential_fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.username and self.password) and not self.missing_fields


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    app_name: str
    env_profile: str
    host: str
    port: int
    debug: bool
    repositories_config: Path
    request_timeout_seconds: float
    health_cache_ttl_seconds: float
    audit_log_path: Path
    dhis2: Dhis2Settings
    database_path: Path
    secret_key: str
    owner_token_configured: bool


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from `.env` (if present) and process environment."""
    load_dotenv(env_file or (ROOT_DIR / ".env"), override=False)
    from hub.agent_center.provider_secrets import load_secrets_into_environ

    load_secrets_into_environ()

    config_path = os.getenv(
        "CENTRAL_HUB_REPOSITORIES_CONFIG",
        str(ROOT_DIR / "config" / "repositories.yaml"),
    )
    audit_path = os.getenv(
        "CENTRAL_HUB_AUDIT_LOG",
        str(ROOT_DIR / "data" / "audit" / "audit.jsonl"),
    )

    resolved = resolve_dhis2_credentials(os.getenv)

    timeout = _as_float(os.getenv("DHIS2_TIMEOUT_SECONDS"), 10.0)
    probe_default = min(5.0, timeout) if timeout > 0 else 5.0

    dhis2 = Dhis2Settings(
        base_url=resolved.base_url,
        username=resolved.username,
        password=resolved.password,
        timeout_seconds=timeout,
        # Read the gate, but write APIs are not implemented — always fail-closed in client.
        allow_writes=_as_bool(os.getenv("ALLOW_DHIS2_WRITES"), default=False),
        enabled=_as_bool(os.getenv("DHIS2_ENABLED"), default=True),
        probe_timeout_seconds=_as_float(
            os.getenv("DHIS2_PROBE_TIMEOUT_SECONDS"), probe_default
        ),
        retry_max=_as_int(os.getenv("DHIS2_RETRY_MAX"), 2, minimum=0, maximum=5),
        retry_backoff_seconds=_as_float(os.getenv("DHIS2_RETRY_BACKOFF_SECONDS"), 0.5),
        page_size=_as_int(os.getenv("DHIS2_PAGE_SIZE"), 100, minimum=1, maximum=300),
        max_pages=_as_int(os.getenv("DHIS2_MAX_PAGES"), 10, minimum=1, maximum=100),
        http_pool_maxsize=_as_int(
            os.getenv("DHIS2_HTTP_POOL_MAXSIZE"), 10, minimum=1, maximum=50
        ),
        environment=resolved.environment,
        missing_fields=resolved.missing_fields,
        configuration_errors=resolved.configuration_errors,
        credential_fields=dict(resolved.credential_fields),
    )

    db_path = os.getenv(
        "CENTRAL_HUB_DATABASE",
        str(ROOT_DIR / "data" / "hub.db"),
    )
    secret = (os.getenv("CENTRAL_HUB_SECRET_KEY") or "central-hub-dev-only-change-me").strip()
    owner_token = (os.getenv("CENTRAL_HUB_OWNER_TOKEN") or "").strip()

    return Settings(
        app_name=os.getenv("CENTRAL_HUB_APP_NAME", "Central Hub"),
        env_profile=os.getenv("CENTRAL_HUB_ENV", "dev"),
        host=os.getenv("CENTRAL_HUB_HOST", "127.0.0.1"),
        port=int(os.getenv("CENTRAL_HUB_PORT", "8080")),
        debug=_as_bool(os.getenv("CENTRAL_HUB_DEBUG"), default=True),
        repositories_config=Path(config_path).expanduser().resolve(),
        request_timeout_seconds=float(os.getenv("CENTRAL_HUB_REQUEST_TIMEOUT", "5")),
        health_cache_ttl_seconds=_as_float(
            os.getenv("CENTRAL_HUB_HEALTH_CACHE_TTL"), 30.0
        ),
        audit_log_path=Path(audit_path).expanduser().resolve(),
        dhis2=dhis2,
        database_path=Path(db_path).expanduser().resolve(),
        secret_key=secret,
        owner_token_configured=bool(owner_token),
    )
