"""Observation capping / pruning for multi-step Tool Runtime (Phase 2)."""

from __future__ import annotations

import json
import re
from typing import Any

_FACT_KEYS = (
    "uid",
    "id",
    "path",
    "query_id",
    "connection_id",
    "row_count",
    "columns",
    "rows",
    "org_units",
    "items",
    "matches",
    "source",
    "answer",
    "value",
    "count",
    "n",
)


def cap_observation(text: str, *, max_chars: int = 6_000) -> str:
    raw = str(text or "")
    limit = max(80, int(max_chars))
    if len(raw) <= limit:
        return raw
    head = max(40, limit - 48)
    return raw[:head] + f"\n…[truncated {len(raw) - head} chars]"


def extract_grounded_facts(observation: str | dict[str, Any] | None) -> dict[str, Any]:
    """Pull compact fact fields that completion/grounding may still need."""
    data: dict[str, Any]
    if isinstance(observation, dict):
        data = observation
    else:
        text = str(observation or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else {"raw": text[:240]}
        except json.JSONDecodeError:
            # Keep numeric / uid-like snippets from free text.
            facts: dict[str, Any] = {}
            nums = re.findall(r"\b\d+(?:\.\d+)?\b", text)
            if nums:
                facts["numbers"] = nums[:8]
            uids = re.findall(r"\b[A-Za-z](?=[A-Za-z0-9]*\d)[A-Za-z0-9]{10}\b", text)
            if uids:
                facts["uids"] = uids[:6]
            paths = re.findall(r"[\w./\\-]+\.(?:py|js|ts|md|sql|yaml|yml|json)\b", text)
            if paths:
                facts["paths"] = paths[:6]
            return facts
    out: dict[str, Any] = {}
    for key in _FACT_KEYS:
        if key in data and data.get(key) is not None:
            val = data.get(key)
            if isinstance(val, list):
                out[key] = val[:10]
            elif isinstance(val, str):
                out[key] = val[:400]
            else:
                out[key] = val
    return out


def prune_observations(
    observations: list[dict[str, Any]],
    *,
    keep: int = 4,
    max_chars: int = 6_000,
    preserve_grounded: bool = True,
    required_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Keep newest observations; elide older payloads while preserving grounded facts
    and completion-relevant tool rows.
    """
    rows = list(observations or [])
    if not rows:
        return []
    keep_n = max(1, int(keep))
    required = set(required_tools or [])

    def _cap_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "observation": cap_observation(str(row.get("observation") or ""), max_chars=max_chars),
        }

    if len(rows) <= keep_n:
        return [_cap_row(row) for row in rows]

    older = rows[:-keep_n]
    recent = rows[-keep_n:]
    elided: list[dict[str, Any]] = []
    for row in older:
        tool = str(row.get("tool") or "")
        keep_full = preserve_grounded and (
            tool in required or bool(row.get("from_t0")) or bool(row.get("preserve"))
        )
        facts = extract_grounded_facts(row.get("observation")) if preserve_grounded else {}
        if keep_full and facts:
            elided.append(
                {
                    "tool": tool,
                    "ok": row.get("ok"),
                    "summary": str(row.get("summary") or "")[:160],
                    "observation": cap_observation(
                        json.dumps({"facts": facts, "summary": row.get("summary")}, ensure_ascii=False),
                        max_chars=min(1200, max_chars),
                    ),
                    "elided": True,
                    "preserved_facts": True,
                    "from_t0": bool(row.get("from_t0")),
                }
            )
        else:
            elided.append(
                {
                    "tool": tool,
                    "ok": row.get("ok"),
                    "summary": str(row.get("summary") or "")[:160],
                    "observation": (
                        f"[elided prior observation for {tool or 'tool'}]"
                        + (f" facts={json.dumps(facts, ensure_ascii=False)[:240]}" if facts else "")
                    ),
                    "elided": True,
                    "preserved_facts": bool(facts),
                    "from_t0": bool(row.get("from_t0")),
                }
            )
    return elided + [_cap_row(row) for row in recent]


def estimate_context_chars(
    *,
    system: str = "",
    prompt: str = "",
    observations: list[dict[str, Any]] | None = None,
    tools: list[Any] | None = None,
) -> int:
    total = len(system or "") + len(prompt or "")
    for row in observations or []:
        total += len(str(row.get("observation") or "")) + len(str(row.get("summary") or ""))
    for tool in tools or []:
        if hasattr(tool, "description"):
            total += len(str(getattr(tool, "description", "") or ""))
        elif isinstance(tool, dict):
            total += len(str(tool.get("description") or "")) + len(str(tool.get("name") or ""))
    return total
