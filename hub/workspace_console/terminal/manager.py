"""In-memory interactive terminal session manager (not restored after hub restart)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from hub.workspace_console.terminal.pty_backend import (
    OutputPump,
    PtyLaunch,
    kill_process_tree,
    spawn_pty,
)
from hub.workspace_console.terminal.security import (
    TerminalSecurityError,
    default_shell_id,
    resolve_session_cwd,
    resolve_shell_executable,
    scrub_child_env,
)
from hub.workspace_console.terminal.settings import TerminalSettings, load_terminal_settings


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


AuditFn = Callable[..., None]


@dataclass
class TerminalSession:
    id: str
    name: str
    repository_id: str
    repository_name: str
    cwd: str
    shell: str
    env_label: str
    workspace: str
    actor: str
    created_at: str
    status: str = "starting"
    pid: int | None = None
    exit_code: int | None = None
    cols: int = 120
    rows: int = 32
    has_active_children: bool = False
    unread_bytes: int = 0
    last_output_at: str | None = None
    ended_at: str | None = None
    split_group: str | None = None
    _pty: Any = field(default=None, repr=False)
    _pump: OutputPump | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_public(self) -> dict[str, Any]:
        alive = bool(self._pty and getattr(self._pty, "alive", False))
        status = self.status
        if status == "running" and not alive:
            status = "exited"
        return {
            "id": self.id,
            "name": self.name,
            "repository_id": self.repository_id,
            "repository_name": self.repository_name,
            "cwd": self.cwd,
            "shell": self.shell,
            "environment": self.env_label,
            "workspace": self.workspace,
            "status": status,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "unread_bytes": self.unread_bytes,
            "last_output_at": self.last_output_at,
            "split_group": self.split_group,
            "alive": alive or status == "running",
            "has_active_process": alive or status == "running",
        }


class TerminalSessionManager:
    """Create / rename / duplicate / close / restart interactive repo terminals."""

    def __init__(
        self,
        *,
        registry: Any,
        settings: TerminalSettings | None = None,
        audit: AuditFn | None = None,
        hub_host: str = "127.0.0.1",
    ) -> None:
        self.registry = registry
        self.settings = settings or load_terminal_settings()
        self.audit = audit
        self.hub_host = hub_host
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.RLock()
        self._ws_clients: dict[str, int] = {}

    def _audit(self, action: str, **detail: Any) -> None:
        if not self.audit:
            return
        # Never include raw commands or terminal output.
        safe = {
            k: v
            for k, v in detail.items()
            if k
            in {
                "session_id",
                "repository_id",
                "shell",
                "pid",
                "exit_code",
                "actor",
                "name",
                "status",
                "workspace",
                "ok",
                "reason",
            }
        }
        try:
            self.audit(action=action, detail=safe)
        except Exception:
            pass

    def list_sessions(self, *, workspace: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = []
            for sess in self._sessions.values():
                if workspace and sess.workspace != workspace:
                    continue
                self._refresh_status(sess)
                rows.append(sess.to_public())
            rows.sort(key=lambda r: r.get("created_at") or "")
            return rows

    def get(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess:
                self._refresh_status(sess)
            return sess

    def create(
        self,
        *,
        repository_id: str,
        shell: str | None = None,
        name: str | None = None,
        relative_cwd: str | None = None,
        workspace: str = "work",
        actor: str = "owner",
        cols: int | None = None,
        rows: int | None = None,
        env_label: str = "development",
        split_group: str | None = None,
        duplicate_of: str | None = None,
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            raise TerminalSecurityError("Interactive terminal is disabled.", code="disabled")
        host = (self.hub_host or "").strip().lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise TerminalSecurityError(
                "Interactive terminals require localhost bind (CENTRAL_HUB_HOST).",
                code="non_local_bind",
            )

        repo = self.registry.get(repository_id) if self.registry is not None else None
        if repo is None or not getattr(repo, "enabled", True):
            raise TerminalSecurityError("Unknown or disabled repository.", code="repo_unknown")

        with self._lock:
            active = [s for s in self._sessions.values() if s.status in {"starting", "running"}]
            if len(active) >= self.settings.max_sessions:
                raise TerminalSecurityError(
                    f"Maximum of {self.settings.max_sessions} active terminals reached.",
                    code="session_limit",
                )

        cwd = resolve_session_cwd(repo, relative_cwd)
        shell_id, argv = resolve_shell_executable(
            shell or default_shell_id(), allow_cmd=self.settings.allow_cmd
        )
        cols_i = int(cols or self.settings.default_cols)
        rows_i = int(rows or self.settings.default_rows)
        session_id = uuid.uuid4().hex[:12]
        repo_name = getattr(repo, "name", repository_id) or repository_id
        label = (name or "").strip() or self._default_session_name(
            shell_id=shell_id,
            repository_id=repository_id,
            repository_name=repo_name,
        )
        if duplicate_of and (name or "").strip():
            label = f"{label} (copy)"
        elif duplicate_of and not (name or "").strip():
            label = self._default_session_name(
                shell_id=shell_id,
                repository_id=repository_id,
                repository_name=repo_name,
            )

        sess = TerminalSession(
            id=session_id,
            name=label[:80],
            repository_id=repository_id,
            repository_name=repo_name,
            cwd=str(cwd),
            shell=shell_id,
            env_label=env_label or "development",
            workspace=workspace,
            actor=actor,
            created_at=_utcnow(),
            cols=cols_i,
            rows=rows_i,
            split_group=split_group,
        )

        env = scrub_child_env()
        env["CENTRAL_HUB_TERMINAL"] = "1"
        env["CENTRAL_HUB_TERMINAL_SESSION"] = session_id
        env["CENTRAL_HUB_REPOSITORY_ID"] = repository_id

        launch = PtyLaunch(argv=argv, cwd=cwd, env=env, cols=cols_i, rows=rows_i)
        try:
            pty = spawn_pty(launch)
        except Exception as exc:
            raise TerminalSecurityError(f"Failed to start PTY: {exc}", code="pty_spawn_failed") from exc

        sess._pty = pty
        sess.pid = pty.pid
        sess.status = "running"

        def _on_data(chunk: bytes) -> None:
            sess.unread_bytes += len(chunk)
            sess.last_output_at = _utcnow()

        sess._pump = OutputPump(
            pty,
            max_buffer=self.settings.max_output_buffer_bytes,
            chunk_size=self.settings.read_chunk_bytes,
            on_data=_on_data,
        )

        with self._lock:
            self._sessions[session_id] = sess

        self._audit(
            "WC_TERMINAL_START",
            session_id=session_id,
            repository_id=repository_id,
            shell=shell_id,
            pid=sess.pid,
            actor=actor,
            name=sess.name,
            workspace=workspace,
            ok=True,
        )
        return sess.to_public()

    def rename(self, session_id: str, name: str) -> dict[str, Any]:
        sess = self._require(session_id)
        cleaned = (name or "").strip()
        if not cleaned:
            raise TerminalSecurityError("Name is required.", code="name_required")
        sess.name = cleaned[:80]
        return sess.to_public()

    def duplicate(self, session_id: str, *, actor: str = "owner") -> dict[str, Any]:
        sess = self._require(session_id)
        return self.create(
            repository_id=sess.repository_id,
            shell=sess.shell,
            name=None,
            workspace=sess.workspace,
            actor=actor,
            cols=sess.cols,
            rows=sess.rows,
            env_label=sess.env_label,
            split_group=sess.split_group or sess.id,
            duplicate_of=session_id,
        )

    def restart(self, session_id: str, *, actor: str = "owner", confirm: bool = False) -> dict[str, Any]:
        sess = self._require(session_id)
        if sess.status == "running" and not confirm:
            raise TerminalSecurityError(
                "Confirm restart to terminate the active process tree.",
                code="confirm_required",
            )
        meta = {
            "repository_id": sess.repository_id,
            "shell": sess.shell,
            "name": sess.name,
            "workspace": sess.workspace,
            "cols": sess.cols,
            "rows": sess.rows,
            "env_label": sess.env_label,
            "split_group": sess.split_group,
        }
        self.close(session_id, confirm=True, reason="restart")
        return self.create(
            repository_id=meta["repository_id"],
            shell=meta["shell"],
            name=meta["name"],
            workspace=meta["workspace"],
            actor=actor,
            cols=meta["cols"],
            rows=meta["rows"],
            env_label=meta["env_label"],
            split_group=meta["split_group"],
        )

    def close(self, session_id: str, *, confirm: bool = False, reason: str = "close") -> dict[str, Any]:
        sess = self._require(session_id)
        alive = bool(sess._pty and sess._pty.alive)
        if alive and not confirm:
            raise TerminalSecurityError(
                "Terminal has an active process. Confirm close to terminate its process tree.",
                code="confirm_required",
            )
        exit_code = self._terminate(sess)
        public = sess.to_public()
        with self._lock:
            self._sessions.pop(session_id, None)
            self._ws_clients.pop(session_id, None)
        self._audit(
            "WC_TERMINAL_STOP",
            session_id=session_id,
            repository_id=sess.repository_id,
            shell=sess.shell,
            pid=sess.pid,
            exit_code=exit_code,
            actor=sess.actor,
            reason=reason,
            ok=True,
        )
        return public

    def write(self, session_id: str, data: str | bytes) -> None:
        sess = self._require(session_id)
        if not sess._pty or not sess._pty.alive:
            raise TerminalSecurityError("Terminal is not running.", code="not_running")
        # AI/automation must not call this — routes enforce human WS only.
        sess._pty.write(data)

    def resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        sess = self._require(session_id)
        cols_i = max(20, min(400, int(cols)))
        rows_i = max(8, min(120, int(rows)))
        sess.cols = cols_i
        sess.rows = rows_i
        if sess._pty:
            sess._pty.resize(cols_i, rows_i)
        return sess.to_public()

    def mark_read(self, session_id: str) -> None:
        sess = self.get(session_id)
        if sess:
            sess.unread_bytes = 0

    def register_ws(self, session_id: str) -> None:
        with self._lock:
            self._ws_clients[session_id] = self._ws_clients.get(session_id, 0) + 1
            sess = self._sessions.get(session_id)
            if sess:
                sess.unread_bytes = 0

    def unregister_ws(self, session_id: str) -> None:
        with self._lock:
            cur = self._ws_clients.get(session_id, 0) - 1
            if cur <= 0:
                self._ws_clients.pop(session_id, None)
            else:
                self._ws_clients[session_id] = cur

    def active_process_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s._pty and s._pty.alive)

    def sessions_by_pid(self) -> dict[int, str]:
        """Map root PTY PID → session id for Ports ownership."""
        out: dict[int, str] = {}
        with self._lock:
            for sess in self._sessions.values():
                if sess.pid:
                    out[int(sess.pid)] = sess.id
        return out

    def annotate_ports(self, ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach terminal session metadata when a port PID matches a session tree root.

        Full tree walk is best-effort: we match exact PID or note repository_id overlap.
        """
        by_pid = self.sessions_by_pid()
        by_id: dict[str, TerminalSession] = {}
        by_repo: dict[str, list[str]] = {}
        with self._lock:
            for sess in self._sessions.values():
                by_id[sess.id] = sess
                if sess.status == "running":
                    by_repo.setdefault(sess.repository_id, []).append(sess.id)
        for row in ports:
            pid = row.get("pid")
            sid = by_pid.get(int(pid)) if pid not in (None, "") else None
            if sid:
                row["terminal_session_id"] = sid
                row["terminal_owned"] = True
                sess = by_id.get(sid)
                if sess:
                    row["terminal_name"] = sess.name
                    row["terminal_shell"] = sess.shell
                    row["terminal_pid"] = sess.pid
            else:
                repo_id = row.get("repository_id") or ""
                candidates = by_repo.get(repo_id) or []
                if candidates and row.get("managed_by_hub") is False:
                    # Soft association for long-running servers started from a repo terminal.
                    row["terminal_session_candidates"] = candidates
                    names = [by_id[c].name for c in candidates if c in by_id]
                    if names:
                        row["terminal_name"] = names[0]
                row.setdefault("terminal_owned", False)
        return ports

    def _default_session_name(
        self,
        *,
        shell_id: str,
        repository_id: str,
        repository_name: str,
    ) -> str:
        labels = {
            "powershell": "PowerShell",
            "cmd": "CMD",
            "bash": "bash",
            "sh": "sh",
        }
        shell_label = labels.get(shell_id, shell_id or "Terminal")
        with self._lock:
            n = 1 + sum(
                1
                for s in self._sessions.values()
                if s.repository_id == repository_id and s.shell == shell_id
            )
        return f"{shell_label} {n} — {repository_name}"

    def shutdown_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            try:
                self.close(sid, confirm=True, reason="shutdown")
            except Exception:
                pass

    def _require(self, session_id: str) -> TerminalSession:
        sess = self.get(session_id)
        if sess is None:
            raise TerminalSecurityError("Terminal session not found.", code="not_found")
        return sess

    def _refresh_status(self, sess: TerminalSession) -> None:
        if sess.status in {"closed", "exited"}:
            return
        if sess._pty and not sess._pty.alive:
            sess.status = "exited"
            sess.exit_code = sess._pty.exit_code
            if not sess.ended_at:
                sess.ended_at = _utcnow()

    def _terminate(self, sess: TerminalSession) -> int | None:
        pid = sess.pid
        exit_code = None
        if sess._pump:
            sess._pump.stop()
            sess._pump = None
        if sess._pty:
            try:
                exit_code = sess._pty.close()
            except Exception:
                exit_code = None
            sess._pty = None
        # Ensure child trees die even if ConPTY close left grandchildren.
        kill_process_tree(pid, force=True)
        sess.status = "closed"
        sess.exit_code = exit_code
        sess.ended_at = _utcnow()
        return exit_code
