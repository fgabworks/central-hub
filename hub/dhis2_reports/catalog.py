"""Load centralized DHIS2 report catalog from YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from hub.dhis2_reports.models import REPORT_TYPES, ReportDefinition, ReportParameter
from hub.dhis2_reports.security import ReportSecurityError
from hub.settings import ROOT_DIR


def default_catalog_path() -> Path:
    configured = (os.environ.get("DHIS2_REPORTS_CATALOG") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "config" / "dhis2_reports.yaml"


def _parse_parameter(raw: dict[str, Any]) -> ReportParameter:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ReportSecurityError("Parameter name is required.", code="invalid_catalog")
    choices = raw.get("choices") or []
    if not isinstance(choices, list):
        choices = [choices]
    return ReportParameter(
        name=name,
        label=str(raw.get("label") or name).strip(),
        param_type=str(raw.get("type") or "string").strip().lower(),
        required=bool(raw.get("required", False)),
        default=str(raw.get("default") or ""),
        choices=tuple(str(c) for c in choices),
        description=str(raw.get("description") or "").strip(),
    )


def parse_report(raw: dict[str, Any]) -> ReportDefinition:
    rid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or rid).strip()
    rtype = str(raw.get("type") or "").strip().lower()
    if not rid or not name:
        raise ReportSecurityError("Report id and name are required.", code="invalid_catalog")
    if rtype not in REPORT_TYPES:
        raise ReportSecurityError(f"Unknown report type {rtype!r}.", code="invalid_catalog")
    envs = tuple(
        e.strip().lower()
        for e in (raw.get("environments") or ["stage"])
        if str(e).strip()
    )
    for e in envs:
        if e not in {"stage", "live"}:
            raise ReportSecurityError(f"Invalid environment {e!r}.", code="invalid_catalog")
    params_raw = raw.get("parameters") or []
    if not isinstance(params_raw, list):
        raise ReportSecurityError("parameters must be a list.", code="invalid_catalog")
    parameters = tuple(_parse_parameter(p) for p in params_raw if isinstance(p, dict))
    output_roots = raw.get("output_roots") or []
    if not isinstance(output_roots, list):
        output_roots = [output_roots]
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    formats = raw.get("output_formats") or ["html"]
    if not isinstance(formats, list):
        formats = [formats]

    report = ReportDefinition(
        id=rid,
        name=name,
        report_type=rtype,
        description=str(raw.get("description") or "").strip(),
        source=str(raw.get("source") or "").strip(),
        repository_id=(str(raw.get("repository_id")).strip() if raw.get("repository_id") else None),
        environments=envs or ("stage",),
        parameters=parameters,
        url_template=(str(raw.get("url_template")).strip() if raw.get("url_template") else None),
        run_profile_id=(str(raw.get("run_profile_id")).strip() if raw.get("run_profile_id") else None),
        capability_id=(str(raw.get("capability_id")).strip() if raw.get("capability_id") else None),
        output_glob=str(raw.get("output_glob") or "*.html").strip(),
        static_relative_path=(
            str(raw.get("static_relative_path")).strip() if raw.get("static_relative_path") else None
        ),
        output_roots=tuple(str(x) for x in output_roots),
        tags=tuple(str(t) for t in tags),
        output_formats=tuple(str(f) for f in formats) or ("html",),
        allow_scripts=bool(raw.get("allow_scripts", False)),
        enabled=bool(raw.get("enabled", True)),
    )
    if report.report_type == "dhis2_standard" and not report.url_template:
        raise ReportSecurityError(
            f"Report {rid}: dhis2_standard requires url_template.", code="invalid_catalog"
        )
    if report.report_type == "repository_html" and not (
        report.run_profile_id or report.capability_id
    ):
        raise ReportSecurityError(
            f"Report {rid}: repository_html requires run_profile_id or capability_id.",
            code="invalid_catalog",
        )
    if report.report_type == "static_html" and not report.static_relative_path:
        raise ReportSecurityError(
            f"Report {rid}: static_html requires static_relative_path.",
            code="invalid_catalog",
        )
    return report


def load_report_catalog(path: Path | None = None) -> list[ReportDefinition]:
    cfg = path or default_catalog_path()
    if not cfg.exists():
        return []
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    items = raw.get("reports") or []
    if not isinstance(items, list):
        raise ReportSecurityError("dhis2_reports.yaml: reports must be a list.", code="invalid_catalog")
    out: list[ReportDefinition] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        report = parse_report(item)
        if report.id in seen:
            raise ReportSecurityError(f"Duplicate report id {report.id}.", code="invalid_catalog")
        seen.add(report.id)
        if report.enabled:
            out.append(report)
    return out


def get_report(report_id: str, *, path: Path | None = None) -> ReportDefinition | None:
    for report in load_report_catalog(path):
        if report.id == report_id:
            return report
    return None
