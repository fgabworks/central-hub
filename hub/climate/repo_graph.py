"""Optional local/read-only repository graph hints (stdlib AST).

This is a provider-independent spike: symbols, definitions, imports, and
concept-relevant files. It does not write source, does not require Graphify,
and is consumed by the Context Resolver as extra hints only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Iterable

from hub.climate.domain_query import DomainQuery, extract_domain_query, identifier_matches_query

_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".graphify",
}
_MAX_FILES = 400
_MAX_FILE_BYTES = 200_000
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.M,
)


def concept_file_hints(
    root: Path,
    prompt: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return ranked files that define or import symbols matching the prompt."""
    query = extract_domain_query(prompt)
    if not query.match_needles():
        return []
    index = build_python_index(root)
    return rank_concept_files(index, query, limit=limit)


def build_python_index(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    scanned = 0
    for path in root.rglob("*.py"):
        if scanned >= _MAX_FILES:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        rel = path.relative_to(root).as_posix()
        symbols, imports = _extract_python(text)
        rows.append({
            "path": rel,
            "symbols": symbols,
            "imports": imports,
            "kind": "definition" if symbols else "module",
        })
    return rows


def rank_concept_files(
    index: Iterable[dict[str, Any]],
    query: DomainQuery,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    defined_paths = {
        str(row.get("path") or "")
        for row in index
        if any(identifier_matches_query(sym, query) for sym in (row.get("symbols") or []))
    }
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in index:
        path = str(row.get("path") or "")
        symbols = list(row.get("symbols") or [])
        imports = list(row.get("imports") or [])
        relevant = [sym for sym in symbols if identifier_matches_query(sym, query)]
        score = 0
        if relevant:
            score += 40 + min(12, len(relevant) * 3)
        path_hit = identifier_matches_query(path, query)
        if path_hit:
            score += 18
        imported_authority = False
        for item in imports:
            if identifier_matches_query(item, query):
                score += 8
            module_path = item.replace(".", "/")
            if any(module_path in defined or defined.endswith(module_path + ".py") for defined in defined_paths):
                imported_authority = True
        if imported_authority:
            score += 10
        if score <= 0:
            continue
        scored.append((score, {
            **row,
            "path": path,
            "relevant_symbols": relevant,
            "score": score,
            "reason": "graph:definition" if relevant else ("graph:path" if path_hit else "graph:import"),
        }))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("path") or "")))
    return [row for _score, row in scored[: max(1, limit)]]


def _extract_python(text: str) -> tuple[list[str], list[str]]:
    symbols: list[str] = []
    imports: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        imports = [g for m in _IMPORT_RE.finditer(text) for g in m.groups() if g]
        return [], list(dict.fromkeys(imports))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
            imports.extend(
                f"{node.module}.{alias.name}" for alias in node.names if alias.name and alias.name != "*"
            )
    return list(dict.fromkeys(symbols)), list(dict.fromkeys(imports))
