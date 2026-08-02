"""Focused tests for environment-isolated read-only SQL SSH tunnels."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.sql_workspace.connections import SqlConnectionProfile, SqlConnectionRegistry
from hub.sql_workspace.tunnels import SshTunnelError, SshTunnelManager


class FakeTunnel:
    next_port = 41000

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.local_bind_port = FakeTunnel.next_port
        FakeTunnel.next_port += 1
        self.is_active = False
        self.stopped = False

    def start(self):
        self.is_active = True

    def stop(self, force=False):
        self.is_active = False
        self.stopped = True


def profile(environment: str, prefix: str) -> SqlConnectionProfile:
    return SqlConnectionProfile(
        id=f"{environment}-ro",
        label=f"{environment} read-only",
        environment=environment,
        driver="postgresql",
        enabled=True,
        host=f"{environment}-db.internal",
        port=5432,
        database=f"{environment}_db",
        user="readonly",
        password="secret",
        ssh_tunnel_env_prefix=prefix,
        configured=True,
    )


def tunnel_env(monkeypatch, tmp_path: Path, prefix: str, *, ssh_host: str):
    key = tmp_path / f"{prefix.lower()}_key"
    key.write_text("test-only", encoding="utf-8")
    values = {
        f"{prefix}_SSH_TUNNEL_ENABLED": "true",
        f"{prefix}_SSH_HOST": ssh_host,
        f"{prefix}_SSH_PORT": "22",
        f"{prefix}_SSH_USERNAME": "tunnel-user",
        f"{prefix}_SSH_PRIVATE_KEY": str(key),
        f"{prefix}_SSH_REMOTE_BIND_HOST": f"{prefix.lower()}-db.internal",
        f"{prefix}_SSH_REMOTE_BIND_PORT": "5432",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_tunnel_resolves_to_loopback_and_reuses_handle(monkeypatch, tmp_path: Path):
    tunnel_env(monkeypatch, tmp_path, "LIVE", ssh_host="live-bastion")
    created = []

    def factory(**kwargs):
        handle = FakeTunnel(**kwargs)
        created.append(handle)
        return handle

    manager = SshTunnelManager(
        forwarder_factory=factory,
        host_key_resolver=lambda _settings: object(),
    )
    original = profile("live", "LIVE")
    first = manager.resolve(original)
    second = manager.resolve(original)

    assert first.host == "127.0.0.1"
    assert first.port == second.port
    assert len(created) == 1
    assert created[0].kwargs["remote_bind_address"] == ("live-db.internal", 5432)
    assert created[0].kwargs["local_bind_address"] == ("127.0.0.1", 0)
    manager.shutdown()
    assert created[0].stopped



def test_stage_and_live_tunnels_are_isolated(monkeypatch, tmp_path: Path):
    tunnel_env(monkeypatch, tmp_path, "STAGE", ssh_host="stage-bastion")
    tunnel_env(monkeypatch, tmp_path, "LIVE", ssh_host="live-bastion")
    handles = []

    def factory(**kwargs):
        handle = FakeTunnel(**kwargs)
        handles.append(handle)
        return handle

    manager = SshTunnelManager(
        forwarder_factory=factory,
        host_key_resolver=lambda _settings: object(),
    )
    registry = SqlConnectionRegistry(
        [profile("stage", "STAGE"), profile("live", "LIVE")],
        tunnel_manager=manager,
    )
    stage = registry.get_configured("stage-ro")
    live = registry.get_configured("live-ro")

    assert stage.host == live.host == "127.0.0.1"
    assert stage.port != live.port
    assert handles[0].kwargs["remote_bind_address"][0] == "stage-db.internal"
    assert handles[1].kwargs["remote_bind_address"][0] == "live-db.internal"


def test_tunnel_requires_trusted_host_key(monkeypatch, tmp_path: Path):
    tunnel_env(monkeypatch, tmp_path, "LIVE", ssh_host="unknown-bastion")
    manager = SshTunnelManager(forwarder_factory=FakeTunnel)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-home"))

    with pytest.raises(SshTunnelError, match="host key is not trusted"):
        manager.resolve(profile("live", "LIVE"))


def test_disabled_tunnel_preserves_direct_profile(monkeypatch):
    monkeypatch.setenv("LIVE_SSH_TUNNEL_ENABLED", "false")
    original = profile("live", "LIVE")
    assert SshTunnelManager().resolve(original) is original
