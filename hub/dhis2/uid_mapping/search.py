"""Search and filter helpers for the UID mapping explorer."""

from __future__ import annotations

from typing import Any


def filter_records(
    records: list[dict[str, Any]],
    *,
    query: str = "",
    object_type: str = "",
    source_repository: str = "",
    environment: str = "",
    limit: int | None = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return filtered rows. Pass limit=None for the full match set."""
    q = (query or "").strip().lower()
    otype = (object_type or "").strip().lower()
    repo = (source_repository or "").strip().lower()
    env = (environment or "").strip().lower()
    offset = max(0, int(offset or 0))

    matches: list[dict[str, Any]] = []
    for rec in records:
        if otype and otype != "all" and str(rec.get("object_type") or "").lower() != otype:
            continue
        if repo and repo != "all" and str(rec.get("source_repository") or "").lower() != repo:
            continue
        if env and env != "all" and str(rec.get("source_environment") or "").lower() != env:
            continue
        if q:
            blob = " ".join(
                [
                    str(rec.get("uid") or ""),
                    str(rec.get("name") or ""),
                    str(rec.get("code") or ""),
                ]
            ).lower()
            if q not in blob:
                continue
        matches.append(rec)

    if limit is None:
        return matches[offset:] if offset else matches

    page_size = max(1, min(int(limit), 5000))
    return matches[offset : offset + page_size]


def facet_values(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    types: set[str] = set()
    repos: set[str] = set()
    envs: set[str] = set()
    for rec in records:
        if rec.get("object_type"):
            types.add(str(rec["object_type"]))
        if rec.get("source_repository"):
            repos.add(str(rec["source_repository"]))
        if rec.get("source_environment"):
            envs.add(str(rec["source_environment"]))
    return {
        "object_types": sorted(types),
        "source_repositories": sorted(repos),
        "environments": sorted(envs),
    }
