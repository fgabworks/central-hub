"""Configurable repository grouping for registry list/dashboard rows.

Grouping key is ``repository_group_id`` only. Adapter IDs stay separate.
Workspace readiness never implies the application is running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from hub.registry.models import Registry, Repository
from hub.registry.status import ui_repo_status
from hub.repository_workspace.security import resolve_repo_root

ACTIVE_RUN_STATUSES = frozenset(
    {"starting", "running", "healthy", "unhealthy", "stopping"}
)


@dataclass
class GroupAction:
    label: str
    href: str
    kind: str = "link"  # link | external
    available: bool = True
    title: str = ""


@dataclass
class GroupedRepositoryRow:
    """One logical project row (grouped or ungrouped singleton)."""

    key: str
    name: str
    description: str
    repository_group_id: str | None
    member_ids: list[str]
    primary_repo_id: str
    types: list[str]
    enabled: bool
    git_url: str | None
    connection: str
    capability_count: int
    # Independent facet statuses (None = facet not present)
    workspace_status: str | None = None  # Ready | Not Connected
    application_status: str | None = None  # Running | Stopped
    api_status: str | None = None  # Online | Offline
    # Legacy single badge for ungrouped / fallback
    status: str = "unreachable"
    actions: list[GroupAction] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    is_group: bool = False

    def to_template(self) -> dict[str, Any]:
        return {
            "id": self.primary_repo_id,
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "repository_group_id": self.repository_group_id,
            "member_ids": list(self.member_ids),
            "primary_repo_id": self.primary_repo_id,
            "repo_id": self.primary_repo_id,
            "type": "+".join(self.types) if len(self.types) > 1 else (self.types[0] if self.types else "—"),
            "types": list(self.types),
            "enabled": self.enabled,
            "git_url": self.git_url,
            "connection": self.connection,
            "capability_count": self.capability_count,
            "workspace_status": self.workspace_status,
            "application_status": self.application_status,
            "api_status": self.api_status,
            "status": self.status,
            "actions": [
                {
                    "label": a.label,
                    "href": a.href,
                    "kind": a.kind,
                    "available": a.available,
                    "title": a.title,
                }
                for a in self.actions
            ],
            "members": list(self.members),
            "is_group": self.is_group,
            "subtitle": self.description or (", ".join(self.types) if self.types else ""),
            "branch_path": self.git_url or self.connection or "—",
            "icon": "GRP" if self.is_group else ("API" if "api" in self.types else "CLI"),
        }


def _group_bucket(repo: Repository) -> str:
    gid = (repo.repository_group_id or "").strip()
    if gid:
        return f"group:{gid}"
    return f"solo:{repo.id}"


def _workspace_ready(repo: Repository) -> bool:
    return resolve_repo_root(repo.local_path or repo.working_directory) is not None


def _pick_display_name(members: list[Repository]) -> str:
    # Prefer command entry name when mixed; otherwise first by id.
    commands = [m for m in members if m.type == "command"]
    if commands:
        return commands[0].name
    return members[0].name


def _pick_primary(members: list[Repository]) -> Repository:
    ready = [m for m in members if m.type == "command" and _workspace_ready(m)]
    if ready:
        return ready[0]
    commands = [m for m in members if m.type == "command"]
    if commands:
        return commands[0]
    return members[0]


def build_grouped_rows(
    registry: Registry | None,
    health_by_id: dict[str, dict[str, Any]] | None = None,
    *,
    active_run_repo_ids: set[str] | None = None,
    url_for: Callable[..., str] | None = None,
) -> list[dict[str, Any]]:
    """Build list/dashboard rows with optional grouping by repository_group_id."""
    health_by_id = health_by_id or {}
    active_run_repo_ids = active_run_repo_ids or set()
    if registry is None:
        return []

    buckets: dict[str, list[Repository]] = {}
    order: list[str] = []
    for repo in registry.repositories:
        key = _group_bucket(repo)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(repo)

    rows: list[GroupedRepositoryRow] = []
    for key in order:
        members = sorted(buckets[key], key=lambda r: (r.type != "command", r.id))
        gid = (members[0].repository_group_id or "").strip() or None
        if key.startswith("solo:"):
            gid = None
        is_group = bool(gid) and len(members) >= 1 and key.startswith("group:")
        # Solo with group_id still "grouped" conceptually but one member
        is_group = bool(gid)

        primary = _pick_primary(members)
        types = sorted({m.type for m in members})
        enabled = any(m.enabled for m in members)
        git_url = next((m.git_url for m in members if m.git_url), None)
        connection_parts = []
        for m in members:
            if m.type == "api" and m.base_url:
                connection_parts.append(m.base_url)
            elif m.local_path:
                connection_parts.append(m.local_path)
        connection = " · ".join(connection_parts) if connection_parts else "—"
        capability_count = sum(len(m.capabilities) for m in members)

        workspace_status = None
        application_status = None
        api_status = None
        legacy_status = ui_repo_status(primary, health_by_id.get(primary.id))

        command_members = [m for m in members if m.type == "command"]
        api_members = [m for m in members if m.type == "api"]

        if command_members:
            if any(_workspace_ready(m) for m in command_members):
                workspace_status = "Ready"
            else:
                workspace_status = "Not Connected"
            if any(
                m.id in active_run_repo_ids and _workspace_ready(m) for m in command_members
            ):
                application_status = "Running"
            elif workspace_status == "Ready":
                application_status = "Stopped"
            else:
                # App facet exists but workspace missing — still show Stopped, not Running
                application_status = "Stopped"

        if api_members:
            if any(
                ui_repo_status(m, health_by_id.get(m.id)) == "healthy" for m in api_members
            ):
                api_status = "Online"
            else:
                api_status = "Offline"

        actions = _build_actions(
            command_members=command_members,
            api_members=api_members,
            workspace_status=workspace_status,
            application_status=application_status,
            url_for=url_for,
        )

        member_payload = []
        for m in members:
            member_payload.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "type": m.type,
                    "enabled": m.enabled,
                    "status": ui_repo_status(m, health_by_id.get(m.id)),
                    "base_url": m.base_url,
                    "local_path": m.local_path,
                }
            )

        rows.append(
            GroupedRepositoryRow(
                key=key,
                name=_pick_display_name(members),
                description=next((m.description for m in members if m.description), "")
                or primary.description,
                repository_group_id=gid,
                member_ids=[m.id for m in members],
                primary_repo_id=primary.id,
                types=types,
                enabled=enabled,
                git_url=git_url,
                connection=connection,
                capability_count=capability_count,
                workspace_status=workspace_status,
                application_status=application_status,
                api_status=api_status,
                status=legacy_status,
                actions=actions,
                members=member_payload,
                is_group=is_group,
            )
        )

    return [r.to_template() for r in rows]


def _build_actions(
    *,
    command_members: list[Repository],
    api_members: list[Repository],
    workspace_status: str | None,
    application_status: str | None,
    url_for: Callable[..., str] | None,
) -> list[GroupAction]:
    actions: list[GroupAction] = []

    def href(endpoint: str, **values: str) -> str:
        if url_for is None:
            # Stable relative paths for tests / non-Flask contexts
            if endpoint == "repository_detail":
                return f"/repositories/{values['repo_id']}"
            if endpoint == "repository_files":
                return f"/repositories/{values['repo_id']}/files"
            if endpoint == "repository_run":
                return f"/repositories/{values['repo_id']}/run"
            if endpoint == "repository_logs":
                return f"/repositories/{values['repo_id']}/logs"
            if endpoint == "api_repository_health":
                return f"/api/repositories/{values['repo_id']}/health"
            return "#"
        return url_for(endpoint, **values)

    workspace_repo = next((m for m in command_members if _workspace_ready(m)), None)
    run_repo = workspace_repo or (command_members[0] if command_members else None)

    if command_members:
        actions.append(
            GroupAction(
                label="Open Workspace",
                href=href("repository_files", repo_id=workspace_repo.id)
                if workspace_repo
                else href("repository_detail", repo_id=command_members[0].id),
                available=workspace_status == "Ready",
                title="Requires a configured local path",
            )
        )
        actions.append(
            GroupAction(
                label="Start / Stop",
                href=href("repository_run", repo_id=run_repo.id) if run_repo else "#",
                available=bool(run_repo) and workspace_status == "Ready",
                title="Open Run tab for Start / Stop / Restart",
            )
        )
        actions.append(
            GroupAction(
                label="Logs",
                href=href("repository_logs", repo_id=run_repo.id) if run_repo else "#",
                available=bool(run_repo) and workspace_status == "Ready",
            )
        )

    for api in api_members:
        if api.base_url:
            actions.append(
                GroupAction(
                    label="Open API",
                    href=api.base_url.rstrip("/") + "/",
                    kind="external",
                    available=True,
                )
            )
        actions.append(
            GroupAction(
                label="Health Check",
                href=href("api_repository_health", repo_id=api.id),
                available=api.enabled,
            )
        )

    if not api_members and command_members:
        actions.append(
            GroupAction(
                label="Health Check",
                href=href("api_repository_health", repo_id=command_members[0].id),
                available=command_members[0].enabled,
            )
        )

    # Deduplicate labels keeping first
    seen: set[str] = set()
    unique: list[GroupAction] = []
    for action in actions:
        if action.label in seen:
            continue
        seen.add(action.label)
        unique.append(action)
    return unique


def group_siblings(
    registry: Registry | None, repo_id: str
) -> list[Repository]:
    """Return all repositories sharing the same repository_group_id (including self)."""
    if registry is None:
        return []
    repo = registry.get(repo_id)
    if repo is None:
        return []
    gid = (repo.repository_group_id or "").strip()
    if not gid:
        return [repo]
    return [r for r in registry.repositories if (r.repository_group_id or "").strip() == gid]


def linked_api_repositories(
    registry: Registry | None, repo_id: str
) -> list[Repository]:
    """API adapters in the same group as ``repo_id`` (for post-start health refresh)."""
    return [r for r in group_siblings(registry, repo_id) if r.type == "api"]
