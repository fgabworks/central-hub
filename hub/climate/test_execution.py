"""Explicit, allowlisted test execution for accepted coding proposals."""

from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import redact_text
from hub.repository_workspace.run_profiles import merged_profiles_for_repository, prepare_launch
from hub.repository_workspace.security import WorkspaceSecurityError


def _limit(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name) or default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_TEST_OUTPUT_CHARS = _limit("CODING_AGENT_TEST_OUTPUT_CHARS", 24_000, 1_000, 100_000)
DEFAULT_TEST_TIMEOUT_SECONDS = _limit("CODING_AGENT_TEST_TIMEOUT_SECONDS", 120, 1, 900)
_SAFE_EXECUTABLES = {"python", "python.exe", "pytest", "pytest.exe", "npm", "npm.cmd"}
_BANNED_TOKEN_RE = re.compile(r"(?:[|;&<>`]|\$\(|%COMSPEC%|\b(?:install|uninstall|commit|push|reset|clean|rm|del|rmdir|shutdown|reboot)\b)", re.I)
_SAFE_NPM_SCRIPT_RE = re.compile(r"^(?:jest|vitest|mocha|node\s+--test|react-scripts\s+test)(?:\s|$)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TestProfile:
    id: str
    name: str
    command: tuple[str, ...]
    targeted: bool = False
    full_suite: bool = False
    source: str = "detected"
    cwd: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": list(self.command),
            "targeted": self.targeted,
            "full_suite": self.full_suite,
            "source": self.source,
            "cwd": self.cwd,
        }


class CodingTestRunStore:
    def __init__(self, db: AgentCenterDb) -> None:
        self.db = db

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        columns = [
            "id", "proposal_id", "proposal_run_id", "workspace", "repository_id",
            "profile_id", "profile_name", "command_json", "cwd", "status",
            "started_at", "finished_at", "exit_code", "stdout", "stderr",
            "timed_out", "cancel_requested", "failed_tests_json", "changed_files_json",
            "follow_up_run_id", "follow_up_proposal_id", "created_at", "updated_at",
            "root_proposal_id", "iteration_depth", "failure_fingerprint",
            "repeated_failure_detected",
        ]
        values = dict(record)
        values["command_json"] = json.dumps(values.get("command") or [])
        values["failed_tests_json"] = json.dumps(values.get("failed_tests") or [])
        values["changed_files_json"] = json.dumps(values.get("changed_files") or [])
        values["timed_out"] = int(bool(values.get("timed_out")))
        values["cancel_requested"] = int(bool(values.get("cancel_requested")))
        values["repeated_failure_detected"] = int(bool(values.get("repeated_failure_detected")))
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO coding_test_runs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                "ON CONFLICT(id) DO UPDATE SET "
                + ",".join(f"{col}=excluded.{col}" for col in columns if col not in {"id", "created_at"}),
                tuple(values.get(col) for col in columns),
            )
        return self.get(str(record["id"])) or dict(record)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM coding_test_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        for public, stored in (("command", "command_json"), ("failed_tests", "failed_tests_json"), ("changed_files", "changed_files_json")):
            try:
                data[public] = json.loads(data.pop(stored) or "[]")
            except (TypeError, ValueError):
                data[public] = []
        data["timed_out"] = bool(data.get("timed_out"))
        data["cancel_requested"] = bool(data.get("cancel_requested"))
        data["repeated_failure_detected"] = bool(data.get("repeated_failure_detected"))
        return data

    def update(self, run_id: str, **changes: Any) -> dict[str, Any]:
        record = self.get(run_id)
        if record is None:
            raise WorkspaceSecurityError("Test run not found.", code="not_found")
        record.update(changes)
        record["updated_at"] = _now()
        return self.save(record)

    def latest_for_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM coding_test_runs WHERE proposal_id = ? ORDER BY created_at DESC LIMIT 1",
                (proposal_id,),
            ).fetchone()
        return self.get(str(row["id"])) if row is not None else None

    def has_previous_failure(self, root_proposal_id: str, fingerprint: str, *, exclude_id: str) -> bool:
        if not root_proposal_id or not fingerprint:
            return False
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM coding_test_runs WHERE root_proposal_id = ? "
                "AND failure_fingerprint = ? AND id <> ? LIMIT 1",
                (root_proposal_id, fingerprint, exclude_id),
            ).fetchone()
        return row is not None


class CodingTestExecutionService:
    def __init__(self, store: CodingTestRunStore, *, timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS, output_cap: int = MAX_TEST_OUTPUT_CHARS, on_complete: Any = None) -> None:
        self.store = store
        self.timeout_seconds = max(1, min(int(timeout_seconds), 900))
        self.output_cap = max(1000, min(int(output_cap), 100_000))
        self.on_complete = on_complete
        self._lock = threading.RLock()
        self._procs: dict[str, subprocess.Popen[str]] = {}

    def discover(self, repo: Any, root: Path, changed_files: list[str], profile_store: Any = None) -> list[TestProfile]:
        profiles: list[TestProfile] = []
        targeted = self._targeted_test_files(root, changed_files)
        if targeted:
            profiles.append(TestProfile("python-unittest-targeted", "Targeted Python tests", (sys.executable, "-m", "unittest", *targeted), True, False))
            if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
                profiles.append(TestProfile("python-pytest-targeted", "Targeted pytest tests", (sys.executable, "-m", "pytest", *targeted), True, False))
        if (root / "tests").is_dir():
            profiles.append(TestProfile("python-unittest", "Full Python unittest suite", (sys.executable, "-m", "unittest", "discover", "-s", "tests"), False, True))
            if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
                profiles.append(TestProfile("python-pytest", "Full pytest suite", (sys.executable, "-m", "pytest"), False, True))
        profiles.extend(self._npm_profiles(root))
        try:
            for profile in merged_profiles_for_repository(repo.id, store=profile_store):
                if profile.port_mode == "none" and not profile.live_profile and not profile.write_capable and "test" in f"{profile.id} {profile.name}".lower():
                    environment = "development" if "development" in profile.environments else ("stage" if "stage" in profile.environments else "")
                    if not environment:
                        continue
                    launch = prepare_launch(profile, repo_id=repo.id, repository_path=root, environment=environment)
                    command = (launch.executable, *launch.argv)
                    self._validate_command(command)
                    profiles.append(TestProfile(f"run-profile:{profile.id}", profile.name, command, False, False, "run_profile", str(launch.cwd)))
        except Exception:
            pass
        unique: dict[str, TestProfile] = {}
        for profile in profiles:
            unique.setdefault(profile.id, profile)
        return list(unique.values())[:12]

    def start(self, *, proposal: Any, repo: Any, root: Path, profile_id: str, profile_store: Any = None) -> dict[str, Any]:
        profiles = {p.id: p for p in self.discover(repo, root, proposal.affected_files, profile_store)}
        profile = profiles.get(str(profile_id or ""))
        if profile is None:
            raise WorkspaceSecurityError("Unknown or disallowed test profile.", code="test_profile_blocked")
        command = list(profile.command)
        self._validate_command(command)
        resolved_root = Path(profile.cwd).resolve() if profile.cwd else root.resolve()
        try:
            resolved_root.relative_to(root.resolve())
        except ValueError as exc:
            raise WorkspaceSecurityError("Test working directory escaped the repository.", code="workspace_isolation") from exc
        record = {
            "id": uuid.uuid4().hex,
            "proposal_id": proposal.id,
            "proposal_run_id": proposal.run_id,
            "workspace": proposal.workspace,
            "repository_id": proposal.repository_id,
            "profile_id": profile.id,
            "profile_name": profile.name,
            "command": command,
            "cwd": str(resolved_root),
            "status": "running",
            "started_at": _now(),
            "finished_at": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "cancel_requested": False,
            "failed_tests": [],
            "changed_files": list(proposal.affected_files),
            "follow_up_run_id": "",
            "follow_up_proposal_id": "",
            "root_proposal_id": str(getattr(proposal, "root_proposal_id", "") or proposal.id),
            "iteration_depth": int(getattr(proposal, "iteration_depth", 0) or 0),
            "failure_fingerprint": "",
            "repeated_failure_detected": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.store.save(record)
        threading.Thread(target=self._execute, args=(record["id"], command, resolved_root), daemon=True).start()
        return self.store.get(record["id"]) or record

    def skip(self, proposal: Any) -> dict[str, Any]:
        now = _now()
        return self.store.save({
            "id": uuid.uuid4().hex, "proposal_id": proposal.id, "proposal_run_id": proposal.run_id,
            "workspace": proposal.workspace, "repository_id": proposal.repository_id,
            "profile_id": "", "profile_name": "Skipped by user", "command": [], "cwd": "",
            "status": "skipped", "started_at": None, "finished_at": now, "exit_code": None,
            "stdout": "", "stderr": "", "timed_out": False, "cancel_requested": False,
            "failed_tests": [], "changed_files": list(proposal.affected_files),
            "follow_up_run_id": "", "follow_up_proposal_id": "", "created_at": now, "updated_at": now,
            "root_proposal_id": str(getattr(proposal, "root_proposal_id", "") or proposal.id),
            "iteration_depth": int(getattr(proposal, "iteration_depth", 0) or 0),
            "failure_fingerprint": "", "repeated_failure_detected": False,
        })

    def cancel(self, run_id: str) -> dict[str, Any]:
        record = self.store.get(run_id)
        if record is None:
            raise WorkspaceSecurityError("Test run not found.", code="not_found")
        if record["status"] != "running":
            return record
        self.store.update(run_id, cancel_requested=True)
        with self._lock:
            proc = self._procs.get(run_id)
        if proc is not None and proc.poll() is None:
            self._stop_process(proc)
        return self.store.get(run_id) or record

    def _execute(self, run_id: str, command: list[str], root: Path) -> None:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}}
        env.update({"CI": "1", "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"})
        try:
            proc = subprocess.Popen(command, cwd=root, env=env, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=flags, start_new_session=(os.name != "nt"))
            with self._lock:
                self._procs[run_id] = proc
            buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}
            per_stream_cap = max(500, self.output_cap // 2)
            def drain(pipe: Any, key: str) -> None:
                kept = 0
                if pipe is None:
                    return
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    if kept < per_stream_cap:
                        piece = chunk[: per_stream_cap - kept]
                        buffers[key].append(piece)
                        kept += len(piece)
            readers = [
                threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
                threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True),
            ]
            for reader in readers:
                reader.start()
            try:
                proc.wait(timeout=self.timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                self._stop_process(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)
            stdout = "".join(buffers["stdout"])
            stderr = "".join(buffers["stderr"])
            current = self.store.get(run_id) or {}
            cancelled = bool(current.get("cancel_requested"))
            status = "cancelled" if cancelled else ("timed_out" if timed_out else ("passed" if proc.returncode == 0 else "failed"))
            out = self._bounded(stdout)
            err = self._bounded(stderr)
            failed_tests = self._failed_tests(out + "\n" + err)
            fingerprint = self._failure_fingerprint(failed_tests, out, err) if status == "failed" else ""
            repeated = self.store.has_previous_failure(
                str(current.get("root_proposal_id") or ""), fingerprint, exclude_id=run_id
            ) if fingerprint else False
            # Keep the public state running until chain/audit persistence is complete,
            # so pollers never observe a terminal test without its timeline event.
            interim = self.store.update(run_id, status="running", finished_at=_now(), exit_code=proc.returncode, stdout=out, stderr=err, timed_out=timed_out, failed_tests=failed_tests, failure_fingerprint=fingerprint, repeated_failure_detected=repeated)
            completed = {**interim, "status": status}
            if self.on_complete:
                self.on_complete(completed)
            completed = self.store.update(run_id, status=status)
        except Exception as exc:
            interim = self.store.update(run_id, status="running", finished_at=_now(), stderr=self._bounded(str(exc)))
            completed = {**interim, "status": "failed"}
            if self.on_complete:
                self.on_complete(completed)
            completed = self.store.update(run_id, status="failed")
        finally:
            with self._lock:
                proc = self._procs.pop(run_id, None)
            if proc is not None:
                for pipe in (proc.stdout, proc.stderr):
                    try:
                        if pipe is not None:
                            pipe.close()
                    except OSError:
                        pass

    @staticmethod
    def _stop_process(proc: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError, ValueError):
            try:
                proc.terminate()
            except OSError:
                pass

    def _bounded(self, value: str) -> str:
        return redact_text(str(value or ""), limit=self.output_cap)[: self.output_cap]

    @staticmethod
    def _failed_tests(output: str) -> list[str]:
        found: list[str] = []
        patterns = (r"^FAIL:\s+([^\s(]+)", r"^ERROR:\s+([^\s(]+)", r"^FAILED\s+([^\s]+)")
        for line in str(output or "").splitlines():
            for pattern in patterns:
                match = re.search(pattern, line, re.I)
                if match and match.group(1) not in found:
                    found.append(match.group(1)[:240])
        return found[:40]

    @staticmethod
    def _failure_fingerprint(failed_tests: list[str], stdout: str, stderr: str) -> str:
        evidence = "\n".join([*sorted(failed_tests), stdout, stderr]).lower()
        evidence = re.sub(r"\b\d+(?:\.\d+)?s\b|0x[0-9a-f]+|\bpid[=: ]+\d+\b", "#", evidence)
        evidence = re.sub(r"\s+", " ", evidence).strip()[:8_000]
        return hashlib.sha256(evidence.encode("utf-8")).hexdigest() if evidence else ""

    @staticmethod
    def _targeted_test_files(root: Path, changed_files: list[str]) -> list[str]:
        found: list[str] = []
        for raw in changed_files[:20]:
            rel = str(raw).replace("\\", "/")
            path = Path(rel)
            candidates = [path] if path.name.startswith("test_") else [Path("tests") / f"test_{path.stem}.py", path.parent / f"test_{path.stem}.py"]
            for candidate in candidates:
                try:
                    resolved = (root / candidate).resolve()
                    resolved.relative_to(root.resolve())
                except ValueError:
                    continue
                if resolved.is_file() and resolved.suffix == ".py":
                    label = resolved.relative_to(root).as_posix()
                    if label not in found:
                        found.append(label)
        return found[:12]

    def _npm_profiles(self, root: Path) -> list[TestProfile]:
        path = root / "package.json"
        if not path.is_file() or path.stat().st_size > 512_000:
            return []
        try:
            scripts = dict(json.loads(path.read_text(encoding="utf-8")).get("scripts") or {})
        except (OSError, ValueError, TypeError):
            return []
        out: list[TestProfile] = []
        for name, body in scripts.items():
            if name != "test" and not str(name).startswith("test:"):
                continue
            if _BANNED_TOKEN_RE.search(str(body)) or not _SAFE_NPM_SCRIPT_RE.match(str(body).strip()):
                continue
            command = ("npm", "test") if name == "test" else ("npm", "run", str(name))
            out.append(TestProfile(f"npm:{name}", f"npm {name}", command, False, True, "package_json"))
        return out[:6]

    @staticmethod
    def _validate_command(command: list[str] | tuple[str, ...]) -> None:
        if not command:
            raise WorkspaceSecurityError("Empty test command.", code="test_command_blocked")
        executable = Path(str(command[0])).name.lower()
        if executable not in _SAFE_EXECUTABLES:
            raise WorkspaceSecurityError("Test executable is not allowlisted.", code="test_command_blocked")
        blocked_flags = ("--basetemp", "--rootdir", "--junitxml", "--cov-report", "--cache-dir")
        for index, token in enumerate(command):
            if not isinstance(token, str) or not token or _BANNED_TOKEN_RE.search(token):
                raise WorkspaceSecurityError("Unsafe test command token.", code="test_command_blocked")
            normalized = token.replace("\\", "/")
            if index > 0 and (
                normalized.startswith("/")
                or re.match(r"^[A-Za-z]:", normalized)
                or ".." in Path(normalized).parts
                or normalized.lower().startswith(blocked_flags)
            ):
                raise WorkspaceSecurityError("Test command may not write or resolve outside the repository.", code="test_command_blocked")
        joined = " ".join(str(token).lower() for token in command[1:4])
        if executable.startswith("python") and not ("-m unittest" in joined or "-m pytest" in joined):
            raise WorkspaceSecurityError("Python test command is not allowlisted.", code="test_command_blocked")
        if executable.startswith("pytest"):
            return
        if executable.startswith("npm") and not (len(command) >= 2 and command[1] in {"test", "run"}):
            raise WorkspaceSecurityError("npm command is not an approved test script.", code="test_command_blocked")
