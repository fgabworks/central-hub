"""Discover Codex CLI models via official CLI/config surfaces (no hard-coded-only catalog).

Primary: ``codex debug models`` (official debug catalog dump).
Secondary: ``~/.codex/models_cache.json`` written by the Codex CLI itself.
Tertiary: when authenticated but discovery returns nothing, expose only the
provider-default token so Codex uses its configured/recommended default.

Never invent account-unavailable models as "available". Known Sol/Terra/Luna
slugs appear only when the CLI catalog/cache lists them.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from hub.agent_center.codex_safety import codex_home
from hub.agent_center.redact import redact_text

logger = logging.getLogger("hub.agent_center.codex_models")

PROVIDER_DEFAULT = "__provider_default__"

# Preference order for Smart Routing when multiple Codex models are available.
_COST_ASC = (
    "luna",
    "mini",
    "terra",
    "gpt-5.4-mini",
    "gpt-5-mini",
    "codex-mini",
    "sol",
)
_STRENGTH_DESC = (
    "sol",
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "terra",
    "gpt-5.6-terra",
)


def _run_debug_models(executable: str, *, timeout: float = 25.0) -> tuple[str, int]:
    try:
        result = subprocess.run(
            [executable, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.info("codex debug models failed: %s", exc)
        return "", 1
    out = (result.stdout or "").strip() or (result.stderr or "").strip()
    return out, int(result.returncode or 0)


def _extract_json_blob(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Sometimes CLI prints logs before JSON.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _slug_from_row(row: Any) -> str:
    if isinstance(row, str):
        return row.strip()
    if not isinstance(row, dict):
        return ""
    for key in ("slug", "id", "model", "name", "model_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_visible(row: dict[str, Any]) -> bool:
    visibility = str(row.get("visibility") or row.get("list_visibility") or "").strip().lower()
    if visibility in {"hidden", "hide", "unavailable", "unsupported"}:
        return False
    if row.get("hidden") is True:
        return False
    if row.get("available") is False:
        return False
    return True


def _display_name(row: dict[str, Any], slug: str) -> str:
    for key in ("display_name", "displayName", "title", "label", "name"):
        value = str(row.get(key) or "").strip()
        if value and value != slug:
            return value
    # Friendly labels for GPT-5.6 family when catalog only has slug.
    lower = slug.lower()
    if "sol" in lower:
        return f"{slug} (Sol — strongest)"
    if "terra" in lower:
        return f"{slug} (Terra — balanced)"
    if "luna" in lower:
        return f"{slug} (Luna — fast/low-cost)"
    return slug


def _normalize_catalog(payload: Any) -> list[dict[str, Any]]:
    rows_in: list[Any]
    if isinstance(payload, list):
        rows_in = payload
    elif isinstance(payload, dict):
        for key in ("models", "items", "data", "catalog", "available_models"):
            if isinstance(payload.get(key), list):
                rows_in = list(payload.get(key) or [])
                break
        else:
            # Map of slug → meta
            if payload and all(isinstance(v, dict) for v in payload.values()):
                rows_in = [{"slug": k, **v} for k, v in payload.items()]
            else:
                rows_in = []
    else:
        rows_in = []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows_in:
        if isinstance(row, dict) and not _row_visible(row):
            continue
        slug = _slug_from_row(row)
        if not slug or slug in seen:
            continue
        if slug.startswith("__"):
            continue
        seen.add(slug)
        meta = row if isinstance(row, dict) else {}
        out.append(
            {
                "id": slug,
                "display_name": _display_name(meta, slug),
                "availability": "available",
                "raw": {k: meta.get(k) for k in ("visibility", "provider", "context_window") if k in meta},
            }
        )
    return out


def read_models_cache() -> list[dict[str, Any]]:
    """Parse CLI-managed ``models_cache.json`` if present (never secrets)."""
    path = codex_home() / "models_cache.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    return _normalize_catalog(data)


def read_configured_default_model() -> str:
    """Read ``model = "..."`` from ``~/.codex/config.toml`` when present."""
    path = codex_home() / "config.toml"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"\s*$', text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(?m)^\s*model\s*=\s*'([^']+)'\s*$", text)
    return (m2.group(1).strip() if m2 else "")


def discover_codex_models(
    executable: str | None,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Return models/details/source/recommended for the authenticated Codex account.

    ``force_refresh`` currently only affects whether debug models is preferred
    over a possibly-stale cache (always tries CLI first when executable is set).
    """
    details: list[dict[str, Any]] = []
    source = "none"
    error = ""

    if executable:
        raw, code = _run_debug_models(executable)
        payload = _extract_json_blob(raw)
        if payload is not None:
            details = _normalize_catalog(payload)
            source = "cli_debug_models"
        elif raw:
            error = redact_text(raw.splitlines()[0] if raw else "", limit=160)
            if code != 0:
                logger.info("codex debug models rc=%s detail=%s", code, error)

    if not details:
        cached = read_models_cache()
        if cached:
            details = cached
            source = "cli_models_cache"

    configured = read_configured_default_model()
    ids = [row["id"] for row in details]

    # Always offer provider default as an explicit choice when models exist,
    # but never as the *only* option when a real catalog is available.
    recommended = ""
    if configured and configured in ids:
        recommended = configured
    elif ids:
        # Prefer balanced Terra-like, else first catalog entry.
        recommended = pick_model_for_complexity(ids, complexity=40, task_type="coding") or ids[0]

    model_ids = list(ids)
    model_details = list(details)
    if model_ids:
        # Append provider-default as last option (Codex configured default).
        if PROVIDER_DEFAULT not in model_ids:
            model_ids.append(PROVIDER_DEFAULT)
            model_details.append(
                {
                    "id": PROVIDER_DEFAULT,
                    "display_name": "Codex configured default",
                    "availability": "available",
                }
            )
        if not recommended:
            recommended = model_ids[0]
    else:
        # Authenticated account with no discoverable catalog → configured default only.
        model_ids = [PROVIDER_DEFAULT]
        model_details = [
            {
                "id": PROVIDER_DEFAULT,
                "display_name": "Codex configured/recommended default",
                "availability": "available",
            }
        ]
        recommended = PROVIDER_DEFAULT
        source = source if source != "none" else "provider_default"
        if not error:
            error = "Codex model catalog unavailable; using provider configured default only."

    return {
        "models": model_ids,
        "model_details": model_details,
        "recommended_model": recommended,
        "configured_default": configured,
        "models_source": source,
        "error": error,
        "dynamic_models": bool(ids),
    }


def pick_model_for_complexity(
    models: list[str],
    *,
    complexity: int,
    task_type: str = "",
) -> str:
    """Pick a Codex/Grok/OpenAI model slug from an available list for Smart Routing."""
    ids = [str(m).strip() for m in models if str(m).strip() and not str(m).startswith("__")]
    if not ids:
        return ""
    lower_map = {m.lower(): m for m in ids}
    want_strong = (
        complexity >= 65
        or task_type in {"architecture", "refactor"}
        or (task_type == "coding" and complexity >= 55)
    )
    want_cheap = complexity < 35 or task_type in {"css_ui", "testing"} and complexity < 45

    def _find(tokens: tuple[str, ...]) -> str:
        for token in tokens:
            for low, original in lower_map.items():
                if token in low:
                    return original
        return ""

    if want_strong:
        hit = _find(_STRENGTH_DESC)
        if hit:
            return hit
        return ids[-1] if ids else ""
    if want_cheap:
        hit = _find(_COST_ASC)
        if hit:
            return hit
        return ids[0]
    # Balanced
    hit = _find(("terra", "gpt-5.4", "gpt-5", "mini"))
    return hit or ids[0]
