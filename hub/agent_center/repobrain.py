"""Versioned, deterministic repository orientation built on Repository Intelligence.

RepoBrain stores bounded high-level knowledge.  It never replaces live repository
retrieval and never writes to a connected repository.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hub.agent_center.context_builder import resolve_repo_path
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.repository_intelligence import (
    STATUS_CURRENT,
    STATUS_NOT_LEARNED,
    RepositoryIntelligenceService,
)
from hub.agent_center.secrets import is_secret_path
from hub.registry.models import Registry, Repository
from hub.repository_workspace.security import is_supported_text_path, should_skip_dir


SNAPSHOT_SCHEMA_VERSION = 1
CROSS_SNAPSHOT_SCHEMA_VERSION = 1
STATUS_CURRENT_SNAPSHOT = "current"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"

_TOKENS = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{2,}")
_JS_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:class|function|interface|type)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_JS_IMPORT = re.compile(
    r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_ENTRY_NAMES = frozenset({
    "app.py", "main.py", "manage.py", "wsgi.py", "asgi.py", "cli.py",
    "server.py", "index.js", "index.ts", "server.js", "server.ts",
})
_BUSINESS_MARKERS = (
    "business", "calculation", "compliance", "convergence", "derive", "domain",
    "eligibility", "logic", "policy", "rule", "score", "service", "workflow",
)
_DATA_MARKERS = (
    "api", "client", "connection", "data", "db", "event", "export", "import",
    "model", "query", "repository", "route", "schema", "sql", "store", "sync",
)
_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]{10}(?![A-Za-z0-9])")
_CONFIG_RE = re.compile(
    r"(?:os\.environ(?:\.get)?\(|getenv\(|process\.env\.|\$\{)[\"']?([A-Z][A-Z0-9_]{3,})"
)
_REPORT_MARKERS = frozenset({"report", "reporting", "template", "dashboard", "indicator", "query"})
_PROCESS_MARKERS = frozenset({"process", "processing", "transform", "derive", "calculation", "workflow", "logic"})
_PRODUCER_MARKERS = frozenset({"export", "output", "produce", "generate", "transform", "derive", "write"})
_CONSUMER_MARKERS = frozenset({"consume", "import", "query", "read", "report", "template", "dashboard"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return default


def _as_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RepoBrainSettings:
    max_files: int = 160
    max_file_chars: int = 24_000
    max_symbols: int = 300
    max_relationships: int = 400
    max_source_references: int = 240
    max_snapshot_chars: int = 240_000
    max_context_chars: int = 6_000
    max_ranked_repositories: int = 8
    max_cross_relationships: int = 500
    max_cross_source_references: int = 300
    max_cross_context_chars: int = 6_000
    max_cross_features_per_file: int = 80
    git_timeout_seconds: int = 20


def load_repobrain_settings() -> RepoBrainSettings:
    return RepoBrainSettings(
        max_files=_as_int("REPOBRAIN_MAX_FILES", 160, minimum=10, maximum=800),
        max_file_chars=_as_int("REPOBRAIN_MAX_FILE_CHARS", 24_000, minimum=2_000, maximum=100_000),
        max_symbols=_as_int("REPOBRAIN_MAX_SYMBOLS", 300, minimum=20, maximum=2_000),
        max_relationships=_as_int("REPOBRAIN_MAX_RELATIONSHIPS", 400, minimum=20, maximum=3_000),
        max_source_references=_as_int("REPOBRAIN_MAX_SOURCE_REFERENCES", 240, minimum=20, maximum=1_000),
        max_snapshot_chars=_as_int("REPOBRAIN_MAX_SNAPSHOT_CHARS", 240_000, minimum=20_000, maximum=1_000_000),
        max_context_chars=_as_int("REPOBRAIN_MAX_CONTEXT_CHARS", 6_000, minimum=1_000, maximum=20_000),
        max_ranked_repositories=_as_int("REPOBRAIN_MAX_RANKED_REPOSITORIES", 8, minimum=1, maximum=50),
        max_cross_relationships=_as_int("REPOBRAIN_MAX_CROSS_RELATIONSHIPS", 500, minimum=20, maximum=3_000),
        max_cross_source_references=_as_int("REPOBRAIN_MAX_CROSS_SOURCE_REFERENCES", 300, minimum=20, maximum=2_000),
        max_cross_context_chars=_as_int("REPOBRAIN_MAX_CROSS_CONTEXT_CHARS", 6_000, minimum=1_000, maximum=20_000),
        max_cross_features_per_file=_as_int("REPOBRAIN_MAX_CROSS_FEATURES_PER_FILE", 80, minimum=10, maximum=300),
        git_timeout_seconds=_as_int("REPOBRAIN_GIT_TIMEOUT_SECONDS", 20, minimum=2, maximum=120),
    )


class RepoBrainService:
    def __init__(
        self,
        db: AgentCenterDb,
        registry: Registry,
        repository_intelligence: RepositoryIntelligenceService,
        *,
        settings: RepoBrainSettings | None = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.repository_intelligence = repository_intelligence
        self.settings = settings or load_repobrain_settings()

    def _repository(self, repository_id: str) -> tuple[Repository, Path]:
        repo = self.registry.get(repository_id)
        root = resolve_repo_path(repo) if repo is not None else None
        if repo is None or not repo.enabled:
            raise ValueError(f"Unknown or disabled repository: {repository_id}")
        if repo.type != "command" or root is None:
            raise ValueError(f"Repository '{repository_id}' has no accessible local checkout")
        return repo, root

    def _git(self, root: Path, *args: str) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), *args],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.git_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, type(exc).__name__
        return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()

    def _state(self, root: Path, *, prior_commit: str = "") -> dict[str, Any]:
        ok, head_raw = self._git(root, "rev-parse", "HEAD")
        head = head_raw.splitlines()[0].strip() if ok and head_raw else ""
        ok, ref_raw = self._git(root, "rev-parse", "--abbrev-ref", "HEAD")
        git_ref = ref_raw.splitlines()[0].strip() if ok and ref_raw else ""
        changed: list[str] = []
        if prior_commit and head and prior_commit != head:
            ok, diff = self._git(root, "diff", "--name-only", prior_commit, head)
            if ok:
                changed.extend(
                    rel for line in diff.splitlines()
                    if (rel := line.strip().replace("\\", "/")) and not self._excluded(rel)
                )
        ok, porcelain = self._git(root, "status", "--porcelain=v1", "--untracked-files=all")
        dirty_rows: list[str] = []
        if ok:
            for line in porcelain.splitlines():
                rel = line[3:].strip() if len(line) > 3 else ""
                if " -> " in rel:
                    rel = rel.split(" -> ", 1)[-1]
                rel = rel.strip('"').replace("\\", "/")
                if rel and not self._excluded(rel):
                    changed.append(rel)
                    dirty_rows.append(f"{line[:2]} {rel}")
        changed = sorted(dict.fromkeys(changed))[: self.settings.max_files]
        hash_parts = [head, git_ref]
        for row in dirty_rows[: self.settings.max_files]:
            rel = row[3:].strip()
            path = self._safe_path(root, rel)
            digest = "deleted"
            if path is not None:
                try:
                    digest = hashlib.sha256(path.read_bytes()[: self.settings.max_file_chars]).hexdigest()
                except OSError:
                    digest = "unreadable"
            hash_parts.append(f"{row}:{digest}")
        return {
            "git_commit": head,
            "git_ref": git_ref,
            "changed_files": changed,
            "state_token": hashlib.sha256("\n".join(hash_parts).encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _excluded(rel: str) -> bool:
        return any(should_skip_dir(part) or part.lower() in {".git", ".hg", ".svn"} for part in Path(rel).parts)

    def _safe_path(self, root: Path, rel: str) -> Path | None:
        clean = str(rel or "").replace("\\", "/").strip().lstrip("/")
        if not clean or self._excluded(clean):
            return None
        try:
            path = (root / clean).resolve()
            path.relative_to(root.resolve())
        except (OSError, ValueError):
            return None
        if not path.is_file() or is_secret_path(path, repo_root=root) or not is_supported_text_path(path):
            return None
        try:
            if path.stat().st_size > self.settings.max_file_chars * 8:
                return None
        except OSError:
            return None
        return path

    def latest(self, repository_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM repobrain_snapshots
                WHERE repository_id=? ORDER BY version DESC LIMIT 1
                """,
                (repository_id,),
            ).fetchone()
        return self._public(dict(row)) if row else None

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        snapshot = _loads(row.get("snapshot_json"), {})
        return {
            "id": str(row.get("id") or ""),
            "repository_id": str(row.get("repository_id") or ""),
            "version": int(row.get("version") or 0),
            "repository_name": str(row.get("repository_name") or ""),
            "root_path": str(row.get("root_path") or ""),
            "git_commit": str(row.get("git_commit") or ""),
            "git_ref": str(row.get("git_ref") or ""),
            "state_token": str(row.get("state_token") or ""),
            "generated_at": str(row.get("generated_at") or ""),
            "build_mode": str(row.get("build_mode") or ""),
            "changed_files": _loads(row.get("changed_files_json"), []),
            "reused_snapshot_id": str(row.get("reused_snapshot_id") or ""),
            "source_references": _loads(row.get("source_references_json"), []),
            "snapshot": snapshot,
            "status": STATUS_CURRENT_SNAPSHOT,
            "stale": False,
            "reused": False,
        }

    def get_snapshot(self, repository_id: str, *, refresh: bool = False) -> dict[str, Any] | None:
        latest = self.latest(repository_id)
        if latest is None:
            return self.build(repository_id) if refresh else None
        _repo, root = self._repository(repository_id)
        state = self._state(root, prior_commit=latest.get("git_commit") or "")
        stale = state["state_token"] != latest.get("state_token")
        if stale and refresh:
            try:
                return self.build(repository_id, changed_files=state["changed_files"])
            except Exception as exc:  # stale orientation remains usable, never exact authority
                latest["refresh_error"] = type(exc).__name__
        latest["status"] = STATUS_STALE if stale else STATUS_CURRENT_SNAPSHOT
        latest["stale"] = stale
        latest["current_git_commit"] = state["git_commit"]
        latest["current_git_ref"] = state["git_ref"]
        latest["pending_changed_files"] = state["changed_files"] if stale else []
        return latest

    def build(
        self,
        repository_id: str,
        *,
        full_rebuild: bool = False,
        changed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        repo, root = self._repository(repository_id)
        prior = self.latest(repository_id)
        before = self._state(root, prior_commit=(prior or {}).get("git_commit") or "")
        changed = sorted(dict.fromkeys(changed_files or before["changed_files"]))[: self.settings.max_files]
        if prior and not full_rebuild and before["state_token"] == prior.get("state_token"):
            reused = dict(prior)
            reused["reused"] = True
            reused["refresh"] = {
                "mode": "reuse",
                "changed_files": [],
                "files_analyzed": 0,
                "files_reused": len((prior.get("snapshot") or {}).get("file_analysis") or {}),
            }
            return reused

        ri_state = self.repository_intelligence.get_status(repository_id)
        initial = not bool(ri_state.get("last_scan")) or ri_state.get("status") == STATUS_NOT_LEARNED
        incremental = bool(prior and not full_rebuild and not initial)
        if full_rebuild or initial:
            self.repository_intelligence.scan(
                repository_id,
                incremental=False,
                trigger="repobrain_full_rebuild" if full_rebuild else "repobrain_initial_build",
            )
            incremental = False
        elif changed:
            self.repository_intelligence.scan(
                repository_id,
                incremental=True,
                changed_files=changed,
                trigger="repobrain_incremental_refresh",
            )

        knowledge = self.repository_intelligence.knowledge(repository_id, limit=500)
        entries = list(knowledge.get("entries") or [])
        selected = self._select_files(entries)
        prior_analysis = dict(((prior or {}).get("snapshot") or {}).get("file_analysis") or {})
        if incremental:
            analysis = {
                path: value for path, value in prior_analysis.items()
                if path not in set(changed)
            }
            targets = [path for path in changed if path in {row["path"] for row in selected}]
            # Changed source files remain relevant even when they were not in the prior top set.
            targets.extend(path for path in changed if path not in targets)
        else:
            analysis = {}
            targets = [str(row.get("path") or "") for row in selected]
        analyzed = 0
        for rel in list(dict.fromkeys(targets))[: self.settings.max_files]:
            path = self._safe_path(root, rel)
            if path is None:
                analysis.pop(rel, None)
                continue
            item = self._analyze_file(root, path, rel)
            if item:
                analysis[rel] = item
                analyzed += 1
        # Keep the persisted analysis bounded, prioritizing files still in the RI index.
        known = {str(row.get("path") or "") for row in entries}
        analysis = {
            path: value for path, value in analysis.items()
            if path in known
        }
        ordered_paths = [str(row.get("path") or "") for row in selected]
        ordered_paths.extend(sorted(path for path in analysis if path not in ordered_paths))
        analysis = {path: analysis[path] for path in ordered_paths if path in analysis}
        analysis = dict(list(analysis.items())[: self.settings.max_files])

        after = self._state(root)
        snapshot = self._compose_snapshot(
            repo=repo,
            root=root,
            state=after,
            entries=entries,
            analysis=analysis,
            prior=prior,
            changed_files=changed,
            build_mode="full" if not incremental else "incremental",
            files_analyzed=analyzed,
        )
        snapshot = self._bound_snapshot(snapshot)
        version = int((prior or {}).get("version") or 0) + 1
        snapshot_id = uuid.uuid4().hex
        generated_at = str(snapshot.get("generated_at") or _now())
        refs = list(snapshot.get("source_references") or [])[: self.settings.max_source_references]
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO repobrain_snapshots(
                    id,repository_id,version,repository_name,root_path,git_commit,git_ref,
                    state_token,generated_at,build_mode,changed_files_json,reused_snapshot_id,
                    snapshot_json,source_references_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id, repo.id, version, repo.name, str(root), after["git_commit"],
                    after["git_ref"], after["state_token"], generated_at,
                    "full" if not incremental else "incremental", _json(changed),
                    str((prior or {}).get("id") or "") if incremental else "",
                    _json(snapshot), _json(refs), generated_at,
                ),
            )
        result = self.latest(repository_id) or {}
        result["refresh"] = dict(snapshot.get("refresh") or {})
        return result

    def full_rebuild(self, repository_id: str) -> dict[str, Any]:
        return self.build(repository_id, full_rebuild=True)

    def _select_files(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        category_rank = {
            "guidance": 0, "architecture": 1, "introduction": 2,
            "configuration": 3, "integrations": 4, "data_sources": 5,
            "business_logic": 6, "tools": 7, "terminology": 8,
        }
        def rank(row: dict[str, Any]) -> tuple[int, int, str]:
            path = str(row.get("path") or "")
            name = Path(path).name.lower()
            entry = 0 if name in _ENTRY_NAMES or path.lower().startswith(("src/", "app/", "hub/")) else 1
            return (category_rank.get(str(row.get("category") or ""), 9), entry, path.lower())
        rows = [row for row in entries if not self._excluded(str(row.get("path") or ""))]
        return sorted(rows, key=rank)[: self.settings.max_files]

    def _analyze_file(self, root: Path, path: Path, rel: str) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[: self.settings.max_file_chars]
        except OSError:
            return {}
        symbols: list[dict[str, Any]] = []
        dependencies: list[str] = []
        entry_point = Path(rel).name.lower() in _ENTRY_NAMES or "if __name__" in text
        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append({
                            "name": node.name,
                            "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                            "line": int(getattr(node, "lineno", 0) or 0),
                        })
                    elif isinstance(node, ast.Import):
                        dependencies.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        dependencies.append(node.module)
        else:
            symbols.extend({"name": match.group(1), "kind": "symbol", "line": text[:match.start()].count("\n") + 1} for match in _JS_SYMBOL.finditer(text))
            dependencies.extend(match.group(1) for match in _JS_IMPORT.finditer(text))
        low = f"{rel} {text[:4_000]}".lower()
        topics = sorted({marker for marker in _BUSINESS_MARKERS if marker in low})
        data_markers = sorted({marker for marker in _DATA_MARKERS if marker in low})
        identifiers = sorted({
            value for value in _IDENTIFIER_RE.findall(text)
            if any(char.isdigit() for char in value)
        })
        config_references = sorted(dict.fromkeys(_CONFIG_RE.findall(text)))
        return {
            "path": rel,
            "module": str(Path(rel).parent).replace("\\", "/") or ".",
            "entry_point": entry_point,
            "symbols": symbols[:80],
            "dependencies": sorted(dict.fromkeys(dependencies))[:80],
            "business_topics": topics,
            "data_markers": data_markers,
            "identifiers": identifiers[: self.settings.max_cross_features_per_file],
            "config_references": config_references[: self.settings.max_cross_features_per_file],
            "is_test": self._is_test_path(rel),
            "content_hash": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        }

    @staticmethod
    def _is_test_path(rel: str) -> bool:
        low = rel.lower().replace("\\", "/")
        name = Path(low).name
        return low.startswith(("tests/", "test/")) or "/tests/" in low or name.startswith("test_") or name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))

    def _compose_snapshot(
        self,
        *,
        repo: Repository,
        root: Path,
        state: dict[str, Any],
        entries: list[dict[str, Any]],
        analysis: dict[str, dict[str, Any]],
        prior: dict[str, Any] | None,
        changed_files: list[str],
        build_mode: str,
        files_analyzed: int,
    ) -> dict[str, Any]:
        modules: dict[str, list[str]] = {}
        important_files: list[dict[str, Any]] = []
        entry_points: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        dependencies: list[dict[str, str]] = []
        relationships: list[dict[str, str]] = []
        business: dict[str, set[str]] = {}
        data_flows: list[dict[str, str]] = []
        tests: list[dict[str, Any]] = []
        entry_by_path = {str(row.get("path") or ""): row for row in entries}
        for path, item in analysis.items():
            module = str(item.get("module") or ".")
            modules.setdefault(module, []).append(path)
            source = entry_by_path.get(path) or {}
            important_files.append({
                "path": path,
                "category": str(source.get("category") or "important_paths"),
                "summary": str(source.get("summary") or "")[:360],
            })
            if item.get("entry_point"):
                entry_points.append({"path": path, "reason": "runtime or conventional entry point"})
            for symbol in list(item.get("symbols") or []):
                symbols.append({"path": path, **symbol})
            for dependency in list(item.get("dependencies") or []):
                edge = {"from": path, "to": str(dependency), "type": "imports"}
                dependencies.append(edge)
                if any(marker in str(dependency).lower() for marker in _DATA_MARKERS):
                    data_flows.append({**edge, "type": "data_dependency"})
                if any(marker in path.lower() for marker in ("route", "controller", "api")) and any(marker in str(dependency).lower() for marker in ("service", "store", "repository")):
                    relationships.append({**edge, "type": "delegates_to"})
            for topic in list(item.get("business_topics") or []):
                business.setdefault(str(topic), set()).add(path)
            if item.get("is_test"):
                tests.append({
                    "path": path,
                    "symbols": [str(row.get("name") or "") for row in list(item.get("symbols") or [])[:20]],
                })
        module_rows = [
            {"module": module, "files": sorted(paths)[:20], "file_count": len(paths)}
            for module, paths in sorted(modules.items())
        ]
        recent = self._recent_changes(root, since=(prior or {}).get("git_commit") or "")
        refs = sorted(analysis)[: self.settings.max_source_references]
        summary_parts = [repo.description.strip()] if repo.description.strip() else []
        if module_rows:
            summary_parts.append(f"{len(module_rows)} modules: " + ", ".join(row["module"] for row in module_rows[:12]))
        if business:
            summary_parts.append("Business logic: " + ", ".join(sorted(business)[:12]))
        if entry_points:
            summary_parts.append("Entry points: " + ", ".join(row["path"] for row in entry_points[:10]))
        confidence = "high" if len(refs) >= 12 and not changed_files[self.settings.max_files:] else "medium" if refs else "low"
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "repository_id": repo.id,
            "repository_name": repo.name,
            "repository_path": str(root),
            "git_commit": state["git_commit"],
            "git_ref": state["git_ref"],
            "generated_at": _now(),
            "summary": " | ".join(summary_parts)[:2_400] or f"{repo.name} repository",
            "architecture": {
                "module_count": len(module_rows),
                "entry_point_count": len(entry_points),
                "relationship_count": min(len(relationships), self.settings.max_relationships),
            },
            "modules": module_rows[:120],
            "important_files": important_files[: self.settings.max_files],
            "entry_points": entry_points[:80],
            "symbols": symbols[: self.settings.max_symbols],
            "dependencies": dependencies[: self.settings.max_relationships],
            "relationships": relationships[: self.settings.max_relationships],
            "business_logic_topics": [
                {"topic": topic, "paths": sorted(paths)[:20]}
                for topic, paths in sorted(business.items())
            ][:80],
            "data_flow_relationships": data_flows[: self.settings.max_relationships],
            "test_map": tests[:160],
            "recent_change_summary": recent,
            "confidence": {
                "level": confidence,
                "source_count": len(refs),
                "bounded": True,
                "live_verification_required": True,
            },
            "source_references": refs,
            "refresh": {
                "mode": build_mode,
                "changed_files": changed_files[: self.settings.max_files],
                "files_analyzed": files_analyzed,
                "files_reused": max(0, len(analysis) - files_analyzed),
                "prior_snapshot_id": str((prior or {}).get("id") or ""),
            },
            # Internal reusable per-file analysis; never emitted in prompt packets.
            "file_analysis": analysis,
        }

    def _recent_changes(self, root: Path, *, since: str = "") -> list[dict[str, Any]]:
        args = ["log", "-8", "--date=iso-strict", "--pretty=format:@@%H|%ad|%s", "--name-only"]
        if since:
            args.append(f"{since}..HEAD")
        ok, value = self._git(root, *args)
        if not ok or not value:
            return []
        rows: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in value.splitlines():
            if line.startswith("@@"):
                if current:
                    rows.append(current)
                parts = line[2:].split("|", 2)
                current = {
                    "commit": parts[0] if parts else "",
                    "date": parts[1] if len(parts) > 1 else "",
                    "summary": parts[2][:300] if len(parts) > 2 else "",
                    "files": [],
                }
            elif current is not None and line.strip():
                rel = line.strip().replace("\\", "/")
                if not self._excluded(rel):
                    current["files"].append(rel)
        if current:
            rows.append(current)
        return rows[:8]

    def _bound_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if len(_json(snapshot)) <= self.settings.max_snapshot_chars:
            return snapshot
        bounded = dict(snapshot)
        bounded["symbols"] = list(bounded.get("symbols") or [])[: max(20, self.settings.max_symbols // 2)]
        bounded["dependencies"] = list(bounded.get("dependencies") or [])[: max(20, self.settings.max_relationships // 2)]
        bounded["relationships"] = list(bounded.get("relationships") or [])[: max(20, self.settings.max_relationships // 2)]
        bounded["data_flow_relationships"] = list(bounded.get("data_flow_relationships") or [])[: max(20, self.settings.max_relationships // 2)]
        bounded["important_files"] = list(bounded.get("important_files") or [])[: max(20, self.settings.max_files // 2)]
        bounded["file_analysis"] = dict(list((bounded.get("file_analysis") or {}).items())[: max(20, self.settings.max_files // 2)])
        bounded.setdefault("confidence", {})["truncated"] = True
        # Prefer dropping reusable detail, then progressively reduce every list.
        # The persisted envelope is a hard limit even for unusually large symbols.
        if len(_json(bounded)) > self.settings.max_snapshot_chars:
            bounded["file_analysis"] = {}
        reducible = (
            "symbols", "dependencies", "relationships", "data_flow_relationships",
            "important_files", "modules", "entry_points", "business_logic_topics",
            "test_map", "recent_change_summary", "source_references",
        )
        while len(_json(bounded)) > self.settings.max_snapshot_chars:
            largest = max(
                reducible,
                key=lambda key: len(_json(bounded.get(key) or [])),
            )
            rows = list(bounded.get(largest) or [])
            if not rows:
                break
            bounded[largest] = rows[: len(rows) // 2]
        if len(_json(bounded)) > self.settings.max_snapshot_chars:
            bounded["summary"] = str(bounded.get("summary") or "")[:400]
        return bounded

    def context(self, repository_id: str, query: str, *, refresh: bool = True) -> dict[str, Any] | None:
        row = self.get_snapshot(repository_id, refresh=refresh)
        if row is None:
            return None
        snapshot = dict(row.get("snapshot") or {})
        terms = {token.lower() for token in _TOKENS.findall(query or "")}
        sections: list[tuple[int, str, str]] = []
        sections.append((self._text_score(terms, snapshot.get("summary")), "Summary", str(snapshot.get("summary") or "")))
        for key, label in (
            ("modules", "Architecture / modules"),
            ("important_files", "Important files"),
            ("entry_points", "Entry points"),
            ("symbols", "Major symbols"),
            ("dependencies", "Dependencies"),
            ("business_logic_topics", "Business logic"),
            ("data_flow_relationships", "Data flow"),
            ("test_map", "Tests"),
            ("recent_change_summary", "Recent changes"),
        ):
            value = snapshot.get(key) or []
            text = _json(value)
            sections.append((self._text_score(terms, text), label, text))
        sections.sort(key=lambda item: (-item[0], item[1]))
        lines = [
            f"RepoBrain snapshot v{row.get('version')} for {row.get('repository_name')} ({repository_id}).",
            f"Snapshot commit/ref: {row.get('git_commit') or '(none)'} / {row.get('git_ref') or '(none)'}.",
            f"Freshness: {row.get('status')}; live retrieval must verify exact current evidence.",
        ]
        for _score, label, text in sections:
            if not text or text == "[]":
                continue
            lines.extend([f"{label}:", text[:1_400]])
            if len("\n".join(lines)) >= self.settings.max_context_chars:
                break
        content = "\n".join(lines)[: self.settings.max_context_chars]
        return {
            "repository_id": repository_id,
            "snapshot_id": row.get("id"),
            "version": row.get("version"),
            "git_commit": row.get("git_commit"),
            "git_ref": row.get("git_ref"),
            "generated_at": row.get("generated_at"),
            "freshness": row.get("status"),
            "stale": bool(row.get("stale")),
            "summary": str(snapshot.get("summary") or "")[:1_000],
            "content": content,
            "confidence": dict(snapshot.get("confidence") or {}),
            "source_references": list(snapshot.get("source_references") or [])[:40],
        }

    @staticmethod
    def _text_score(terms: set[str], value: Any) -> int:
        hay = str(value or "").lower()
        return sum(1 for term in terms if term in hay)

    def rank_repositories(self, repository_ids: Iterable[str], query: str) -> list[dict[str, Any]]:
        terms = {token.lower() for token in _TOKENS.findall(query or "")}
        rows: list[dict[str, Any]] = []
        for repository_id in list(dict.fromkeys(str(value) for value in repository_ids if str(value)))[:50]:
            snapshot = self.get_snapshot(repository_id, refresh=False)
            if snapshot is None:
                continue
            data = dict(snapshot.get("snapshot") or {})
            searchable = " ".join([
                str(data.get("summary") or ""),
                _json(data.get("modules") or []),
                _json(data.get("business_logic_topics") or []),
                _json(data.get("important_files") or []),
            ])
            score = self._text_score(terms, searchable)
            if terms and score <= 0:
                continue
            rows.append({
                "repository_id": repository_id,
                "score": score,
                "summary": str(data.get("summary") or "")[:1_000],
                "snapshot_id": snapshot.get("id"),
                "version": snapshot.get("version"),
                "git_commit": snapshot.get("git_commit"),
                "freshness": snapshot.get("status"),
                "stale": bool(snapshot.get("stale")),
            })
        rows.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("repository_id") or "")))
        return rows[: self.settings.max_ranked_repositories]

    def list_summaries(self, repository_ids: Iterable[str], query: str = "") -> list[dict[str, Any]]:
        return self.rank_repositories(repository_ids, query)

    def history(self, repository_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """
                SELECT id,repository_id,version,repository_name,root_path,git_commit,git_ref,
                       generated_at,build_mode,changed_files_json,reused_snapshot_id,created_at
                FROM repobrain_snapshots
                WHERE repository_id=? ORDER BY version DESC LIMIT ?
                """,
                (repository_id, max(1, min(int(limit), 100))),
            ).fetchall()]
        for row in rows:
            row["changed_files"] = _loads(row.pop("changed_files_json", "[]"), [])
        return rows

    # Cross-repository intelligence extends the same snapshot engine. It uses
    # persisted Phase 1 file analysis first and never sends repositories to a provider.
    def latest_cross_snapshot(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM repobrain_cross_snapshots ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        return {
            "id": str(value.get("id") or ""),
            "version": int(value.get("version") or 0),
            "generated_at": str(value.get("generated_at") or ""),
            "state_token": str(value.get("state_token") or ""),
            "build_mode": str(value.get("build_mode") or ""),
            "affected_repositories": _loads(value.get("affected_repositories_json"), []),
            "input_snapshots": _loads(value.get("input_snapshots_json"), {}),
            "relationships": _loads(value.get("relationships_json"), []),
            "repository_index": _loads(value.get("repository_index_json"), {}),
            "source_references": _loads(value.get("source_references_json"), []),
            "reused_snapshot_id": str(value.get("reused_snapshot_id") or ""),
            "status": STATUS_CURRENT_SNAPSHOT,
            "stale": False,
            "reused": False,
        }

    def _cross_inputs(
        self,
        repository_ids: Iterable[str] | None = None,
        *,
        refresh_repositories: bool = False,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], str]:
        repositories = [
            repo for repo in self.registry.enabled_repositories()
            if repo.type == "command"
        ][:50]
        rows: dict[str, dict[str, Any]] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        state_parts: list[str] = []
        for repo in repositories:
            try:
                # Cross intelligence composes learned snapshots; it never causes
                # an initial scan of every connected repository from a chat query.
                if self.latest(repo.id) is None:
                    continue
                snapshot = self.get_snapshot(repo.id, refresh=refresh_repositories)
            except Exception:
                continue
            if snapshot is None:
                continue
            snapshots[repo.id] = snapshot
            current_commit = str(snapshot.get("current_git_commit") or snapshot.get("git_commit") or "")
            row = {
                "repository_id": repo.id,
                "repository_name": repo.name,
                "snapshot_id": str(snapshot.get("id") or ""),
                "snapshot_version": int(snapshot.get("version") or 0),
                "git_commit": str(snapshot.get("git_commit") or ""),
                "current_git_commit": current_commit,
                "stale": bool(snapshot.get("stale")),
                "generated_at": str(snapshot.get("generated_at") or ""),
            }
            rows[repo.id] = row
            state_parts.append(
                f"{repo.id}:{row['snapshot_id']}:{row['git_commit']}:{current_commit}:{int(row['stale'])}"
            )
        token = hashlib.sha256("\n".join(sorted(state_parts)).encode("utf-8")).hexdigest()
        return rows, snapshots, token

    def get_cross_snapshot(
        self,
        *,
        refresh: bool = False,
        repository_ids: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        latest = self.latest_cross_snapshot()
        inputs, _snapshots, token = self._cross_inputs(repository_ids, refresh_repositories=False)
        if latest is None:
            return self.build_cross_snapshot(repository_ids=repository_ids) if refresh and len(inputs) >= 2 else None
        stale = token != latest.get("state_token") or any(row.get("stale") for row in inputs.values())
        if stale and refresh:
            try:
                return self.build_cross_snapshot(repository_ids=repository_ids)
            except Exception as exc:
                latest["refresh_error"] = type(exc).__name__
        latest["status"] = STATUS_STALE if stale else STATUS_CURRENT_SNAPSHOT
        latest["stale"] = stale
        latest["current_inputs"] = inputs
        return latest

    def build_cross_snapshot(
        self,
        *,
        repository_ids: Iterable[str] | None = None,
        full_rebuild: bool = False,
    ) -> dict[str, Any]:
        inputs, snapshots, token = self._cross_inputs(
            repository_ids, refresh_repositories=True
        )
        if len(inputs) < 2:
            raise ValueError("Cross-repository RepoBrain requires at least two learned local repositories")
        prior = self.latest_cross_snapshot()
        if prior and not full_rebuild and token == prior.get("state_token"):
            reused = dict(prior)
            reused["reused"] = True
            reused["refresh"] = {
                "mode": "reuse", "affected_repositories": [],
                "relationships_recomputed": 0,
                "relationships_reused": len(prior.get("relationships") or []),
            }
            return reused

        prior_inputs = dict((prior or {}).get("input_snapshots") or {})
        current_ids = set(inputs)
        prior_ids = set(prior_inputs)
        affected = sorted(current_ids | prior_ids) if full_rebuild or not prior else sorted(
            repository_id for repository_id in current_ids | prior_ids
            if inputs.get(repository_id, {}).get("snapshot_id")
            != prior_inputs.get(repository_id, {}).get("snapshot_id")
        )
        retained = [] if full_rebuild or not prior else [
            relationship for relationship in list(prior.get("relationships") or [])
            if relationship.get("source_repository") not in affected
            and relationship.get("target_repository") not in affected
            and relationship.get("source_repository") in current_ids
            and relationship.get("target_repository") in current_ids
        ]
        discovered, comparisons = self._discover_cross_relationships(
            snapshots, affected_repositories=set(affected)
        )
        relationships = self._dedupe_relationships(retained + discovered)
        relationships = relationships[: self.settings.max_cross_relationships]
        index = self._cross_repository_index(inputs, snapshots, relationships)
        references = self._cross_source_references(relationships)
        generated_at = _now()
        snapshot_id = uuid.uuid4().hex
        version = int((prior or {}).get("version") or 0) + 1
        mode = "full" if full_rebuild or not prior else "incremental"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO repobrain_cross_snapshots(
                    id,version,generated_at,state_token,build_mode,
                    affected_repositories_json,input_snapshots_json,
                    relationships_json,repository_index_json,source_references_json,
                    reused_snapshot_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id, version, generated_at, token, mode, _json(affected),
                    _json(inputs), _json(relationships), _json(index), _json(references),
                    str((prior or {}).get("id") or "") if mode == "incremental" else "",
                    generated_at,
                ),
            )
        result = self.latest_cross_snapshot() or {}
        result["refresh"] = {
            "mode": mode,
            "affected_repositories": affected,
            "relationships_recomputed": len(discovered),
            "relationships_reused": len(retained),
            "candidate_comparisons": comparisons,
        }
        return result

    def full_rebuild_cross_snapshot(
        self, *, repository_ids: Iterable[str] | None = None
    ) -> dict[str, Any]:
        return self.build_cross_snapshot(repository_ids=repository_ids, full_rebuild=True)

    @staticmethod
    def _feature_token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")

    def _cross_features(self, snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for repository_id, row in snapshots.items():
            snapshot = dict(row.get("snapshot") or {})
            analysis = dict(snapshot.get("file_analysis") or {})
            for path, item in list(analysis.items())[: self.settings.max_files]:
                low = f"{repository_id} {path}".lower()
                topics = {
                    self._feature_token(value) for value in list(item.get("business_topics") or [])
                    if self._feature_token(value)
                }
                topics.update(
                    marker for marker in _BUSINESS_MARKERS if marker in low
                )
                names = {
                    self._feature_token(symbol.get("name"))
                    for symbol in list(item.get("symbols") or [])
                    if isinstance(symbol, dict) and self._feature_token(symbol.get("name"))
                }
                names.add(self._feature_token(Path(path).stem))
                features.append({
                    "repository_id": repository_id,
                    "snapshot_id": row.get("id"),
                    "snapshot_version": row.get("version"),
                    "git_commit": row.get("git_commit"),
                    "path": path,
                    "symbols": [
                        str(symbol.get("name") or "") for symbol in list(item.get("symbols") or [])[:20]
                        if isinstance(symbol, dict)
                    ],
                    "names": sorted(names),
                    "topics": sorted(topics),
                    "identifiers": list(item.get("identifiers") or [])[: self.settings.max_cross_features_per_file],
                    "configs": list(item.get("config_references") or [])[: self.settings.max_cross_features_per_file],
                    "dependencies": [
                        self._feature_token(str(value).split(".")[-1])
                        for value in list(item.get("dependencies") or [])[: self.settings.max_cross_features_per_file]
                    ],
                    "is_test": bool(item.get("is_test")),
                    "is_report": any(marker in low for marker in _REPORT_MARKERS),
                    "is_process": any(marker in low for marker in _PROCESS_MARKERS),
                    "is_producer": any(marker in low for marker in _PRODUCER_MARKERS),
                    "is_consumer": any(marker in low for marker in _CONSUMER_MARKERS),
                })
        return features

    def _discover_cross_relationships(
        self,
        snapshots: dict[str, dict[str, Any]],
        *,
        affected_repositories: set[str],
    ) -> tuple[list[dict[str, Any]], int]:
        features = self._cross_features(snapshots)
        inverted: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in features:
            for kind, values in (
                ("concept", item["topics"]), ("identifier", item["identifiers"]),
                ("config", item["configs"]), ("name", item["names"]),
            ):
                for value in values:
                    if value:
                        inverted.setdefault((kind, str(value)), []).append(item)
        relationships: list[dict[str, Any]] = []
        comparisons = 0
        seen_pairs: set[tuple[str, str, str, str, str]] = set()
        for (kind, value), rows in sorted(inverted.items()):
            by_repo: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_repo.setdefault(str(row["repository_id"]), []).append(row)
            repository_ids = sorted(by_repo)
            if len(repository_ids) < 2:
                continue
            for left_index, source_id in enumerate(repository_ids):
                for target_id in repository_ids[left_index + 1:]:
                    if affected_repositories and not ({source_id, target_id} & affected_repositories):
                        continue
                    comparisons += 1
                    candidates = [
                        self._classify_cross_relationship(kind, value, source, target)
                        for source in by_repo[source_id][:8]
                        for target in by_repo[target_id][:8]
                    ]
                    relation = max(
                        (row for row in candidates if row is not None),
                        key=lambda row: float(row.get("confidence") or 0),
                        default=None,
                    )
                    if relation is None:
                        continue
                    key = (
                        relation["source_repository"], relation["target_repository"],
                        relation["relationship_type"], relation["business_concept"],
                        f"{relation['source_files'][0]}->{relation['target_files'][0]}",
                    )
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        relationships.append(relation)

        # Dependency-to-symbol/module matching uses a name index, not repo pairs.
        names: dict[str, list[dict[str, Any]]] = {}
        for item in features:
            for name in item["names"]:
                names.setdefault(name, []).append(item)
        for source in features:
            for dependency in source["dependencies"]:
                for target in names.get(dependency, []):
                    if source["repository_id"] == target["repository_id"]:
                        continue
                    if affected_repositories and not (
                        {source["repository_id"], target["repository_id"]} & affected_repositories
                    ):
                        continue
                    comparisons += 1
                    relationships.append(self._relationship(
                        source, target, "depends_on", dependency, 0.78,
                        source_symbol=dependency,
                    ))
        return self._dedupe_relationships(relationships), comparisons

    def _classify_cross_relationship(
        self, kind: str, value: str, source: dict[str, Any], target: dict[str, Any]
    ) -> dict[str, Any] | None:
        if kind == "identifier":
            return self._relationship(source, target, "shares_identifier", value, 0.95)
        if kind == "config":
            return self._relationship(source, target, "shares_config", value, 0.9)
        if source["is_test"] != target["is_test"]:
            test, implementation = (source, target) if source["is_test"] else (target, source)
            return self._relationship(test, implementation, "test_covers", value, 0.72)
        if (
            source["is_process"] and source["is_producer"] and target["is_consumer"]
        ) or (
            target["is_process"] and target["is_producer"] and source["is_consumer"]
        ):
            transformer, consumer = (
                (source, target)
                if source["is_process"] and source["is_producer"]
                else (target, source)
            )
            return self._relationship(transformer, consumer, "transforms", value, 0.84)
        if source["is_report"] != target["is_report"] and (source["is_process"] or target["is_process"]):
            report, processing = (source, target) if source["is_report"] else (target, source)
            return self._relationship(report, processing, "reports_on", value, 0.86)
        if source["is_producer"] != target["is_producer"] and (source["is_consumer"] or target["is_consumer"]):
            producer, consumer = (source, target) if source["is_producer"] else (target, source)
            return self._relationship(producer, consumer, "produces", value, 0.8)
        if kind == "concept":
            return self._relationship(source, target, "implements", value, 0.7)
        if kind == "name":
            return self._relationship(source, target, "mirrors", value, 0.68)
        return None

    @staticmethod
    def _relationship(
        source: dict[str, Any], target: dict[str, Any], relationship_type: str,
        concept: str, confidence: float, *, source_symbol: str = "",
    ) -> dict[str, Any]:
        payload = (
            f"{source['repository_id']}|{target['repository_id']}|{relationship_type}|"
            f"{concept}|{source['path']}|{target['path']}"
        )
        return {
            "id": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24],
            "source_repository": source["repository_id"],
            "target_repository": target["repository_id"],
            "source_files": [source["path"]],
            "source_symbols": [source_symbol] if source_symbol else list(source["symbols"])[:10],
            "target_files": [target["path"]],
            "target_symbols": list(target["symbols"])[:10],
            "relationship_type": relationship_type,
            "business_concept": concept,
            "confidence": confidence,
            "source_references": [
                {"repository_id": source["repository_id"], "path": source["path"]},
                {"repository_id": target["repository_id"], "path": target["path"]},
            ],
            "snapshot_versions": {
                source["repository_id"]: {
                    "snapshot_id": source["snapshot_id"],
                    "version": source["snapshot_version"],
                    "git_commit": source["git_commit"],
                },
                target["repository_id"]: {
                    "snapshot_id": target["snapshot_id"],
                    "version": target["snapshot_version"],
                    "git_commit": target["git_commit"],
                },
            },
        }

    @staticmethod
    def _dedupe_relationships(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique = {str(row.get("id") or ""): row for row in rows if row.get("id")}
        return sorted(
            unique.values(),
            key=lambda row: (-float(row.get("confidence") or 0), str(row.get("id") or "")),
        )

    def _cross_repository_index(
        self,
        inputs: dict[str, dict[str, Any]],
        snapshots: dict[str, dict[str, Any]],
        relationships: list[dict[str, Any]],
    ) -> dict[str, Any]:
        index: dict[str, Any] = {}
        for repository_id, input_row in inputs.items():
            snapshot = dict((snapshots.get(repository_id) or {}).get("snapshot") or {})
            related = [
                row for row in relationships
                if repository_id in {row.get("source_repository"), row.get("target_repository")}
            ]
            concepts = sorted({
                str(row.get("business_concept") or "") for row in related
                if str(row.get("business_concept") or "")
            })
            peers = sorted({
                str(row.get("target_repository") if row.get("source_repository") == repository_id else row.get("source_repository"))
                for row in related
            })
            index[repository_id] = {
                **input_row,
                "summary": str(snapshot.get("summary") or "")[:1_000],
                "concepts": concepts[:100],
                "related_repositories": peers[:30],
                "relationship_count": len(related),
            }
        return index

    def _cross_source_references(self, relationships: list[dict[str, Any]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        rows: list[dict[str, str]] = []
        for relationship in relationships:
            for reference in list(relationship.get("source_references") or []):
                key = (str(reference.get("repository_id") or ""), str(reference.get("path") or ""))
                if key in seen or not all(key):
                    continue
                seen.add(key)
                rows.append({"repository_id": key[0], "path": key[1]})
                if len(rows) >= self.settings.max_cross_source_references:
                    return rows
        return rows

    def cross_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """
                SELECT id,version,generated_at,state_token,build_mode,
                       affected_repositories_json,reused_snapshot_id,created_at
                FROM repobrain_cross_snapshots ORDER BY version DESC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()]
        for row in rows:
            row["affected_repositories"] = _loads(row.pop("affected_repositories_json", "[]"), [])
        return rows

    def rank_repositories_cross(
        self, repository_ids: Iterable[str], query: str, *, refresh: bool = False
    ) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(str(value) for value in repository_ids if str(value)))[:50]
        cross = self.get_cross_snapshot(refresh=refresh, repository_ids=requested)
        base = {row["repository_id"]: row for row in self.rank_repositories(requested, query)}
        terms = {token.lower() for token in _TOKENS.findall(query or "")}
        index = dict((cross or {}).get("repository_index") or {})
        relationships = list((cross or {}).get("relationships") or [])
        rows: list[dict[str, Any]] = []
        for repository_id in requested:
            item = dict(index.get(repository_id) or {})
            related = [
                row for row in relationships
                if repository_id in {row.get("source_repository"), row.get("target_repository")}
                and self._text_score(terms, _json(row)) > 0
            ]
            cross_score = sum(self._text_score(terms, _json(row)) for row in related)
            base_row = dict(base.get(repository_id) or {})
            base_score = int(base_row.get("score") or 0)
            if terms and base_score + cross_score <= 0:
                continue
            rows.append({
                "repository_id": repository_id,
                "score": base_score + (2 * cross_score),
                "single_repository_score": base_score,
                "cross_repository_score": cross_score,
                "summary": str(item.get("summary") or base_row.get("summary") or "")[:1_000],
                "related_repositories": list(item.get("related_repositories") or [])[:20],
                "relationship_ids": [str(row.get("id") or "") for row in related[:20]],
                "cross_snapshot_id": (cross or {}).get("id"),
                "cross_snapshot_version": (cross or {}).get("version"),
                "freshness": (cross or {}).get("status", "not_learned"),
                "stale": bool((cross or {}).get("stale")),
            })
        rows.sort(key=lambda row: (-int(row["score"]), row["repository_id"]))
        return rows[: self.settings.max_ranked_repositories]

    def cross_context(
        self,
        query: str,
        *,
        repository_ids: Iterable[str] | None = None,
        anchor_repository_id: str = "",
        refresh: bool = False,
    ) -> dict[str, Any] | None:
        cross = self.get_cross_snapshot(refresh=refresh, repository_ids=repository_ids)
        if cross is None:
            return None
        terms = {token.lower() for token in _TOKENS.findall(query or "")}
        allowed = {str(value) for value in (repository_ids or []) if str(value)}
        relationships = [
            row for row in list(cross.get("relationships") or [])
            if (
                not allowed
                or (
                    row.get("source_repository") in allowed
                    and row.get("target_repository") in allowed
                )
            )
            if (not anchor_repository_id or anchor_repository_id in {
                row.get("source_repository"), row.get("target_repository")
            }) and (not terms or self._text_score(terms, _json(row)) > 0)
        ]
        relationships.sort(key=lambda row: (-self._text_score(terms, _json(row)), -float(row.get("confidence") or 0)))
        relationships = relationships[:24]
        if not relationships:
            return None
        lines = [
            f"Cross-repository RepoBrain snapshot v{cross.get('version')}.",
            f"Freshness: {cross.get('status')}; live retrieval must verify exact current evidence.",
        ]
        if anchor_repository_id:
            lines.append(
                f"Related repositories are orientation only; exact evidence remains scoped to {anchor_repository_id}."
            )
        for row in relationships:
            lines.append(
                f"- {row.get('source_repository')} {row.get('relationship_type')} "
                f"{row.get('target_repository')} for {row.get('business_concept')} "
                f"(confidence {row.get('confidence')}; "
                f"{','.join(row.get('source_files') or [])} -> {','.join(row.get('target_files') or [])})"
            )
        content = "\n".join(lines)[: self.settings.max_cross_context_chars]
        return {
            "snapshot_id": cross.get("id"),
            "version": cross.get("version"),
            "generated_at": cross.get("generated_at"),
            "freshness": cross.get("status"),
            "stale": bool(cross.get("stale")),
            "relationships": relationships,
            "source_references": self._cross_source_references(relationships),
            "content": content,
        }
