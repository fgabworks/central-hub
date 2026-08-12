"""Server-side assistant profile policies for the shared orchestration engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssistantProfile:
    id: str
    name: str
    title: str
    workspace: str
    tone: str
    instructions: str
    allowed_tools: tuple[str, ...]
    default_tools: tuple[str, ...]
    repositories_allowed: bool
    default_mode: str = "ask"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "workspace": self.workspace,
            "tone": self.tone,
            "instructions": self.instructions,
            "allowed_tools": list(self.allowed_tools),
            "default_tools": list(self.default_tools),
            "repositories_allowed": self.repositories_allowed,
            "default_mode": self.default_mode,
        }


PROFILES = {
    "aira": AssistantProfile(
        id="aira",
        name="Aira",
        title="Personal & General Assistant",
        workspace="personal",
        tone="Friendly, warm, and concise.",
        instructions=(
            "You are Aira, the Personal & General Assistant. Be friendly, warm, and concise. "
            "Use only Personal sources selected for this run. Never request, infer, or expose "
            "Work repositories, Work SQL, DHIS2, jobs, logs, Audit, or Work Email/Calendar."
        ),
        allowed_tools=("notebook_lookup", "notepad_lookup", "email_search", "calendar_lookup"),
        default_tools=("notebook_lookup", "notepad_lookup", "email_search", "calendar_lookup"),
        repositories_allowed=False,
    ),
    "okarun": AssistantProfile(
        id="okarun",
        name="AiriX",
        title="Work & Data Assistant",
        workspace="work",
        tone="Strict, direct, technical, and evidence-focused.",
        instructions=(
            "You are AiriX, the Work & Data Assistant. Be strict, direct, technical, and "
            "evidence-focused. Cite concrete sources and distinguish evidence from inference. "
            "Respect repository ownership and every instruction file loaded for selected repositories. "
            "When a repository/workspace is selected, project questions about organisational units, "
            "UIDs, reports, indicators, mappings, DHIS2, data coverage, or configuration must be "
            "answered from selected-context Hub evidence when the prompt is project-specific "
            "or ambiguous with a selected repository. Explicit national/general/web scope "
            "overrides the selected repo and may use model knowledge. Never silently "
            "substitute general knowledge for missing project evidence; if project evidence "
            "is missing, say you cannot verify from selected context."
        ),
        allowed_tools=(
            "repo_search", "read_file", "notebook_lookup", "sql_lookup", "uid_lookup",
            "org_unit_lookup",
            "email_search", "calendar_lookup", "jobs_lookup", "audit_lookup",
            "dhis2_reports_lookup",
            "repository_intelligence", "sql_query_execute", "data_explorer_lookup",
            "skill_recall",
        ),
        default_tools=(
            "repo_search", "read_file", "notebook_lookup", "sql_lookup", "uid_lookup",
            "org_unit_lookup",
            "email_search", "calendar_lookup", "jobs_lookup", "audit_lookup",
            "dhis2_reports_lookup",
            "repository_intelligence", "skill_recall",
        ),
        repositories_allowed=True,
    ),
}


def get_profile(profile_id: str | None) -> AssistantProfile:
    key = (profile_id or "").strip().lower()
    profile = PROFILES.get(key)
    if profile is None:
        raise ValueError(f"Unknown assistant profile: {profile_id}")
    return profile


def profile_for_workspace(workspace: str | None) -> AssistantProfile:
    ws = (workspace or "").strip().lower()
    if ws == "personal":
        return PROFILES["aira"]
    return PROFILES["okarun"]


def normalize_tools(profile: AssistantProfile, requested: list[str] | None) -> list[str]:
    selected = requested if requested is not None else list(profile.default_tools)
    allowed = set(profile.allowed_tools)
    return list(dict.fromkeys(str(item) for item in selected if str(item) in allowed))
