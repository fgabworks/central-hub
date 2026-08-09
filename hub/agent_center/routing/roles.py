"""Specialized AiriX routing roles — tool/provider scopes over existing adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hub.agent_center.routing.models import PromptClassification

ROLE_IDS = (
    "repository",
    "dhis2",
    "sql_data",
    "hcsc_reports",
    "ui_playwright",
    "operations",
    "general",
)


@dataclass(frozen=True)
class RoleProfile:
    id: str
    label: str
    description: str
    preferred_tools: tuple[str, ...]
    preferred_providers: tuple[str, ...]  # within capability rules
    task_types: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "preferred_tools": list(self.preferred_tools),
            "preferred_providers": list(self.preferred_providers),
            "task_types": list(self.task_types),
        }


ROLES: dict[str, RoleProfile] = {
    "repository": RoleProfile(
        id="repository",
        label="Repository",
        description="Code search, file read, and repo-scoped coding.",
        preferred_tools=("repo_search", "read_file", "notebook_lookup"),
        preferred_providers=("low-cost", "grok", "codex"),
        task_types=("coding", "refactor", "architecture", "testing"),
    ),
    "dhis2": RoleProfile(
        id="dhis2",
        label="DHIS2",
        description="UID metadata, DHIS2 reports, and investigation (read-only).",
        preferred_tools=("uid_lookup", "dhis2_reports_lookup", "sql_lookup", "repo_search"),
        preferred_providers=("deterministic", "grok", "codex"),
        task_types=("dhis2_investigation", "lookup"),
    ),
    "sql_data": RoleProfile(
        id="sql_data",
        label="SQL / Data",
        description="SQL library lookup and data investigation (read-only).",
        preferred_tools=("sql_lookup", "notebook_lookup", "repo_search", "read_file"),
        preferred_providers=("deterministic", "grok"),
        task_types=("sql_investigation",),
    ),
    "hcsc_reports": RoleProfile(
        id="hcsc_reports",
        label="HCSC / Reports",
        description="HCSC indicators and DHIS2 report workspace lookups.",
        preferred_tools=("dhis2_reports_lookup", "uid_lookup", "sql_lookup", "notebook_lookup"),
        preferred_providers=("deterministic", "grok"),
        task_types=("dhis2_investigation", "lookup"),
    ),
    "ui_playwright": RoleProfile(
        id="ui_playwright",
        label="UI / Playwright",
        description="CSS/UI fixes and browser-oriented testing cues.",
        preferred_tools=("repo_search", "read_file"),
        preferred_providers=("low-cost", "grok"),
        task_types=("css_ui", "testing"),
    ),
    "operations": RoleProfile(
        id="operations",
        label="Operations",
        description="Jobs, audit, and operational status lookups.",
        preferred_tools=("jobs_lookup", "audit_lookup", "notebook_lookup", "uid_lookup"),
        preferred_providers=("deterministic", "grok"),
        task_types=("lookup", "general"),
    ),
    "general": RoleProfile(
        id="general",
        label="General",
        description="Default AiriX scope when no specialist role matches.",
        preferred_tools=("notebook_lookup", "repo_search", "read_file"),
        preferred_providers=("deterministic", "low-cost", "grok", "codex"),
        task_types=("general", "coding", "lookup"),
    ),
}

_HCSC = re.compile(r"\b(hcsc|progress\s+npmo|indicator\s+report|national\s+roll[- ]?up)\b", re.I)
_PLAYWRIGHT = re.compile(r"\b(playwright|e2e|browser\s+test|screenshot|selector)\b", re.I)
_OPS = re.compile(r"\b(job|audit|health|process\s+manager|ops|operations|queue)\b", re.I)
_REPO = re.compile(r"\b(repository|repo\s+search|codebase|pull\s+request|git)\b", re.I)


def detect_role(prompt: str, classification: PromptClassification) -> RoleProfile:
    text = prompt or ""
    if _HCSC.search(text) or "hcsc" in " ".join(classification.signals).lower():
        return ROLES["hcsc_reports"]
    if _PLAYWRIGHT.search(text) or classification.task_type == "css_ui":
        if classification.task_type == "css_ui" or _PLAYWRIGHT.search(text):
            return ROLES["ui_playwright"]
    if classification.task_type == "sql_investigation" or (
        "sql" in text.lower() and "dhis2" not in text.lower()
    ):
        return ROLES["sql_data"]
    if classification.task_type == "dhis2_investigation" or "dhis2" in text.lower():
        return ROLES["dhis2"]
    if classification.task_type in {"coding", "refactor", "architecture", "testing"} or _REPO.search(
        text
    ):
        return ROLES["repository"]
    if classification.task_type == "lookup" and _OPS.search(text):
        return ROLES["operations"]
    if classification.deterministic_capable and _OPS.search(text):
        return ROLES["operations"]
    return ROLES["general"]


def list_roles() -> list[dict[str, Any]]:
    return [ROLES[rid].public() for rid in ROLE_IDS]


def tools_for_role(role: RoleProfile, classification: PromptClassification) -> list[str]:
    tools = list(role.preferred_tools)
    if classification.deterministic_capable:
        tools = [t for t in tools if t.endswith("_lookup") or t in {"notebook_lookup", "uid_lookup"}]
        if not tools:
            tools = list(role.preferred_tools)[:3]
    return list(dict.fromkeys(tools))[:6]
