"""Persistent, deterministic repository knowledge for AiriX minimal context."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from hub.agent_center.context_builder import resolve_repo_path
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import redact_text
from hub.agent_center.secrets import is_secret_path
from hub.registry.models import Registry, Repository

STATUS_NOT_LEARNED = "not_learned"
STATUS_LEARNING = "learning"
STATUS_CURRENT = "current"
STATUS_UPDATE_AVAILABLE = "update_available"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_NOT_LEARNED: "Not Learned",
    STATUS_LEARNING: "Learning",
    STATUS_CURRENT: "Current",
    STATUS_UPDATE_AVAILABLE: "Update Available",
    STATUS_FAILED: "Failed",
}

INSTRUCTION_NAMES = frozenset(
    {"agents.md", "skills.md", "ai_reference.md", "ai_start_here.md", "security.md", ".cursorrules"}
)
TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini", ".sql", ".sh", ".ps1"}
)
MAX_INDEX_FILES = 800
MAX_READ_CHARS = 64_000
MAX_RETRIEVAL_ITEMS = 6
STANDARD_ANALYSIS_MODE = "standard"
DEEP_AI_ANALYSIS_MODE = "deep_ai"
ANALYSIS_MODES = {
    STANDARD_ANALYSIS_MODE: {
        "label": "Standard Scan & Learn",
        "enabled": True,
        "implemented": True,
        "execution_type": "Deterministic",
    },
    DEEP_AI_ANALYSIS_MODE: {
        "label": "Deep AI Analysis",
        "enabled": False,
        "implemented": False,
        "execution_type": "AI",
    },
}
EXCLUDED_TREE_NAMES = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
)
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{2,}")
_STRUCTURE = re.compile(
    r"^(?:async\s+def|def|class|function|interface|type|export\s+(?:class|function|const)|[A-Z][A-Z0-9_]{2,}\s*=)"
)
_STOPWORDS = frozenset(
    {"the", "and", "for", "from", "with", "that", "this", "return", "none", "true", "false", "import"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return default


def _git(root: Path, *args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout or proc.stderr or "").strip()


def _git_head(root: Path) -> str:
    ok, value = _git(root, "rev-parse", "HEAD")
    return value.splitlines()[0].strip() if ok and value else ""


def _tracked_files(root: Path) -> list[str]:
    ok, value = _git(root, "ls-files", "--cached", "--others", "--exclude-standard")
    if ok:
        return sorted(dict.fromkeys(line.strip().replace("\\", "/") for line in value.splitlines() if line.strip()))
    rows: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                rows.append(path.relative_to(root).as_posix())
            except ValueError:
                continue
    return sorted(rows)


def _prioritize_files(files: Iterable[str]) -> list[str]:
    """Keep foundational instructions/docs ahead of broad source discovery."""
    unique = list(dict.fromkeys(str(p).replace("\\", "/") for p in files if p))

    def rank(rel: str) -> tuple[int, str]:
        low = rel.lower()
        name = Path(low).name
        if name in INSTRUCTION_NAMES:
            return (0, low)
        if name.startswith("readme") or any(
            marker in low for marker in ("architecture", "security", "ai_handoff", "docs/")
        ):
            return (1, low)
        if any(marker in low for marker in ("config", "pyproject", "package.json", "requirements")):
            return (2, low)
        return (3, low)

    return sorted(unique, key=rank)


def _safe_index_path(root: Path, rel: str) -> Path | None:
    if any(part.lower() in EXCLUDED_TREE_NAMES for part in Path(rel).parts):
        return None
    try:
        path = (root / rel).resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not path.is_file() or is_secret_path(path, repo_root=root):
        return None
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name.lower() not in {"makefile", "dockerfile"}:
        return None
    try:
        if path.stat().st_size > 2_000_000:
            return None
    except OSError:
        return None
    return path


def _category(rel: str) -> str:
    low = rel.lower().replace("\\", "/")
    name = Path(low).name
    if name in INSTRUCTION_NAMES:
        return "guidance"
    if name.startswith("readme") or "introduction" in low or "overview" in low:
        return "introduction"
    if "security" in low or name.startswith(".env.example") or "environment" in low:
        return "security_environment"
    if any(part in low for part in ("architecture", "design", "adr/", "docs/system")):
        return "architecture"
    if any(part in low for part in ("adapter", "integration", "client", "api/", "connector")):
        return "integrations"
    if any(part in low for part in ("sql", "data_source", "datasource", "dhis2", "migration", "schema")):
        return "data_sources"
    if any(part in low for part in ("domain", "service", "rules", "logic", "calculation", "scoring")):
        return "business_logic"
    if any(part in low for part in ("glossary", "terminology", "models", "types")):
        return "terminology"
    if any(part in low for part in ("scripts/", "tools/", "tests/", "pyproject", "package.json", "makefile")):
        return "tools"
    if any(part in low for part in ("config", ".yaml", ".yml", ".toml", ".json")):
        return "configuration"
    return "important_paths"


def _summarize(rel: str, text: str) -> tuple[str, str, list[str]]:
    text = redact_text(text, limit=MAX_READ_CHARS)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [line.lstrip("#").strip() for line in lines if line.startswith("#")][:5]
    useful = [line for line in lines if not line.startswith(("#", "//", "/*", "*"))][:5]
    structures = [line for line in lines if _STRUCTURE.match(line)][:8]
    summary = " ".join(dict.fromkeys(headings[:2] + useful[:2] + structures[:4]))[:700]
    if not summary:
        summary = f"Repository file {rel}"
    title = headings[0][:160] if headings else Path(rel).name
    tokens = [t.lower() for t in _TOKEN.findall(" ".join([rel, title, text]))]
    counts = Counter(token for token in tokens if token not in _STOPWORDS)
    keywords = [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:40]]
    return title, summary, keywords


class RepositoryIntelligenceService:
    def __init__(self, db: AgentCenterDb, registry: Registry) -> None:
        self.db = db
        self.registry = registry

    def _repo_root(self, repository_id: str) -> tuple[Repository, Path]:
        repo = self.registry.get(repository_id)
        if repo is None or not repo.enabled:
            raise ValueError(f"Unknown or disabled repository: {repository_id}")
        root = resolve_repo_path(repo)
        if repo.type != "command" or root is None:
            raise ValueError(f"Repository '{repository_id}' has no accessible local checkout")
        return repo, root

    def _row(self, repository_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM repository_intelligence_profiles WHERE repository_id=?",
                (repository_id,),
            ).fetchone()
        return self._public(dict(row)) if row else None

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or STATUS_NOT_LEARNED)
        return {
            "repository_id": row.get("repository_id"),
            "status": status,
            "status_label": STATUS_LABELS.get(status, status.replace("_", " ").title()),
            "root_path": row.get("root_path") or "",
            "indexed_commit": row.get("indexed_commit") or "",
            "profile": _loads(row.get("profile_json"), {}),
            "categories": _loads(row.get("categories_json"), []),
            "changed_files": _loads(row.get("changed_files_json"), []),
            "last_scan_telemetry": _loads(row.get("last_scan_telemetry_json"), {}),
            "analysis_modes": ANALYSIS_MODES,
            "last_scan": row.get("last_scan"),
            "last_error": row.get("last_error") or "",
            "updated_at": row.get("updated_at"),
        }

    def get_status(self, repository_id: str, *, auto_refresh: bool = False) -> dict[str, Any]:
        current = self._row(repository_id)
        if current is None:
            return {
                "repository_id": repository_id,
                "status": STATUS_NOT_LEARNED,
                "status_label": STATUS_LABELS[STATUS_NOT_LEARNED],
                "profile": {},
                "categories": [],
                "changed_files": [],
                "last_scan_telemetry": {},
                "analysis_modes": ANALYSIS_MODES,
                "indexed_commit": "",
                "last_scan": None,
                "last_error": "",
            }
        try:
            _repo, root = self._repo_root(repository_id)
            changed = self._changed_files(current, root)
            instruction_changed = [p for p in changed if Path(p).name.lower() in INSTRUCTION_NAMES]
            if instruction_changed:
                # Refresh the complete affected set in the same transaction so
                # advancing indexed_commit cannot hide sibling code changes.
                return self.scan(
                    repository_id,
                    incremental=True,
                    changed_files=changed,
                    trigger="instruction_refresh",
                )
            if changed and auto_refresh:
                return self.scan(
                    repository_id,
                    incremental=True,
                    changed_files=changed,
                    trigger="automatic_refresh",
                )
            if changed:
                self._set_status(repository_id, STATUS_UPDATE_AVAILABLE, changed_files=changed)
                current = self._row(repository_id) or current
            elif current["status"] == STATUS_UPDATE_AVAILABLE:
                self._set_status(repository_id, STATUS_CURRENT, changed_files=[])
                current = self._row(repository_id) or current
        except Exception as exc:  # noqa: BLE001
            self._set_status(repository_id, STATUS_FAILED, error=str(exc))
            current = self._row(repository_id) or current
        return current

    def list_statuses(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for repo in self.registry.repositories:
            if repo.enabled and repo.type == "command":
                try:
                    rows.append(self.get_status(repo.id))
                except Exception as exc:  # noqa: BLE001
                    rows.append({
                        "repository_id": repo.id,
                        "status": STATUS_FAILED,
                        "status_label": STATUS_LABELS[STATUS_FAILED],
                        "last_error": str(exc),
                        "categories": [],
                        "changed_files": [],
                    })
        return rows

    def _set_status(
        self,
        repository_id: str,
        status: str,
        *,
        root: Path | None = None,
        changed_files: list[str] | None = None,
        error: str = "",
    ) -> None:
        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO repository_intelligence_profiles(
                    repository_id,status,root_path,changed_files_json,last_error,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    status=excluded.status,
                    root_path=CASE WHEN excluded.root_path='' THEN root_path ELSE excluded.root_path END,
                    changed_files_json=excluded.changed_files_json,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (repository_id, status, str(root or ""), _json(changed_files or []), error[:1000], now),
            )

    def scan(
        self,
        repository_id: str,
        *,
        incremental: bool = False,
        changed_files: list[str] | None = None,
        trigger: str = "manual_scan",
        analysis_mode: str = STANDARD_ANALYSIS_MODE,
    ) -> dict[str, Any]:
        mode = str(analysis_mode or STANDARD_ANALYSIS_MODE).strip().lower()
        if mode != STANDARD_ANALYSIS_MODE:
            raise ValueError("Deep AI Analysis is disabled and not implemented")
        _repo, root = self._repo_root(repository_id)
        current = self._row(repository_id)
        if incremental and current is None:
            incremental = False
        started_at = _now()
        started = time.perf_counter()
        head = ""
        files: list[str] = []
        indexed_count = 0
        error = ""
        self._set_status(repository_id, STATUS_LEARNING, root=root, changed_files=changed_files or [])
        try:
            head = _git_head(root)
            files = list(changed_files or []) if incremental else _tracked_files(root)
            files = _prioritize_files(files or [])[:MAX_INDEX_FILES]
            indexed_count = self._index_files(
                repository_id, root, files, head=head, incremental=incremental
            )
            profile, categories, guidance_hash = self._build_profile(repository_id, root, head)
            now = _now()
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE repository_intelligence_profiles SET
                        status=?, root_path=?, indexed_commit=?, guidance_hash=?, profile_json=?,
                        categories_json=?, changed_files_json='[]', last_scan=?, last_error='', updated_at=?
                    WHERE repository_id=?
                    """,
                    (
                        STATUS_CURRENT,
                        str(root),
                        head,
                        guidance_hash,
                        _json(profile),
                        _json(categories),
                        now,
                        now,
                        repository_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            self._set_status(repository_id, STATUS_FAILED, root=root, error=str(exc))
        telemetry = {
            "id": uuid.uuid4().hex,
            "repository_id": repository_id,
            "trigger": str(trigger or "manual_scan")[:40],
            "analysis_mode": mode,
            "status": STATUS_FAILED if error else STATUS_CURRENT,
            "execution_type": "Deterministic",
            "llm_invoked": False,
            "provider": None,
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_ai_tokens": 0,
            "files_scanned": len(files),
            "files_indexed": indexed_count,
            "files_changed": len(changed_files or []),
            "runtime_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "indexed_commit": head,
            "error": error[:1000],
            "started_at": started_at,
            "finished_at": _now(),
        }
        self._record_scan(telemetry)
        return self._row(repository_id) or {}

    def _record_scan(self, telemetry: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO repository_intelligence_scans(
                    id,repository_id,trigger,analysis_mode,status,execution_type,llm_invoked,
                    provider,model,input_tokens,output_tokens,cached_tokens,total_ai_tokens,
                    files_scanned,files_indexed,files_changed,runtime_ms,indexed_commit,error,
                    started_at,finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    telemetry["id"], telemetry["repository_id"], telemetry["trigger"],
                    telemetry["analysis_mode"], telemetry["status"], telemetry["execution_type"],
                    1 if telemetry["llm_invoked"] else 0, telemetry["provider"], telemetry["model"],
                    telemetry["input_tokens"], telemetry["output_tokens"], telemetry["cached_tokens"],
                    telemetry["total_ai_tokens"], telemetry["files_scanned"], telemetry["files_indexed"],
                    telemetry["files_changed"], telemetry["runtime_ms"], telemetry["indexed_commit"],
                    telemetry["error"], telemetry["started_at"], telemetry["finished_at"],
                ),
            )
            conn.execute(
                """
                UPDATE repository_intelligence_profiles
                SET last_scan_telemetry_json=?, updated_at=? WHERE repository_id=?
                """,
                (_json(telemetry), telemetry["finished_at"], telemetry["repository_id"]),
            )

    def _index_files(
        self,
        repository_id: str,
        root: Path,
        files: Iterable[str],
        *,
        head: str,
        incremental: bool,
    ) -> int:
        now = _now()
        file_list = list(files)
        indexed_count = 0
        with self.db.connect() as conn:
            if not incremental:
                conn.execute("DELETE FROM repository_intelligence_entries WHERE repository_id=?", (repository_id,))
                conn.execute("DELETE FROM repository_intelligence_files WHERE repository_id=?", (repository_id,))
            for rel in file_list:
                path = _safe_index_path(root, rel)
                if path is None:
                    conn.execute(
                        "DELETE FROM repository_intelligence_entries WHERE repository_id=? AND path=?",
                        (repository_id, rel),
                    )
                    conn.execute(
                        "DELETE FROM repository_intelligence_files WHERE repository_id=? AND path=?",
                        (repository_id, rel),
                    )
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")[:MAX_READ_CHARS]
                content_hash = _hash_text(text)
                category = _category(rel)
                title, summary, keywords = _summarize(rel, text)
                conn.execute(
                    """
                    INSERT INTO repository_intelligence_entries(
                        id,repository_id,path,category,title,summary,keywords_json,content_hash,indexed_commit,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(repository_id,path) DO UPDATE SET
                        category=excluded.category,title=excluded.title,summary=excluded.summary,
                        keywords_json=excluded.keywords_json,content_hash=excluded.content_hash,
                        indexed_commit=excluded.indexed_commit,updated_at=excluded.updated_at
                    """,
                    (uuid.uuid4().hex, repository_id, rel, category, title, summary, _json(keywords), content_hash, head, now),
                )
                conn.execute(
                    """
                    INSERT INTO repository_intelligence_files(repository_id,path,content_hash,category,indexed_commit,updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(repository_id,path) DO UPDATE SET
                        content_hash=excluded.content_hash,category=excluded.category,
                        indexed_commit=excluded.indexed_commit,updated_at=excluded.updated_at
                    """,
                    (repository_id, rel, content_hash, category, head, now),
                )
                indexed_count += 1
        return indexed_count

    def _build_profile(self, repository_id: str, root: Path, head: str) -> tuple[dict[str, Any], list[str], str]:
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT path,category,title,summary,keywords_json,content_hash FROM repository_intelligence_entries WHERE repository_id=? ORDER BY category,path",
                (repository_id,),
            ).fetchall()]
        categories = sorted(dict.fromkeys(str(r["category"]) for r in rows))
        by_category: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            by_category.setdefault(str(row["category"]), []).append(
                {"path": str(row["path"]), "title": str(row["title"]), "summary": str(row["summary"])[:280]}
            )
        guidance_parts = [f"{r['path']}:{r['content_hash']}" for r in rows if r["category"] == "guidance"]
        top_dirs = sorted(dict.fromkeys(Path(str(r["path"])).parts[0] for r in rows if Path(str(r["path"])).parts))[:30]
        profile = {
            "repository_id": repository_id,
            "indexed_commit": head,
            "file_count": len(rows),
            "top_level_paths": top_dirs,
            "categories": {
                category: values[:8] for category, values in by_category.items()
            },
            "compact_summary": " | ".join(
                f"{category}: {', '.join(v['path'] for v in values[:4])}"
                for category, values in by_category.items()
            )[:4000],
        }
        return profile, categories, _hash_text("\n".join(guidance_parts))

    def _changed_files(self, current: dict[str, Any], root: Path) -> list[str]:
        indexed = str(current.get("indexed_commit") or "")
        head = _git_head(root)
        changed: list[str] = []
        if indexed and head and indexed != head:
            ok, value = _git(root, "diff", "--name-only", indexed, head)
            if ok:
                changed.extend(line.strip().replace("\\", "/") for line in value.splitlines() if line.strip())
            else:
                changed.extend(_tracked_files(root))
        ok, value = _git(root, "status", "--porcelain", "--untracked-files=all")
        if ok:
            for line in value.splitlines():
                rel = line[3:].strip() if len(line) > 3 else ""
                if " -> " in rel:
                    rel = rel.split(" -> ", 1)[1]
                if rel:
                    changed.append(rel.replace("\\", "/"))
        # Guidance is cheap to verify directly and must never remain stale, even
        # when Git metadata is unavailable or an instruction file is ignored.
        with self.db.connect() as conn:
            indexed_rows = [dict(row) for row in conn.execute(
                "SELECT path,content_hash,category FROM repository_intelligence_files WHERE repository_id=?",
                (current.get("repository_id"),),
            ).fetchall()]
            guidance_rows = [row for row in indexed_rows if row.get("category") == "guidance"]
        known_guidance = {str(row["path"]): str(row["content_hash"]) for row in guidance_rows}
        current_guidance = {
            rel for rel in _tracked_files(root)
            if Path(rel).name.lower() in INSTRUCTION_NAMES
        }
        for rel in sorted(set(known_guidance) | current_guidance):
            path = _safe_index_path(root, rel)
            actual = ""
            if path is not None:
                actual = _hash_text(path.read_text(encoding="utf-8", errors="replace")[:MAX_READ_CHARS])
            if actual != known_guidance.get(rel, ""):
                changed.append(rel)
        known_paths = {str(row["path"]) for row in indexed_rows}
        safe_changes = [
            rel for rel in dict.fromkeys(changed)
            if rel in known_paths or _safe_index_path(root, rel) is not None
        ]
        return sorted(safe_changes)[:MAX_INDEX_FILES]

    def retrieve(
        self,
        repository_ids: list[str],
        prompt: str,
        *,
        limit: int = MAX_RETRIEVAL_ITEMS,
    ) -> dict[str, Any]:
        tokens = {t.lower() for t in _TOKEN.findall(prompt or "")}
        items: list[dict[str, Any]] = []
        profiles: list[dict[str, Any]] = []
        for repository_id in repository_ids[:3]:
            prior = self._row(repository_id)
            pending: list[str] = []
            current_commit = ""
            if prior is not None:
                try:
                    _repo, root = self._repo_root(repository_id)
                    current_commit = _git_head(root)
                    pending = self._changed_files(prior, root)
                except (OSError, ValueError):
                    pending = []
            state = self.get_status(repository_id, auto_refresh=True)
            if state.get("status") != STATUS_CURRENT:
                continue
            if not current_commit:
                current_commit = str(state.get("indexed_commit") or "")
            profiles.append({
                "repository_id": repository_id,
                "indexed_commit": state.get("indexed_commit") or "",
                "current_commit": current_commit,
                "freshness": "refreshed" if pending else "current",
                "stale_before_use": bool(pending),
                "changed_files_refreshed": pending,
                "categories": state.get("categories") or [],
                "compact_summary": (state.get("profile") or {}).get("compact_summary") or "",
            })
            with self.db.connect() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT path,category,title,summary,keywords_json,indexed_commit FROM repository_intelligence_entries WHERE repository_id=?",
                    (repository_id,),
                ).fetchall()]
            scored: list[tuple[int, dict[str, Any]]] = []
            for row in rows:
                hay = " ".join([
                    str(row.get("path") or ""), str(row.get("category") or ""),
                    str(row.get("title") or ""), str(row.get("summary") or ""),
                    " ".join(_loads(row.get("keywords_json"), [])),
                ]).lower()
                score = sum(3 if token in str(row.get("path") or "").lower() else 1 for token in tokens if token in hay)
                if str(row.get("category")) == "guidance":
                    score += 2
                if score > 0:
                    scored.append((score, row))
            scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("path") or "")))
            for score, row in scored[: max(1, min(limit, MAX_RETRIEVAL_ITEMS))]:
                items.append({
                    "repository_id": repository_id,
                    "path": row.get("path"),
                    "category": row.get("category"),
                    "title": row.get("title"),
                    "summary": str(row.get("summary") or "")[:700],
                    "score": score,
                    "indexed_commit": row.get("indexed_commit") or state.get("indexed_commit") or "",
                    "authority": "cached_repository_context",
                })
        items.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("path") or "")))
        chosen = items[: max(1, min(limit, MAX_RETRIEVAL_ITEMS))]
        by_repo: dict[str, int] = {}
        for item in chosen:
            rid = str(item.get("repository_id") or "")
            by_repo[rid] = by_repo.get(rid, 0) + 1
        context_chars = sum(
            len(str(profile.get("compact_summary") or "")) for profile in profiles
        ) + sum(len(str(item.get("summary") or "")) for item in chosen)
        return {
            "profiles": profiles,
            "items": chosen,
            "item_count": len(chosen),
            "include_full_index": False,
            "max_items": MAX_RETRIEVAL_ITEMS,
            "note": "Cached repository knowledge is contextual; runtime DB/DHIS2 results remain authoritative.",
            "diagnostics": {
                "used": bool(profiles),
                "repository_ids": [str(profile.get("repository_id") or "") for profile in profiles],
                "repositories": [
                    {
                        "repository_id": profile.get("repository_id"),
                        "indexed_commit": profile.get("indexed_commit"),
                        "current_commit": profile.get("current_commit"),
                        "freshness": profile.get("freshness"),
                        "stale_before_use": bool(profile.get("stale_before_use")),
                        "knowledge_entries_used": by_repo.get(str(profile.get("repository_id") or ""), 0),
                    }
                    for profile in profiles
                ],
                "knowledge_entries_used": len(chosen),
                "freshness": "refreshed" if any(p.get("stale_before_use") for p in profiles) else "current",
                "context_chars_contributed": context_chars,
                "full_index_included": False,
                "max_entries": MAX_RETRIEVAL_ITEMS,
            },
        }

    def scan_history(self, repository_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """
                SELECT * FROM repository_intelligence_scans
                WHERE repository_id=? ORDER BY finished_at DESC LIMIT ?
                """,
                (repository_id, max(1, min(limit, 100))),
            ).fetchall()]
        for row in rows:
            row["llm_invoked"] = bool(row.get("llm_invoked"))
        return rows

    def knowledge(self, repository_id: str, *, limit: int = 200) -> dict[str, Any]:
        state = self.get_status(repository_id)
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT path,category,title,summary,keywords_json,indexed_commit,updated_at FROM repository_intelligence_entries WHERE repository_id=? ORDER BY category,path LIMIT ?",
                (repository_id, max(1, min(limit, 500))),
            ).fetchall()]
        for row in rows:
            row["keywords"] = _loads(row.pop("keywords_json", "[]"), [])
        return {
            "status": state,
            "entries": rows,
            "entry_count": len(rows),
            "scan_history": self.scan_history(repository_id),
            "analysis_modes": ANALYSIS_MODES,
        }
