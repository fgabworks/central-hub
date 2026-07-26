"""Run log storage with retention and secret redaction."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from hub.settings import ROOT_DIR

_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token|bearer|authorization|private[_-]?key)\s*[:=]\s*\S+"
)
_ENV_ASSIGN = re.compile(r"(?i)\b([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|KEY))\s*=\s*\S+")


def default_logs_dir() -> Path:
    configured = (os.environ.get("REPO_WS_RUN_LOG_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "data" / "repository_runs" / "logs"


def max_log_bytes() -> int:
    raw = (os.environ.get("REPO_WS_RUN_LOG_MAX_BYTES") or "").strip()
    try:
        value = int(raw) if raw else 1_048_576
    except ValueError:
        value = 1_048_576
    return max(16_384, min(value, 50_000_000))


def max_log_files() -> int:
    raw = (os.environ.get("REPO_WS_RUN_LOG_MAX_FILES") or "").strip()
    try:
        value = int(raw) if raw else 40
    except ValueError:
        value = 40
    return max(5, min(value, 500))


def redact_log_line(line: str) -> str:
    text = line or ""
    text = _SECRET_LINE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = _ENV_ASSIGN.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return text.rstrip("\n")


class RunLogStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_logs_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path_for(self, run_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", run_id)[:80]
        return self.root / f"{safe}.log"

    def append(self, run_id: str, line: str, *, stream: str = "stdout") -> None:
        cleaned = redact_log_line(line)
        if cleaned is None:
            return
        prefix = "ERR" if stream == "stderr" else "OUT"
        record = f"[{prefix}] {cleaned}\n"
        path = self.path_for(run_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(record)
            self._trim_file(path)
            self._enforce_retention()

    def read(self, run_id: str, *, offset: int = 0, limit: int = 400) -> dict:
        path = self.path_for(run_id)
        if not path.exists():
            return {"lines": [], "offset": 0, "next_offset": 0, "path": str(path)}
        with self._lock:
            text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(0, min(int(offset), len(lines)))
        chunk = lines[start : start + max(1, min(int(limit), 2000))]
        return {
            "lines": chunk,
            "offset": start,
            "next_offset": start + len(chunk),
            "total_lines": len(lines),
            "path": str(path),
        }

    def _trim_file(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        limit = max_log_bytes()
        if size <= limit:
            return
        # Keep the tail
        data = path.read_bytes()
        tail = data[-limit:]
        # align to newline
        nl = tail.find(b"\n")
        if nl >= 0 and nl + 1 < len(tail):
            tail = tail[nl + 1 :]
        path.write_bytes(tail)

    def _enforce_retention(self) -> None:
        files = sorted(self.root.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[max_log_files() :]:
            try:
                stale.unlink()
            except OSError:
                pass
