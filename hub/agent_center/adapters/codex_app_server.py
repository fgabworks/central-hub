"""Minimal JSON-RPC client for supported Codex app-server discovery methods."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import Any


def call(executable: str, method: str, params: dict[str, Any] | None = None, *, timeout: float = 8.0) -> dict[str, Any]:
    process = subprocess.Popen(
        [executable, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    output: queue.Queue[str] = queue.Queue()

    def read_lines() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)

    threading.Thread(target=read_lines, daemon=True).start()
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "central-hub", "version": "1"}, "capabilities": {}}}) + "\n")
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}}) + "\n")
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}) + "\n")
        process.stdin.flush()
        while True:
            line = output.get(timeout=timeout)
            payload = json.loads(line)
            if payload.get("id") == 2:
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"].get("message") or "Codex app-server error"))
                return payload.get("result") or {}
    except queue.Empty as exc:
        raise RuntimeError("Codex app-server discovery timed out") from exc
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
