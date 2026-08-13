"""JSON-RPC client for Codex app-server (stdio).

Supports one-shot discovery calls and a reusable long-lived session that can
receive server notifications such as ``account/rateLimits/updated``.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any, Callable


NotificationHandler = Callable[[str, dict[str, Any]], None]


class CodexAppServerError(RuntimeError):
    """Raised when app-server returns an error or the session fails."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def call(
    executable: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """One-shot initialize → method → terminate (legacy discovery helper)."""
    session = CodexAppServerSession(executable)
    try:
        session.start(timeout=timeout)
        return session.request(method, params, timeout=timeout)
    finally:
        session.close()


class CodexAppServerSession:
    """Reusable stdio JSON-RPC session for Codex ``app-server``."""

    def __init__(
        self,
        executable: str,
        *,
        on_notification: NotificationHandler | None = None,
        client_name: str = "central-hub",
        client_version: str = "1",
    ) -> None:
        self.executable = executable
        self.on_notification = on_notification
        self.client_name = client_name
        self.client_version = client_version
        self._process: subprocess.Popen[str] | None = None
        self._output: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._reader: threading.Thread | None = None
        self._closed = False
        self._initialized = False

    @property
    def alive(self) -> bool:
        proc = self._process
        return bool(proc is not None and proc.poll() is None and self._initialized)

    def start(self, *, timeout: float = 8.0) -> None:
        with self._lock:
            if self.alive:
                return
            self.close()
            self._closed = False
            self._initialized = False
            self._process = subprocess.Popen(
                [self.executable, "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self.client_name,
                        "version": self.client_version,
                    },
                    "capabilities": {},
                },
                timeout=timeout,
            )
            self._notify("initialized", {})
            self._initialized = True

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed or self._process is None or self._process.poll() is not None:
                raise CodexAppServerError("Codex app-server is not running")
            req_id = self._next_id
            self._next_id += 1
            wait: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[req_id] = wait
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            self._write(payload)
        try:
            message = wait.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(req_id, None)
            raise CodexAppServerError(f"Codex app-server timed out on {method}") from exc
        if message.get("error"):
            err = message["error"] if isinstance(message["error"], dict) else {}
            raise CodexAppServerError(
                str(err.get("message") or "Codex app-server error"),
                code=err.get("code") if isinstance(err.get("code"), int) else None,
                data=err.get("data"),
            )
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._initialized = False
            pending = list(self._pending.items())
            self._pending.clear()
            proc = self._process
            self._process = None
        for _, wait in pending:
            try:
                wait.put_nowait({
                    "error": {"message": "Codex app-server closed", "code": -32000},
                })
            except queue.Full:
                pass
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        finally:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.stdout:
                try:
                    proc.stdout.close()
                except OSError:
                    pass

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._process
        if proc is None or proc.stdin is None:
            raise CodexAppServerError("Codex app-server stdin unavailable")
        proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def _read_loop(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._closed:
                    break
                text = (line or "").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                self._dispatch(message)
        finally:
            # Unblock any waiters if the process exits unexpectedly.
            with self._lock:
                pending = list(self._pending.items())
                self._pending.clear()
                self._initialized = False
            for _, wait in pending:
                try:
                    wait.put_nowait({
                        "error": {"message": "Codex app-server exited", "code": -32000},
                    })
                except queue.Full:
                    pass

    def _dispatch(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        if msg_id is not None:
            try:
                key = int(msg_id)
            except (TypeError, ValueError):
                return
            with self._lock:
                wait = self._pending.pop(key, None)
            if wait is not None:
                wait.put(message)
            return
        method = str(message.get("method") or "").strip()
        if not method:
            return
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        handler = self.on_notification
        if handler is None:
            return
        try:
            handler(method, params)
        except Exception:
            # Notification handlers must not break the reader loop.
            return


class ReusedCodexAppServer:
    """Process pool of one reused session with idle shutdown."""

    def __init__(
        self,
        executable: str,
        *,
        idle_seconds: float = 120.0,
        on_notification: NotificationHandler | None = None,
    ) -> None:
        self.executable = executable
        self.idle_seconds = idle_seconds
        self.on_notification = on_notification
        self._lock = threading.RLock()
        self._session: CodexAppServerSession | None = None
        self._last_used = 0.0

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        with self._lock:
            self._reap_idle_locked()
            if self._session is None or not self._session.alive:
                self._session = CodexAppServerSession(
                    self.executable,
                    on_notification=self._forward_notification,
                )
                self._session.start(timeout=timeout)
            self._last_used = time.monotonic()
            session = self._session
        return session.request(method, params, timeout=timeout)

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    def _forward_notification(self, method: str, params: dict[str, Any]) -> None:
        handler = self.on_notification
        if handler is not None:
            handler(method, params)

    def _reap_idle_locked(self) -> None:
        if self._session is None:
            return
        if self._last_used and (time.monotonic() - self._last_used) > self.idle_seconds:
            self._session.close()
            self._session = None
