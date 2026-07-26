"""Approved repository run profiles (argv arrays, no shell strings)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hub.repository_workspace.security import WorkspaceSecurityError, resolve_repo_root
from hub.settings import ROOT_DIR

ALLOWED_PLACEHOLDERS = frozenset({"port", "repository_path", "environment"})
ALLOWED_ENVIRONMENTS = frozenset({"development", "stage", "live"})
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SAFE_EXE = re.compile(r"^[A-Za-z0-9_./\\:+-]+$")
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_./\\:+=,@%{} -]*$")


class RunProfileError(WorkspaceSecurityError):
    """Invalid or blocked run profile."""


@dataclass(frozen=True)
class RunProfile:
    id: str
    name: str
    executable: str
    args: tuple[str, ...]
    working_directory: str
    environments: tuple[str, ...]
    default_port: int
    local_url: str
    health_url: str | None
    startup_timeout_seconds: float
    allowed_env_names: tuple[str, ...]
    live_profile: bool
    repository_ids: tuple[str, ...] = ()
    port_arg: str | None = None  # unused when {port} already in args
    port_env: str | None = None
    description: str = ""

    def applies_to(self, repo_id: str) -> bool:
        if not self.repository_ids:
            return True
        return repo_id in self.repository_ids


@dataclass
class PreparedLaunch:
    profile_id: str
    environment: str
    port: int
    executable: str
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    local_url: str
    health_url: str | None
    startup_timeout_seconds: float
    live_profile: bool
    argv_redacted: list[str]
    env_names: list[str]


def default_profiles_path() -> Path:
    configured = (os.environ.get("REPO_WS_RUN_PROFILES") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "config" / "run_profiles.yaml"


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _validate_placeholders(text: str, *, field_name: str) -> None:
    for match in _PLACEHOLDER_RE.finditer(text or ""):
        name = match.group(1)
        if name not in ALLOWED_PLACEHOLDERS:
            raise RunProfileError(
                f"Disallowed placeholder {{{name}}} in {field_name}.",
                code="bad_placeholder",
            )


def _substitute(text: str, *, port: int, repository_path: str, environment: str) -> str:
    mapping = {
        "port": str(port),
        "repository_path": repository_path,
        "environment": environment,
    }

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            raise RunProfileError(f"Disallowed placeholder {{{key}}}.", code="bad_placeholder")
        return mapping[key]

    return _PLACEHOLDER_RE.sub(repl, text)


def parse_profile(raw: dict[str, Any]) -> RunProfile:
    pid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or pid).strip()
    if not pid:
        raise RunProfileError("Profile id is required.", code="invalid_profile")
    executable = str(raw.get("executable") or "").strip()
    if not executable or not _SAFE_EXE.match(executable):
        raise RunProfileError(
            f"Profile {pid}: executable must be a safe path/token (no shell).",
            code="invalid_executable",
        )
    args = tuple(_as_str_list(raw.get("args")))
    for arg in args:
        _validate_placeholders(arg, field_name=f"{pid}.args")
        if not _SAFE_ARG.match(arg):
            raise RunProfileError(
                f"Profile {pid}: argument contains unsafe characters.",
                code="invalid_args",
            )
    cwd = str(raw.get("working_directory") or "{repository_path}").strip()
    _validate_placeholders(cwd, field_name=f"{pid}.working_directory")
    envs = tuple(
        e.strip().lower()
        for e in _as_str_list(raw.get("environments") or ["development"])
        if e.strip()
    )
    for e in envs:
        if e not in ALLOWED_ENVIRONMENTS:
            raise RunProfileError(
                f"Profile {pid}: invalid environment {e!r}.",
                code="invalid_environment",
            )
    try:
        default_port = int(raw.get("default_port") or 8000)
    except (TypeError, ValueError) as exc:
        raise RunProfileError(f"Profile {pid}: invalid default_port.", code="invalid_port") from exc
    if not (1 <= default_port <= 65535):
        raise RunProfileError(f"Profile {pid}: default_port out of range.", code="invalid_port")

    local_url = str(raw.get("local_url") or "http://127.0.0.1:{port}/").strip()
    health_url_raw = raw.get("health_url")
    health_url = str(health_url_raw).strip() if health_url_raw else None
    _validate_placeholders(local_url, field_name=f"{pid}.local_url")
    if health_url:
        _validate_placeholders(health_url, field_name=f"{pid}.health_url")

    try:
        startup = float(raw.get("startup_timeout_seconds") or 30)
    except (TypeError, ValueError) as exc:
        raise RunProfileError(f"Profile {pid}: invalid startup timeout.", code="invalid_timeout") from exc
    startup = max(1.0, min(startup, 600.0))

    allowed_env = tuple(
        n.strip()
        for n in _as_str_list(raw.get("allowed_env_names"))
        if n and n.strip() and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n.strip())
    )
    live = bool(raw.get("live_profile", False))
    if live and "live" not in envs:
        envs = envs + ("live",)

    port_env = raw.get("port_env")
    port_env_s = str(port_env).strip() if port_env else None
    if port_env_s and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", port_env_s):
        raise RunProfileError(f"Profile {pid}: invalid port_env.", code="invalid_port_env")

    return RunProfile(
        id=pid,
        name=name,
        executable=executable,
        args=args,
        working_directory=cwd,
        environments=envs,
        default_port=default_port,
        local_url=local_url,
        health_url=health_url,
        startup_timeout_seconds=startup,
        allowed_env_names=allowed_env,
        live_profile=live,
        repository_ids=tuple(_as_str_list(raw.get("repository_ids"))),
        port_arg=str(raw.get("port_arg")).strip() if raw.get("port_arg") else None,
        port_env=port_env_s,
        description=str(raw.get("description") or "").strip(),
    )


def load_run_profiles(path: Path | None = None) -> list[RunProfile]:
    cfg = path or default_profiles_path()
    if not cfg.exists():
        return []
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    items = raw.get("profiles") or []
    if not isinstance(items, list):
        raise RunProfileError("run_profiles.yaml: profiles must be a list.", code="invalid_config")
    out: list[RunProfile] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        profile = parse_profile(item)
        if profile.id in seen:
            raise RunProfileError(f"Duplicate profile id {profile.id}.", code="duplicate_profile")
        seen.add(profile.id)
        out.append(profile)
    return out


def profiles_for_repository(repo_id: str, profiles: list[RunProfile] | None = None) -> list[RunProfile]:
    all_profiles = profiles if profiles is not None else load_run_profiles()
    return [p for p in all_profiles if p.applies_to(repo_id)]


def live_runs_allowed() -> bool:
    return (os.environ.get("REPO_WS_ALLOW_LIVE_RUNS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def prepare_launch(
    profile: RunProfile,
    *,
    repo_id: str,
    repository_path: Path,
    environment: str,
    port: int,
    confirm_live: bool = False,
) -> PreparedLaunch:
    env_name = (environment or "").strip().lower()
    if env_name not in ALLOWED_ENVIRONMENTS:
        raise RunProfileError("Invalid environment.", code="invalid_environment")
    if env_name not in profile.environments:
        raise RunProfileError(
            f"Profile {profile.id} does not allow environment {env_name}.",
            code="environment_blocked",
        )
    if not profile.applies_to(repo_id):
        raise RunProfileError("Profile not allowed for this repository.", code="profile_scope")
    if not (1 <= int(port) <= 65535):
        raise RunProfileError("Port out of range.", code="invalid_port")

    if profile.live_profile or env_name == "live":
        if not live_runs_allowed():
            raise RunProfileError(
                "Live run profiles are blocked. Set REPO_WS_ALLOW_LIVE_RUNS=true to enable.",
                code="live_blocked",
            )
        if not confirm_live:
            raise RunProfileError(
                "Live profile requires explicit confirmation.",
                code="confirm_required",
            )

    root = repository_path.resolve()
    if resolve_repo_root(str(root)) is None:
        raise RunProfileError("Repository local path is unavailable.", code="unavailable")

    repo_posix = root.as_posix()
    argv = [
        _substitute(arg, port=port, repository_path=repo_posix, environment=env_name)
        for arg in profile.args
    ]
    if profile.port_arg and str(port) not in argv:
        argv = list(argv) + [
            _substitute(profile.port_arg, port=port, repository_path=repo_posix, environment=env_name),
            str(port),
        ]

    cwd_raw = _substitute(
        profile.working_directory, port=port, repository_path=repo_posix, environment=env_name
    )
    cwd_path = Path(cwd_raw).expanduser()
    if not cwd_path.is_absolute():
        cwd_path = (root / cwd_path).resolve()
    else:
        cwd_path = cwd_path.resolve()
    try:
        cwd_path.relative_to(root)
    except ValueError as exc:
        raise RunProfileError(
            "Working directory must resolve under the repository root.",
            code="cwd_escape",
        ) from exc
    if not cwd_path.is_dir():
        raise RunProfileError("Working directory does not exist.", code="cwd_missing")
    cwd = cwd_path

    # Build env: inherit minimal safe set + allowed names from os.environ (values stay server-side).
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "")),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "LANG": os.environ.get("LANG", "C"),
        "REPO_WS_ENVIRONMENT": env_name,
        "REPO_WS_PORT": str(port),
        "REPO_WS_REPOSITORY_PATH": repo_posix,
    }
    # Drop empty optional keys
    child_env = {k: v for k, v in child_env.items() if v}
    used_names: list[str] = []
    for name in profile.allowed_env_names:
        if name in os.environ:
            child_env[name] = os.environ[name]
            used_names.append(name)
    if profile.port_env:
        child_env[profile.port_env] = str(port)
        if profile.port_env not in used_names:
            used_names.append(profile.port_env)

    local_url = _substitute(
        profile.local_url, port=port, repository_path=repo_posix, environment=env_name
    )
    health_url = None
    if profile.health_url:
        health_url = _substitute(
            profile.health_url, port=port, repository_path=repo_posix, environment=env_name
        )

    # Redact env values from argv preview (argv itself shouldn't contain secrets).
    argv_redacted = list(argv)

    return PreparedLaunch(
        profile_id=profile.id,
        environment=env_name,
        port=int(port),
        executable=profile.executable,
        argv=argv,
        cwd=cwd,
        env=child_env,
        local_url=local_url,
        health_url=health_url,
        startup_timeout_seconds=profile.startup_timeout_seconds,
        live_profile=bool(profile.live_profile or env_name == "live"),
        argv_redacted=argv_redacted,
        env_names=used_names,
    )


def public_profile(profile: RunProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "environments": list(profile.environments),
        "default_port": profile.default_port,
        "local_url_template": profile.local_url,
        "health_url_template": profile.health_url,
        "startup_timeout_seconds": profile.startup_timeout_seconds,
        "allowed_env_names": list(profile.allowed_env_names),
        "live_profile": profile.live_profile,
        "repository_ids": list(profile.repository_ids),
        "executable": profile.executable,
        "args_template": list(profile.args),
    }
