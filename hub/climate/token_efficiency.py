"""Manual per-run CLIMATE vs Direct Codex token-efficiency benchmark.

Direct Codex is never launched from a normal CLIMATE prompt. The user must click
Evaluate Token Savings. The original CLIMATE run, conversation, and provider
session are not reused or rewritten.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.codex_jsonl import CodexJsonlAccumulator
from hub.agent_center.codex_safety import (
    assert_git_unchanged,
    assert_safe_codex_argv,
    git_status_snapshot,
)
from hub.agent_center.redact import redact_text
from hub.settings import ROOT_DIR

STATUS_NOT_MEASURED = "Not measured"
STATUS_MEASURING = "Measuring…"
STATUS_MEASURED = "Measured"
STATUS_UNAVAILABLE = "Benchmark unavailable"
STATUS_NOT_COMPARABLE = "Not comparable"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

COMMIT_CHANGED = "Benchmark cannot be reproduced: repository commit has changed."
ELIGIBLE_PROVIDER = "codex"
PACKET_MARKERS = (
    "CLIMATE context packet",
    "CLIMATE preflight context packet",
    "CLIMATE coding request",
    "Hub tools:",
)
_TASK_RE = re.compile(r"Task:\n(.*?)(?:\n(?:Confidence:|Repository access:|\n))", re.S)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def nullable_int(value: Any) -> int | None:
    if value is None or value is False:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_from_provider(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Copy provider-reported fields only. Missing keys stay null — never infer 0."""
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("input_tokens_details") if isinstance(raw.get("input_tokens_details"), dict) else {}
    cached = first_present(
        raw,
        "cached_input_tokens",
        "cache_read_input_tokens",
        "input_tokens_cached",
        "cached_tokens",
    )
    if cached is None:
        cached = first_present(details, "cached_tokens", "cache_read_input_tokens")
    input_tokens = first_present(raw, "input_tokens", "input", "prompt_tokens")
    output_tokens = first_present(raw, "output_tokens", "output", "completion_tokens")
    total = first_present(raw, "total_tokens", "total")
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    source = "unavailable"
    if total is not None or input_tokens is not None or output_tokens is not None:
        source = "provider"
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "source": source,
    }


def first_present(raw: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in raw and raw[key] is not None:
            return nullable_int(raw[key])
    return None


def extract_user_prompt(packed: str) -> str:
    text = str(packed or "")
    match = _TASK_RE.search(text)
    if match:
        return match.group(1).strip()
    return ""


def git_head(repo: Path) -> str:
    try:
        from hub.agent_center.adapters.cli_common import run_cli_capture

        result = run_cli_capture(
            ["git", "rev-parse", "HEAD"],
            timeout=10.0,
            cwd=str(repo),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def count_files_inspected(tool_activity: list[dict[str, Any]] | None, logs: str = "") -> int | None:
    if tool_activity is None and not logs:
        return None
    blob = "\n".join(
        str(item.get("name") or "") + "\n" + str(item.get("detail") or "")
        for item in (tool_activity or [])
        if isinstance(item, dict)
    )
    blob = blob + "\n" + str(logs or "")
    paths: set[str] = set()
    for match in re.finditer(
        r"(?:[A-Za-z]:)?[\\/]?(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z0-9]+",
        blob.replace("\\", "/"),
    ):
        path = match.group(0).lstrip("/")
        if any(part in path.lower() for part in ("windows/system32", "program files")):
            continue
        paths.add(path)
    commands = [
        item
        for item in (tool_activity or [])
        if isinstance(item, dict) and item.get("type") == "command_execution"
    ]
    if paths:
        return len(paths)
    if commands:
        return len(commands)
    if tool_activity is not None:
        return 0
    return None


def runtime_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


def comparison_from_totals(climate_total: int | None, direct_total: int | None) -> dict[str, Any] | None:
    if climate_total is None or direct_total is None or direct_total <= 0:
        return None
    signed = climate_total - direct_total
    percent = round((signed / direct_total) * 100, 1)
    relative = round(climate_total / direct_total, 2)
    if signed < 0:
        tone = "savings"
        primary = f"Saved {abs(signed):,} tokens ({percent:.1f}%)"
        secondary = f"CLIMATE used {abs(percent):.1f}% fewer provider tokens"
    elif signed > 0:
        tone = "increase"
        primary = f"Used {signed:,} more tokens (+{percent:.1f}%)"
        secondary = f"CLIMATE used {relative:.2f}× Direct provider usage"
    else:
        tone = "neutral"
        primary = "No token difference"
        secondary = "CLIMATE matched Direct provider usage"
    return {
        "climate_total": climate_total,
        "direct_total": direct_total,
        "difference": signed,
        "percent": percent,
        "relative": relative,
        "tone": tone,
        "headline": primary,
        "primary": primary,
        "secondary": secondary,
    }


def _empty_record(run_id: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "climate_run_id": run_id,
        "status": STATUS_UNAVAILABLE,
        "reason": "",
        "snapshot": None,
        "direct": None,
        "comparison": None,
        "measured_at": None,
        "validity": {"ok": False, "failures": []},
    }


def _comparability_note(snapshot: dict[str, Any], status: str | None) -> str:
    if snapshot.get("session_reused") is True or snapshot.get("session_fresh") is False:
        return (
            "CLIMATE resumed a prior Codex session; Direct was a fresh ephemeral run. "
            "CLIMATE provider totals include accumulated prior-turn context."
        )
    if status == STATUS_MEASURED and snapshot.get("session_fresh") is True:
        return "CLIMATE and Direct were both fresh provider sessions."
    return ""


class TokenEfficiencyService:
    """Persists benchmark metadata beside the original Agent Center run."""

    def __init__(
        self,
        *,
        persist_root: Path | None = None,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.persist_root = Path(persist_root or (ROOT_DIR / "data" / "agent_center" / "runs"))
        self._popen = popen or subprocess.Popen
        self._live: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._active_id: str | None = None
        self._cancel: set[str] = set()
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._threads: dict[str, threading.Thread] = {}

    def path_for(self, run_id: str) -> Path:
        return self.persist_root / run_id / "token_efficiency.json"

    def load(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            if run_id in self._live:
                return json.loads(json.dumps(self._live[run_id]))
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        with self._lock:
            self._live[run_id] = data
        return json.loads(json.dumps(data))

    def save(self, run_id: str, record: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        stored = json.loads(json.dumps(record))
        with self._lock:
            self._live[run_id] = stored
        if persist:
            folder = self.persist_root / run_id
            try:
                folder.mkdir(parents=True, exist_ok=True)
                self.path_for(run_id).write_text(
                    json.dumps(stored, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return json.loads(json.dumps(stored))

    def public(self, record: dict[str, Any] | None) -> dict[str, Any]:
        record = record if isinstance(record, dict) else _empty_record("")
        snapshot = dict(record.get("snapshot") or {})
        direct = dict(record.get("direct") or {}) if record.get("direct") else None
        if direct:
            direct = {
                key: direct.get(key)
                for key in (
                    "status",
                    "usage",
                    "runtime_ms",
                    "files_inspected",
                    "tool_executions",
                    "success",
                    "git_unchanged",
                    "error",
                    "started_at",
                    "finished_at",
                    "argv",
                )
            }
        return {
            "status": record.get("status") or STATUS_UNAVAILABLE,
            "reason": record.get("reason") or "",
            "eligible": bool(snapshot.get("provider") == ELIGIBLE_PROVIDER and snapshot.get("user_prompt")),
            "climate": {
                "total": (snapshot.get("climate_usage") or {}).get("total_tokens"),
                "input": (snapshot.get("climate_usage") or {}).get("input_tokens"),
                "cached_input": (snapshot.get("climate_usage") or {}).get("cached_input_tokens"),
                "output": (snapshot.get("climate_usage") or {}).get("output_tokens"),
                "runtime_ms": snapshot.get("runtime_ms"),
                "files_inspected": snapshot.get("files_inspected"),
                "preflight_tokens_est": snapshot.get("context_tokens_est"),
                "context_packet_chars": snapshot.get("context_packet_chars"),
                "source_candidates": snapshot.get("source_candidates"),
                "session_fresh": snapshot.get("session_fresh"),
                "session_reused": snapshot.get("session_reused"),
                "usage_source": (snapshot.get("climate_usage") or {}).get("source") or "unavailable",
            },
            "direct": direct,
            "comparison": self._public_comparison(record, snapshot),
            "comparability_note": _comparability_note(snapshot, record.get("status")),
            "measured_at": record.get("measured_at"),
            "validity": record.get("validity") or {"ok": False, "failures": []},
            "snapshot": {
                "repository_id": snapshot.get("repository_id"),
                "commit_sha": snapshot.get("commit_sha"),
                "provider": snapshot.get("provider"),
                "model": snapshot.get("model"),
                "read_only": snapshot.get("read_only"),
                "codex_version": snapshot.get("codex_version"),
                "reasoning_config": snapshot.get("reasoning_config"),
            },
        }

    @staticmethod
    def _public_comparison(record: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
        climate_total = (snapshot.get("climate_usage") or {}).get("total_tokens")
        direct_total = ((record.get("direct") or {}).get("usage") or {}).get("total_tokens")
        rebuilt = comparison_from_totals(climate_total, direct_total)
        if rebuilt:
            return rebuilt
        if record.get("status") == STATUS_MEASURED:
            return {
                "climate_total": climate_total,
                "direct_total": direct_total,
                "difference": None,
                "percent": None,
                "relative": None,
                "tone": "unavailable",
                "headline": "Token comparison unavailable",
                "primary": "Token comparison unavailable",
                "secondary": "",
            }
        return record.get("comparison")

    def capture_snapshot(
        self,
        *,
        run_id: str,
        user_prompt: str,
        repository_id: str,
        repository_path: str,
        provider: str,
        model: str,
        read_only: bool = True,
        session_reused: bool | None = None,
        context_packet_chars: int | None = None,
        context_tokens_est: int | None = None,
        source_candidates: int | None = None,
        codex_executable: str = "",
        codex_version: str = "",
        reasoning_config: Any = None,
        persist: bool = False,
        commit_sha: str | None = None,
        infer_commit: bool = True,
    ) -> dict[str, Any]:
        existing = self.load(run_id) or _empty_record(run_id)
        if existing.get("status") == STATUS_MEASURED and existing.get("direct"):
            # Never replace a completed Direct comparison when refreshing CLIMATE fields.
            snapshot = dict(existing.get("snapshot") or {})
        else:
            snapshot = {}
        if commit_sha:
            commit = str(commit_sha)
        elif infer_commit:
            commit = git_head(Path(repository_path)) if repository_path else ""
        else:
            commit = str(snapshot.get("commit_sha") or "")
        snapshot.update(
            {
                "user_prompt": str(user_prompt or "").strip(),
                "repository_id": repository_id,
                "repository_path": str(repository_path or ""),
                "commit_sha": commit or snapshot.get("commit_sha") or "",
                "provider": provider,
                "model": model,
                "reasoning_config": reasoning_config,
                "codex_executable": codex_executable or snapshot.get("codex_executable") or "",
                "codex_version": codex_version or snapshot.get("codex_version") or "",
                "read_only": bool(read_only),
                "sandbox": "read-only",
                "session_reused": session_reused,
                "session_fresh": (not session_reused) if session_reused is not None else None,
                "context_packet_chars": context_packet_chars,
                "context_tokens_est": context_tokens_est,
                "source_candidates": source_candidates,
                "captured_at": snapshot.get("captured_at") or utcnow(),
            }
        )
        status = existing.get("status") or STATUS_NOT_MEASURED
        reason = existing.get("reason") or ""
        if provider != ELIGIBLE_PROVIDER:
            status = STATUS_UNAVAILABLE
            reason = "Token efficiency comparison is available for Codex runs only."
        elif not snapshot.get("user_prompt"):
            status = STATUS_UNAVAILABLE
            reason = "Original user prompt was not recorded."
        elif not snapshot.get("commit_sha"):
            status = STATUS_UNAVAILABLE
            reason = (
                "This run was recorded without a commit SHA. "
                "Re-run the prompt on the current commit to enable a measured comparison."
            )
        elif status not in {
            STATUS_MEASURING,
            STATUS_MEASURED,
            STATUS_CANCELLED,
            STATUS_FAILED,
            STATUS_NOT_COMPARABLE,
        }:
            status = STATUS_NOT_MEASURED
            reason = ""
        if existing.get("status") == STATUS_MEASURED and existing.get("direct"):
            status = STATUS_MEASURED
            reason = existing.get("reason") or ""
        record = {
            **existing,
            "climate_run_id": run_id,
            "status": status,
            "reason": reason,
            "snapshot": snapshot,
        }
        folder_exists = (self.persist_root / run_id).is_dir()
        return self.save(run_id, record, persist=persist or folder_exists)

    def update_climate_metrics(
        self,
        run_id: str,
        *,
        usage: dict[str, Any] | None,
        started_at: str | None = None,
        finished_at: str | None = None,
        tool_activity: list[dict[str, Any]] | None = None,
        logs: str = "",
        session_reused: bool | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        record = self.load(run_id) or _empty_record(run_id)
        snapshot = dict(record.get("snapshot") or {})
        snapshot["climate_usage"] = usage_from_provider(usage)
        snapshot["runtime_ms"] = runtime_ms(started_at, finished_at)
        snapshot["files_inspected"] = count_files_inspected(tool_activity, logs)
        if session_reused is not None:
            snapshot["session_reused"] = bool(session_reused)
            snapshot["session_fresh"] = not bool(session_reused)
        record["snapshot"] = snapshot
        if record.get("status") == STATUS_UNAVAILABLE and snapshot.get("provider") == ELIGIBLE_PROVIDER:
            if snapshot.get("user_prompt"):
                record["status"] = STATUS_NOT_MEASURED
                record["reason"] = ""
        folder_exists = (self.persist_root / run_id).is_dir()
        return self.save(run_id, record, persist=persist and (folder_exists or persist))

    def reconstruct_from_agent_run(self, run: dict[str, Any], *, repository_path: str = "") -> dict[str, Any]:
        run_id = str(run.get("id") or "")
        packed = str(run.get("packed_prompt") or run.get("prompt") or "")
        prompt = extract_user_prompt(packed) or ""
        repos = list(run.get("repository_ids") or [])
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        record = self.capture_snapshot(
            run_id=run_id,
            user_prompt=prompt,
            repository_id=str(repos[0] if repos else ""),
            repository_path=repository_path,
            provider=str(run.get("agent_id") or run.get("provider") or ""),
            model=str(run.get("model") or ""),
            read_only=True,
            session_reused=bool(usage.get("session_reused")) if "session_reused" in usage else None,
            context_packet_chars=len(packed) or None,
            persist=(self.persist_root / run_id).is_dir(),
            commit_sha=str(run.get("commit_sha") or ""),
            infer_commit=False,
        )
        record = self.update_climate_metrics(
            run_id,
            usage=usage,
            started_at=run.get("started_at"),
            finished_at=run.get("finished_at"),
            tool_activity=list(run.get("tool_activity") or []),
            logs=str(run.get("logs") or ""),
            session_reused=usage.get("session_reused") if "session_reused" in usage else None,
            persist=(self.persist_root / run_id).is_dir(),
        )
        snapshot = dict(record.get("snapshot") or {})
        if not snapshot.get("commit_sha"):
            record["status"] = STATUS_UNAVAILABLE
            record["reason"] = (
                "This run was recorded without a commit SHA. "
                "Re-run the prompt on the current commit to enable a measured comparison."
            )
            record = self.save(run_id, record, persist=(self.persist_root / run_id).is_dir())
        return record

    def fairness_gate(
        self,
        record: dict[str, Any],
        *,
        repository_id: str,
        repository_path: str,
        codex_version: str = "",
    ) -> dict[str, Any]:
        snapshot = dict(record.get("snapshot") or {})
        failures: list[str] = []
        current_sha = git_head(Path(repository_path)) if repository_path else ""
        recorded_sha = str(snapshot.get("commit_sha") or "")
        if snapshot.get("provider") != ELIGIBLE_PROVIDER:
            failures.append("Provider is not Codex.")
        if not snapshot.get("user_prompt"):
            failures.append("Original user prompt is missing.")
        if str(snapshot.get("repository_id") or "") != str(repository_id or ""):
            failures.append("Repository does not match the recorded run.")
        if not recorded_sha:
            failures.append(
                "This run was recorded without a commit SHA. "
                "Re-run the prompt on the current commit to enable a measured comparison."
            )
        elif current_sha != recorded_sha:
            failures.append(COMMIT_CHANGED)
        if not snapshot.get("read_only", True):
            failures.append("Recorded run was not read-only.")
        recorded_model = str(snapshot.get("model") or "").strip()
        if not recorded_model:
            failures.append("Recorded model is missing.")
        recorded_reasoning = snapshot.get("reasoning_config") if isinstance(snapshot.get("reasoning_config"), dict) else {}
        if recorded_reasoning.get("sandbox") and str(recorded_reasoning.get("sandbox")) != "read-only":
            failures.append("Recorded run was not read-only.")
        recorded_version = str(snapshot.get("codex_version") or "").strip()
        if recorded_version and codex_version and recorded_version != str(codex_version).strip():
            failures.append("Codex version has changed.")
        ok = not failures
        status = record.get("status") or STATUS_NOT_MEASURED
        reason = ""
        if not ok:
            status = STATUS_NOT_COMPARABLE if COMMIT_CHANGED in failures else STATUS_UNAVAILABLE
            reason = failures[0]
        return {
            "ok": ok,
            "failures": failures,
            "status": status,
            "reason": reason,
            "current_commit_sha": current_sha,
        }

    def start_direct(
        self,
        run_id: str,
        *,
        adapter: CodexAdapter,
        repository_id: str,
        repository_path: str,
        timeout_seconds: float = 600.0,
    ) -> dict[str, Any]:
        record = self.load(run_id) or _empty_record(run_id)
        snapshot = dict(record.get("snapshot") or {})
        if record.get("status") == STATUS_MEASURED and record.get("direct"):
            return record
        if record.get("status") == STATUS_MEASURING:
            return record
        version = ""
        recorded_exe = str(snapshot.get("codex_executable") or "")
        try:
            exe = recorded_exe if recorded_exe and Path(recorded_exe).is_file() else (adapter.resolve_executable() or recorded_exe or "")
            if hasattr(adapter, "_detect_version") and exe:
                version = adapter._detect_version(exe) or ""
        except Exception:
            exe = recorded_exe
        gate = self.fairness_gate(
            record,
            repository_id=repository_id,
            repository_path=repository_path,
            codex_version=version,
        )
        if not gate["ok"]:
            record["status"] = gate["status"]
            record["reason"] = gate["reason"]
            record["validity"] = {"ok": False, "failures": gate["failures"]}
            return self.save(run_id, record, persist=True)

        with self._lock:
            if self._active_id and self._active_id != run_id:
                record["status"] = STATUS_FAILED
                record["reason"] = "A Direct benchmark is already running."
                return self.save(run_id, record, persist=True)
            self._active_id = run_id
            self._cancel.discard(run_id)

        cwd = str(Path(repository_path))
        prompt_text = str(snapshot.get("user_prompt") or "")
        folder = self.persist_root / run_id
        folder.mkdir(parents=True, exist_ok=True)
        prompt_path = folder / "direct_prompt.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        packed_lower = prompt_text
        if any(marker.lower() in packed_lower.lower() for marker in PACKET_MARKERS):
            record["status"] = STATUS_FAILED
            record["reason"] = "Direct prompt unexpectedly contained a CLIMATE packet."
            with self._lock:
                if self._active_id == run_id:
                    self._active_id = None
            return self.save(run_id, record, persist=True)

        argv = adapter.build_argv(
            mode="ask",
            prompt=prompt_text,
            model=str(snapshot.get("model") or ""),
            cwd=cwd,
            prompt_file=str(prompt_path),
            provider_session_id="",
            persist_session=False,
        )
        if exe:
            argv = list(argv)
            argv[0] = exe
        assert_safe_codex_argv(argv, require_ephemeral=True)
        lowered = [part.lower() for part in argv]
        if "resume" in lowered or "--ephemeral" not in lowered or "--json" not in lowered:
            record["status"] = STATUS_FAILED
            record["reason"] = "Direct Codex argv was not a fresh ephemeral JSON run."
            with self._lock:
                if self._active_id == run_id:
                    self._active_id = None
            return self.save(run_id, record, persist=True)

        record["status"] = STATUS_MEASURING
        record["reason"] = ""
        record["direct"] = {
            "status": STATUS_MEASURING,
            "argv": [redact_text(part, limit=240) for part in argv],
            "started_at": utcnow(),
        }
        record["validity"] = {"ok": True, "failures": []}
        self.save(run_id, record, persist=True)

        thread = threading.Thread(
            target=self._run_direct,
            kwargs={
                "run_id": run_id,
                "argv": argv,
                "cwd": cwd,
                "prompt_path": str(prompt_path),
                "timeout_seconds": timeout_seconds,
                "jsonl_path": str(folder / "direct_benchmark.jsonl"),
            },
            daemon=True,
            name=f"climate-te-{run_id[:8]}",
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return self.load(run_id) or record

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancel.add(run_id)
            proc = self._procs.get(run_id)
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
            except OSError:
                pass
        record = self.load(run_id) or _empty_record(run_id)
        if record.get("status") == STATUS_MEASURING:
            record["status"] = STATUS_CANCELLED
            record["reason"] = "Cancelled"
            direct = dict(record.get("direct") or {})
            direct["status"] = STATUS_CANCELLED
            direct["finished_at"] = utcnow()
            record["direct"] = direct
            self.save(run_id, record, persist=True)
        with self._lock:
            if self._active_id == run_id:
                self._active_id = None
        return self.load(run_id) or record

    def _run_direct(
        self,
        *,
        run_id: str,
        argv: list[str],
        cwd: str,
        prompt_path: str,
        timeout_seconds: float,
        jsonl_path: str,
    ) -> None:
        before = git_status_snapshot(Path(cwd))
        child_env = dict(os.environ)
        for key in list(child_env):
            upper = key.upper()
            if any(token in upper for token in ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "PRIVATE_KEY", "COOKIE")):
                child_env.pop(key, None)
        stdin_handle = None
        jsonl_handle = None
        started = time.monotonic()
        accumulator = CodexJsonlAccumulator(session_reused=False)
        try:
            stdin_handle = open(prompt_path, "r", encoding="utf-8", errors="replace")
            jsonl_handle = open(jsonl_path, "w", encoding="utf-8", errors="replace")
            proc = self._popen(
                argv,
                cwd=cwd,
                shell=False,
                stdin=stdin_handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
        except OSError as exc:
            self._finish_direct(
                run_id,
                status=STATUS_FAILED,
                error=redact_text(str(exc), limit=240),
                started_monotonic=started,
                before=before,
                cwd=cwd,
            )
            if stdin_handle:
                stdin_handle.close()
            if jsonl_handle:
                jsonl_handle.close()
            return

        with self._lock:
            self._procs[run_id] = proc
        deadline = time.monotonic() + max(30.0, float(timeout_seconds))
        try:
            assert proc.stdout is not None
            while True:
                if run_id in self._cancel:
                    self._terminate(proc)
                    self._finish_direct(
                        run_id,
                        status=STATUS_CANCELLED,
                        error="Cancelled",
                        accumulator=accumulator,
                        started_monotonic=started,
                        before=before,
                        cwd=cwd,
                    )
                    return
                if time.monotonic() > deadline:
                    self._terminate(proc)
                    self._finish_direct(
                        run_id,
                        status=STATUS_FAILED,
                        error="Direct Codex timed out",
                        accumulator=accumulator,
                        started_monotonic=started,
                        before=before,
                        cwd=cwd,
                    )
                    return
                line = proc.stdout.readline()
                if line:
                    if jsonl_handle:
                        jsonl_handle.write(line)
                        jsonl_handle.flush()
                    accumulator.feed(line)
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.05)
            if run_id in self._cancel:
                self._finish_direct(
                    run_id,
                    status=STATUS_CANCELLED,
                    error="Cancelled",
                    accumulator=accumulator,
                    started_monotonic=started,
                    before=before,
                    cwd=cwd,
                )
                return
            after = git_status_snapshot(Path(cwd))
            git_ok = True
            git_error = ""
            try:
                assert_git_unchanged(before, after)
            except RuntimeError as exc:
                git_ok = False
                git_error = str(exc)
            success = proc.returncode == 0 and not accumulator.errors and git_ok
            status = STATUS_MEASURED if success or accumulator.usage.get("total_tokens") or accumulator.usage.get("input_tokens") else STATUS_FAILED
            if not git_ok:
                status = STATUS_FAILED
            if accumulator.errors and status != STATUS_MEASURED:
                status = STATUS_FAILED
            error = git_error or accumulator.error_summary() or (
                "" if success or status == STATUS_MEASURED else "Direct Codex did not report token usage"
            )
            self._finish_direct(
                run_id,
                status=status,
                error=error,
                accumulator=accumulator,
                started_monotonic=started,
                before=before,
                after=after,
                cwd=cwd,
                git_unchanged=git_ok,
                success=success and git_ok,
            )
        finally:
            if stdin_handle:
                stdin_handle.close()
            if jsonl_handle:
                jsonl_handle.close()
            with self._lock:
                self._procs.pop(run_id, None)
                if self._active_id == run_id:
                    self._active_id = None
                self._cancel.discard(run_id)

    def _finish_direct(
        self,
        run_id: str,
        *,
        status: str,
        error: str = "",
        accumulator: CodexJsonlAccumulator | None = None,
        started_monotonic: float,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None = None,
        cwd: str = "",
        git_unchanged: bool | None = None,
        success: bool | None = None,
    ) -> None:
        record = self.load(run_id) or _empty_record(run_id)
        if run_id in self._cancel:
            status = STATUS_CANCELLED
            error = error or "Cancelled"
        snapshot = dict(record.get("snapshot") or {})
        raw_usage = getattr(accumulator, "raw_usage", None) if accumulator else None
        usage = usage_from_provider(raw_usage if isinstance(raw_usage, dict) else None)
        files = count_files_inspected(accumulator.tool_activity if accumulator else None)
        tools = None
        if accumulator is not None:
            tools = len(
                [row for row in accumulator.tool_activity if row.get("type") == "command_execution"]
            )
        elapsed_ms = int(max(0.0, (time.monotonic() - started_monotonic) * 1000))
        if git_unchanged is None and before is not None:
            try:
                assert_git_unchanged(before, after or git_status_snapshot(Path(cwd)))
                git_unchanged = True
            except Exception:
                git_unchanged = False
        climate_total = (snapshot.get("climate_usage") or {}).get("total_tokens")
        direct_total = usage.get("total_tokens")
        comparison = None
        final_status = status
        if status == STATUS_MEASURED and (climate_total is None or direct_total is None):
            final_status = STATUS_UNAVAILABLE
            error = error or "Provider token usage was unavailable for comparison."
        elif status == STATUS_MEASURED:
            comparison = comparison_from_totals(climate_total, direct_total)
        record["status"] = final_status
        record["reason"] = error if final_status != STATUS_MEASURED else ""
        record["measured_at"] = utcnow() if final_status == STATUS_MEASURED else record.get("measured_at")
        record["comparison"] = comparison
        record["direct"] = {
            "status": final_status,
            "usage": usage,
            "runtime_ms": elapsed_ms,
            "files_inspected": files,
            "tool_executions": tools,
            "success": bool(success) if success is not None else final_status == STATUS_MEASURED,
            "git_unchanged": git_unchanged,
            "error": redact_text(error, limit=400) if error else "",
            "started_at": (record.get("direct") or {}).get("started_at") or utcnow(),
            "finished_at": utcnow(),
            "argv": (record.get("direct") or {}).get("argv") or [],
        }
        self.save(run_id, record, persist=True)

    @staticmethod
    def _terminate(proc: subprocess.Popen[str]) -> None:
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
