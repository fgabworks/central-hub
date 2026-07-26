"""Connect Local Workspace — preview and confirm save."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from hub.registry.git_util import normalize_git_url
from hub.registry.models import Repository
from hub.registry.store import RegistryStore
from hub.repository_workspace.connect_scan import (
    SuggestedProfile,
    WorkspaceScanResult,
    scan_workspace_path,
)
from hub.repository_workspace.run_profiles import (
    default_profiles_path,
    parse_profile,
)
from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    redact_audit_detail,
    resolve_repo_root,
)

AuditFn = Callable[[str, str, str, bool], None]


def preview_connect(
    repo: Repository,
    *,
    path: str,
) -> dict[str, Any]:
    existing = (repo.local_path or repo.working_directory or "").strip() or None
    scan = scan_workspace_path(
        path,
        registered_git_url=repo.git_url,
        registered_name=repo.name,
        existing_local_path=existing,
        repo_id=repo.id,
    )
    if not scan.ok:
        raise WorkspaceSecurityError(scan.error or "Scan failed.", code=scan.error_code or "scan_failed")
    return {
        "scan": scan.to_public(),
        "editable": {
            "name": repo.name,
            "path": scan.path,
            "git_url": repo.git_url or scan.git_remote_url or "",
            "default_environment": "development",
            "profiles": [p.to_public() for p in scan.suggested_profiles],
        },
        "requires": {
            "confirm_save": True,
            "confirm_remote_mismatch": bool(scan.remote_mismatch),
            "confirm_replace_path": bool(scan.replacing_existing_path),
            "review_profiles": True,
        },
    }


def _profile_from_edit(raw: dict[str, Any], *, repo_id: str) -> dict[str, Any]:
    pid = str(raw.get("id") or raw.get("suggestion_id") or "").strip()
    if not pid:
        raise WorkspaceSecurityError("Profile id is required.", code="invalid_profile")
    # Scope profile to this repository
    entry = {
        "id": pid,
        "name": str(raw.get("name") or pid).strip(),
        "description": str(raw.get("rationale") or raw.get("description") or "Connected via workspace scan.").strip(),
        "repository_ids": [repo_id],
        "executable": str(raw.get("executable") or "").strip(),
        "args": list(raw.get("args") or []),
        "working_directory": str(raw.get("working_directory") or "{repository_path}").strip(),
        "environments": list(raw.get("environments") or ["development"]),
        "default_port": int(raw.get("default_port") or raw.get("port") or 8000),
        "local_url": str(raw.get("local_url") or "http://127.0.0.1:{port}/").strip(),
        "health_url": raw.get("health_url"),
        "startup_timeout_seconds": float(raw.get("startup_timeout_seconds") or 30),
        "allowed_env_names": list(raw.get("allowed_env_names") or []),
        "live_profile": bool(raw.get("live_profile", False)),
        "port_env": raw.get("port_env"),
    }
    # Validate via existing schema
    parse_profile(entry)
    return entry


def append_run_profiles(entries: list[dict[str, Any]], *, path: Path | None = None) -> list[str]:
    """Append validated profiles to run_profiles.yaml. Returns added ids."""
    cfg_path = path or default_profiles_path()
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {"profiles": []}
    profiles = list(raw.get("profiles") or [])
    if not isinstance(profiles, list):
        raise WorkspaceSecurityError("Invalid run_profiles.yaml", code="invalid_config")
    existing_ids = {
        str(p.get("id")) for p in profiles if isinstance(p, dict) and p.get("id")
    }
    added: list[str] = []
    for entry in entries:
        pid = str(entry.get("id"))
        if pid in existing_ids:
            # Replace same id only when scoped update — skip duplicates silently with rename
            entry = {**entry, "id": f"{pid}-connected"}
            pid = entry["id"]
            if pid in existing_ids:
                continue
        parse_profile(entry)
        profiles.append(entry)
        existing_ids.add(pid)
        added.append(pid)
    raw["profiles"] = profiles
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Approved Repository Workspace run profiles.\n"
        "# Executables + argument arrays only — never raw shell strings.\n"
        "# Allowed placeholders: {port}, {repository_path}, {environment}\n\n"
    )
    body = yaml.safe_dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False, width=100)
    tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(cfg_path)
    return added


def save_connect(
    repo: Repository,
    *,
    store: RegistryStore,
    path: str,
    name: str | None = None,
    git_url: str | None = None,
    confirm_save: bool = False,
    confirm_remote_mismatch: bool = False,
    confirm_replace_path: bool = False,
    selected_profiles: list[dict[str, Any]] | None = None,
    audit: AuditFn | None = None,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    if not confirm_save:
        raise WorkspaceSecurityError(
            "Explicit confirmation is required before saving.",
            code="confirm_required",
        )

    preview = preview_connect(repo, path=path)
    scan = preview["scan"]
    if scan.get("remote_mismatch") and not confirm_remote_mismatch:
        raise WorkspaceSecurityError(
            "Git remote mismatch requires explicit confirmation.",
            code="confirm_remote_mismatch",
        )
    if scan.get("replacing_existing_path") and not confirm_replace_path:
        raise WorkspaceSecurityError(
            "Replacing an existing local path requires explicit confirmation.",
            code="confirm_replace_path",
        )

    root = resolve_repo_root(path)
    if root is None:
        raise WorkspaceSecurityError("Local path is unavailable.", code="unavailable")

    new_name = (name or repo.name or root.name).strip()
    updates: dict[str, Any] = {
        "name": new_name,
        "local_path": str(root),
        "working_directory": str(root),
        "health_check": {
            "type": "path",
            "local_path": str(root),
            "executable": "python",
            "timeout_seconds": 3,
        },
    }
    # Only set git_url when provided and repo has none, or user explicitly edits
    incoming_git = (git_url or "").strip()
    if incoming_git:
        updates["git_url"] = incoming_git
    elif not repo.git_url and scan.get("git_remote_url"):
        updates["git_url"] = scan["git_remote_url"]

    saved = store.update(repo.id, updates)

    added_profiles: list[str] = []
    selected = selected_profiles or []
    if selected:
        entries = [_profile_from_edit(item, repo_id=repo.id) for item in selected]
        added_profiles = append_run_profiles(entries, path=profiles_path)

    detail = (
        f"Connected local workspace path_set=1 replace={bool(scan.get('replacing_existing_path'))} "
        f"remote_mismatch={bool(scan.get('remote_mismatch'))} "
        f"profiles_added={len(added_profiles)} "
        f"folder={root.name}"
    )
    if audit:
        audit("REPO_WS_CONNECT_SAVE", repo.id, redact_audit_detail(detail), True)

    return {
        "repository_id": repo.id,
        "local_path": str(root),
        "name": new_name,
        "git_url": saved.get("git_url"),
        "profiles_added": added_profiles,
        "redirect": f"/repositories/{repo.id}",
        "scan_summary": {
            "is_git": scan.get("is_git"),
            "frameworks": scan.get("frameworks"),
            "languages": scan.get("languages"),
            "remote_mismatch": scan.get("remote_mismatch"),
        },
    }
