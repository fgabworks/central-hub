"""Authenticated Report Rendering Bridge — native /api/reports + safe asset proxy."""

from __future__ import annotations

import hashlib
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse

from hub.dhis2_reports.cache import CATALOG_CACHE
from hub.dhis2_reports.catalog import load_report_catalog
from hub.dhis2_reports.security import ReportSecurityError, validate_environment

# Paths the hub may proxy to the configured DHIS2 host (SSRF guard).
PROXY_ALLOWED_PREFIXES = (
    "/api/",
    "/dhis-web-commons/",
    "/dhis-web-commons-ajax/",
    "/dhis-web-commons-ajax-json/",
    "/dhis-web-reports/",
    "/dhis-web-dashboard/",
    "/dhis-web-dataentry/",
    "/icons/",
    "/images/",
    "/favicon.ico",
)

_ATTR_URL_RE = re.compile(
    r"""(?P<prefix>\b(?:src|href|action)\s*=\s*)(?P<quote>["'])(?P<url>[^"']+)(?P=quote)""",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^)'"]+)\1\s*\)""", re.IGNORECASE)


def is_app_shell_template(url_template: str | None) -> bool:
    """True when the catalog entry points at a DHIS2 app index, not a report UID."""
    raw = (url_template or "").strip().lower()
    if not raw:
        return False
    if "{uid}" in raw or "/api/reports/" in raw:
        return False
    return raw.endswith("index.html") or "/dhis-web-" in raw


def classify_catalog_entry(report: Any) -> str:
    """Return source_type for YAML catalog rows."""
    rtype = getattr(report, "report_type", "") or ""
    if rtype == "dhis2_standard" and is_app_shell_template(getattr(report, "url_template", None)):
        return "dhis2_app_shell"
    if rtype == "dhis2_standard":
        return "dhis2_standard_url"
    if rtype == "repository_html":
        return "repository_html"
    if rtype == "static_html":
        return "static_html"
    return rtype or "unknown"


def validate_proxy_path(path: str) -> str:
    """Normalize and allowlist a DHIS2-relative path for the credentialed proxy."""
    raw = (path or "").strip()
    if not raw:
        raise ReportSecurityError("Proxy path is required.", code="invalid_proxy_path")
    if "://" in raw or raw.startswith("//"):
        raise ReportSecurityError("Absolute proxy URLs are blocked.", code="ssrf_blocked")
    if ".." in raw.replace("\\", "/").split("/"):
        raise ReportSecurityError("Path traversal is blocked.", code="path_traversal")
    if not raw.startswith("/"):
        raw = "/" + raw
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        raise ReportSecurityError("Absolute proxy URLs are blocked.", code="ssrf_blocked")
    path_only = parsed.path or "/"
    if not any(path_only == p.rstrip("/") or path_only.startswith(p) for p in PROXY_ALLOWED_PREFIXES):
        raise ReportSecurityError(
            "Proxy path is outside the approved DHIS2 report/API prefixes.",
            code="proxy_path_blocked",
        )
    q = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        low = key.lower()
        if any(s in low for s in ("password", "token", "authorization", "secret", "api_key")):
            continue
        q.append((key, value))
    query = urlencode(q)
    return path_only + (("?" + query) if query else "")


def build_proxy_url(environment: str, path: str, *, confirm_live: bool = False) -> str:
    env = validate_environment(environment)
    safe = validate_proxy_path(path)
    query = {"path": safe}
    if env == "live" and confirm_live:
        query["confirm_live"] = "1"
    return f"/dhis2/reports/proxy/{env}?{urlencode(query)}"


def rewrite_report_html(
    html: str,
    *,
    environment: str,
    dhis2_base: str,
    confirm_live: bool = False,
) -> str:
    """Rewrite relative/same-host asset URLs to the hub credentialed proxy."""
    body = str(html or "")
    env = validate_environment(environment)
    base = (dhis2_base or "").strip().rstrip("/")
    base_host = urlparse(base).netloc.lower() if base else ""

    def _map_url(url: str) -> str:
        u = unescape((url or "").strip())
        if not u or u.startswith(("#", "data:", "mailto:", "javascript:")):
            return url
        if u.startswith("/dhis2/reports/"):
            return url
        parsed = urlparse(u)
        if parsed.scheme in {"http", "https"}:
            if not base_host or parsed.netloc.lower() != base_host:
                return url
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            try:
                return build_proxy_url(env, path, confirm_live=confirm_live)
            except ReportSecurityError:
                return url
        if u.startswith("//"):
            return url
        if u.startswith("/"):
            try:
                return build_proxy_url(env, u, confirm_live=confirm_live)
            except ReportSecurityError:
                return url
        joined = urljoin((base or "https://invalid.local") + "/", u)
        parsed2 = urlparse(joined)
        path = parsed2.path or "/"
        if parsed2.query:
            path = f"{path}?{parsed2.query}"
        try:
            return build_proxy_url(env, path, confirm_live=confirm_live)
        except ReportSecurityError:
            return url

    def _attr_sub(match: re.Match[str]) -> str:
        mapped = _map_url(match.group("url"))
        return f"{match.group('prefix')}{match.group('quote')}{mapped}{match.group('quote')}"

    body = _ATTR_URL_RE.sub(_attr_sub, body)

    def _css_sub(match: re.Match[str]) -> str:
        q = match.group(1) or ""
        mapped = _map_url(match.group(2))
        return f"url({q}{mapped}{q})"

    return _CSS_URL_RE.sub(_css_sub, body)


def build_run_catalog(
    *,
    store: Any,
    environment: str,
    favorites: set[str] | None = None,
) -> dict[str, Any]:
    """Unified Run Report catalog: native /api/reports + classified YAML shortcuts."""
    env = validate_environment(environment)
    cache_key = f"catalog:{env}"
    cached = CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return {**cached, "cache": "hit"}

    favs = favorites if favorites is not None else set(store.list_favorites())
    native: list[dict[str, Any]] = []
    for row in store.list_synced_reports(environment=env):
        native.append(
            {
                "id": f"std:{env}:{row.uid}",
                "stable_id": f"std:{env}:{row.uid}",
                "uid": row.uid,
                "name": row.name,
                "source_type": "native_standard",
                "report_type": row.report_type,
                "environment": env,
                "needs_period": row.needs_period,
                "needs_org_unit": row.needs_org_unit,
                "render_supported": row.render_supported,
                "output_formats": ["html", "pdf", "xls"] if row.render_supported else ["html"],
                "browser_only": False,
                "favorite": f"std:{env}:{row.uid}" in favs or row.uid in favs,
                "parameters": _params_from_synced(row),
            }
        )

    shells: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for report in load_report_catalog():
        if not report.enabled:
            continue
        if env not in report.environments:
            continue
        source_type = classify_catalog_entry(report)
        entry = {
            "id": report.id,
            "stable_id": report.id,
            "uid": "",
            "name": report.name,
            "source_type": source_type,
            "report_type": report.report_type,
            "environment": env,
            "needs_period": any(p.param_type == "period" and p.required for p in report.parameters),
            "needs_org_unit": any(p.param_type == "org_unit" and p.required for p in report.parameters),
            "render_supported": source_type in {"repository_html", "static_html"},
            "output_formats": list(report.output_formats) or ["html"],
            "browser_only": source_type == "dhis2_app_shell",
            "favorite": report.id in favs,
            "parameters": [p.to_public() for p in report.parameters],
            "url_template": report.url_template,
            "description": report.description,
        }
        if source_type == "dhis2_app_shell":
            shells.append(entry)
        else:
            others.append(entry)

    payload = {
        "ok": True,
        "environment": env,
        "native_standard": native,
        "app_shells": shells,
        "catalog_other": others,
        "counts": {
            "native_standard": len(native),
            "app_shells": len(shells),
            "catalog_other": len(others),
        },
        "message": (
            "Native Standard Reports render via /api/reports/{uid}/data.html with hub credentials. "
            "DHIS2 app shells (Reports/Pivot) open in the browser only — they are not individual reports."
        ),
        "cache": "miss",
    }
    CATALOG_CACHE.set(cache_key, payload)
    return payload


def _params_from_synced(row: Any) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    if row.needs_period:
        params.append(
            {
                "name": "period",
                "label": "Period",
                "type": "period",
                "required": True,
                "default": "",
                "choices": [],
                "description": "Reporting period (YYYY, YYYYMM, YYYYQn, …).",
            }
        )
    if row.needs_org_unit:
        params.append(
            {
                "name": "orgUnit",
                "label": "Organisation unit",
                "type": "org_unit",
                "required": True,
                "default": "",
                "choices": [],
                "description": "Select an organisation unit.",
            }
        )
    return params


def parse_run_report_id(report_id: str) -> tuple[str, str, str]:
    """Return (kind, environment_or_empty, uid_or_catalog_id). kind: native | native_uid | catalog."""
    text = (report_id or "").strip()
    if text.startswith("std:"):
        parts = text.split(":", 2)
        if len(parts) != 3:
            raise ReportSecurityError("Invalid standard report id.", code="invalid_report_id")
        return "native", validate_environment(parts[1]), parts[2]
    if re.match(r"^[A-Za-z][A-Za-z0-9]{10}$", text):
        return "native_uid", "", text
    return "catalog", "", text


def content_fingerprint(html: str) -> str:
    return hashlib.sha256((html or "").encode("utf-8", errors="replace")).hexdigest()[:16]
