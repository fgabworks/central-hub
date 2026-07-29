"""Approved repository run profiles (argv arrays, no shell strings).

YAML templates in config/run_profiles.yaml remain the built-in defaults.
Repository-specific profiles live in SQLite (profile_store) and override
templates by profile id without rewriting YAML.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from hub.repository_workspace.profile_store import RunProfileStore
from hub.repository_workspace.security import WorkspaceSecurityError, resolve_repo_root
from hub.settings import ROOT_DIR

ALLOWED_PLACEHOLDERS = frozenset({"port", "repository_path", "environment"})
ALLOWED_ENVIRONMENTS = frozenset({"development", "stage", "live", "custom"})
PORT_MODES = frozenset({"none", "fixed", "argument", "environment_variable"})
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_SAFE_EXE = re.compile(r"^[A-Za-z0-9_./\\:+-]+$")
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_./\\:+=,@%{}()\[\]'\" -]*$")
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


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
    default_port: int | None
    local_url: str
    health_url: str | None
    startup_timeout_seconds: float
    allowed_env_names: tuple[str, ...]
    live_profile: bool
    repository_ids: tuple[str, ...] = ()
    port_mode: str = "argument"
    fixed_port: int | None = None
    port_arg: str | None = None
    port_env: str | None = None
    description: str = ""
    enabled: bool = True
    approved: bool = True
    source: str = "yaml"
    provides_api: bool = False
    write_capable: bool = False

    def applies_to(self, repo_id: str) -> bool:
        if not self.repository_ids:
            return True
        return repo_id in self.repository_ids

    @property
    def uses_port(self) -> bool:
        return self.port_mode != "none"

    @property
    def allows_dynamic_port(self) -> bool:
        return self.port_mode in {"argument", "environment_variable"}


@dataclass
class PreparedLaunch:
    profile_id: str
    environment: str
    port: int | None
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
    port_mode: str = "argument"
    write_capable: bool = False


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


def _substitute(text: str, *, port: int | None, repository_path: str, environment: str) -> str:
    mapping = {
        "port": "" if port is None else str(port),
        "repository_path": repository_path,
        "environment": environment,
    }

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in mapping:
            raise RunProfileError(f"Disallowed placeholder {{{key}}}.", code="bad_placeholder")
        if key == "port" and port is None:
            raise RunProfileError(
                "Port placeholder used but profile port mode is none.",
                code="invalid_port",
            )
        return mapping[key]

    return _PLACEHOLDER_RE.sub(repl, text)


def _validate_localhost_url(url: str, *, field_name: str) -> None:
    raw = (url or "").strip()
    if not raw:
        return
    # Allow unresolved placeholders for parse; replace temporarily
    probe = _PLACEHOLDER_RE.sub("1", raw)
    parsed = urlparse(probe)
    if parsed.scheme not in {"http", "https"}:
        raise RunProfileError(
            f"{field_name} must be an http(s) URL.",
            code="invalid_url",
        )
    host = (parsed.hostname or "").lower()
    if host not in _LOCALHOST_HOSTS:
        raise RunProfileError(
            f"{field_name} must resolve to localhost/127.0.0.1 for managed local apps.",
            code="non_local_url",
        )


def _clamp_port(value: Any, *, field_name: str, required: bool = True) -> int | None:
    if value is None or value == "":
        if required:
            raise RunProfileError(f"{field_name} is required.", code="invalid_port")
        return None
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise RunProfileError(f"Invalid {field_name}.", code="invalid_port") from exc
    if not (1 <= port <= 65535):
        raise RunProfileError(f"{field_name} out of range.", code="invalid_port")
    return port


def infer_port_mode(raw: dict[str, Any], args: list[str]) -> str:
    explicit = str(raw.get("port_mode") or "").strip().lower()
    if explicit in PORT_MODES:
        return explicit
    if raw.get("port_env"):
        return "environment_variable"
    if raw.get("port_arg") or any("{port}" in str(a) for a in args):
        return "argument"
    if raw.get("fixed_port") is not None:
        return "fixed"
    local_url = str(raw.get("local_url") or "")
    if "{port}" in local_url:
        return "argument"
    if raw.get("default_port") is not None and not any("{port}" in str(a) for a in args):
        # Legacy profiles with a default port but no placeholder → treat as argument for compat
        return "argument"
    return "none"


def parse_profile(raw: dict[str, Any]) -> RunProfile:
    pid = str(raw.get("id") or raw.get("profile_id") or "").strip()
    name = str(raw.get("name") or pid).strip()
    if not pid:
        raise RunProfileError("Profile id is required.", code="invalid_profile")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", pid):
        raise RunProfileError("Profile id has invalid characters.", code="invalid_profile")
    executable = str(raw.get("executable") or "").strip()
    if not executable or not _SAFE_EXE.match(executable):
        raise RunProfileError(
            f"Profile {pid}: executable must be a safe path/token (no shell).",
            code="invalid_executable",
        )
    # Reject raw shell command strings masquerading as a single arg
    args_raw = raw.get("args")
    if isinstance(args_raw, str):
        raise RunProfileError(
            f"Profile {pid}: arguments must be an array, not a shell string.",
            code="invalid_args",
        )
    args_list = _as_str_list(args_raw)
    args = tuple(args_list)
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
    if not envs:
        envs = ("development",)

    port_mode = infer_port_mode(raw, args_list)
    fixed_port = _clamp_port(raw.get("fixed_port"), field_name="fixed_port", required=False)
    default_port = _clamp_port(
        raw.get("default_port"),
        field_name="default_port",
        required=False,
    )
    if port_mode == "fixed":
        if fixed_port is None:
            fixed_port = default_port or 8000
        default_port = fixed_port
    elif port_mode == "none":
        fixed_port = None
        if default_port is None:
            default_port = None
    else:
        if default_port is None:
            default_port = 8000

    local_url = str(raw.get("local_url") or (
        "http://127.0.0.1/" if port_mode == "none" else "http://127.0.0.1:{port}/"
    )).strip()
    health_url_raw = raw.get("health_url")
    health_url = str(health_url_raw).strip() if health_url_raw else None
    _validate_placeholders(local_url, field_name=f"{pid}.local_url")
    _validate_localhost_url(local_url, field_name=f"{pid}.local_url")
    if health_url:
        _validate_placeholders(health_url, field_name=f"{pid}.health_url")
        _validate_localhost_url(health_url, field_name=f"{pid}.health_url")

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
    write_capable = bool(raw.get("write_capable", False))
    if live and "live" not in envs:
        envs = envs + ("live",)

    port_env = raw.get("port_env")
    port_env_s = str(port_env).strip() if port_env else None
    if port_env_s and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", port_env_s):
        raise RunProfileError(f"Profile {pid}: invalid port_env.", code="invalid_port_env")
    port_arg = str(raw.get("port_arg")).strip() if raw.get("port_arg") else None
    if port_arg:
        _validate_placeholders(port_arg, field_name=f"{pid}.port_arg")
        if not _SAFE_ARG.match(port_arg):
            raise RunProfileError(f"Profile {pid}: invalid port_arg.", code="invalid_port_arg")

    if port_mode == "argument" and not port_arg and not any("{port}" in a for a in args):
        # Acceptable: caller may inject via prepare when port_arg later set; warn via default
        port_arg = port_arg or "--port"
    if port_mode == "environment_variable" and not port_env_s:
        port_env_s = "PORT"

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
        port_mode=port_mode,
        fixed_port=fixed_port,
        port_arg=port_arg,
        port_env=port_env_s,
        description=str(raw.get("description") or "").strip(),
        enabled=bool(raw.get("enabled", True)),
        approved=bool(raw.get("approved", True)),
        source=str(raw.get("source") or "yaml"),
        provides_api=bool(raw.get("provides_api", False)),
        write_capable=write_capable,
    )


def profile_to_dict(profile: RunProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "executable": profile.executable,
        "args": list(profile.args),
        "working_directory": profile.working_directory,
        "environments": list(profile.environments),
        "default_port": profile.default_port,
        "fixed_port": profile.fixed_port,
        "port_mode": profile.port_mode,
        "port_arg": profile.port_arg,
        "port_env": profile.port_env,
        "local_url": profile.local_url,
        "health_url": profile.health_url,
        "startup_timeout_seconds": profile.startup_timeout_seconds,
        "allowed_env_names": list(profile.allowed_env_names),
        "live_profile": profile.live_profile,
        "write_capable": profile.write_capable,
        "provides_api": profile.provides_api,
        "repository_ids": list(profile.repository_ids),
        "enabled": profile.enabled,
        "approved": profile.approved,
        "source": profile.source,
    }


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
        profile = parse_profile({**item, "source": "yaml", "approved": True, "enabled": True})
        if profile.id in seen:
            raise RunProfileError(f"Duplicate profile id {profile.id}.", code="duplicate_profile")
        seen.add(profile.id)
        out.append(profile)
    return out


def merged_profiles_for_repository(
    repo_id: str,
    *,
    store: RunProfileStore | None = None,
    yaml_profiles: list[RunProfile] | None = None,
    include_disabled: bool = False,
    include_unapproved: bool = False,
) -> list[RunProfile]:
    """YAML templates + DB overrides/additions for one repository."""
    templates = yaml_profiles if yaml_profiles is not None else load_run_profiles()
    by_id: dict[str, RunProfile] = {}
    for profile in templates:
        if profile.applies_to(repo_id):
            by_id[profile.id] = profile

    db = store or RunProfileStore()
    for row in db.list_for_repo(repo_id, include_unapproved=True):
        # DB rows are always scoped to this repo
        profile = parse_profile(row)
        # Frozen dataclass — rebuild with store metadata already in parse via row
        by_id[profile.id] = profile

    out: list[RunProfile] = []
    for profile in by_id.values():
        if not include_unapproved and not profile.approved:
            continue
        if not include_disabled and not profile.enabled:
            continue
        out.append(profile)
    out.sort(key=lambda p: (p.name.lower(), p.id))
    return out


def profiles_for_repository(
    repo_id: str,
    profiles: list[RunProfile] | None = None,
    *,
    store: RunProfileStore | None = None,
) -> list[RunProfile]:
    """Enabled+approved profiles for Run UI (YAML∪DB)."""
    if profiles is not None:
        return [p for p in profiles if p.applies_to(repo_id) and p.enabled and p.approved]
    return merged_profiles_for_repository(
        repo_id,
        store=store,
        include_disabled=False,
        include_unapproved=False,
    )


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
    port: int | None = None,
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
    if not profile.enabled:
        raise RunProfileError("Profile is disabled.", code="profile_disabled")
    if not profile.approved:
        raise RunProfileError("Profile is not approved.", code="profile_unapproved")

    resolved_port: int | None
    if profile.port_mode == "none":
        resolved_port = None
    elif profile.port_mode == "fixed":
        resolved_port = int(profile.fixed_port or profile.default_port or 0)
        if not (1 <= resolved_port <= 65535):
            raise RunProfileError("Fixed port out of range.", code="invalid_port")
    else:
        candidate = port if port is not None else profile.default_port
        if candidate is None:
            raise RunProfileError("Port is required for this profile.", code="invalid_port")
        resolved_port = int(candidate)
        if not (1 <= resolved_port <= 65535):
            raise RunProfileError("Port out of range.", code="invalid_port")

    needs_live_gate = bool(
        profile.live_profile
        or env_name == "live"
        or (profile.write_capable and (profile.live_profile or env_name == "live"))
    )
    if profile.write_capable and (profile.live_profile or env_name == "live"):
        needs_live_gate = True
    if needs_live_gate:
        if not live_runs_allowed():
            raise RunProfileError(
                "Live / write-capable live profiles are blocked. "
                "Set REPO_WS_ALLOW_LIVE_RUNS=true to enable.",
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
        _substitute(arg, port=resolved_port, repository_path=repo_posix, environment=env_name)
        for arg in profile.args
    ]
    if (
        profile.port_mode == "argument"
        and profile.port_arg
        and resolved_port is not None
        and not any("{port}" in a for a in profile.args)
        and str(resolved_port) not in argv
    ):
        # Append port_arg when {port} was not already baked into args.
        # If port_arg contains {port} (e.g. "--port {port}"), keep a single token.
        substituted = _substitute(
            profile.port_arg,
            port=resolved_port,
            repository_path=repo_posix,
            environment=env_name,
        )
        if "{port}" in profile.port_arg:
            argv = list(argv) + [substituted]
        else:
            argv = list(argv) + [substituted, str(resolved_port)]

    cwd_raw = _substitute(
        profile.working_directory,
        port=resolved_port,
        repository_path=repo_posix,
        environment=env_name,
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

    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "")),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "LANG": os.environ.get("LANG", "C"),
        "REPO_WS_ENVIRONMENT": env_name,
        "REPO_WS_REPOSITORY_PATH": repo_posix,
    }
    if resolved_port is not None:
        child_env["REPO_WS_PORT"] = str(resolved_port)
    child_env = {k: v for k, v in child_env.items() if v}
    used_names: list[str] = []
    for name in profile.allowed_env_names:
        if name in os.environ:
            child_env[name] = os.environ[name]
            used_names.append(name)
    if profile.port_mode == "environment_variable" and profile.port_env and resolved_port is not None:
        child_env[profile.port_env] = str(resolved_port)
        if profile.port_env not in used_names:
            used_names.append(profile.port_env)

    local_url = _substitute(
        profile.local_url, port=resolved_port, repository_path=repo_posix, environment=env_name
    )
    health_url = None
    if profile.health_url:
        health_url = _substitute(
            profile.health_url,
            port=resolved_port,
            repository_path=repo_posix,
            environment=env_name,
        )

    return PreparedLaunch(
        profile_id=profile.id,
        environment=env_name,
        port=resolved_port,
        executable=profile.executable,
        argv=argv,
        cwd=cwd_path,
        env=child_env,
        local_url=local_url,
        health_url=health_url,
        startup_timeout_seconds=profile.startup_timeout_seconds,
        live_profile=bool(profile.live_profile or env_name == "live"),
        argv_redacted=list(argv),
        env_names=used_names,
        port_mode=profile.port_mode,
        write_capable=profile.write_capable,
    )


def public_profile(profile: RunProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "environments": list(profile.environments),
        "default_port": profile.default_port,
        "fixed_port": profile.fixed_port,
        "port_mode": profile.port_mode,
        "port_arg": profile.port_arg,
        "port_env": profile.port_env,
        "allows_dynamic_port": profile.allows_dynamic_port,
        "uses_port": profile.uses_port,
        "local_url_template": profile.local_url,
        "health_url_template": profile.health_url,
        "startup_timeout_seconds": profile.startup_timeout_seconds,
        "allowed_env_names": list(profile.allowed_env_names),
        "live_profile": profile.live_profile,
        "write_capable": profile.write_capable,
        "provides_api": profile.provides_api,
        "repository_ids": list(profile.repository_ids),
        "executable": profile.executable,
        "args_template": list(profile.args),
        "working_directory": profile.working_directory,
        "enabled": profile.enabled,
        "approved": profile.approved,
        "source": profile.source,
    }
