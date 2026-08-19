"""Provider-agnostic, bounded context for AiriX.

Sources search CLIMATE-owned stores and registered external read-only
connectors.  The resolver ranks candidates, retrieves a small evidence set,
and returns one plain-text packet so provider adapters never need
source-specific logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from hub.climate.context_scope import ALL, GENERAL, REPOSITORY
from hub.climate.preflight import resolve_climate_context
from hub.registry.models import Registry, Repository


_WORD_RE = re.compile(r"[a-z0-9_./-]{2,}", re.IGNORECASE)


def _text(value: Any, limit: int = 800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(value or "")}


def _score(query: str, title: str, snippet: str, *, base: float = 0.0) -> float:
    terms = _tokens(query)
    if not terms:
        return base
    title_terms = _tokens(title)
    body_terms = _tokens(snippet)
    score = base + (4.0 * len(terms & title_terms)) + len(terms & body_terms)
    phrase = _text(query, 200).lower()
    if phrase and phrase in f"{title} {snippet}".lower():
        score += 8.0
    return score


def _rank(query: str, rows: list[ContextCandidate], limit: int) -> list[ContextCandidate]:
    for row in rows:
        row.score = _score(query, row.title, row.snippet, base=row.score)
    rows.sort(key=lambda row: (-row.score, row.evidence_id))
    return rows[:limit]


@dataclass(frozen=True)
class ContextRequest:
    query: str
    workspace: str
    scope: str = GENERAL
    repository_id: str = ""
    provider: str = ""
    model: str = ""
    current_file: str = ""
    selected_files: tuple[str, ...] = ()
    selection: str = ""


@dataclass
class ContextCandidate:
    source_id: str
    evidence_id: str
    title: str
    snippet: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextEvidence:
    source_id: str
    reference: str
    title: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class ClimateContextSource(Protocol):
    id: str
    type: str

    def availability(self, request: ContextRequest) -> dict[str, Any]: ...
    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]: ...
    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]: ...
    def source_metadata(self) -> dict[str, Any]: ...


@dataclass
class ContextResolution:
    packet: str
    sources_considered: list[dict[str, Any]]
    sources_queried: list[str]
    sources_used: list[str]
    evidence_references: list[dict[str, Any]]
    failures: list[dict[str, str]]
    repository_evidence_origin: str = "none"
    repository_evidence_origins: list[str] = field(default_factory=list)


class ClimateContextRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, ClimateContextSource] = {}

    def register(self, source: ClimateContextSource) -> None:
        source_id = str(getattr(source, "id", "") or "").strip()
        source_type = str(getattr(source, "type", "") or "").strip()
        if not source_id or not source_type:
            raise ValueError("Context sources require non-empty id and type")
        if source_id in self._sources:
            raise ValueError(f"Context source already registered: {source_id}")
        self._sources[source_id] = source

    def sources(self) -> list[ClimateContextSource]:
        return list(self._sources.values())


class ClimateContextResolver:
    def __init__(
        self,
        registry: ClimateContextRegistry,
        *,
        max_candidates_per_source: int = 12,
        max_evidence: int = 8,
        max_chars: int = 12_000,
        max_item_chars: int = 2_400,
    ) -> None:
        self.registry = registry
        self.max_candidates_per_source = max(1, min(max_candidates_per_source, 50))
        self.max_evidence = max(1, min(max_evidence, 24))
        self.max_chars = max(1_000, min(max_chars, 40_000))
        self.max_item_chars = max(400, min(max_item_chars, 8_000))

    def resolve(self, request: ContextRequest) -> ContextResolution:
        considered: list[dict[str, Any]] = []
        queried: list[str] = []
        failures: list[dict[str, str]] = []
        candidates: list[tuple[ClimateContextSource, ContextCandidate]] = []
        for source in self.registry.sources():
            row = {"id": source.id, "type": source.type, "metadata": source.source_metadata()}
            try:
                available = dict(source.availability(request) or {})
                row.update({
                    "available": bool(available.get("available")),
                    "detail": _text(available.get("detail"), 240),
                })
            except Exception as exc:  # one unavailable source cannot block AiriX
                row.update({"available": False, "detail": "Availability check failed"})
                failures.append({"source_id": source.id, "stage": "availability", "error": type(exc).__name__})
            considered.append(row)
            if not row["available"]:
                continue
            queried.append(source.id)
            try:
                found = source.search(request, limit=self.max_candidates_per_source)
                for item in list(found or [])[: self.max_candidates_per_source]:
                    item.score = float(item.score) + _score(
                        request.query, item.title, item.snippet
                    )
                    candidates.append((source, item))
            except Exception as exc:  # isolate search failures too
                failures.append({"source_id": source.id, "stage": "search", "error": type(exc).__name__})

        # Prefer the strongest candidate from each source, then fill globally.
        candidates.sort(key=lambda pair: (-pair[1].score, pair[0].id, pair[1].evidence_id))
        candidates = [pair for pair in candidates if pair[1].score >= 2.0]
        selected: list[tuple[ClimateContextSource, ContextCandidate]] = []
        seen_sources: set[str] = set()
        for pair in candidates:
            if pair[0].id not in seen_sources:
                selected.append(pair)
                seen_sources.add(pair[0].id)
                if len(selected) >= self.max_evidence:
                    break
        for pair in candidates:
            if len(selected) >= self.max_evidence:
                break
            if pair not in selected:
                selected.append(pair)

        evidence: list[ContextEvidence] = []
        remaining = self.max_chars
        for source in self.registry.sources():
            source_candidates = [candidate for owner, candidate in selected if owner is source]
            if not source_candidates or remaining <= 0:
                continue
            budget = min(remaining, self.max_item_chars * len(source_candidates))
            try:
                rows = source.retrieve(request, source_candidates, char_budget=budget)
            except Exception as exc:
                failures.append({"source_id": source.id, "stage": "retrieve", "error": type(exc).__name__})
                continue
            for item in list(rows or []):
                content = str(item.content or "").strip()[: min(self.max_item_chars, remaining)]
                if not content:
                    continue
                item.content = content
                evidence.append(item)
                remaining -= len(content)
                if remaining <= 0 or len(evidence) >= self.max_evidence:
                    break

        used = list(dict.fromkeys(item.source_id for item in evidence))
        refs = [
            {
                "source_id": item.source_id,
                "reference": item.reference,
                "title": item.title,
                "score": round(float(item.score), 3),
                "metadata": dict(item.metadata or {}),
            }
            for item in evidence
        ]
        packet = self._packet(request, evidence)[: self.max_chars]
        origins = {
            str((item.metadata or {}).get("evidence_origin") or "")
            for item in evidence
        }
        origins.discard("")
        has_brain = "repobrain_snapshot" in origins
        has_cross = "repobrain_cross_repository" in origins
        has_live = "live_repository_retrieval" in origins
        ordered_origins = [
            value for value in (
                "repobrain_snapshot", "repobrain_cross_repository",
                "live_repository_retrieval",
            ) if value in origins
        ]
        if has_brain and has_live and not has_cross:
            repository_origin = "both"
        elif len(ordered_origins) > 1:
            repository_origin = "+".join(ordered_origins)
        elif has_brain:
            repository_origin = "repobrain_snapshot"
        elif has_cross:
            repository_origin = "repobrain_cross_repository"
        elif has_live:
            repository_origin = "live_repository_retrieval"
        else:
            repository_origin = "none"
        return ContextResolution(
            packet, considered, queried, used, refs, failures, repository_origin,
            ordered_origins,
        )

    @staticmethod
    def _packet(request: ContextRequest, evidence: list[ContextEvidence]) -> str:
        if not evidence:
            return ""
        lines = [
            "CLIMATE context packet (bounded, read-only).",
            f"Scope: {request.scope}" + (
                f" / {request.repository_id}" if request.scope == REPOSITORY else ""
            ),
            "Use only relevant evidence below; treat source references as citations.",
        ]
        for item in evidence:
            lines.extend([
                "",
                f"[{item.source_id}] {item.title}",
                f"Reference: {item.reference}",
                item.content,
            ])
        return "\n".join(lines).strip()


class _BaseSource:
    id = ""
    type = ""

    def source_metadata(self) -> dict[str, Any]:
        return {"bounded": True, "read_only": True}

    @staticmethod
    def _evidence(candidate: ContextCandidate, content: str, **metadata: Any) -> ContextEvidence:
        return ContextEvidence(
            source_id=candidate.source_id,
            reference=candidate.evidence_id,
            title=candidate.title,
            content=content,
            score=candidate.score,
            metadata=metadata or {
                key: value for key, value in candidate.metadata.items() if not key.startswith("_")
            },
        )


class RepoBrainContextSource(_BaseSource):
    id = "repobrain"
    type = "repository_intelligence"

    def __init__(self, registry: Registry, service_loader: Callable[[], Any | None]) -> None:
        self.registry = registry
        self.service_loader = service_loader

    def source_metadata(self) -> dict[str, Any]:
        return {
            "bounded": True,
            "read_only": True,
            "persistent": True,
            "live_verification_required": True,
            "snapshot_schema_version": 1,
        }

    def _repos(self, request: ContextRequest) -> list[Repository]:
        rows = [
            repo for repo in self.registry.enabled_repositories()
            if repo.type == "command"
            and RepositoriesContextSource._workspace(repo) == request.workspace
        ]
        if request.scope == REPOSITORY:
            rows = [repo for repo in rows if repo.id == request.repository_id]
        return rows


    def availability(self, request: ContextRequest) -> dict[str, Any]:
        service = self.service_loader()
        repos = self._repos(request)
        if service is None:
            return {"available": False, "detail": "RepoBrain service is not configured"}
        if request.scope == REPOSITORY:
            return {"available": bool(repos), "detail": "Specific repository snapshot can be built or refreshed"}
        learned = sum(1 for repo in repos if service.latest(repo.id) is not None)
        return {
            "available": learned > 0,
            "detail": f"{learned} persisted RepoBrain snapshot(s)",
        }

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        service = self.service_loader()
        repos = self._repos(request)
        if service is None or not repos:
            return []
        if request.scope == REPOSITORY:
            context = service.context(repos[0].id, request.query, refresh=True)
            if not context:
                return []
            return [ContextCandidate(
                self.id,
                f"repobrain:{repos[0].id}:v{context.get('version')}",
                f"{repos[0].name} RepoBrain orientation",
                str(context.get("summary") or ""),
                score=7.0,
                metadata={
                    "repository_id": repos[0].id,
                    "snapshot_id": context.get("snapshot_id"),
                    "snapshot_version": context.get("version"),
                    "git_commit": context.get("git_commit"),
                    "freshness": context.get("freshness"),
                    "stale": bool(context.get("stale")),
                    "confidence": context.get("confidence") or {},
                    "source_references": context.get("source_references") or [],
                    "evidence_origin": "repobrain_snapshot",
                    "_content": str(context.get("content") or ""),
                },
            )]
        ranked = service.rank_repositories([repo.id for repo in repos], request.query)
        names = {repo.id: repo.name for repo in repos}
        rows = [ContextCandidate(
            self.id,
            f"repobrain:{item.get('repository_id')}:v{item.get('version')}",
            f"{names.get(str(item.get('repository_id') or ''), item.get('repository_id'))} RepoBrain summary",
            str(item.get("summary") or "")[:1_000],
            score=4.0 + float(item.get("score") or 0),
            metadata={
                "repository_id": str(item.get("repository_id") or ""),
                "snapshot_id": item.get("snapshot_id"),
                "snapshot_version": item.get("version"),
                "git_commit": item.get("git_commit"),
                "freshness": item.get("freshness"),
                "stale": bool(item.get("stale")),
                "evidence_origin": "repobrain_snapshot",
            },
        ) for item in ranked]
        return rows[:limit]

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        service = self.service_loader()
        if service is None:
            return []
        rows: list[ContextEvidence] = []
        remaining = char_budget
        for candidate in candidates:
            metadata = dict(candidate.metadata or {})
            content = str(metadata.pop("_content", "") or "")
            if not content:
                context = service.context(
                    str(metadata.get("repository_id") or ""), request.query, refresh=True
                )
                if not context:
                    continue
                content = str(context.get("content") or "")
                metadata.update({
                    "snapshot_id": context.get("snapshot_id"),
                    "snapshot_version": context.get("version"),
                    "git_commit": context.get("git_commit"),
                    "freshness": context.get("freshness"),
                    "stale": bool(context.get("stale")),
                    "confidence": context.get("confidence") or {},
                    "source_references": context.get("source_references") or [],
                })
            content = content[:remaining]
            if content:
                rows.append(self._evidence(candidate, content, **metadata))
                remaining -= len(content)
            if remaining <= 0:
                break
        return rows


class CrossRepoBrainContextSource(_BaseSource):
    id = "repobrain_cross"
    type = "cross_repository_intelligence"

    def __init__(self, registry: Registry, service_loader: Callable[[], Any | None]) -> None:
        self.registry = registry
        self.service_loader = service_loader

    def source_metadata(self) -> dict[str, Any]:
        return {
            "bounded": True,
            "read_only": True,
            "persistent": True,
            "live_verification_required": True,
            "cross_snapshot_schema_version": 1,
        }

    def _repos(self, request: ContextRequest) -> list[Repository]:
        return [
            repo for repo in self.registry.enabled_repositories()
            if repo.type == "command"
            and RepositoriesContextSource._workspace(repo) == request.workspace
        ]

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        service = self.service_loader()
        repos = self._repos(request)
        if service is None or len(repos) < 2:
            return {"available": False, "detail": "Cross-repository RepoBrain requires two repositories"}
        latest = service.latest_cross_snapshot()
        learned = sum(1 for repo in repos if service.latest(repo.id) is not None)
        available = latest is not None or (request.scope != GENERAL and learned >= 2)
        return {
            "available": available,
            "detail": f"{learned} learned repository snapshot(s); cross snapshot "
            + ("available" if latest is not None else "not built"),
        }

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        service = self.service_loader()
        repos = self._repos(request)
        if service is None or len(repos) < 2:
            return []
        repository_ids = [repo.id for repo in repos]
        context = service.cross_context(
            request.query,
            repository_ids=repository_ids,
            anchor_repository_id=request.repository_id if request.scope == REPOSITORY else "",
            refresh=request.scope == ALL,
        )
        if not context:
            return []
        title = (
            f"Related repositories for {request.repository_id}"
            if request.scope == REPOSITORY else "Cross-repository RepoBrain relationships"
        )
        return [ContextCandidate(
            self.id,
            f"repobrain-cross:v{context.get('version')}",
            title,
            str(context.get("content") or "")[:1_000],
            score=6.5,
            metadata={
                "cross_snapshot_id": context.get("snapshot_id"),
                "cross_snapshot_version": context.get("version"),
                "freshness": context.get("freshness"),
                "stale": bool(context.get("stale")),
                "relationship_ids": [
                    str(row.get("id") or "") for row in list(context.get("relationships") or [])[:24]
                ],
                "source_references": context.get("source_references") or [],
                "anchor_repository_id": request.repository_id if request.scope == REPOSITORY else "",
                "orientation_only": request.scope == REPOSITORY,
                "evidence_origin": "repobrain_cross_repository",
                "_content": str(context.get("content") or ""),
            },
        )][:limit]

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        rows: list[ContextEvidence] = []
        remaining = char_budget
        for candidate in candidates:
            metadata = dict(candidate.metadata or {})
            content = str(metadata.pop("_content", "") or "")[:remaining]
            if content:
                rows.append(self._evidence(candidate, content, **metadata))
                remaining -= len(content)
            if remaining <= 0:
                break
        return rows


class RepositoriesContextSource(_BaseSource):
    id = "repositories"
    type = "repository"

    def __init__(
        self,
        registry: Registry,
        repository_workspace: Any,
        intelligence_loader: Callable[[], Any | None],
        context_loader: Callable[..., Any] = resolve_climate_context,
        repobrain_loader: Callable[[], Any | None] | None = None,
    ) -> None:
        self.registry = registry
        self.repository_workspace = repository_workspace
        self.intelligence_loader = intelligence_loader
        self.context_loader = context_loader
        self.repobrain_loader = repobrain_loader or (lambda: None)

    @staticmethod
    def _workspace(repo: Repository) -> str:
        tags = {str(tag).strip().lower() for tag in repo.tags}
        return "personal" if tags & {"personal", "arctic"} else "work"

    def _repos(self, request: ContextRequest) -> list[Repository]:
        rows = [
            repo for repo in self.registry.enabled_repositories()
            if self._workspace(repo) == request.workspace
        ]
        if request.scope == REPOSITORY:
            rows = [repo for repo in rows if repo.id == request.repository_id]
        return rows

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        rows = self._repos(request)
        return {"available": bool(rows), "detail": f"{len(rows)} registered repository(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        repos = self._repos(request)
        if request.scope == GENERAL:
            rows = [
                ContextCandidate(
                    self.id,
                    f"repository:{repo.id}",
                    repo.name,
                    f"{repo.name} ({repo.id}), type={repo.type} {repo.description} {' '.join(repo.tags)}",
                    score=2.0,
                    metadata={"repository_id": repo.id, "kind": "registry"},
                )
                for repo in repos[:limit]
            ]
            return _rank(request.query, rows, limit)
        if request.scope == ALL:
            registry_rows = [
                ContextCandidate(
                    self.id,
                    f"repository:{repo.id}",
                    repo.name,
                    f"{repo.name} ({repo.id}), type={repo.type} {repo.description} {' '.join(repo.tags)}",
                    score=2.0,
                    metadata={"repository_id": repo.id, "kind": "registry"},
                )
                for repo in repos[:limit]
            ]
            available_ids = []
            for repo in repos:
                try:
                    if bool((self.repository_workspace.availability(repo) or {}).get("available")):
                        available_ids.append(repo.id)
                except Exception:
                    continue
            repobrain = self.repobrain_loader()
            if repobrain is not None and available_ids and hasattr(repobrain, "rank_repositories"):
                try:
                    ranker = getattr(repobrain, "rank_repositories_cross", None)
                    ranked = (
                        ranker(available_ids, request.query, refresh=True)
                        if callable(ranker)
                        else repobrain.rank_repositories(available_ids, request.query)
                    )
                    ranked_ids = [
                        str(item.get("repository_id") or "") for item in list(ranked or [])
                        if str(item.get("repository_id") or "") in available_ids
                    ]
                    available_ids = ranked_ids + [rid for rid in available_ids if rid not in ranked_ids]
                except Exception:
                    # Persistent orientation is optional; live retrieval remains authoritative.
                    pass
            intelligence = self.intelligence_loader()
            if intelligence is not None and available_ids and hasattr(intelligence, "retrieve"):
                knowledge = intelligence.retrieve(
                    available_ids,
                    request.query,
                    limit=min(limit, 8),
                    max_repositories=max(1, len(available_ids)),
                    include_empty_fallback=False,
                )
                items = list((knowledge or {}).get("items") or [])[:limit]
                hits = [
                    ContextCandidate(
                        self.id,
                        f"repository:{_text(item.get('repository_id'), 100)}:{_text(item.get('path'), 300)}",
                        _text(item.get("path") or item.get("repository_id"), 300),
                        _text(item.get("summary"), 1_000),
                        score=float(item.get("score") or 3.0),
                        metadata={
                            "repository_id": _text(item.get("repository_id"), 100),
                            "path": _text(item.get("path"), 400),
                            "kind": "repository_intelligence",
                            "evidence_origin": "live_repository_retrieval",
                        },
                    )
                    for item in items if isinstance(item, dict)
                ]
                return _rank(request.query, registry_rows + hits, limit)
            return _rank(request.query, registry_rows, limit)

        repo = repos[0] if repos else None
        if repo is None:
            return []
        resolved = self.context_loader(
            workspace=request.workspace,
            repo=repo,
            repository_workspace=self.repository_workspace,
            prompt=request.query,
            provider=request.provider,
            model=request.model,
            task_mode="ask",
            current_file=request.current_file,
            selected_files=list(request.selected_files),
            selection=request.selection,
            include_repo_context=True,
            repository_intelligence=self.intelligence_loader() if request.workspace == "work" else None,
            handoff=False,
            repository_agent=False,
        )
        if not getattr(resolved, "ok", False) or not str(getattr(resolved, "packet", "") or "").strip():
            return []
        raw_paths = getattr(resolved, "source_files", None)
        if not isinstance(raw_paths, (list, tuple)):
            raw_paths = []
        paths = [str(path) for path in list(raw_paths)[:16]]
        return [ContextCandidate(
            self.id,
            f"repository:{repo.id}:context",
            f"{repo.name} relevant repository context",
            " ".join(paths) or repo.description,
            score=6.0,
            metadata={
                "repository_id": repo.id,
                "paths": paths,
                "kind": "context_resolver",
                "evidence_origin": "live_repository_retrieval",
                "_content": str(getattr(resolved, "packet", "") or ""),
            },
        )]

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out: list[ContextEvidence] = []
        remaining = char_budget
        for item in candidates:
            if item.metadata.get("kind") == "context_resolver":
                content = str(item.metadata.get("_content") or "")
            elif item.metadata.get("kind") == "repository_intelligence":
                content = (
                    "Bounded relevant repository hits (not full repositories): "
                    + item.snippet
                )
            else:
                content = (
                    "CLIMATE connected repositories (registry/config, not repository contents): "
                    + item.snippet
                )
            content = content[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
            if remaining <= 0:
                break
        return out


class NotebookContextSource(_BaseSource):
    def __init__(self, store: Any, *, tasks: bool) -> None:
        self.store = store
        self.tasks = tasks
        self.id = "tasks" if tasks else "notebook_notes"
        self.type = "task" if tasks else "note"

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if self.store is None:
            return {"available": False, "detail": "Notebook store is not configured"}
        self.store.status_counts(scope=request.workspace)
        return {"available": True, "detail": "Notebook store available"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        rows = self.store.search(
            scope=request.workspace,
            repository_id=request.repository_id if request.scope == REPOSITORY else "",
            limit=min(200, max(limit * 8, 40)),
        )
        out = []
        for row in rows:
            is_task = str(row.get("note_type") or "").lower() == "task"
            if is_task != self.tasks:
                continue
            title = _text(row.get("title"), 300)
            snippet = _text(row.get("body_md"), 1_000)
            out.append(ContextCandidate(
                self.id,
                f"note:{row.get('id')}",
                title or ("Task" if self.tasks else "Note"),
                snippet,
                score=1.0,
                metadata={
                    "note_id": str(row.get("id") or ""),
                    "status": str(row.get("status") or ""),
                    "priority": str(row.get("priority") or ""),
                    "updated_at": str(row.get("updated_at") or ""),
                    "repository_ids": [
                        str(repo.get("repository_id") or "")
                        for repo in list(row.get("repositories") or []) if isinstance(repo, dict)
                    ][:8],
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out = []
        remaining = char_budget
        for item in candidates:
            meta = item.metadata
            content = "\n".join(filter(None, [
                f"Status: {meta.get('status')}; priority: {meta.get('priority')}",
                item.snippet,
            ]))[:remaining]
            if content:
                out.append(self._evidence(item, content))
                remaining -= len(content)
            if remaining <= 0:
                break
        return out


class SqlWorkspaceContextSource(_BaseSource):
    id = "sql_workspace"
    type = "sql_metadata_history"

    def __init__(self, store: Any) -> None:
        self.store = store

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        if self.store is None:
            return {"available": False, "detail": "SQL Workspace store is not configured"}
        self.store.list_folders()
        return {"available": True, "detail": "SQL Workspace metadata/history available"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        queries = list(self.store.list_queries(limit=min(200, max(limit * 8, 40))) or [])
        if request.scope == REPOSITORY:
            queries = [q for q in queries if str(q.get("repository_id") or "") == request.repository_id]
        query_ids = {str(q.get("id") or "") for q in queries}
        runs = list(self.store.list_runs(limit=min(200, max(limit * 8, 40))) or [])
        if request.scope == REPOSITORY:
            runs = [run for run in runs if str(run.get("query_id") or "") in query_ids]
        by_query: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            by_query.setdefault(str(run.get("query_id") or ""), []).append(run)
        out = []
        for query in queries:
            query_id = str(query.get("id") or "")
            history = by_query.get(query_id, [])[:5]
            snippet = " ".join(filter(None, [
                _text(query.get("description"), 500),
                "tags " + " ".join(str(tag) for tag in list(query.get("tags") or [])),
                _text(query.get("sql_text"), 500),
                " ".join(f"{run.get('status')} {run.get('environment')}" for run in history),
            ]))
            out.append(ContextCandidate(
                self.id,
                f"sql-query:{query_id}",
                _text(query.get("title"), 300) or "Saved SQL query",
                snippet,
                score=1.0,
                metadata={
                    "query_id": query_id,
                    "repository_id": str(query.get("repository_id") or ""),
                    "connection_id": str(query.get("connection_id") or ""),
                    "updated_at": str(query.get("updated_at") or ""),
                    "history": history,
                    "sql_preview": _text(query.get("sql_text"), 320),
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out = []
        remaining = char_budget
        for item in candidates:
            meta = item.metadata
            history = list(meta.get("history") or [])[:5]
            lines = [
                f"Repository: {meta.get('repository_id') or '(none)'}; connection: {meta.get('connection_id') or '(none)'}",
                f"SQL preview: {meta.get('sql_preview') or '(empty)'}",
            ]
            for run in history:
                lines.append(
                    "Run " + _text(
                        f"{run.get('id')} status={run.get('status')} environment={run.get('environment')} "
                        f"rows={run.get('row_count')} duration_ms={run.get('duration_ms')} created_at={run.get('created_at')}",
                        500,
                    )
                )
            content = "\n".join(lines)[:remaining]
            if content:
                public_meta = {key: value for key, value in meta.items() if key != "history"}
                out.append(self._evidence(item, content, **public_meta))
                remaining -= len(content)
            if remaining <= 0:
                break
        return out


class RepositoryActivityContextSource(_BaseSource):
    id = "repository_activity"
    type = "activity"

    def __init__(self, registry: Registry, repository_workspace: Any) -> None:
        self.registry = registry
        self.repository_workspace = repository_workspace

    def _repos(self, request: ContextRequest) -> list[Repository]:
        rows = [
            repo for repo in self.registry.enabled_repositories()
            if repo.type == "command"
            and RepositoriesContextSource._workspace(repo) == request.workspace
        ]
        if request.scope == REPOSITORY:
            rows = [repo for repo in rows if repo.id == request.repository_id]
        return rows[:8]

    def availability(self, request: ContextRequest) -> dict[str, Any]:
        rows = self._repos(request)
        return {"available": bool(rows), "detail": f"{len(rows)} repository workspace(s)"}

    def search(self, request: ContextRequest, *, limit: int) -> list[ContextCandidate]:
        activity_terms = {
            "activity", "branch", "changed", "changes", "dirty", "git", "modified",
            "recent", "run", "running", "status", "untracked",
        }
        if not (_tokens(request.query) & activity_terms):
            return []
        out = []
        for repo in self._repos(request):
            availability = self.repository_workspace.availability(repo) or {}
            if not availability.get("available"):
                continue
            status = self.repository_workspace.changes(repo) or {}
            runs = list(self.repository_workspace.list_runs(repo) or [])[:5]
            changed = [
                str(row.get("path") or "") for row in list(status.get("files") or [])
                if isinstance(row, dict) and row.get("path")
            ][:20]
            snippet = _text(
                f"branch {status.get('branch')} {status.get('detail')} changed {' '.join(changed)} "
                + " ".join(
                    f"{run.get('status')} {run.get('started_at')}" for run in runs if isinstance(run, dict)
                ),
                1_200,
            )
            out.append(ContextCandidate(
                self.id,
                f"repository-activity:{repo.id}",
                f"{repo.name} activity",
                snippet,
                score=1.0,
                metadata={
                    "repository_id": repo.id,
                    "branch": str(status.get("branch") or ""),
                    "clean": bool(status.get("clean", True)),
                    "changed_paths": changed,
                    "runs": runs,
                },
            ))
        return _rank(request.query, out, limit)

    def retrieve(
        self, request: ContextRequest, candidates: list[ContextCandidate], *, char_budget: int
    ) -> list[ContextEvidence]:
        out = []
        remaining = char_budget
        for item in candidates:
            meta = item.metadata
            lines = [
                f"Branch: {meta.get('branch') or '(unknown)'}; clean: {bool(meta.get('clean'))}",
                "Changed paths: " + (", ".join(list(meta.get("changed_paths") or [])[:20]) or "none"),
            ]
            for run in list(meta.get("runs") or [])[:5]:
                if isinstance(run, dict):
                    lines.append(_text(
                        f"Run {run.get('id')}: status={run.get('status')} started_at={run.get('started_at')}",
                        400,
                    ))
            content = "\n".join(lines)[:remaining]
            if content:
                public_meta = {key: value for key, value in meta.items() if key != "runs"}
                out.append(self._evidence(item, content, **public_meta))
                remaining -= len(content)
            if remaining <= 0:
                break
        return out


def build_default_context_resolver(
    *,
    registry: Registry,
    repository_workspace: Any,
    notebook_store: Any = None,
    sql_workspace_store: Any = None,
    intelligence_loader: Callable[[], Any | None],
    repobrain_loader: Callable[[], Any | None] = lambda: None,
    context_loader: Callable[..., Any] = resolve_climate_context,
    email_service: Any = None,
    calendar_service: Any = None,
    drive_service: Any = None,
    dhis2_client: Any = None,
    uid_index: Any = None,
    enrichment_store: Any = None,
    dhis2_reports: Any = None,
    job_store: Any = None,
    audit_store: Any = None,
    dhis2_instance: str = "",
) -> ClimateContextResolver:
    from hub.climate.dhis2_sources import (
        Dhis2EnrichmentContextSource,
        Dhis2EnvironmentContextSource,
        Dhis2ExplorerContextSource,
        Dhis2OperationsContextSource,
        Dhis2ReportsContextSource,
        Dhis2UidIndexContextSource,
    )
    from hub.climate.external_sources import (
        CalendarContextSource,
        DriveContextSource,
        GmailContextSource,
    )

    sources = ClimateContextRegistry()
    sources.register(RepoBrainContextSource(registry, repobrain_loader))
    sources.register(CrossRepoBrainContextSource(registry, repobrain_loader))
    sources.register(RepositoriesContextSource(
        registry, repository_workspace, intelligence_loader, context_loader,
        repobrain_loader=repobrain_loader,
    ))
    sources.register(NotebookContextSource(notebook_store, tasks=True))
    sources.register(NotebookContextSource(notebook_store, tasks=False))
    sources.register(SqlWorkspaceContextSource(sql_workspace_store))
    sources.register(RepositoryActivityContextSource(registry, repository_workspace))
    sources.register(GmailContextSource(email_service))
    sources.register(DriveContextSource(drive_service))
    sources.register(CalendarContextSource(calendar_service))
    sources.register(Dhis2EnvironmentContextSource(dhis2_client, instance=dhis2_instance))
    sources.register(Dhis2UidIndexContextSource(uid_index))
    sources.register(Dhis2EnrichmentContextSource(enrichment_store))
    sources.register(Dhis2ExplorerContextSource(dhis2_client))
    sources.register(Dhis2ReportsContextSource(dhis2_reports))
    sources.register(Dhis2OperationsContextSource(job_store, audit_store))
    return ClimateContextResolver(sources)
