"""Codex CLI adapter — read-only exec with JSONL streaming (Okarun MVP)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from hub.agent_center.adapters.cli_common import BaseCliAdapter, _safe_cli_env
from hub.agent_center.codex_safety import (
    INCOMPLETE_CODEX_HOST_DETAIL,
    assert_git_unchanged,
    assert_safe_codex_argv,
    discover_codex_executable,
    inspect_codex_installation,
    is_complete_codex_runtime,
    git_status_snapshot,
    resolve_approved_repo_cwd,
    windows_codex_host_path,
)
from hub.agent_center.redact import classify_provider_error, redact_text


class CodexAdapter(BaseCliAdapter):
    authentication_method = "Visible `codex login` (ChatGPT / Codex device auth)"
    credential_storage = "Provider CLI managed (~/.codex). Central Hub never stores Codex tokens."
    settings_vendor = "OpenAI"
    settings_logo = "img/providers/codex.svg"
    profiles_allowed = ("okarun",)
    uses_jsonl = True
    default_model_token = "__provider_default__"

    def resolve_executable(self) -> str | None:
        return discover_codex_executable(self.descriptor.executable or "codex")

    def _runtime_diagnostics(self, exe: str | None = None) -> dict[str, Any]:
        import os

        inspection = inspect_codex_installation(self.descriptor.executable or "codex")
        chosen = str(exe or inspection.get("executable") or "")
        complete = bool(chosen) and is_complete_codex_runtime(chosen)
        source = str(inspection.get("source") or "")
        if chosen and inspection.get("executable"):
            try:
                if Path(chosen).resolve() != Path(str(inspection.get("executable"))).resolve():
                    source = "resolved"
            except OSError:
                source = "resolved"
        elif chosen and not inspection.get("executable"):
            source = source or "resolved"
        health = "ok" if complete else str(
            inspection.get("runtime_health")
            or inspection.get("error_code")
            or ("incomplete_host" if chosen else "missing")
        )
        host = str(inspection.get("host_path") or "")
        if chosen:
            try:
                host = str(windows_codex_host_path(chosen)) if os.name == "nt" else ""
            except OSError:
                pass
        return {
            "executable_path": chosen or str(inspection.get("incomplete_path") or ""),
            "runtime_complete": complete,
            "runtime_health": health,
            "discovery_source": source,
            "host_path": host,
        }

    def capabilities(self) -> dict[str, Any]:
        caps = super().capabilities()
        caps.update(
            {
                "dynamic_models": True,
                "provider_default_model": True,
                "jsonl_streaming": True,
                "repository_runs": True,
                "native_repository_investigation": True,
                "safe_session_continuation": True,
                "profiles": list(self.profiles_allowed),
            }
        )
        return caps

    def list_models(self) -> tuple[list[str], str]:
        details = self.list_model_details(force_refresh=False)
        return list(details.get("models") or []), str(details.get("models_source") or "none")

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        from hub.agent_center.codex_models import discover_codex_models

        exe = self.resolve_executable()
        discovered = discover_codex_models(exe, force_refresh=force_refresh)
        return {
            "models": list(discovered.get("models") or []),
            "model_details": list(discovered.get("model_details") or []),
            "groups": {
                "codex": [m for m in (discovered.get("models") or []) if m != self.default_model_token]
            },
            "recommended_model": discovered.get("recommended_model") or self.default_model_token,
            "recommendation_reason": discovered.get("models_source") or "provider_default",
            "models_source": discovered.get("models_source") or "provider_default",
            "configured_default": discovered.get("configured_default") or "",
            "reasoning_efforts": [],
            "error": discovered.get("error") or "",
        }

    def connection_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        exe = self.resolve_executable()
        if not exe:
            inspection = inspect_codex_installation(self.descriptor.executable or "codex")
            incomplete = inspection.get("error_code") == "incomplete_cli"
            return {
                "state": "unavailable",
                "detail": (
                    str(inspection.get("detail") or "").strip()
                    or (INCOMPLETE_CODEX_HOST_DETAIL if incomplete else "Codex CLI is not installed or not discoverable")
                ),
                "error_code": str(inspection.get("error_code") or "missing_cli"),
                "installed": bool(inspection.get("installed")),
                "authenticated": False,
                "version": "",
                "available": False,
                "cli_commands": ["codex"],
                "install_help": (
                    "Install the Codex CLI (`codex`) and use Connect to run official `codex login`. "
                    "Codex uses the authenticated Codex/ChatGPT account — separate from OPENAI_API_KEY billing. "
                    "On Windows the official `codex.exe` must sit beside `codex-code-mode-host.exe`."
                ),
                **self._runtime_diagnostics(),
            }
        version = self._detect_version(exe)
        runtime = self._runtime_diagnostics(exe)
        try:
            result = self._run_probe([exe, "login", "status"], timeout=20.0)
        except subprocess.TimeoutExpired:
            return {
                "state": "error",
                "detail": "Codex login status timed out",
                "error_code": "timeout",
                "installed": True,
                "authenticated": False,
                "version": version,
                "available": False,
                **runtime,
            }
        except OSError as exc:
            classified = classify_provider_error(str(exc))
            return {
                "state": "error",
                "detail": classified["detail"],
                "error_code": classified["code"],
                "installed": True,
                "authenticated": False,
                "version": version,
                "available": False,
                **runtime,
            }

        text = (result.stdout or result.stderr or "").strip()
        lower = text.lower()
        if result.returncode == 0 and "logged in" in lower:
            return {
                "state": "connected",
                "detail": redact_text(text, limit=240) or "Codex authenticated",
                "error_code": "",
                "installed": True,
                "authenticated": True,
                "version": version,
                "available": True,
                "account_label": _account_label_from_status(text),
                **runtime,
            }
        if "quota" in lower or "rate limit" in lower:
            classified = classify_provider_error(text)
            return {
                "state": "error",
                "detail": classified["detail"],
                "error_code": classified["code"],
                "installed": True,
                "authenticated": False,
                "version": version,
                "available": False,
                **runtime,
            }
        return {
            "state": "authentication_required",
            "detail": "Authentication required. Use Connect to run `codex login`.",
            "error_code": "authentication_required",
            "installed": True,
            "authenticated": False,
            "version": version,
            "available": False,
            **runtime,
        }

    def test_connection(self) -> dict[str, Any]:
        status = self.connection_status(force_refresh=True)
        ok = status.get("state") == "connected"
        return {"ok": ok, **status}

    def test_connection_with_repo(self, repo_path: str | Path, *, approved_roots: list[str | Path]) -> dict[str, Any]:
        """Connection probe plus read-only git safety verification against an approved repo."""
        status = self.test_connection()
        if not status.get("ok"):
            return status
        try:
            cwd = resolve_approved_repo_cwd(repo_path, approved_roots)
        except ValueError as exc:
            return {
                "ok": False,
                "state": "error",
                "detail": str(exc),
                "error_code": "invalid_repository",
                **{k: status.get(k) for k in ("installed", "authenticated", "version")},
            }
        before = git_status_snapshot(cwd)
        if not before.get("ok"):
            return {
                "ok": False,
                "state": "error",
                "detail": before.get("error") or "Unable to read git status",
                "error_code": "execution_error",
                "installed": status.get("installed"),
                "authenticated": status.get("authenticated"),
                "version": status.get("version"),
            }
        # Re-check auth/version only — do not mutate the tree.
        after = git_status_snapshot(cwd)
        try:
            assert_git_unchanged(before, after)
        except RuntimeError as exc:
            return {
                "ok": False,
                "state": "error",
                "detail": str(exc),
                "error_code": "read_only_violation",
                "installed": status.get("installed"),
                "authenticated": status.get("authenticated"),
                "version": status.get("version"),
                "git_before": before.get("porcelain"),
                "git_after": after.get("porcelain"),
            }
        return {
            **status,
            "ok": True,
            "git_before": before.get("porcelain"),
            "git_after": after.get("porcelain"),
            "safety_ok": True,
        }

    def connect(self) -> dict[str, Any]:
        """Start visible `codex login` — never authenticate silently."""
        import os
        import subprocess

        exe = self.resolve_executable()
        if not exe:
            inspection = inspect_codex_installation(self.descriptor.executable or "codex")
            incomplete = inspection.get("error_code") == "incomplete_cli"
            return {
                "ok": False,
                "state": "unavailable",
                "detail": (
                    str(inspection.get("detail") or "").strip()
                    or (INCOMPLETE_CODEX_HOST_DETAIL if incomplete else "Codex CLI is not installed or not discoverable")
                ),
                "error_code": str(inspection.get("error_code") or "missing_cli"),
            }
        argv = self._login_argv(exe)
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
        subprocess.Popen(argv, shell=False, creationflags=flags, env=_safe_cli_env())
        return {
            "ok": True,
            "state": "authentication_required",
            "detail": "Started `codex login` in a visible window. Complete authentication there, then Test Connection.",
            "error_code": "authentication_required",
            "login_command": "codex login",
        }

    def _login_argv(self, executable: str) -> list[str]:
        return [executable, "login"]

    def _logout_argv(self, executable: str) -> list[str]:
        return [executable, "logout"]

    def _detect_version(self, executable: str) -> str:
        try:
            result = self._run_probe([executable, "--version"], timeout=10.0)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        text = (result.stdout or result.stderr or "").strip()
        return redact_text(text.splitlines()[0] if text else "", limit=80)

    def build_argv(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        cwd: str,
        prompt_file: str = "",
        provider_session_id: str = "",
        persist_session: bool = False,
    ) -> list[str]:
        exe = self.resolve_executable() or (self.descriptor.executable or "codex")
        # Prefer stdin ("-") when a prompt file exists to avoid Windows argv limits.
        prompt_arg = "-" if prompt_file else (prompt or "")
        session_id = str(provider_session_id or "").strip()
        if session_id and not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            session_id,
        ):
            raise ValueError("Invalid Codex session id")
        argv = [exe, "-C", cwd, "--sandbox", "read-only", "exec"]
        if session_id:
            argv.extend(["resume", "--json"])
        else:
            argv.append("--json")
        if not persist_session:
            argv.append("--ephemeral")
        # Omit --model when using provider default so Codex uses its configured/recommended default.
        if model and model not in {"", self.default_model_token, "__provider_default__"}:
            argv.extend(["--model", model])
        if session_id:
            argv.append(session_id)
        argv.append(prompt_arg)
        assert_safe_codex_argv(argv, require_ephemeral=not persist_session)
        return argv

    def _default_template(self, mode: str) -> list[str]:
        return [
            "{executable}",
            "exec",
            "-C",
            "{cwd}",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--json",
            "-",
        ]


def _account_label_from_status(text: str) -> str:
    # Example: "Logged in using ChatGPT" — never pull tokens/emails from auth files.
    cleaned = redact_text(text, limit=120)
    lower = cleaned.lower()
    if "chatgpt" in lower:
        return "ChatGPT"
    if "api" in lower:
        return "API key"
    return cleaned[:80]
