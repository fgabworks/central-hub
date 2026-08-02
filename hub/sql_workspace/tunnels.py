"""Environment-isolated SSH tunnels for read-only PostgreSQL profiles."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("hub.sql_workspace.tunnels")


class SshTunnelError(LookupError):
    """Safe, credential-free tunnel configuration or startup failure."""


@dataclass(frozen=True)
class SshTunnelSettings:
    prefix: str
    enabled: bool
    ssh_host: str
    ssh_port: int
    ssh_username: str
    private_key: str
    private_key_password: str | None
    ssh_password: str | None
    remote_host: str
    remote_port: int
    local_host: str
    local_port: int
    keepalive_seconds: float
    known_hosts_path: str | None
    explicit_host_key: str | None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    try:
        return int(_env(name) or default)
    except (TypeError, ValueError):
        return default


def load_tunnel_settings(prefix: str, profile: Any) -> SshTunnelSettings:
    scope = str(prefix or "").strip().upper()
    remote_host = _env(f"{scope}_SSH_REMOTE_BIND_HOST") or str(profile.host or "")
    remote_port = _int_env(
        f"{scope}_SSH_REMOTE_BIND_PORT", default=int(profile.port or 5432)
    )
    private_password = os.environ.get(f"{scope}_SSH_PRIVATE_KEY_PASSWORD") or None
    ssh_password = os.environ.get(f"{scope}_SSH_PASSWORD") or None
    return SshTunnelSettings(
        prefix=scope,
        enabled=_bool_env(f"{scope}_SSH_TUNNEL_ENABLED"),
        ssh_host=_env(f"{scope}_SSH_HOST"),
        ssh_port=_int_env(f"{scope}_SSH_PORT", default=22),
        ssh_username=_env(f"{scope}_SSH_USERNAME"),
        private_key=_env(f"{scope}_SSH_PRIVATE_KEY"),
        private_key_password=private_password,
        ssh_password=ssh_password,
        remote_host=remote_host,
        remote_port=remote_port,
        local_host=_env(f"{scope}_SSH_LOCAL_BIND_HOST") or "127.0.0.1",
        local_port=_int_env(f"{scope}_SSH_LOCAL_BIND_PORT", default=0),
        keepalive_seconds=float(_int_env(f"{scope}_SSH_KEEPALIVE_SECONDS", default=30)),
        known_hosts_path=_env(f"{scope}_SSH_KNOWN_HOSTS") or None,
        explicit_host_key=_env(f"{scope}_SSH_HOST_KEY") or None,
    )


def _validate(settings: SshTunnelSettings) -> None:
    missing: list[str] = []
    for field, value in (
        ("SSH host", settings.ssh_host),
        ("SSH username", settings.ssh_username),
        ("database remote host", settings.remote_host),
    ):
        if not value:
            missing.append(field)
    if not settings.private_key and not settings.ssh_password:
        missing.append("SSH private key or password")
    if settings.private_key and not Path(settings.private_key).expanduser().is_file():
        raise SshTunnelError("SSH private key file does not exist.")
    if settings.local_host not in {"127.0.0.1", "localhost", "::1"}:
        raise SshTunnelError("SSH tunnel local bind host must be loopback.")
    if missing:
        raise SshTunnelError(
            "SSH tunnel is enabled but missing: " + ", ".join(missing) + "."
        )


def _trusted_host_key(settings: SshTunnelSettings) -> Any:
    import paramiko

    if settings.explicit_host_key:
        line = f"{settings.ssh_host} {settings.explicit_host_key}"
        entry = paramiko.hostkeys.HostKeyEntry.from_line(line)
        if entry is None or entry.key is None:
            raise SshTunnelError("SSH_HOST_KEY is invalid.")
        return entry.key

    keys = paramiko.HostKeys()
    candidates = (
        [Path(settings.known_hosts_path).expanduser()]
        if settings.known_hosts_path
        else [Path.home() / ".ssh" / "known_hosts", Path.home() / ".ssh" / "known_hosts2"]
    )
    for path in candidates:
        if path.is_file():
            try:
                keys.load(str(path))
            except Exception:  # noqa: BLE001
                log.warning("ssh_known_hosts_load_failed path=%s", path.name)
    names = [f"[{settings.ssh_host}]:{settings.ssh_port}", settings.ssh_host]
    for name in names:
        found = keys.lookup(name)
        if found:
            return next(iter(found.values()))
    raise SshTunnelError(
        "SSH host key is not trusted. Add it to known_hosts or configure "
        f"{settings.prefix}_SSH_HOST_KEY."
    )


class SshTunnelManager:
    def __init__(
        self,
        *,
        forwarder_factory: Callable[..., Any] | None = None,
        host_key_resolver: Callable[[SshTunnelSettings], Any] | None = None,
    ) -> None:
        self._factory = forwarder_factory
        self._host_key_resolver = host_key_resolver or _trusted_host_key
        self._lock = threading.RLock()
        self._tunnels: dict[str, Any] = {}

    def resolve(self, profile: Any) -> Any:
        prefix = str(getattr(profile, "ssh_tunnel_env_prefix", "") or "").strip()
        if not prefix or profile.driver != "postgresql":
            return profile
        settings = load_tunnel_settings(prefix, profile)
        if not settings.enabled:
            return profile
        _validate(settings)

        with self._lock:
            current = self._tunnels.get(profile.id)
            if current is not None and bool(getattr(current, "is_active", False)):
                return replace(
                    profile,
                    host=settings.local_host,
                    port=int(current.local_bind_port),
                )
            if current is not None:
                self._stop_one(current)

            host_key = self._host_key_resolver(settings)
            factory = self._factory
            if factory is None:
                from sshtunnel import SSHTunnelForwarder

                factory = SSHTunnelForwarder
            kwargs: dict[str, Any] = {
                "ssh_address_or_host": (settings.ssh_host, settings.ssh_port),
                "ssh_username": settings.ssh_username,
                "ssh_host_key": host_key,
                "remote_bind_address": (settings.remote_host, settings.remote_port),
                "local_bind_address": (settings.local_host, settings.local_port),
                "set_keepalive": settings.keepalive_seconds,
                "allow_agent": False,
                "host_pkey_directories": [],
                "ssh_config_file": None,
            }
            if settings.private_key:
                kwargs["ssh_pkey"] = str(Path(settings.private_key).expanduser())
                kwargs["ssh_private_key_password"] = settings.private_key_password
            elif settings.ssh_password:
                kwargs["ssh_password"] = settings.ssh_password
            try:
                tunnel = factory(**kwargs)
                tunnel.start()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "ssh_tunnel_start_failed env=%s connection=%s error_type=%s",
                    profile.environment,
                    profile.id,
                    type(exc).__name__,
                )
                raise SshTunnelError(
                    f"Unable to establish the {profile.environment.title()} SSH tunnel."
                ) from exc
            if not bool(getattr(tunnel, "is_active", False)):
                self._stop_one(tunnel)
                raise SshTunnelError(
                    f"The {profile.environment.title()} SSH tunnel did not become active."
                )
            self._tunnels[profile.id] = tunnel
            log.info(
                "ssh_tunnel_ready env=%s connection=%s local_host=loopback",
                profile.environment,
                profile.id,
            )
            return replace(
                profile,
                host=settings.local_host,
                port=int(tunnel.local_bind_port),
            )


    def shutdown(self) -> None:
        with self._lock:
            for tunnel in self._tunnels.values():
                self._stop_one(tunnel)
            self._tunnels.clear()

    @staticmethod
    def _stop_one(tunnel: Any) -> None:
        try:
            tunnel.stop(force=True)
        except TypeError:
            tunnel.stop()
        except Exception:  # noqa: BLE001
            pass
