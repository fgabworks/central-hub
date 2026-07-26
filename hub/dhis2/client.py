"""Read-only DHIS2 HTTP client.

GET-only. Never creates, updates, deletes, or imports metadata.
Writes remain blocked even if ALLOW_DHIS2_WRITES is true — write APIs
are not implemented in this milestone.

Hardening (adapted from Live Processing transport ideas, not domain logic):
- long-lived requests.Session + connection pool
- distinct probe vs operation timeouts
- bounded retries on idempotent GET only
- capped multi-page collection iteration (never full export)
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote, urljoin

import requests
from requests.adapters import HTTPAdapter

from hub.dhis2.redact import public_dhis2_config, redact_text, redact_url
from hub.settings import Dhis2Settings

# DHIS2 UIDs are typically 11 alphanumeric characters starting with a letter.
_UID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")

# Retry only these HTTP statuses on GET (idempotent).
_RETRY_STATUSES = frozenset({429, 502, 503})

# Allowlisted metadata collections for name search / detail fetch.
ALLOWED_RESOURCES: dict[str, str] = {
    "dataElements": "Data Element",
    "optionSets": "Option Set",
    "programs": "Program",
    "programIndicators": "Program Indicator",
    "programRules": "Program Rule",
    "indicators": "Indicator",
    "organisationUnits": "Organisation Unit",
    "categories": "Category",
    "categoryOptions": "Category Option",
    "dataSets": "Data Set",
    "trackedEntityAttributes": "Tracked Entity Attribute",
}

_DETAIL_FIELDS = (
    "id,name,displayName,code,shortName,description,created,lastUpdated,"
    "href,domainType,valueType,aggregationType,formName"
)


class Dhis2Error(Exception):
    """Friendly, redacted DHIS2 client error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class Dhis2Client:
    """Read-only DHIS2 Web API client with session reuse and bounded GET retries."""

    def __init__(self, settings: Dhis2Settings) -> None:
        self.settings = settings
        self._secrets = [s for s in [settings.password, settings.username] if s]
        self._stats: dict[str, int] = {
            "get": 0,
            "retry": 0,
            "errors": 0,
            "timeouts": 0,
        }
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.auth = (self.settings.username or "", self.settings.password or "")
        session.headers.update({"Accept": "application/json"})
        pool = max(1, int(self.settings.http_pool_maxsize))
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def request_stats(self) -> dict[str, int]:
        """Cumulative GET / retry / error counters since process start (or last reset)."""
        return dict(self._stats)

    def reset_request_stats(self) -> None:
        for key in self._stats:
            self._stats[key] = 0

    def public_config(self) -> dict[str, Any]:
        return public_dhis2_config(
            base_url=self.settings.base_url,
            username=self.settings.username,
            password=self.settings.password,
            timeout_seconds=self.settings.timeout_seconds,
            allow_writes=False,  # foundation is read-only
            enabled=self.settings.enabled,
            configured=self.settings.is_configured and self.settings.enabled,
            probe_timeout_seconds=self.settings.probe_timeout_seconds,
            retry_max=self.settings.retry_max,
            retry_backoff_seconds=self.settings.retry_backoff_seconds,
            page_size=self.settings.page_size,
            max_pages=self.settings.max_pages,
            http_pool_maxsize=self.settings.http_pool_maxsize,
            mode="readonly",
            environment=getattr(self.settings, "environment", "canonical") or "canonical",
            credential_fields=dict(getattr(self.settings, "credential_fields", {}) or {}),
            configuration_errors=tuple(
                getattr(self.settings, "configuration_errors", ()) or ()
            ),
            missing_fields=tuple(getattr(self.settings, "missing_fields", ()) or ()),
        )

    def writes_allowed(self) -> bool:
        """Always False for this milestone — no write methods exist."""
        return False

    def ping(self) -> bool:
        """Lightweight authenticated probe (GET /api/system/info?fields=version)."""
        try:
            self._get_json(
                "/api/system/info",
                params={"fields": "version"},
                timeout=self.settings.probe_timeout_seconds,
            )
            return True
        except Dhis2Error:
            return False

    def check_status(self) -> dict[str, Any]:
        """Probe connection with GET /api/system/info (and /api/me fallback)."""
        if not self.settings.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "latency_ms": 0,
                "base_url": redact_url(self.settings.base_url),
                "system": None,
                "user": None,
                "allow_writes": False,
                "mode": "readonly",
                "detail": "DHIS2 integration is disabled (DHIS2_ENABLED=false).",
            }
        if not self.settings.is_configured:
            return {
                "ok": False,
                "status": "not_configured",
                "latency_ms": 0,
                "base_url": redact_url(self.settings.base_url),
                "system": None,
                "user": None,
                "allow_writes": False,
                "mode": "readonly",
                "detail": self._configuration_detail(),
            }

        started = time.perf_counter()
        probe_timeout = self.settings.probe_timeout_seconds
        try:
            info = self._get_json(
                "/api/system/info",
                params={"fields": "version,revision,serverDate,contextPath,systemName,instanceName"},
                timeout=probe_timeout,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            me: dict[str, Any] | None = None
            try:
                me = self._get_json(
                    "/api/me",
                    params={"fields": "id,username,displayName"},
                    timeout=probe_timeout,
                )
            except Dhis2Error:
                me = None
            return {
                "ok": True,
                "status": "online",
                "latency_ms": latency_ms,
                "base_url": redact_url(self.settings.base_url),
                "system": {
                    "version": info.get("version"),
                    "revision": info.get("revision"),
                    "serverDate": info.get("serverDate"),
                    "contextPath": info.get("contextPath"),
                    "systemName": info.get("systemName") or info.get("instanceName"),
                },
                "user": {
                    "id": (me or {}).get("id"),
                    "username": (me or {}).get("username") or self.settings.username,
                    "displayName": (me or {}).get("displayName"),
                },
                "allow_writes": False,
                "mode": "readonly",
                "request_stats": self.request_stats(),
                "detail": "Connected",
            }
        except Dhis2Error as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": False,
                "status": "offline",
                "latency_ms": latency_ms,
                "base_url": redact_url(self.settings.base_url),
                "system": None,
                "user": None,
                "allow_writes": False,
                "mode": "readonly",
                "request_stats": self.request_stats(),
                "detail": exc.message,
            }

    def search(self, query: str, *, limit: int = 25) -> dict[str, Any]:
        """Search metadata by UID or name (read-only)."""
        self._require_ready()
        cleaned = (query or "").strip()
        if not cleaned:
            raise Dhis2Error("Enter a UID or name to search.")
        if len(cleaned) > 200:
            raise Dhis2Error("Search query is too long (max 200 characters).")

        limit = max(1, min(int(limit), 50))
        if _UID_RE.match(cleaned):
            return self._search_by_uid(cleaned)
        return self._search_by_name(cleaned, limit=limit)

    def get_metadata(self, resource_type: str, uid: str) -> dict[str, Any]:
        """Fetch a single metadata object by type + UID."""
        self._require_ready()
        if resource_type not in ALLOWED_RESOURCES:
            raise Dhis2Error(f"Resource type is not allowed: {resource_type}")
        if not _UID_RE.match(uid or ""):
            raise Dhis2Error("Invalid UID format.")

        payload = self._get_json(
            f"/api/{resource_type}/{quote(uid, safe='')}",
            params={"fields": _DETAIL_FIELDS},
        )
        return {
            "ok": True,
            "resource_type": resource_type,
            "resource_label": ALLOWED_RESOURCES[resource_type],
            "item": self._normalize_item(payload, resource_type),
            "raw_fields": {
                key: payload.get(key)
                for key in (
                    "id",
                    "name",
                    "displayName",
                    "code",
                    "shortName",
                    "description",
                    "created",
                    "lastUpdated",
                    "domainType",
                    "valueType",
                    "aggregationType",
                    "formName",
                )
                if key in payload
            },
        }

    def get_metadata_object(
        self,
        plural: str,
        uid: str,
        *,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """
        GET a single metadata object by collection plural + UID.

        Used by the UID mapping explorer for comparison and relationships.
        Still read-only — no create/update/delete/import.
        """
        self._require_ready()
        if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", plural or ""):
            raise Dhis2Error("Invalid resource collection name.")
        if not _UID_RE.match(uid or ""):
            raise Dhis2Error("Invalid UID format.")

        field_spec = fields or (
            "id,name,displayName,code,shortName,description,created,lastUpdated,"
            "domainType,valueType,aggregationType,formName,href,"
            "optionSet[id,name,code,valueType,options[id,name,code,sortOrder]],"
            "categoryCombo[id,name],program[id,name],"
            "expression,filter,numerator,denominator,"
            "options[id,name,code,sortOrder],dashboardItems[id,type,visualization[id,name],"
            "map[id,name],eventReport[id,name],eventChart[id,name],report[id,name]],"
            "dataSetElements[dataSet[id,name]],dataSets[id,name],"
            "programStageDataElements[programStage[id,name],dataElement[id,name]]"
        )
        payload = self._get_json(
            f"/api/{plural}/{quote(uid, safe='')}",
            params={"fields": field_spec},
        )
        return {
            "ok": True,
            "resource_type": plural,
            "resource_label": ALLOWED_RESOURCES.get(plural, plural),
            "item": self._normalize_item(payload, plural),
            "raw": payload,
        }

    def _search_by_uid(self, uid: str) -> dict[str, Any]:
        try:
            payload = self._get_json(f"/api/identifiableObjects/{quote(uid, safe='')}")
        except Dhis2Error as exc:
            if exc.status_code == 404:
                return {
                    "ok": True,
                    "mode": "uid",
                    "query": uid,
                    "results": [],
                    "detail": f"No metadata found for UID {uid}.",
                }
            raise

        # identifiableObjects returns type + id/name or nested object depending on version.
        resource_type = payload.get("type")
        if not resource_type and payload.get("href"):
            parts = str(payload.get("href")).rstrip("/").split("/")
            if len(parts) >= 2:
                resource_type = parts[-2]
        item = self._normalize_item(payload, resource_type)
        return {
            "ok": True,
            "mode": "uid",
            "query": uid,
            "results": [item] if item.get("id") else [],
            "detail": "UID lookup complete.",
        }

    def _search_by_name(self, query: str, *, limit: int) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        # Prefer identifiableObjects query when available; fall back per-resource filters.
        try:
            payload = self._get_json(
                "/api/identifiableObjects",
                params={
                    "query": query,
                    "paging": "false",
                    "fields": "id,name,displayName,href",
                },
            )
            for row in self._extract_collection(payload, "identifiableObjects"):
                results.append(self._normalize_item(row, row.get("type")))
                if len(results) >= limit:
                    break
        except Dhis2Error:
            results = []

        if not results:
            per_type = max(3, limit // max(len(ALLOWED_RESOURCES), 1))
            for resource_type in ALLOWED_RESOURCES:
                try:
                    payload = self._get_json(
                        f"/api/{resource_type}",
                        params={
                            "filter": f"name:ilike:{query}",
                            "pageSize": per_type,
                            "fields": "id,name,displayName,code",
                            "paging": "true",
                        },
                    )
                    for row in self._extract_collection(payload, resource_type):
                        results.append(self._normalize_item(row, resource_type))
                        if len(results) >= limit:
                            break
                except Dhis2Error:
                    continue
                if len(results) >= limit:
                    break

        return {
            "ok": True,
            "mode": "name",
            "query": query,
            "results": results[:limit],
            "detail": f"Found {min(len(results), limit)} result(s).",
        }

    def get_schema(self, klass: str) -> dict[str, Any]:
        """Fetch DHIS2 schema definition for a metadata klass (GET /api/schemas/{klass})."""
        self._require_ready()
        klass = (klass or "").strip()
        if not klass or not re.match(r"^[A-Za-z][A-Za-z0-9]*$", klass):
            raise Dhis2Error("Invalid schema klass.")
        return self._get_json(f"/api/schemas/{klass}")

    def list_resource(
        self,
        resource_type: str,
        *,
        fields: str = "id,name,displayName,code",
        page_size: int | None = None,
        query: str | None = None,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """List metadata for dependency selectors (GET only, hard page ceiling)."""
        result = self.iter_collection(
            resource_type,
            fields=fields,
            page_size=page_size,
            max_pages=max_pages,
            query=query,
        )
        return list(result["items"])

    def find_by_filter(
        self,
        plural: str,
        filter_expr: str,
        *,
        fields: str = "id,name,displayName,code",
        page_size: int = 50,
        max_pages: int = 2,
        normalize: bool = False,
    ) -> list[dict[str, Any]]:
        """GET a collection with an explicit DHIS2 filter (read-only, capped pages)."""
        result = self.iter_collection(
            plural,
            fields=fields,
            page_size=page_size,
            max_pages=max_pages,
            filter_expr=filter_expr,
            normalize=normalize,
        )
        return list(result.get("items") or [])

    def iter_collection(
        self,
        plural: str,
        *,
        fields: str = "id,name,displayName,code",
        page_size: int | None = None,
        max_pages: int | None = None,
        query: str | None = None,
        filter_expr: str | None = None,
        normalize: bool = True,
    ) -> dict[str, Any]:
        """
        Paginated GET over a collection with a hard page ceiling.

        Never performs a full metadata export. Returns items plus pager metadata
        including whether the walk was truncated by max_pages.
        """
        self._require_ready()
        if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", plural or ""):
            raise Dhis2Error("Invalid resource collection name.")
        if filter_expr is not None:
            filt = str(filter_expr).strip()
            # Allow only safe filter characters used by DHIS2 query syntax.
            if not re.match(r"^[A-Za-z0-9_.:\[\]|,=\-]+$", filt):
                raise Dhis2Error("Invalid DHIS2 filter expression.")
        else:
            filt = ""

        size = max(1, min(int(page_size or self.settings.page_size), 300))
        page_limit = max(1, min(int(max_pages or self.settings.max_pages), 100))
        items: list[dict[str, Any]] = []
        page = 1
        pages_fetched = 0
        total: int | None = None
        truncated = False

        while page <= page_limit:
            params: dict[str, Any] = {
                "fields": fields,
                "pageSize": size,
                "page": page,
                "paging": "true",
                "totalPages": "true",
            }
            if filt:
                params["filter"] = filt
            elif query:
                params["filter"] = f"name:ilike:{query}"
            payload = self._get_json(f"/api/{plural}", params=params)
            pages_fetched += 1
            batch = self._extract_collection(payload, plural)
            pager = payload.get("pager") if isinstance(payload.get("pager"), dict) else {}
            if total is None and pager.get("total") is not None:
                try:
                    total = int(pager["total"])
                except (TypeError, ValueError):
                    total = None
            for row in batch:
                if normalize:
                    items.append(self._normalize_item(row, plural))
                else:
                    items.append(row)
            page_count = pager.get("pageCount")
            try:
                page_count_i = int(page_count) if page_count is not None else None
            except (TypeError, ValueError):
                page_count_i = None
            if not batch:
                break
            if page_count_i is not None and page >= page_count_i:
                break
            if total is not None and len(items) >= total:
                break
            if page >= page_limit:
                # More pages may exist but we stop — safety ceiling.
                if page_count_i is not None and page < page_count_i:
                    truncated = True
                elif total is not None and len(items) < total:
                    truncated = True
                elif page_count_i is None and len(batch) >= size:
                    truncated = True
                break
            page += 1

        return {
            "ok": True,
            "resource_type": plural,
            "items": items,
            "count": len(items),
            "total": total,
            "pages_fetched": pages_fetched,
            "page_size": size,
            "max_pages": page_limit,
            "truncated": truncated,
        }

    def find_duplicates(
        self,
        resource_type: str,
        *,
        name: str | None = None,
        code: str | None = None,
        uid: str | None = None,
    ) -> dict[str, Any]:
        """Check existing objects by name, code, and/or UID (read-only)."""
        self._require_ready()
        matches: dict[str, list[dict[str, Any]]] = {"name": [], "code": [], "id": []}

        if uid and _UID_RE.match(uid):
            try:
                item = self._get_json(
                    f"/api/{resource_type}/{quote(uid, safe='')}",
                    params={"fields": "id,name,displayName,code"},
                )
                matches["id"].append(self._normalize_item(item, resource_type))
            except Dhis2Error as exc:
                if exc.status_code != 404:
                    raise

        if name and name.strip():
            payload = self._get_json(
                f"/api/{resource_type}",
                params={
                    "filter": f"name:eq:{name.strip()}",
                    "fields": "id,name,displayName,code",
                    "paging": "false",
                },
            )
            matches["name"] = [
                self._normalize_item(row, resource_type)
                for row in self._extract_collection(payload, resource_type)
            ]

        if code and code.strip():
            payload = self._get_json(
                f"/api/{resource_type}",
                params={
                    "filter": f"code:eq:{code.strip()}",
                    "fields": "id,name,displayName,code",
                    "paging": "false",
                },
            )
            matches["code"] = [
                self._normalize_item(row, resource_type)
                for row in self._extract_collection(payload, resource_type)
            ]

        total = sum(len(items) for items in matches.values())
        return {
            "ok": True,
            "has_duplicates": total > 0,
            "matches": matches,
            "detail": f"Found {total} duplicate candidate(s)." if total else "No duplicates found.",
        }

    def get_me(self) -> dict[str, Any]:
        """GET /api/me — authenticated user profile (no secrets)."""
        self._require_ready()
        return self._get_json(
            "/api/me",
            params={
                "fields": "id,username,displayName,surname,firstName,email,phoneNumber,"
                "organisationUnits[id,name],userRoles[id,name],userGroups[id,name]"
            },
        )

    def get_authorities(self) -> list[str]:
        """GET /api/me/authorization — authority strings only."""
        self._require_ready()
        data = self._get_json_any("/api/me/authorization")
        if isinstance(data, list):
            return sorted({str(item) for item in data})
        if isinstance(data, dict):
            # Some versions wrap authorities.
            for key in ("authorities", "systemAuthorities", "permissions"):
                value = data.get(key)
                if isinstance(value, list):
                    return sorted({str(item) for item in value})
        return []

    def get_schemas_document(self) -> list[dict[str, Any]]:
        """GET /api/schemas.json — full schema list (metadata definitions only)."""
        self._require_ready()
        data = self._get_json_any("/api/schemas.json")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            schemas = data.get("schemas")
            if isinstance(schemas, list):
                return [item for item in schemas if isinstance(item, dict)]
        raise Dhis2Error("Unexpected /api/schemas.json response shape.")

    def get_openapi_summary(self) -> dict[str, Any]:
        """GET /api/openapi.json — summarize paths/tags only (do not store full doc)."""
        self._require_ready()
        try:
            data = self._get_json_any("/api/openapi.json")
        except Dhis2Error as exc:
            # Fallback: some servers expose OpenAPI under alternate paths.
            if exc.status_code == 404:
                try:
                    data = self._get_json_any("/api/openapi.json", params={"type": "METADATA"})
                except Dhis2Error:
                    return {
                        "available": False,
                        "detail": exc.message,
                        "path_count": 0,
                        "tags": [],
                        "sample_paths": [],
                    }
            else:
                return {
                    "available": False,
                    "detail": exc.message,
                    "path_count": 0,
                    "tags": [],
                    "sample_paths": [],
                }

        if not isinstance(data, dict):
            return {
                "available": False,
                "detail": "OpenAPI document was not an object.",
                "path_count": 0,
                "tags": [],
                "sample_paths": [],
            }

        paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
        tags = []
        raw_tags = data.get("tags")
        if isinstance(raw_tags, list):
            tags = [
                str(tag.get("name") or tag)
                for tag in raw_tags
                if isinstance(tag, (dict, str))
            ][:40]
        sample_paths = sorted(paths.keys())[:40]
        return {
            "available": True,
            "detail": "OpenAPI summary loaded.",
            "path_count": len(paths),
            "tags": tags,
            "sample_paths": sample_paths,
            "info": {
                "title": (data.get("info") or {}).get("title")
                if isinstance(data.get("info"), dict)
                else None,
                "version": (data.get("info") or {}).get("version")
                if isinstance(data.get("info"), dict)
                else None,
            },
        }

    def get_api_entry(self) -> dict[str, Any]:
        """GET /api — entry-point resources map when available."""
        self._require_ready()
        data = self._get_json_any("/api")
        if isinstance(data, dict):
            # Keep only shallow keys / href-like values — no nested bulk data.
            resources: list[dict[str, str]] = []
            for key, value in list(data.items())[:200]:
                if isinstance(value, str):
                    resources.append({"name": str(key), "href": value})
                elif isinstance(value, dict) and value.get("href"):
                    resources.append({"name": str(key), "href": str(value.get("href"))})
            return {"available": True, "resource_count": len(resources), "resources": resources}
        return {"available": False, "resource_count": 0, "resources": [], "detail": "Unexpected /api shape."}

    def get_resource_count_and_sample(
        self,
        plural: str,
        *,
        sample_size: int = 3,
    ) -> dict[str, Any]:
        """GET pager total + tiny sample for one collection (never full export)."""
        self._require_ready()
        if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", plural or ""):
            raise Dhis2Error("Invalid resource collection name.")
        payload = self._get_json(
            f"/api/{plural}",
            params={
                "fields": "id,name,displayName,code",
                "pageSize": max(1, min(int(sample_size), 5)),
                "page": 1,
                "paging": "true",
                "totalPages": "true",
            },
        )
        pager = payload.get("pager") if isinstance(payload.get("pager"), dict) else {}
        items = self._extract_collection(payload, plural)
        return {
            "count": pager.get("total"),
            "sample": [
                {
                    "id": item.get("id"),
                    "name": item.get("displayName") or item.get("name"),
                    "code": item.get("code"),
                }
                for item in items[:sample_size]
            ],
        }

    def _require_ready(self) -> None:
        if not self.settings.enabled:
            raise Dhis2Error("DHIS2 integration is disabled (DHIS2_ENABLED=false).")
        if not self.settings.is_configured:
            raise Dhis2Error(self._configuration_detail())

    def _configuration_detail(self) -> str:
        parts = [
            str(p).strip()
            for p in (getattr(self.settings, "configuration_errors", ()) or ())
            if str(p).strip()
        ]
        if not parts:
            missing = tuple(getattr(self.settings, "missing_fields", ()) or ())
            if missing:
                parts.append("Missing fields: " + ", ".join(missing))
        if not parts:
            parts.append(
                "DHIS2 is not configured. Set canonical DHIS2_* credentials, "
                "or set DHIS2_ENVIRONMENT=stage|live with matching STAGE_/LIVE_ aliases."
            )
        return " ".join(parts)

    def get_text(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "text/html, text/plain, */*",
        timeout: float | None = None,
        max_bytes: int = 5_000_000,
    ) -> str:
        """
        GET a text/HTML body (read-only). Used for standard report data.html / design.

        Never returns credentials. Response body is size-capped.
        """
        raw = self._get_bytes(
            path,
            params=params,
            accept=accept,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        return raw.decode("utf-8", errors="replace")

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        data = self._get_json_any(path, params=params, timeout=timeout)
        if not isinstance(data, dict):
            raise Dhis2Error("Unexpected DHIS2 response shape.")
        return data

    def _get_json_any(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Hard read-only: this client only issues GET (with bounded retries)."""
        response = self._get_response(
            path,
            params=params,
            timeout=timeout,
            accept="application/json",
        )
        try:
            return response.json()
        except ValueError as exc:
            self._stats["errors"] += 1
            raise Dhis2Error("DHIS2 returned a non-JSON response.") from exc

    def _get_bytes(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "*/*",
        timeout: float | None = None,
        max_bytes: int = 5_000_000,
    ) -> bytes:
        response = self._get_response(
            path,
            params=params,
            timeout=timeout,
            accept=accept,
        )
        content = response.content or b""
        if len(content) > max_bytes:
            raise Dhis2Error(
                f"DHIS2 response exceeded size limit ({max_bytes} bytes)."
            )
        return content

    def _get_response(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        accept: str = "application/json",
    ) -> requests.Response:
        """Hard read-only GET with bounded retries. Never POST/PUT/PATCH/DELETE."""
        if not self.settings.base_url:
            raise Dhis2Error("DHIS2 base URL is not configured.")
        url = urljoin(self.settings.base_url.rstrip("/") + "/", path.lstrip("/"))
        timeout_s = float(
            self.settings.timeout_seconds if timeout is None else timeout
        )
        max_attempts = 1 + max(0, int(self.settings.retry_max))
        last_error: Dhis2Error | None = None
        headers = {"Accept": accept} if accept and accept != "application/json" else None

        for attempt in range(max_attempts):
            if attempt > 0:
                self._stats["retry"] += 1
                delay = min(
                    8.0,
                    float(self.settings.retry_backoff_seconds) * (2 ** (attempt - 1)),
                )
                if delay > 0:
                    time.sleep(delay)
            try:
                self._stats["get"] += 1
                get_kwargs: dict[str, Any] = {
                    "params": params,
                    "timeout": timeout_s,
                }
                if headers:
                    get_kwargs["headers"] = headers
                response = self._session.get(url, **get_kwargs)
            except requests.Timeout:
                self._stats["timeouts"] += 1
                self._stats["errors"] += 1
                last_error = Dhis2Error(
                    f"DHIS2 request timed out after {timeout_s:g}s."
                )
                continue
            except requests.ConnectionError:
                self._stats["errors"] += 1
                last_error = Dhis2Error(
                    "Could not reach DHIS2. Check DHIS2_BASE_URL and network access."
                )
                continue
            except requests.RequestException as exc:
                self._stats["errors"] += 1
                raise Dhis2Error(
                    redact_text(f"DHIS2 request failed: {exc}", self._secrets)
                ) from None

            if response.status_code in {401, 403}:
                self._stats["errors"] += 1
                raise Dhis2Error(
                    "DHIS2 authentication failed. Check username and password.",
                    status_code=response.status_code,
                )
            if response.status_code == 404:
                raise Dhis2Error("DHIS2 resource not found.", status_code=404)
            if response.status_code in _RETRY_STATUSES:
                self._stats["errors"] += 1
                body = redact_text(response.text[:240], self._secrets)
                last_error = Dhis2Error(
                    f"DHIS2 returned HTTP {response.status_code}. {body}".strip(),
                    status_code=response.status_code,
                )
                continue
            if response.status_code >= 400:
                self._stats["errors"] += 1
                body = redact_text(response.text[:240], self._secrets)
                raise Dhis2Error(
                    f"DHIS2 returned HTTP {response.status_code}. {body}".strip(),
                    status_code=response.status_code,
                )
            return response

        if last_error is not None:
            raise last_error
        self._stats["errors"] += 1
        raise Dhis2Error("DHIS2 request failed after retries.")

    @staticmethod
    def _extract_collection(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        # Some endpoints return a bare list under alternate keys.
        for candidate in payload.values():
            if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
                return [item for item in candidate if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_item(payload: dict[str, Any], resource_type: str | None) -> dict[str, Any]:
        href = payload.get("href") or ""
        inferred = resource_type
        if not inferred and href:
            parts = href.rstrip("/").split("/")
            if len(parts) >= 2:
                inferred = parts[-2]
        label = ALLOWED_RESOURCES.get(inferred or "", inferred or "Unknown")
        return {
            "id": payload.get("id") or payload.get("uid"),
            "name": payload.get("displayName") or payload.get("name"),
            "code": payload.get("code"),
            "resource_type": inferred,
            "resource_label": label,
            "href": href or None,
        }
