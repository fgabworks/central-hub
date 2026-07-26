"""DHIS2 Report Workspace models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REPORT_TYPES = frozenset({"dhis2_standard", "repository_html", "static_html"})
ENVIRONMENTS = frozenset({"stage", "live"})
RUN_STATUSES = frozenset(
    {"queued", "running", "completed", "failed", "cancelled", "missing_output"}
)


@dataclass(frozen=True)
class ReportParameter:
    name: str
    label: str
    param_type: str = "string"  # string | period | org_unit | choice | boolean
    required: bool = False
    default: str = ""
    choices: tuple[str, ...] = ()
    description: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.param_type,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "description": self.description,
        }


@dataclass(frozen=True)
class ReportDefinition:
    id: str
    name: str
    report_type: str
    description: str = ""
    source: str = ""
    repository_id: str | None = None
    environments: tuple[str, ...] = ("stage",)
    parameters: tuple[ReportParameter, ...] = ()
    # dhis2_standard
    url_template: str | None = None
    # repository_html
    run_profile_id: str | None = None
    capability_id: str | None = None
    output_glob: str = "*.html"
    # static_html
    static_relative_path: str | None = None
    output_roots: tuple[str, ...] = ()  # env keys or absolute hints resolved later
    tags: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ("html",)
    allow_scripts: bool = False
    enabled: bool = True

    def to_public(self, *, last_run: dict[str, Any] | None = None, favorite: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.report_type,
            "description": self.description,
            "source": self.source,
            "repository_id": self.repository_id,
            "environments": list(self.environments),
            "parameters": [p.to_public() for p in self.parameters],
            "url_template": self.url_template,
            "run_profile_id": self.run_profile_id,
            "capability_id": self.capability_id,
            "output_glob": self.output_glob,
            "static_relative_path": self.static_relative_path,
            "tags": list(self.tags),
            "output_formats": list(self.output_formats),
            "allow_scripts": self.allow_scripts,
            "enabled": self.enabled,
            "favorite": favorite,
            "last_run": last_run,
        }


@dataclass
class ResolvedRun:
    report_id: str
    environment: str
    parameters: dict[str, str] = field(default_factory=dict)
    output_format: str = "html"
    resolved_url: str | None = None
    command_preview: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    live_confirm_required: bool = False
