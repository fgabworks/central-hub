"""Configured read-only SQL connections (secrets from env only)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hub.dhis2.redact import redact_mapping, redact_text
from hub.settings import ROOT_DIR


@dataclass(frozen=True)
class SqlConnectionProfile:
    id: str
    label: str
    environment: str  # stage | live | dev
    driver: str  # postgresql | sqlite
    enabled: bool
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    sslmode: str | None = None
    sqlite_path: str | None = None
    configured: bool = False
    missing_fields: tuple[str, ...] = ()

    @property
    def is_live(self) -> bool:
        return self.environment.strip().lower() == "live"

    def public_dict(self) -> dict[str, Any]:
        """Safe for UI/API — never includes password."""
        return {
            "id": self.id,
            "label": self.label,
            "environment": self.environment,
            "driver": self.driver,
            "enabled": self.enabled,
            "configured": self.configured,
            "missing_fields": list(self.missing_fields),
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "sslmode": self.sslmode,
            "sqlite_path": self.sqlite_path,
            "is_live": self.is_live,
            "password_set": bool(self.password),
        }

    def secret_values(self) -> list[str]:
        secrets: list[str] = []
        if self.password:
            secrets.append(self.password)
        if self.user:
            secrets.append(self.user)
        return secrets


class SqlConnectionRegistry:
    def __init__(self, profiles: list[SqlConnectionProfile]) -> None:
        self._by_id = {p.id: p for p in profiles}

    def list_public(self) -> list[dict[str, Any]]:
        return [p.public_dict() for p in self._by_id.values() if p.enabled]

    def get(self, connection_id: str) -> SqlConnectionProfile | None:
        return self._by_id.get((connection_id or "").strip())

    def get_configured(self, connection_id: str) -> SqlConnectionProfile:
        profile = self.get(connection_id)
        if profile is None or not profile.enabled:
            raise LookupError("Connection not found or disabled.")
        if not profile.configured:
            missing = ", ".join(profile.missing_fields) or "credentials"
            raise LookupError(f"Connection is not configured (missing: {missing}).")
        return profile


def default_connections_path() -> Path:
    return ROOT_DIR / "config" / "sql_connections.yaml"


def _env(name: str | None) -> str:
    if not name:
        return ""
    return (os.environ.get(name) or "").strip()


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_connection_registry(path: Path | None = None) -> SqlConnectionRegistry:
    cfg_path = path or default_connections_path()
    if not cfg_path.exists():
        return SqlConnectionRegistry([])

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    items = list(raw.get("connections") or [])
    profiles: list[SqlConnectionProfile] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        driver = str(item.get("driver") or "postgresql").strip().lower()
        if driver in {"postgres", "pg"}:
            driver = "postgresql"
        environment = str(item.get("environment") or "dev").strip().lower()
        label = str(item.get("label") or cid).strip()
        enabled = _as_bool(item.get("enabled"), True)

        missing: list[str] = []
        host = port = database = user = password = sslmode = sqlite_path = None
        configured = False

        if driver == "sqlite":
            path_env = str(item.get("path_env") or "").strip()
            path_value = _env(path_env) if path_env else str(item.get("path") or "").strip()
            if path_value:
                if path_value != ":memory:":
                    p = Path(path_value)
                    if not p.is_absolute():
                        p = ROOT_DIR / p
                    sqlite_path = str(p)
                else:
                    sqlite_path = path_value
                configured = True
            else:
                missing.append("path")
        else:
            host = _env(str(item.get("host_env") or "")) or None
            port_raw = _env(str(item.get("port_env") or ""))
            database = _env(str(item.get("database_env") or "")) or None
            user = _env(str(item.get("user_env") or "")) or None
            password = os.environ.get(str(item.get("password_env") or "")) or None
            if password is not None and password.strip() == "":
                password = None
            sslmode = _env(str(item.get("sslmode_env") or "")) or str(
                item.get("sslmode") or "prefer"
            )
            try:
                port = int(port_raw) if port_raw else int(item.get("port") or 5432)
            except (TypeError, ValueError):
                port = 5432
            for field, value in (
                ("host", host),
                ("database", database),
                ("user", user),
                ("password", password),
            ):
                if not value:
                    missing.append(field)
            configured = not missing

        profiles.append(
            SqlConnectionProfile(
                id=cid,
                label=label,
                environment=environment,
                driver=driver,
                enabled=enabled,
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                sslmode=sslmode,
                sqlite_path=sqlite_path,
                configured=configured,
                missing_fields=tuple(missing),
            )
        )
    return SqlConnectionRegistry(profiles)


def public_error(message: str, profile: SqlConnectionProfile | None = None) -> str:
    secrets = profile.secret_values() if profile else []
    return redact_text(str(message or ""), secrets)


def public_payload(data: Any, profile: SqlConnectionProfile | None = None) -> Any:
    secrets = profile.secret_values() if profile else []
    return redact_mapping(data, secrets)
