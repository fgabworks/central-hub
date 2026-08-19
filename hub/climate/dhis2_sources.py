"""Bounded read-only DHIS2 / CLIMATE operational sources for AiriX.

Reuse existing DHIS2 client, UID index, enrichment store, reports, jobs, and
audit services. Providers still receive one plain packet. Direct mode never
calls the resolver; repository/All scopes keep these sources unavailable.
"""

from __future__ import annotations

import re
from typing import Any

from hub.agent_center.redact import redact_text as agent_redact
from hub.climate.context_registry import (
    ContextCandidate,
    ContextEvidence,
    ContextRequest,
    _BaseSource,
    _rank,
    _text,
    _tokens,
)
from hub.climate.context_scope import GENERAL
from hub.dhis2.client import ALLOWED_RESOURCES
from hub.dhis2.redact import redact_text as dhis2_redact
from hub.dhis2.uid_mapping.search import filter_records

_UID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{10}\b")
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "it",
    "be", "with", "from", "this", "that", "what", "which", "about", "please",
    "show", "find", "lookup", "get", "how", "does", "are", "can", "me",
}
_DHIS2_TERMS = {
    "dhis2", "dhis", "uid", "metadata", "indicator", "program", "dataset",
    "dataelement", "org", "unit", "organisation", "organization", "ou",
    "enrichment", "audit", "tracker", "stage", "live", "instance",
    "report", "category", "optionset", "option", "coverage",
}
_ENV_TERMS = {
    "dhis2", "dhis", "environment", "instance", "stage", "live", "config",
    "configured", "credential", "connection",
}
_OPS_TERMS = {
    "dhis2", "dhis", "job", "jobs", "run", "history", "audit", "operational",
    "status", "sync", "enrichment",
}
_REPORT_TERMS = {
    "dhis2", "dhis", "report", "reports", "org", "unit", "ou", "standard",
}
_FORBIDDEN_DHIS2_WRITES = (
    "create", "update", "delete", "post", "put", "patch", "import",
    "apply", "insert", "upsert", "write",
)


def _dhis2_allowed(request: ContextRequest) -> bool:
    return request.scope == GENERAL and request.workspace == "work"


def _query_has(request: ContextRequest, terms: set[str]) -> bool:
    tokens = _tokens(request.query)
    if tokens & terms:
        return True
    return bool(_UID_RE.search(request.query or ""))


def _query_needles(query: str, *, max_terms: int = 6) -> list[str]:
    """Reuse existing substring filters with the full prompt, then tokens/UIDs."""
    raw = (query or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        item = " ".join(str(term or "").split())
        key = item.lower()
        if not item or key in seen:
            return
        seen.add(key)
        out.append(item)

    if len(raw) <= 80:
        add(raw)
    for match in _UID_RE.finditer(raw):
        add(match.group(0))
    for token in _tokens(raw):
        if token in _STOPWORDS or len(token) < 3:
            continue
        add(token)
        if len(out) >= max_terms:
            break
    return out[:max_terms]


def _secrets(client: Any) -> list[str]:
    raw = list(getattr(client, "_secrets", None) or [])
    settings = getattr(client, "settings", None)
    if settings is not None:
        for key in ("password", "username"):
            value = str(getattr(settings, key, "") or "")
            if value:
                raw.append(value)
    return [item for item in raw if item]


def _safe(value: Any, *, limit: int = 800, secrets: list[str] | None = None) -> str:
    text = dhis2_redact(_text(value, limit * 2), secrets)
    return agent_redact(text, limit=limit)


def _public_config(client: Any) -> dict[str, Any]:
    if client is None or not hasattr(client, "public_config"):
        return {}
    cfg = dict(client.public_config() or {})
    cfg.pop("password", None)
    cfg.pop("username", None)
    cfg.pop("authorization", None)
    return cfg


class _Dhis2Base(_BaseSource):
    def source_metadata(self) -> dict[str, Any]:
        return {"bounded": True, "read_only": True, "external": True, "writes": False}

    def _unavailable(self, request: ContextRequest, detail: str) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return {"available": False, "detail": "DHIS2 sources are General / VANTA only"}
        return {"available": False, "detail": detail}


class Dhis2EnvironmentContextSource(_Dhis2Base):
    id = "dhis2_environment"
    type = "dhis2_config"

    def __init__(self, client: Any = None, *, instance: str = "") -> None:
        self.client = client
        self.instance = str(instance or "").strip()

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.client is None:
            return {"available": False, "detail": "DHIS2 client is not configured"}
        cfg = _public_config(self.client)
        if not cfg.get("enabled"):
            return {"available": False, "detail": "DHIS2 is disabled"}
        if not cfg.get("configured"):
            return {"available": False, "detail": "DHIS2 is unconfigured"}
        env = self.instance or str(cfg.get("environment") or "canonical")
        return {"available": True, "detail": f"DHIS2 {env} configured (readonly)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        if self.client is None or not _query_has(request, _ENV_TERMS):
            return []
        cfg = _public_config(self.client)
        env = self.instance or str(cfg.get("environment") or "canonical")
        snippet = " ".join(filter(None, [
            f"instance={env}",
            f"enabled={cfg.get('enabled')}",
            f"configured={cfg.get('configured')}",
            f"mode={cfg.get('mode')}",
            f"allow_writes={cfg.get('allow_writes')}",
            f"base_url={cfg.get('base_url')}",
        ]))
        return _rank(request.query, [ContextCandidate(
            self.id,
            f"dhis2-env:{env or 'canonical'}",
            f"DHIS2 environment ({env or 'canonical'})",
            snippet,
            score=3.0,
            metadata={
                "instance": env,
                "mode": str(cfg.get("mode") or "readonly"),
                "allow_writes": bool(cfg.get("allow_writes")),
                "base_url": str(cfg.get("base_url") or ""),
                "username_set": bool(cfg.get("username_set")),
                "password_set": bool(cfg.get("password_set")),
            },
        )], limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        secrets = _secrets(self.client)
        cfg = _public_config(self.client)
        writes = False
        if self.client is not None and hasattr(self.client, "writes_allowed"):
            writes = bool(self.client.writes_allowed())
        lines = [
            f"Instance: {self.instance or cfg.get('environment') or 'canonical'}",
            f"Mode: {cfg.get('mode') or 'readonly'}; allow_writes: {cfg.get('allow_writes') or writes}",
            f"Enabled: {cfg.get('enabled')}; configured: {cfg.get('configured')}",
            f"Base URL: {_safe(cfg.get('base_url'), limit=200, secrets=secrets)}",
            f"Username configured: {bool(cfg.get('username_set'))}; secret configured: {bool(cfg.get('password_set'))}",
            "Credential values are never included.",
        ]
        content = _safe("\n".join(lines), limit=char_budget, secrets=secrets)
        return [self._evidence(item, content[:char_budget]) for item in candidates[:1] if content]


class Dhis2UidIndexContextSource(_Dhis2Base):
    id = "dhis2_uid_index"
    type = "dhis2_uid"

    def __init__(self, uid_index: Any = None) -> None:
        self.uid_index = uid_index

    def _records(self) -> list[dict[str, Any]]:
        if self.uid_index is None:
            return []
        store = getattr(self.uid_index, "mapping_store", None)
        if store is not None and hasattr(store, "records"):
            return [row for row in list(store.records() or []) if isinstance(row, dict)]
        if hasattr(self.uid_index, "records"):
            return [row for row in list(self.uid_index.records() or []) if isinstance(row, dict)]
        return []

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.uid_index is None:
            return {"available": False, "detail": "UID Index is not configured"}
        count = len(self._records())
        if not count:
            return {"available": False, "detail": "UID Index has no local records"}
        return {"available": True, "detail": f"{count} local UID index record(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        query = (request.query or "").strip()
        if not query:
            return []
        size = max(1, min(limit, 8))
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for needle in _query_needles(query):
            for row in filter_records(self._records(), query=needle, limit=size):
                uid = str(row.get("uid") or row.get("id") or "")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                rows.append(row)
                if len(rows) >= size:
                    break
            if len(rows) >= size:
                break
        out = []
        for row in rows:
            uid = str(row.get("uid") or row.get("id") or "")
            if not uid:
                continue
            title = _safe(row.get("name") or uid, limit=300)
            snippet = " ".join(filter(None, [
                uid,
                _safe(row.get("object_type"), limit=80),
                _safe(row.get("code"), limit=80),
                _safe(row.get("source_repository"), limit=120),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"dhis2-uid:{uid}",
                title,
                snippet,
                score=3.0,
                metadata={
                    "uid": uid,
                    "object_type": _safe(row.get("object_type"), limit=80),
                    "code": _safe(row.get("code"), limit=80),
                    "source_repository": _safe(row.get("source_repository"), limit=120),
                    "conflict": bool(row.get("conflict")),
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            content = "\n".join(filter(None, [
                f"UID: {meta.get('uid')}",
                f"Type: {meta.get('object_type') or '(unknown)'}",
                f"Code: {meta.get('code') or '(none)'}",
                f"Source: {meta.get('source_repository') or '(local index)'}",
                item.title,
            ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


class Dhis2EnrichmentContextSource(_Dhis2Base):
    id = "dhis2_enrichment"
    type = "dhis2_audit"

    def __init__(self, store: Any = None) -> None:
        self.store = store

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.store is None:
            return {"available": False, "detail": "Enrichment store is not configured"}
        snap = self.store.current_snapshot_id()
        if not snap:
            return {"available": False, "detail": "No enrichment snapshot is available"}
        return {"available": True, "detail": f"Enrichment snapshot {snap}"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        query = (request.query or "").strip()
        if not query or self.store is None:
            return []
        size = max(1, min(limit, 8))
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for needle in _query_needles(query):
            found, _total = self.store.search(q=needle, limit=size)
            for row in list(found or []):
                uid = str(row.get("uid") or "")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                rows.append(row)
                if len(rows) >= size:
                    break
            if len(rows) >= size:
                break
        out = []
        for row in rows[:size]:
            uid = str(row.get("uid") or "")
            if not uid:
                continue
            audits = row.get("audit_status_list") or []
            if isinstance(audits, str):
                audits = [audits]
            title = _safe(row.get("name") or uid, limit=300)
            snippet = " ".join(filter(None, [
                uid,
                _safe(row.get("object_type"), limit=80),
                _safe(row.get("code"), limit=80),
                " ".join(_safe(item, limit=40) for item in list(audits)[:6]),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"dhis2-enrichment:{uid}",
                title,
                snippet,
                score=3.0,
                metadata={
                    "uid": uid,
                    "object_type": _safe(row.get("object_type"), limit=80),
                    "code": _safe(row.get("code"), limit=80),
                    "program_name": _safe(row.get("program_name"), limit=160),
                    "audit_statuses": [str(item) for item in list(audits)[:8]],
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            rel_lines: list[str] = []
            uid = str(meta.get("uid") or "")
            if self.store is not None and uid:
                try:
                    rels = list(self.store.relationships_for(uid) or [])[:8]
                except Exception:
                    rels = []
                for rel in rels:
                    if not isinstance(rel, dict):
                        continue
                    rel_lines.append(_text(
                        f"{rel.get('rel_type')} {rel.get('from_uid')} -> {rel.get('to_uid')} "
                        f"{rel.get('to_name') or rel.get('from_name') or ''}",
                        200,
                    ))
            content = "\n".join(filter(None, [
                f"UID: {uid}",
                f"Type: {meta.get('object_type') or '(unknown)'}",
                f"Program: {meta.get('program_name') or '(none)'}",
                "Audit: " + (", ".join(list(meta.get("audit_statuses") or [])[:8]) or "(none)"),
                *rel_lines,
            ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


class Dhis2ExplorerContextSource(_Dhis2Base):
    id = "dhis2_explorer"
    type = "dhis2_metadata"

    def __init__(self, client: Any = None) -> None:
        self.client = client

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.client is None:
            return {"available": False, "detail": "DHIS2 client is not configured"}
        cfg = _public_config(self.client)
        if not cfg.get("enabled"):
            return {"available": False, "detail": "DHIS2 is disabled"}
        if not cfg.get("configured"):
            return {"available": False, "detail": "DHIS2 is unconfigured"}
        return {"available": True, "detail": "DHIS2 metadata search (GET, bounded)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        query = (request.query or "").strip()[:200]
        if not query or self.client is None:
            return []
        if not _query_has(request, _DHIS2_TERMS):
            return []
        size = max(1, min(limit, 8))
        payload = self.client.search(query, limit=size)
        out = []
        for row in list((payload or {}).get("results") or [])[:size]:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("id") or row.get("uid") or "")
            if not uid:
                continue
            title = _safe(row.get("name") or uid, limit=300, secrets=_secrets(self.client))
            snippet = " ".join(filter(None, [
                uid,
                _safe(row.get("resource_label") or row.get("resource_type"), limit=80),
                _safe(row.get("code"), limit=80),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"dhis2-meta:{uid}",
                title,
                snippet,
                score=3.0,
                metadata={
                    "uid": uid,
                    "resource_type": str(row.get("resource_type") or ""),
                    "resource_label": _safe(row.get("resource_label"), limit=80),
                    "code": _safe(row.get("code"), limit=80),
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        secrets = _secrets(self.client)
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            extra = ""
            resource = str(meta.get("resource_type") or "")
            uid = str(meta.get("uid") or "")
            if (
                self.client is not None
                and resource in ALLOWED_RESOURCES
                and uid
            ):
                try:
                    detail = self.client.get_metadata(resource, uid)
                    raw = dict((detail or {}).get("raw_fields") or {})
                    extra = " ".join(
                        _safe(raw.get(key), limit=160, secrets=secrets)
                        for key in ("shortName", "description", "valueType", "domainType")
                        if raw.get(key)
                    )
                except Exception:
                    extra = ""
            content = _safe("\n".join(filter(None, [
                f"UID: {uid}",
                f"Type: {meta.get('resource_label') or resource or '(unknown)'}",
                f"Code: {meta.get('code') or '(none)'}",
                item.title,
                extra,
            ])), limit=remaining, secrets=secrets)
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


class Dhis2ReportsContextSource(_Dhis2Base):
    id = "dhis2_reports"
    type = "dhis2_reports"

    def __init__(self, reports: Any = None) -> None:
        self._reports = reports

    @property
    def reports(self) -> Any:
        value = self._reports
        return value() if callable(value) else value

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.reports is None:
            return {"available": False, "detail": "DHIS2 Reports service is not configured"}
        return {"available": True, "detail": "DHIS2 Reports library (metadata only)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        query = (request.query or "").strip()
        if not query or self.reports is None or not _query_has(request, _REPORT_TERMS):
            return []
        size = max(1, min(limit, 8))
        out: list[ContextCandidate] = []
        seen_reports: set[str] = set()
        for needle in _query_needles(query):
            library = self.reports.list_standard_library(q=needle)
            for section in list((library or {}).get("sections") or []):
                env = str(section.get("environment") or "")
                for row in list(section.get("reports") or [])[:size]:
                    rid = str(row.get("uid") or row.get("id") or "")
                    key = f"{env}:{rid}"
                    if not rid or key in seen_reports:
                        continue
                    seen_reports.add(key)
                    title = _safe(row.get("name") or rid, limit=300)
                    out.append(ContextCandidate(
                        self.id,
                        f"dhis2-report:{env}:{rid}",
                        title,
                        " ".join(filter(None, [env, rid, _safe(row.get("report_type"), limit=80)])),
                        score=3.0,
                        metadata={
                            "report_id": rid,
                            "environment": env,
                            "report_type": _safe(row.get("report_type"), limit=80),
                            "html_available": bool(row.get("html_available")),
                        },
                    ))
                    if len(out) >= size:
                        break
                if len(out) >= size:
                    break
            if len(out) >= size:
                break
        if hasattr(self.reports, "search_org_units") and len(out) < size:
            ou_rows: list[Any] = []
            for needle in _query_needles(query):
                try:
                    payload = self.reports.search_org_units(
                        "stage", q=needle, limit=min(4, size), refresh=False
                    )
                except Exception:
                    payload = {}
                ou_rows = list(
                    (payload or {}).get("org_units")
                    or (payload or {}).get("items")
                    or []
                )
                if ou_rows:
                    break
            for row in ou_rows[: min(4, size - len(out))]:
                if not isinstance(row, dict):
                    continue
                uid = str(row.get("id") or row.get("uid") or "")
                if not uid:
                    continue
                title = _safe(row.get("displayName") or row.get("name") or uid, limit=300)
                out.append(ContextCandidate(
                    self.id,
                    f"dhis2-ou:{uid}",
                    title,
                    " ".join(filter(None, [uid, f"level={row.get('level')}"])),
                    score=1.0,
                    metadata={
                        "org_unit_id": uid,
                        "level": row.get("level"),
                        "kind": "org_unit",
                    },
                ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            if meta.get("kind") == "org_unit":
                content = "\n".join(filter(None, [
                    f"Org unit: {item.title}",
                    f"UID: {meta.get('org_unit_id')}",
                    f"Level: {meta.get('level')}",
                ]))[:remaining]
            else:
                content = "\n".join(filter(None, [
                    f"Report: {item.title}",
                    f"ID: {meta.get('report_id')}",
                    f"Environment: {meta.get('environment') or '(n/a)'}",
                    f"Type: {meta.get('report_type') or '(unknown)'}",
                    "HTML body is not included (metadata only).",
                ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


class Dhis2OperationsContextSource(_Dhis2Base):
    id = "dhis2_operations"
    type = "dhis2_history"

    def __init__(self, job_store: Any = None, audit_store: Any = None) -> None:
        self.jobs = job_store
        self.audit = audit_store

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if not _dhis2_allowed(request):
            return self._unavailable(request, "")
        if self.jobs is None and self.audit is None:
            return {"available": False, "detail": "DHIS2 operational history is not configured"}
        return {"available": True, "detail": "Recent DHIS2 jobs/audit (bounded)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        if not _query_has(request, _OPS_TERMS):
            return []
        size = max(1, min(limit, 8))
        out: list[ContextCandidate] = []
        if self.jobs is not None:
            for row in list(self.jobs.list_recent(limit=40) or [])[:40]:
                cap = str(row.get("capability_id") or "")
                repo = str(row.get("repository_id") or "")
                blob = f"{cap} {repo} {row.get('status') or ''}".lower()
                if "dhis2" not in blob and not cap.lower().startswith("dhis2"):
                    continue
                job_id = str(row.get("id") or "")
                if not job_id:
                    continue
                title = f"Job {job_id} {row.get('status') or ''}".strip()
                snippet = " ".join(filter(None, [cap, repo, str(row.get("created_at") or "")]))
                out.append(ContextCandidate(
                    self.id,
                    f"dhis2-job:{job_id}",
                    title,
                    snippet,
                    score=2.5,
                    metadata={
                        "job_id": job_id,
                        "capability_id": cap,
                        "repository_id": repo,
                        "status": str(row.get("status") or ""),
                        "created_at": str(row.get("created_at") or ""),
                    },
                ))
        if self.audit is not None:
            for row in list(self.audit.list_recent(limit=40) or [])[:40]:
                action = str(row.get("action") or "")
                if not action.startswith("DHIS2"):
                    continue
                stamp = str(row.get("timestamp") or "")
                title = action
                snippet = _safe(
                    f"{stamp} {row.get('target') or ''} {row.get('detail') or ''}",
                    limit=400,
                )
                out.append(ContextCandidate(
                    self.id,
                    f"dhis2-audit:{action}:{stamp}",
                    title,
                    snippet,
                    score=2.5,
                    metadata={
                        "action": action,
                        "target": _safe(row.get("target"), limit=160),
                        "ok": row.get("ok"),
                        "timestamp": stamp,
                    },
                ))
        return _rank(request.query, out, size)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if remaining <= 0:
                break
            meta = item.metadata
            if meta.get("job_id"):
                content = "\n".join(filter(None, [
                    f"Job: {meta.get('job_id')}",
                    f"Capability: {meta.get('capability_id')}",
                    f"Status: {meta.get('status')}",
                    f"Created: {meta.get('created_at')}",
                ]))[:remaining]
            else:
                content = "\n".join(filter(None, [
                    f"Audit: {meta.get('action')}",
                    f"Target: {meta.get('target')}",
                    f"When: {meta.get('timestamp')}",
                    f"ok: {meta.get('ok')}",
                ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
        return out


def dhis2_write_methods(client: Any) -> list[str]:
    """Public method names that would mutate DHIS2 — must stay empty."""
    found: list[str] = []
    for name in _FORBIDDEN_DHIS2_WRITES:
        if callable(getattr(client, name, None)):
            found.append(name)
    return found
