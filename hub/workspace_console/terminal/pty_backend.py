"""Cross-platform PTY process wrappers (ConPTY on Windows, native PTY elsewhere)."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class PtyClosed(Exception):
    """PTY process has exited or been closed."""


def normalize_conpty_newlines(data: bytes) -> bytes:
    """Insert CR before lone LF so xterm starts the next line at column 0.

    ConPTY/pywinpty often emits Unix LF without CR. Preserve existing CRLF and
    lone CR (cursor-to-start). Do not invent prompts or echo input.
    """
    if not data or b"\n" not in data:
        return data
    out = bytearray()
    prev = 0
    for byte in data:
        if byte == 0x0A and prev != 0x0D:
            out.append(0x0D)
        out.append(byte)
        prev = byte
    return bytes(out)


@dataclass
class PtyLaunch:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    cols: int
    rows: int


class BasePty:
    def write(self, data: bytes | str) -> None:
        raise NotImplementedError

    def read(self, size: int = 16384) -> bytes:
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:
        raise NotImplementedError

    def close(self) -> int | None:
        raise NotImplementedError

    @property
    def pid(self) -> int | None:
        raise NotImplementedError

    @property
    def alive(self) -> bool:
        raise NotImplementedError

    @property
    def exit_code(self) -> int | None:
        raise NotImplementedError


class WinConPty(BasePty):
    """Windows ConPTY via pywinpty."""

    def __init__(self, launch: PtyLaunch) -> None:
        try:
            from winpty import PtyProcess  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pywinpty is required for interactive terminals on Windows. "
                "Install with PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 on Python 3.14+."
            ) from exc
        # PtyProcess.spawn accepts cmdline string or list depending on version.
        cmdline = subprocess.list2cmdline(launch.argv)
        self._proc = PtyProcess.spawn(
            cmdline,
            cwd=str(launch.cwd),
            env=launch.env,
            dimensions=(int(launch.rows), int(launch.cols)),
        )
        self._exit_code: int | None = None
        self._closed = False

    def write(self, data: bytes | str) -> None:
        if self._closed:
            raise PtyClosed("PTY closed")
        text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else data
        self._proc.write(text)

    def read(self, size: int = 16384) -> bytes:
        if self._closed:
            raise PtyClosed("PTY closed")
        try:
            chunk = self._proc.read(size)
        except EOFError as exc:
            self._closed = True
            raise PtyClosed("EOF") from exc
        if chunk is None:
            return b""
        if isinstance(chunk, bytes):
            raw = chunk
        else:
            raw = str(chunk).encode("utf-8", errors="replace")
        return normalize_conpty_newlines(raw)

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        try:
            self._proc.setwinsize(int(rows), int(cols))
        except Exception:
            pass

    def close(self) -> int | None:
        if self._closed:
            return self._exit_code
        self._closed = True
        try:
            if self._proc.isalive():
                self._proc.terminate()
                # Brief wait then force
                deadline = time.time() + 1.5
                while self._proc.isalive() and time.time() < deadline:
                    time.sleep(0.05)
                if self._proc.isalive():
                    try:
                        self._proc.kill(force=True)  # type: ignore[call-arg]
                    except TypeError:
                        self._proc.kill()
        except Exception:
            pass
        try:
            self._exit_code = int(self._proc.exitstatus) if self._proc.exitstatus is not None else None
        except Exception:
            self._exit_code = None
        return self._exit_code

    @property
    def pid(self) -> int | None:
        try:
            return int(self._proc.pid)
        except Exception:
            return None

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


class UnixPty(BasePty):
    """Native POSIX PTY."""

    def __init__(self, launch: PtyLaunch) -> None:
        import fcntl
        import pty
        import termios

        self._master_fd: int | None
        pid, master_fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(str(launch.cwd))
                os.environ.clear()
                os.environ.update(launch.env)
                os.execvpe(launch.argv[0], launch.argv, launch.env)
            except Exception:
                os._exit(127)
        self._pid = int(pid)
        self._master_fd = int(master_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._exit_code: int | None = None
        self._closed = False
        self._termios = termios
        self.resize(launch.cols, launch.rows)

    def write(self, data: bytes | str) -> None:
        if self._closed or self._master_fd is None:
            raise PtyClosed("PTY closed")
        payload = data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8", errors="replace")
        os.write(self._master_fd, payload)

    def read(self, size: int = 16384) -> bytes:
        if self._closed or self._master_fd is None:
            raise PtyClosed("PTY closed")
        try:
            return os.read(self._master_fd, size)
        except BlockingIOError:
            return b""
        except OSError as exc:
            self._reap()
            raise PtyClosed("EOF") from exc

    def resize(self, cols: int, rows: int) -> None:
        if self._closed or self._master_fd is None:
            return
        import fcntl
        import struct

        try:
            winsize = struct.pack("HHHH", int(rows), int(cols), 0, 0)
            fcntl.ioctl(self._master_fd, self._termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def close(self) -> int | None:
        if self._closed:
            return self._exit_code
        self._closed = True
        import signal

        try:
            os.kill(self._pid, signal.SIGHUP)
        except OSError:
            pass
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        self._reap(force=True)
        return self._exit_code

    def _reap(self, force: bool = False) -> None:
        import signal

        try:
            done_pid, status = os.waitpid(self._pid, os.WNOHANG if not force else 0)
        except ChildProcessError:
            return
        except OSError:
            return
        if done_pid == 0 and force:
            try:
                os.kill(self._pid, signal.SIGKILL)
                done_pid, status = os.waitpid(self._pid, 0)
            except OSError:
                return
        if done_pid:
            if os.WIFEXITED(status):
                self._exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._exit_code = -os.WTERMSIG(status)

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            os.kill(self._pid, 0)
            return True
        except OSError:
            self._reap()
            return False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


def spawn_pty(launch: PtyLaunch) -> BasePty:
    if os.name == "nt":
        return WinConPty(launch)
    return UnixPty(launch)


def kill_process_tree(pid: int | None, *, force: bool = True) -> None:
    """Stop only the given PID tree (taskkill /T or killpg) — never broad runtime kills."""
    if not pid:
        return
    if os.name == "nt":
        args = ["taskkill", "/PID", str(int(pid)), "/T"]
        if force:
            args.append("/F")
        subprocess.run(args, shell=False, capture_output=True, check=False, timeout=12)
        return
    import signal

    try:
        os.killpg(int(pid), signal.SIGTERM if not force else signal.SIGKILL)
    except OSError:
        try:
            os.kill(int(pid), signal.SIGTERM if not force else signal.SIGKILL)
        except OSError:
            pass


class OutputPump:
    """Background reader with bounded buffer and optional subscriber queue."""

    def __init__(
        self,
        pty: BasePty,
        *,
        max_buffer: int,
        chunk_size: int,
        on_data: Callable[[bytes], None] | None = None,
    ) -> None:
        self.pty = pty
        self.max_buffer = max_buffer
        self.chunk_size = chunk_size
        self.on_data = on_data
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[bytes | None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="wc-pty-pump", daemon=True)
        self.bytes_written = 0
        self._thread.start()

    def subscribe(self, maxsize: int = 64) -> queue.Queue[bytes | None]:
        q: queue.Queue[bytes | None] = queue.Queue(maxsize=maxsize)
        with self._lock:
            # Replay recent buffer for reconnect / late attach.
            if self._buf:
                try:
                    q.put_nowait(bytes(self._buf))
                except queue.Full:
                    pass
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[bytes | None]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
        try:
            q.put_nowait(None)
        except queue.Full:
            pass

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            subs = list(self._subscribers)
            self._subscribers.clear()
        for q in subs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.pty.read(self.chunk_size)
            except PtyClosed:
                break
            except Exception:
                break
            if not chunk:
                if not self.pty.alive:
                    break
                time.sleep(0.02)
                continue
            self.bytes_written += len(chunk)
            with self._lock:
                self._buf.extend(chunk)
                overflow = len(self._buf) - self.max_buffer
                if overflow > 0:
                    del self._buf[:overflow]
                subs = list(self._subscribers)
            if self.on_data:
                try:
                    self.on_data(chunk)
                except Exception:
                    pass
            for q in subs:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    # Backpressure: drop oldest then enqueue latest.
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        pass
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
