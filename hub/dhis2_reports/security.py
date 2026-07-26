"""Security helpers for DHIS2 Report Workspace."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qsl, urlunparse

from hub.repository_workspace.security import (
    WorkspaceSecurityError,
    is_blocked_secret,
    redact_audit_detail,
    resolve_repo_root,
    safe_join,
)
from hub.settings import ROOT_DIR

_PERIOD_RE = re.compile(r"^(?:\d{4}|\d{4}Q[1-4]|\d{6}|\d{4}W\d{1,2}|[0-9]{4}-[0-9]{2}-[0-9]{2})$")
_ORG_UNIT_RE = re.compile(r"^[A-Za-z0-9]{11}$")
_PARAM_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "secret",
        "authorization",
        "auth",
        "username",
        "user",
        "credential",
    }
)
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
ALLOWED_URL_PLACEHOLDERS = frozenset(
    {"base_url", "period", "orgUnit", "org_unit", "format", "report_id", "uid", "date"}
)

_YYYYMM_RE = re.compile(r"^\d{6}$")


class ReportSecurityError(WorkspaceSecurityError):
    """Report validation / safety failure."""


def validate_environment(environment: str) -> str:
    env = (environment or "").strip().lower()
    if env not in {"stage", "live"}:
        raise ReportSecurityError("Environment must be stage or live.", code="invalid_environment")
    return env


def validate_period(period: str, *, required: bool = False) -> str:
    value = (period or "").strip()
    if not value:
        if required:
            raise ReportSecurityError("Period is required.", code="invalid_period")
        return ""
    if not _PERIOD_RE.match(value):
        raise ReportSecurityError(
            "Period must look like YYYY, YYYYMM, YYYYQn, YYYYWww, or YYYY-MM-DD.",
            code="invalid_period",
        )
    return value


def validate_org_unit(org_unit: str, *, required: bool = False) -> str:
    value = (org_unit or "").strip()
    if not value:
        if required:
            raise ReportSecurityError("Organisation unit UID is required.", code="invalid_org_unit")
        return ""
    if not _ORG_UNIT_RE.match(value):
        raise ReportSecurityError(
            "Organisation unit must be an 11-character DHIS2 UID.",
            code="invalid_org_unit",
        )
    return value


def scrub_parameters(raw: dict[str, Any] | None) -> dict[str, str]:
    """Keep only safe parameter names/values; drop secrets."""
    out: dict[str, str] = {}
    for key, value in (raw or {}).items():
        name = str(key).strip()
        if not _PARAM_KEY_RE.match(name):
            continue
        if name.lower() in _SECRET_KEYS or any(s in name.lower() for s in _SECRET_KEYS):
            continue
        text = str(value if value is not None else "").strip()
        if any(s in text.lower() for s in ("password=", "token=", "bearer ", "api_key=")):
            continue
        if len(text) > 500:
            text = text[:500]
        out[name] = text
    return out


def allowed_dhis2_hosts() -> set[str]:
    """Hosts from Stage/Live/canonical DHIS2 URL env vars (never passwords)."""
    hosts: set[str] = set()
    for key in (
        "STAGE_DHIS2_URL",
        "LIVE_DHIS2_URL",
        "DHIS2_BASE_URL",
        "STAGE_DHIS2_BASE_URL",  # legacy alias
        "LIVE_DHIS2_BASE_URL",
    ):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            host = urlparse(raw).hostname
        except Exception:  # noqa: BLE001
            host = None
        if host:
            hosts.add(host.lower())
    extra = (os.environ.get("DHIS2_REPORT_ALLOWED_HOSTS") or "").strip()
    for part in extra.split(","):
        host = part.strip().lower()
        if host:
            hosts.add(host)
    return hosts


def build_dhis2_report_url(
    *,
    base_url: str,
    url_template: str,
    parameters: dict[str, str],
) -> str:
    """Resolve an allowlisted DHIS2 report URL. Never injects credentials."""
    base = (base_url or "").rstrip("/")
    if not base:
        raise ReportSecurityError("DHIS2 base URL is not configured.", code="dhis2_unconfigured")
    parsed_base = urlparse(base)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
        raise ReportSecurityError("Invalid DHIS2 base URL.", code="invalid_base_url")
    allowed = allowed_dhis2_hosts()
    if allowed and parsed_base.hostname.lower() not in allowed:
        raise ReportSecurityError("DHIS2 host is not allowlisted for reports.", code="host_blocked")

    template = (url_template or "").strip()
    if not template:
        raise ReportSecurityError("Report URL template is missing.", code="invalid_template")
    if template.lower().startswith(("javascript:", "data:", "file:")):
        raise ReportSecurityError("Unsafe URL scheme.", code="unsafe_url")

    for match in _PLACEHOLDER_RE.finditer(template):
        name = match.group(1)
        if name not in ALLOWED_URL_PLACEHOLDERS and name not in parameters:
            raise ReportSecurityError(
                f"Disallowed URL placeholder {{{name}}}.",
                code="bad_placeholder",
            )

    mapping = {
        "base_url": base,
        "period": parameters.get("period", ""),
        "orgUnit": parameters.get("orgUnit") or parameters.get("org_unit", ""),
        "org_unit": parameters.get("org_unit") or parameters.get("orgUnit", ""),
        "format": parameters.get("format", "html"),
        "report_id": parameters.get("report_id", ""),
        **parameters,
    }

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(mapping.get(key, ""))

    # Prefer substituting {base_url} templates directly to avoid double-prefixing.
    if "{base_url}" in template:
        resolved = _PLACEHOLDER_RE.sub(repl, template)
    elif template.startswith("http://") or template.startswith("https://"):
        resolved = _PLACEHOLDER_RE.sub(repl, template)
    elif template.startswith("/"):
        resolved = base + _PLACEHOLDER_RE.sub(repl, template)
    else:
        resolved = base + "/" + _PLACEHOLDER_RE.sub(repl, template.lstrip("/"))

    parsed = urlparse(resolved)
    if parsed.hostname and allowed and parsed.hostname.lower() not in allowed:
        raise ReportSecurityError("Resolved report host is not allowlisted.", code="host_blocked")
    if parsed.username or parsed.password:
        raise ReportSecurityError("Credentials must not appear in report URLs.", code="secrets_in_url")
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for bad in list(q):
        if bad.lower() in _SECRET_KEYS:
            raise ReportSecurityError("Secret query parameters are not allowed.", code="secrets_in_url")
    # Rebuild netloc without userinfo; keep path/query/fragment.
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    clean = parsed._replace(netloc=netloc)
    return urlunparse(clean)


def configured_output_roots(extra: list[str] | None = None) -> list[Path]:
    roots: list[Path] = []
    env_keys = [
        "DHIS2_REPORT_OUTPUT_DIR",
        "REPORT_TEMPLATE_PATH",
        "DATA_SCRIPT_PATH",
        "LIVE_PROCESSING_PATH",
    ]
    for key in env_keys:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        else:
            path = path.resolve()
        if path.is_dir():
            roots.append(path)
    for item in extra or []:
        text = str(item).strip()
        if not text:
            continue
        # Treat as env var name or path
        env_val = (os.environ.get(text) or "").strip()
        candidate = Path(env_val or text).expanduser()
        if not candidate.is_absolute():
            candidate = (ROOT_DIR / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate.is_dir():
            roots.append(candidate)
    # Always allow hub results dir
    results = (ROOT_DIR / "data" / "results").resolve()
    if results.is_dir() or True:
        results.mkdir(parents=True, exist_ok=True)
        roots.append(results)
    # de-dupe
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def resolve_report_html(
    relative_or_absolute: str,
    *,
    roots: list[Path] | None = None,
    repository_id: str | None = None,
    registry_local_path: str | None = None,
) -> Path:
    """Resolve an HTML file under configured output / repository roots only."""
    raw = (relative_or_absolute or "").strip().strip('"')
    if not raw:
        raise ReportSecurityError("Output path is required.", code="missing_output")
    if is_blocked_secret(raw):
        raise ReportSecurityError("Secret paths are blocked.", code="secret_blocked")

    allowed = list(roots or configured_output_roots())
    if registry_local_path:
        repo_root = resolve_repo_root(registry_local_path)
        if repo_root is not None:
            allowed.append(repo_root)

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        for root in allowed:
            try:
                resolved.relative_to(root.resolve())
                if not resolved.is_file():
                    raise ReportSecurityError("Report file not found.", code="missing_output")
                if resolved.suffix.lower() not in {".html", ".htm"}:
                    raise ReportSecurityError("Only HTML report files are allowed.", code="invalid_output")
                return resolved
            except ValueError:
                continue
        raise ReportSecurityError("Report path is outside allowlisted output directories.", code="path_escape")

    # Relative: try each root via safe_join
    last_err: Exception | None = None
    for root in allowed:
        try:
            path = safe_join(root, raw)
            if path.is_file() and path.suffix.lower() in {".html", ".htm"}:
                return path
        except WorkspaceSecurityError as exc:
            last_err = exc
            continue
    if last_err:
        raise ReportSecurityError(str(last_err), code=getattr(last_err, "code", "path_escape"))
    raise ReportSecurityError("Report file not found under allowlisted roots.", code="missing_output")


def iframe_sandbox_flags(*, allow_scripts: bool = False) -> str:
    flags = ["allow-same-origin"]
    if allow_scripts:
        flags.append("allow-scripts")
    # Never allow-top-navigation / allow-popups / allow-forms by default
    return " ".join(flags)


def redact_report_detail(text: str, *, limit: int = 400) -> str:
    return redact_audit_detail(text, limit=limit)


def period_to_dhis2_date(period: str) -> str:
    """
    Convert hub period input to DHIS2 data.html `date` (yyyy-MM-dd) when possible.
    Relative-period reports use this as the basis date.
    """
    value = validate_period(period, required=False)
    if not value:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    if _YYYYMM_RE.match(value):
        return f"{value[:4]}-{value[4:6]}-01"
    if re.match(r"^\d{4}$", value):
        return f"{value}-01-01"
    if re.match(r"^\d{4}Q[1-4]$", value):
        quarter = int(value[-1])
        month = (quarter - 1) * 3 + 1
        return f"{value[:4]}-{month:02d}-01"
    # Weekly / other: leave blank — caller may still open Reports app.
    return ""


def build_standard_report_open_url(*, base_url: str, uid: str) -> str:
    """Browser URL for DHIS2 Reports app (no credentials)."""
    return build_dhis2_report_url(
        base_url=base_url,
        url_template="/dhis-web-reports/index.html#/standard-report",
        parameters={"uid": uid, "report_id": uid},
    )


def build_standard_report_data_url(
    *,
    base_url: str,
    uid: str,
    period: str = "",
    org_unit: str = "",
) -> str:
    """Prefer DHIS2's own /api/reports/{uid}/data.html rendering (no credentials)."""
    if not re.match(r"^[A-Za-z][A-Za-z0-9]{10}$", uid or ""):
        raise ReportSecurityError("Invalid report UID.", code="invalid_uid")
    date = period_to_dhis2_date(period)
    ou = validate_org_unit(org_unit, required=False)
    query_parts: list[str] = []
    if date:
        query_parts.append(f"date={date}")
    if ou:
        query_parts.append(f"ou={ou}")
    suffix = ("?" + "&".join(query_parts)) if query_parts else ""
    return build_dhis2_report_url(
        base_url=base_url,
        url_template=f"/api/reports/{uid}/data.html{suffix}",
        parameters={"uid": uid, "period": period, "orgUnit": ou, "date": date},
    )
