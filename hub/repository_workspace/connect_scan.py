"""Read-only local workspace scanner for Connect Local Workspace.

Never executes commands, installs packages, or reads secret files.
All detection is filesystem metadata + allowlisted manifest text only.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hub.agent_center.models import INSTRUCTION_FILENAMES
from hub.registry.git_util import git_urls_match, normalize_git_url, read_origin_url
from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    redact_audit_detail,
    resolve_repo_root,
    should_skip_dir,
)

_MAX_MANIFEST_BYTES = 262_144
_README_NAMES = (
    "README.md",
    "README.MD",
    "Readme.md",
    "README.txt",
    "README",
    "readme.md",
)
_ENTRY_CANDIDATES = (
    "app.py",
    "main.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
    "server.py",
    "run.py",
    "index.js",
    "index.ts",
    "server.js",
    "src/main.py",
    "src/index.js",
    "src/index.ts",
    "src/main.tsx",
    "src/App.tsx",
)
_LAUNCH_INFO = (
    "start.ps1",
    "start.bat",
    "run.ps1",
    "run.bat",
    "dev.ps1",
    "dev.bat",
    "Dockerfile",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)


@dataclass
class SuggestedProfile:
    suggestion_id: str
    name: str
    executable: str
    args: list[str]
    working_directory: str
    environments: list[str]
    default_port: int
    local_url: str
    health_url: str | None
    port_env: str | None
    allowed_env_names: list[str]
    rationale: str
    untrusted: bool = True

    def to_public(self) -> dict[str, Any]:
        data = asdict(self)
        data["command_preview"] = [self.executable, *self.args]
        return data


@dataclass
class WorkspaceScanResult:
    ok: bool
    path: str
    exists: bool
    is_directory: bool
    accessible: bool
    error: str = ""
    error_code: str = ""
    folder_name: str = ""
    is_git: bool = False
    git_remote_url: str | None = None
    git_branch: str | None = None
    registered_git_url: str | None = None
    remote_matches_registered: bool | None = None
    remote_mismatch: bool = False
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    readme_files: list[str] = field(default_factory=list)
    ai_instruction_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    package_scripts: list[dict[str, str]] = field(default_factory=list)
    informational_files: list[str] = field(default_factory=list)
    suggested_profiles: list[SuggestedProfile] = field(default_factory=list)
    suggested_ports: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    existing_local_path: str | None = None
    replacing_existing_path: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "exists": self.exists,
            "is_directory": self.is_directory,
            "accessible": self.accessible,
            "error": self.error,
            "error_code": self.error_code,
            "folder_name": self.folder_name,
            "is_git": self.is_git,
            "git_remote_url": self.git_remote_url,
            "git_branch": self.git_branch,
            "registered_git_url": self.registered_git_url,
            "remote_matches_registered": self.remote_matches_registered,
            "remote_mismatch": self.remote_mismatch,
            "languages": list(self.languages),
            "frameworks": list(self.frameworks),
            "package_managers": list(self.package_managers),
            "readme_files": list(self.readme_files),
            "ai_instruction_files": list(self.ai_instruction_files),
            "entry_points": list(self.entry_points),
            "package_scripts": list(self.package_scripts),
            "informational_files": list(self.informational_files),
            "suggested_profiles": [p.to_public() for p in self.suggested_profiles],
            "suggested_ports": list(self.suggested_ports),
            "warnings": list(self.warnings),
            "existing_local_path": self.existing_local_path,
            "replacing_existing_path": self.replacing_existing_path,
            "note": (
                "Scan is read-only. Suggested run commands are untrusted and are "
                "not executed during scanning."
            ),
        }


def _normalize_user_path(raw: str) -> str:
    text = (raw or "").strip().strip('"').strip("'")
    if text.lower().startswith("file:///"):
        text = text[8:]
    elif text.lower().startswith("file://"):
        text = text[7:]
    return text


def _safe_root_file(root: Path, name: str) -> Path | None:
    """Return path to a shallow file under root if it exists and is not a secret."""
    if not name:
        return None
    rel = name.replace("\\", "/").lstrip("./")
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    if len(parts) > 2:
        return None
    if any(is_blocked_secret(p) for p in parts):
        return None
    if any(should_skip_dir(p) for p in parts[:-1]):
        return None
    candidate = (root.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if is_blocked_secret(candidate.relative_to(root.resolve()).as_posix()):
        return None
    return candidate


def _read_text_capped(path: Path, *, limit: int = _MAX_MANIFEST_BYTES) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > limit:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_git_branch(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    try:
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if text.startswith("ref:"):
        ref = text.split(":", 1)[1].strip()
        if ref.startswith("refs/heads/"):
            return ref[len("refs/heads/") :]
        return ref
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", text):
        return f"detached:{text[:12]}"
    return None


def _is_git_repo(root: Path) -> bool:
    git = root / ".git"
    return git.is_dir() or git.is_file()


def scan_workspace_path(
    raw_path: str,
    *,
    registered_git_url: str | None = None,
    registered_name: str | None = None,
    existing_local_path: str | None = None,
    repo_id: str | None = None,
) -> WorkspaceScanResult:
    """Validate and scan a user-selected folder. No subprocess / imports of app code."""
    cleaned = _normalize_user_path(raw_path)
    result = WorkspaceScanResult(
        ok=False,
        path=cleaned,
        exists=False,
        is_directory=False,
        accessible=False,
        registered_git_url=registered_git_url or None,
        existing_local_path=existing_local_path or None,
    )
    if not cleaned:
        result.error = "A local folder path is required."
        result.error_code = "missing_path"
        return result

    probe = Path(cleaned).expanduser()
    try:
        if not probe.exists():
            result.error = "Path does not exist."
            result.error_code = "missing"
            return result
        result.exists = True
        if not probe.is_dir():
            result.error = "Path is not a directory."
            result.error_code = "not_directory"
            return result
        result.is_directory = True
        # Touch listing to catch permission errors early.
        next(probe.iterdir(), None)
        result.accessible = True
    except PermissionError:
        result.error = "Path is not accessible."
        result.error_code = "inaccessible"
        return result
    except OSError as exc:
        result.error = redact_audit_detail(f"Unable to access path: {exc}", limit=200)
        result.error_code = "inaccessible"
        return result

    root = resolve_repo_root(str(probe))
    if root is None:
        result.error = "Path could not be resolved as a usable directory."
        result.error_code = "unavailable"
        return result

    result.path = str(root)
    result.folder_name = root.name
    result.ok = True

    if existing_local_path:
        existing_root = resolve_repo_root(existing_local_path)
        if existing_root is not None and existing_root != root:
            result.replacing_existing_path = True
            result.warnings.append(
                "This repository already has a local path. Saving will replace it."
            )

    result.is_git = _is_git_repo(root)
    if result.is_git:
        result.git_remote_url = read_origin_url(root)
        result.git_branch = _read_git_branch(root)
        if registered_git_url:
            if result.git_remote_url:
                matched = git_urls_match(result.git_remote_url, registered_git_url)
                result.remote_matches_registered = matched
                result.remote_mismatch = not matched
                if not matched:
                    result.warnings.append(
                        "Detected Git remote does not match the registered repository URL. "
                        "Review carefully before confirming."
                    )
            else:
                result.remote_matches_registered = None
                result.warnings.append(
                    "Git repository detected but origin remote URL could not be read."
                )
    else:
        result.warnings.append("Folder is not a Git repository.")

    # Marker presence
    for name in _README_NAMES:
        if _safe_root_file(root, name):
            result.readme_files.append(name)
            break
    for name in INSTRUCTION_FILENAMES:
        if _safe_root_file(root, name):
            result.ai_instruction_files.append(name)
    for name in _ENTRY_CANDIDATES:
        if _safe_root_file(root, name):
            result.entry_points.append(name.replace("\\", "/"))
    for name in _LAUNCH_INFO:
        if _safe_root_file(root, name):
            result.informational_files.append(name)

    pyproject = _safe_root_file(root, "pyproject.toml")
    requirements = _safe_root_file(root, "requirements.txt")
    package_json = _safe_root_file(root, "package.json")
    pipfile = _safe_root_file(root, "Pipfile")
    poetry_lock = _safe_root_file(root, "poetry.lock")
    pnpm = _safe_root_file(root, "pnpm-lock.yaml")
    yarn = _safe_root_file(root, "yarn.lock")
    npm_lock = _safe_root_file(root, "package-lock.json")

    dep_blob = ""
    if pyproject:
        result.languages.append("Python")
        result.package_managers.append("pip/poetry")
        text = _read_text_capped(pyproject) or ""
        dep_blob += "\n" + text.lower()
    if requirements:
        if "Python" not in result.languages:
            result.languages.append("Python")
        if "pip" not in result.package_managers and "pip/poetry" not in result.package_managers:
            result.package_managers.append("pip")
        dep_blob += "\n" + (_read_text_capped(requirements) or "").lower()
    if pipfile:
        if "Python" not in result.languages:
            result.languages.append("Python")
        result.package_managers.append("pipenv")
        dep_blob += "\n" + (_read_text_capped(pipfile) or "").lower()
    if poetry_lock and "poetry" not in " ".join(result.package_managers).lower():
        result.package_managers.append("poetry")

    # Additional requirements-*.txt (root only)
    try:
        for child in root.iterdir():
            if not child.is_file():
                continue
            name = child.name.lower()
            if name.startswith("requirements") and name.endswith(".txt"):
                if is_blocked_secret(child.name):
                    continue
                if child.name != "requirements.txt":
                    dep_blob += "\n" + (_read_text_capped(child) or "").lower()
    except OSError:
        pass

    if "flask" in dep_blob:
        if "Flask" not in result.frameworks:
            result.frameworks.append("Flask")
    if "fastapi" in dep_blob or "uvicorn" in dep_blob:
        if "FastAPI" not in result.frameworks:
            result.frameworks.append("FastAPI")
    if "django" in dep_blob or _safe_root_file(root, "manage.py"):
        if "Django" not in result.frameworks:
            result.frameworks.append("Django")

    scripts: dict[str, str] = {}
    pkg_text = ""
    if package_json:
        if "JavaScript/TypeScript" not in result.languages:
            result.languages.append("JavaScript/TypeScript")
        pkg_text = _read_text_capped(package_json) or ""
        try:
            data = json.loads(pkg_text) if pkg_text else {}
        except json.JSONDecodeError:
            data = {}
            result.warnings.append("package.json could not be parsed; scripts skipped.")
        if isinstance(data, dict):
            raw_scripts = data.get("scripts") or {}
            if isinstance(raw_scripts, dict):
                for key, value in raw_scripts.items():
                    k = str(key)
                    # Names only + redacted command preview (no secret-looking assigns)
                    cmd = redact_audit_detail(str(value), limit=120)
                    scripts[k] = cmd
                    result.package_scripts.append({"name": k, "command": cmd})
            deps = " ".join(
                str(x)
                for block in (data.get("dependencies"), data.get("devDependencies"))
                if isinstance(block, dict)
                for x in block.keys()
            ).lower()
            dep_blob += "\n" + deps + "\n" + pkg_text.lower()
            if "next" in deps or "next" in scripts:
                result.frameworks.append("Next.js")
            if "vite" in deps or "vite" in pkg_text.lower():
                result.frameworks.append("Vite")
            if "react" in deps and "Next.js" not in result.frameworks:
                result.frameworks.append("React")
            if "express" in deps:
                result.frameworks.append("Express")

    if pnpm:
        result.package_managers.append("pnpm")
    elif yarn:
        result.package_managers.append("yarn")
    elif npm_lock or package_json:
        result.package_managers.append("npm")

    # Deduplicate lists preserving order
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    result.languages = _uniq(result.languages)
    result.frameworks = _uniq(result.frameworks)
    result.package_managers = _uniq(result.package_managers)

    result.suggested_profiles = _suggest_profiles(
        root=root,
        repo_id=repo_id or root.name,
        frameworks=result.frameworks,
        languages=result.languages,
        package_managers=result.package_managers,
        scripts=scripts,
        entry_points=result.entry_points,
        display_name=registered_name or root.name,
    )
    ports: list[int] = []
    for profile in result.suggested_profiles:
        if profile.default_port not in ports:
            ports.append(profile.default_port)
    result.suggested_ports = ports
    return result


def _suggest_profiles(
    *,
    root: Path,
    repo_id: str,
    frameworks: list[str],
    languages: list[str],
    package_managers: list[str],
    scripts: dict[str, str],
    entry_points: list[str],
    display_name: str,
) -> list[SuggestedProfile]:
    suggestions: list[SuggestedProfile] = []
    slug = re.sub(r"[^a-z0-9]+", "-", (repo_id or "repo").lower()).strip("-") or "repo"

    def add(
        sid: str,
        name: str,
        exe: str,
        args: list[str],
        port: int,
        rationale: str,
        *,
        port_env: str | None = None,
        env_names: list[str] | None = None,
        health: str | None = None,
    ) -> None:
        suggestions.append(
            SuggestedProfile(
                suggestion_id=f"{slug}-{sid}",
                name=name,
                executable=exe,
                args=args,
                working_directory="{repository_path}",
                environments=["development"],
                default_port=port,
                local_url=f"http://127.0.0.1:{{port}}/",
                health_url=health or f"http://127.0.0.1:{{port}}/",
                port_env=port_env,
                allowed_env_names=env_names or [],
                rationale=rationale,
                untrusted=True,
            )
        )

    pm = "npm"
    if "pnpm" in package_managers:
        pm = "pnpm"
    elif "yarn" in package_managers:
        pm = "yarn"

    if "Next.js" in frameworks:
        add(
            "next-dev",
            f"{display_name} · Next.js dev",
            pm,
            ["run", "dev", "--", "-p", "{port}"] if pm == "npm" else ["run", "dev", "--", "-p", "{port}"],
            3000,
            "Detected Next.js; suggested `dev` script (untrusted).",
        )
    elif "Vite" in frameworks or ("dev" in scripts and "JavaScript/TypeScript" in languages):
        port = 5173 if "Vite" in frameworks else 3000
        script = "dev" if "dev" in scripts else ("start" if "start" in scripts else None)
        if script:
            add(
                f"node-{script}",
                f"{display_name} · npm {script}",
                pm,
                ["run", script],
                port,
                f"Detected package.json script `{script}` (untrusted; port may need editing).",
                port_env="PORT",
            )
    elif "start" in scripts and "JavaScript/TypeScript" in languages:
        add(
            "node-start",
            f"{display_name} · npm start",
            pm,
            ["run", "start"],
            3000,
            "Detected package.json `start` script (untrusted).",
            port_env="PORT",
        )

    if "Flask" in frameworks or (
        "Python" in languages and any(e in entry_points for e in ("app.py", "wsgi.py"))
    ):
        add(
            "flask-dev",
            f"{display_name} · Flask",
            "python",
            ["-m", "flask", "run", "--host", "127.0.0.1", "--port", "{port}"],
            5000,
            "Flask indicators found; set FLASK_APP before running (untrusted).",
            port_env="PORT",
            env_names=["FLASK_APP", "FLASK_ENV", "FLASK_DEBUG"],
        )
    if "FastAPI" in frameworks:
        module = "main:app"
        if "app.py" in entry_points:
            module = "app:app"
        elif "main.py" in entry_points:
            module = "main:app"
        add(
            "uvicorn",
            f"{display_name} · Uvicorn",
            "python",
            ["-m", "uvicorn", module, "--host", "127.0.0.1", "--port", "{port}"],
            8000,
            "FastAPI/uvicorn indicators found (module path is a guess; untrusted).",
        )
    if "Django" in frameworks and "manage.py" in entry_points:
        add(
            "django-run",
            f"{display_name} · Django",
            "python",
            ["manage.py", "runserver", "127.0.0.1:{port}"],
            8000,
            "Django manage.py detected (untrusted).",
        )

    # Always offer a safe static preview for any connected folder.
    add(
        "python-http",
        f"{display_name} · Python HTTP",
        "python",
        ["-m", "http.server", "{port}", "--bind", "127.0.0.1"],
        8765,
        "Generic local static/file preview via stdlib http.server (untrusted until reviewed).",
    )

    # Deduplicate by suggestion_id
    seen: set[str] = set()
    out: list[SuggestedProfile] = []
    for item in suggestions:
        if item.suggestion_id in seen:
            continue
        seen.add(item.suggestion_id)
        out.append(item)
    return out


def assert_no_command_execution_markers() -> None:
    """Test helper document — scan module must not import subprocess for scanning."""
    import hub.repository_workspace.connect_scan as mod

    assert not hasattr(mod, "subprocess")
