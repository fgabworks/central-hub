"""Conflict-aware dependency search over the local UID mapping index."""

from __future__ import annotations

import re
from typing import Any

from hub.dhis2.client import Dhis2Client
from hub.dhis2.uid_mapping.store import MappingIndexStore

# Common plural → singular aliases for builder dependency resources.
_ALIASES: dict[str, set[str]] = {
    "programs": {"program"},
    "programstages": {"programstage"},
    "programindicators": {"programindicator"},
    "dataelements": {"dataelement"},
    "optionsets": {"optionset"},
    "options": {"option"},
    "categorycombos": {"categorycombo", "categorycombination"},
    "categories": {"category"},
    "categoryoptions": {"categoryoption"},
    "datasets": {"dataset"},
    "constants": {"constant"},
    "indicators": {"indicator"},
    "organisationunits": {"organisationunit", "orgunit"},
    "trackedentityattributes": {"trackedentityattribute", "tea", "attribute"},
    "trackedentitytypes": {"trackedentitytype"},
    "attributes": {
        "attribute",
        "trackedentityattribute",
        "dataelementattribute",
        "programattribute",
    },
    "dashboards": {"dashboard"},
}


class UidIndex:
    """Adapter used by builders; never mutates or silently resolves the mapping index."""

    def __init__(
        self,
        client: Dhis2Client | None = None,
        *,
        mapping_store: MappingIndexStore | None = None,
        ttl_seconds: float = 300.0,
    ) -> None:
        self.client = client
        self.mapping_store = mapping_store or MappingIndexStore()
        self.ttl_seconds = ttl_seconds  # retained for API compatibility

    def clear(self) -> None:
        """The backing store is read on every request; there is no private cache."""

    def ensure(self, resources: list[str]) -> dict[str, Any]:
        loaded = {resource: len(self.search(resource, limit=1000)) for resource in resources}
        return {"ok": True, "loaded": loaded, "errors": {}}

    def search(self, resource: str, query: str = "", *, limit: int = 25) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        rows = [row for row in self.mapping_store.records() if self._matches_resource(row, resource)]
        if q:
            rows = [
                row
                for row in rows
                if q in str(row.get("uid") or "").lower()
                or q in str(row.get("name") or "").lower()
                or q in str(row.get("code") or "").lower()
            ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get("uid") or ""), []).append(row)
        results: list[dict[str, Any]] = []
        for uid, group in groups.items():
            if not uid:
                continue
            first = group[0]
            results.append(
                {
                    "id": uid,
                    "uid": uid,
                    "name": first.get("name") or "",
                    "code": first.get("code") or "",
                    "object_type": first.get("object_type") or "",
                    "source_repository": first.get("source_repository") or "",
                    "conflict": self._is_conflicting(group),
                }
            )
        results.sort(key=lambda item: (str(item.get("name") or "").lower(), item["id"]))
        return results[: max(1, min(int(limit or 25), 1000))]

    def get(self, resource: str, uid: str) -> dict[str, Any] | None:
        matches = [item for item in self.search(resource, uid, limit=1000) if item.get("id") == uid]
        if len(matches) != 1 or matches[0].get("conflict"):
            return None
        return matches[0]

    def find_duplicates(
        self,
        resource: str,
        *,
        name: str | None = None,
        code: str | None = None,
        uid: str | None = None,
    ) -> list[dict[str, Any]]:
        candidates = self.search(resource, limit=1000)
        matches: list[dict[str, Any]] = []
        for item in candidates:
            matched_fields: list[str] = []
            if uid and str(item.get("id")) == uid:
                matched_fields.append("UID")
            if name and str(item.get("name") or "").casefold() == name.casefold():
                matched_fields.append("name")
            if code and str(item.get("code") or "").casefold() == code.casefold():
                matched_fields.append("code")
            if matched_fields:
                matches.append({**item, "matched_fields": matched_fields})
        return matches

    @staticmethod
    def _matches_resource(row: dict[str, Any], resource: str) -> bool:
        expected = set(_ALIASES.get(_norm(resource), set()))
        expected.add(_singular_norm(resource))
        expected.add(_norm(resource))
        actual = _norm(str(row.get("object_type") or ""))
        if not actual:
            return False
        if actual in expected:
            return True
        # Soft match: dataElement ↔ dataElements, programIndicator ↔ programIndicators
        return any(
            actual == alias
            or actual.endswith(alias)
            or alias.endswith(actual)
            for alias in expected
            if alias
        )

    @staticmethod
    def _is_conflicting(group: list[dict[str, Any]]) -> bool:
        signatures = {
            (
                str(item.get("name") or ""),
                str(item.get("code") or ""),
                str(item.get("object_type") or ""),
            )
            for item in group
        }
        return len(signatures) > 1


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _singular_norm(resource: str) -> str:
    value = _norm(resource)
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("ses") and len(value) > 3:
        return value[:-2]
    return value[:-1] if value.endswith("s") else value
