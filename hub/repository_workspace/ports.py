"""Port availability helpers for Repository Workspace runs."""

from __future__ import annotations

import socket
from typing import Iterable


def port_available(port: int, *, host: str = "127.0.0.1") -> bool:
    """Return True when nothing is accepting and bind succeeds.

    On Windows, ``SO_REUSEADDR`` must not be used for this check — it can make
    bind succeed even while another process is listening.
    """
    if not (1 <= int(port) <= 65535):
        return False
    # If something accepts a connection, the port is occupied.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            probe.connect((host, int(port)))
            return False
        except OSError:
            pass
    # Bind without SO_REUSEADDR to catch ports reserved but not accepting yet.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def find_available_port(
    preferred: int,
    *,
    host: str = "127.0.0.1",
    search_from: int | None = None,
    search_to: int = 65535,
    exclude: Iterable[int] | None = None,
) -> int | None:
    blocked = {int(p) for p in (exclude or [])}
    preferred = int(preferred)
    if preferred not in blocked and port_available(preferred, host=host):
        return preferred
    start = int(search_from or max(1024, preferred))
    for port in range(start, min(search_to, 65535) + 1):
        if port in blocked:
            continue
        if port_available(port, host=host):
            return port
    # wrap lower range
    for port in range(1024, start):
        if port in blocked:
            continue
        if port_available(port, host=host):
            return port
    return None
