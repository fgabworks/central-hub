"""CLIMATE execution mode — orchestration, not provider identity.

AiriX uses the CLIMATE orchestration/context layer, then the selected provider.
Direct sends the prompt to that same provider with minimal CLIMATE wrapping.
Repository/file context is never implied; it is used only when explicitly selected.
"""

from __future__ import annotations

from typing import Any

CLIMATE_ASSISTED = "climate_assisted"
DIRECT = "direct"
EXECUTION_MODES = (CLIMATE_ASSISTED, DIRECT)

MODE_LABELS = {
    CLIMATE_ASSISTED: "AiriX",
    DIRECT: "Direct",
}

MODE_TOOLTIPS = {
    CLIMATE_ASSISTED: "AiriX — CLIMATE orchestration, then the selected provider/model.",
    DIRECT: "Direct — send the prompt to the selected provider/model with minimal CLIMATE orchestration.",
}

_ALIASES = {
    "climate_assisted": CLIMATE_ASSISTED,
    "climate-assisted": CLIMATE_ASSISTED,
    "assisted": CLIMATE_ASSISTED,
    "climate": CLIMATE_ASSISTED,
    "airix": CLIMATE_ASSISTED,
    "direct": DIRECT,
    "direct_provider": DIRECT,
    "direct-provider": DIRECT,
}


def normalize_execution_mode(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(raw, CLIMATE_ASSISTED)


def coerce_execution_mode(value: str | None) -> str:
    """Strict parser for saved defaults. Blank becomes AiriX; unknown raises."""
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not raw:
        return CLIMATE_ASSISTED
    if raw not in _ALIASES:
        raise ValueError("Unknown execution mode")
    return _ALIASES[raw]


def is_direct_mode(value: str | None) -> bool:
    return normalize_execution_mode(value) == DIRECT


PROVIDER_LABELS = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "openai-api": "OpenAI",
    "grok": "Grok",
    "claude": "Claude",
    "claude-code": "Claude Code",
    "anthropic": "Anthropic",
    "anthropic-api": "Anthropic",
    "codex": "Codex",
    "cursor-agent": "Cursor Agent",
}


def provider_display_label(provider_id: str | None, known_label: str = "") -> str:
    """Human provider name for labels/summaries. Known labels win."""
    label = str(known_label or "").strip()
    if label:
        return label
    pid = str(provider_id or "").strip()
    return PROVIDER_LABELS.get(pid, pid or "Provider")


def assistant_label(execution_mode: str | None, provider_label: str = "") -> str:
    """Visible speaker for a completed/in-flight assistant message."""
    if is_direct_mode(execution_mode):
        return str(provider_label or "").strip() or "Provider"
    return "AiriX"


def format_execution_summary(
    *,
    execution_mode: str | None,
    provider_label: str = "",
    model: str = "",
    context_scope: str = "",
    repository_label: str = "",
) -> str:
    """Compact Details line from persisted run metadata, not live UI controls."""
    parts = [mode_label(execution_mode)]
    if str(provider_label or "").strip():
        parts.append(str(provider_label).strip())
    if str(model or "").strip():
        parts.append(str(model).strip())
    scope = str(context_scope or "").strip().lower()
    if scope in {"all", "all-repositories", "all_repositories"}:
        parts.append("All Repositories")
    elif scope in {"repository", "repo", "specific"}:
        if str(repository_label or "").strip():
            parts.append(str(repository_label).strip())
    elif scope == "general":
        parts.append("General")
    return " · ".join(parts)


def mode_label(value: str | None) -> str:
    return MODE_LABELS[normalize_execution_mode(value)]


def mode_tooltip(value: str | None) -> str:
    return MODE_TOOLTIPS[normalize_execution_mode(value)]


def execution_mode_public(value: str | None) -> dict[str, str]:
    mode = normalize_execution_mode(value)
    return {
        "id": mode,
        "label": MODE_LABELS[mode],
        "tooltip": MODE_TOOLTIPS[mode],
        "compare_label": "Compare with CLIMATE" if mode == DIRECT else "Compare with Direct",
    }


def normalize_path_list(value: Any, *, limit: int = 48) -> list[str]:
    """Stable relative-path labels from strings or {repository_id, path} dicts."""
    items: list[str] = []
    seen: set[str] = set()
    raw = value if isinstance(value, (list, tuple)) else []
    for item in raw:
        if isinstance(item, dict):
            repo = str(item.get("repository_id") or item.get("repositoryId") or "").strip()
            path = str(item.get("path") or "").replace("\\", "/").strip().lstrip("/")
            text = f"{repo}:{path}" if repo and path else path
        else:
            text = str(item or "").replace("\\", "/").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def normalize_context_source_list(value: Any, *, limit: int = 24) -> list[Any]:
    """Keep context provenance JSON-safe and bounded before run persistence."""
    def clean(item: Any, depth: int = 0) -> Any:
        if depth >= 3:
            return str(item or "")[:500]
        if isinstance(item, dict):
            return {
                str(key)[:80]: clean(val, depth + 1)
                for key, val in list(item.items())[:24]
                if not str(key).startswith("_")
            }
        if isinstance(item, (list, tuple)):
            return [clean(part, depth + 1) for part in list(item)[:24]]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item[:1_000] if isinstance(item, str) else item
        return str(item)[:500]

    raw = value if isinstance(value, (list, tuple)) else []
    return [clean(item) for item in list(raw)[:limit]]


def climate_execution_record(
    *,
    execution_mode: str | None,
    context_scope: str = "",
    repository_id: str = "",
    repository_name: str = "",
    surface: str = "",
    provider: str = "",
    model: str = "",
    provider_label: str = "",
    attached_files: Any = None,
    retrieved_files: Any = None,
    inspected_files: Any = None,
    current_file: str = "",
    sources_considered: Any = None,
    sources_queried: Any = None,
    sources_used: Any = None,
    evidence_references: Any = None,
    context_source_failures: Any = None,
) -> dict[str, Any]:
    """Authoritative executed configuration persisted on the Agent Center run."""
    return {
        "execution_mode": normalize_execution_mode(execution_mode),
        "context_scope": str(context_scope or ""),
        "repository_id": str(repository_id or ""),
        "repository_name": str(repository_name or ""),
        "surface": str(surface or "").strip().lower(),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "provider_label": str(provider_label or ""),
        "attached_files": normalize_path_list(attached_files),
        "retrieved_files": normalize_path_list(retrieved_files),
        "inspected_files": normalize_path_list(inspected_files),
        "current_file": str(current_file or "").replace("\\", "/").strip().lstrip("/"),
        "sources_considered": normalize_context_source_list(sources_considered),
        "sources_queried": [str(item)[:100] for item in list(sources_queried or [])[:24]],
        "sources_used": [str(item)[:100] for item in list(sources_used or [])[:24]],
        "evidence_references": normalize_context_source_list(evidence_references),
        "context_source_failures": normalize_context_source_list(context_source_failures),
    }
