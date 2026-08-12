"""T0 → Tool Runtime continuation without rebuilding unchanged context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeContinuation:
    """Seed state carried from T0 (or a prior runtime step) into Tool Runtime."""

    evidence_packet: dict[str, Any] = field(default_factory=dict)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    completion_contract: dict[str, Any] = field(default_factory=dict)
    context_fingerprint: str = ""
    repository_intelligence: dict[str, Any] = field(default_factory=dict)
    detected_filters: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    t0_failure_reason: str = ""
    unchanged_context: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "evidence_packet": dict(self.evidence_packet),
            "tool_results": list(self.tool_results),
            "completion_contract": dict(self.completion_contract),
            "context_fingerprint": self.context_fingerprint,
            "repository_intelligence": dict(self.repository_intelligence),
            "detected_filters": dict(self.detected_filters),
            "observations": list(self.observations),
            "t0_failure_reason": self.t0_failure_reason,
            "unchanged_context": bool(self.unchanged_context),
        }


def fingerprint_context(
    *,
    prompt: str,
    repository_ids: list[str] | None = None,
    context_sources: list[str] | None = None,
    dhis2_environment: str = "",
    evidence_sources: list[str] | None = None,
) -> str:
    payload = {
        "prompt": (prompt or "").strip()[:2000],
        "repos": list(repository_ids or [])[:8],
        "sources": list(context_sources or [])[:8],
        "env": str(dhis2_environment or "").strip().lower(),
        "evidence": list(evidence_sources or [])[:16],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_continuation_from_t0(
    prior: dict[str, Any] | None,
    *,
    context_preview: dict[str, Any] | None = None,
) -> RuntimeContinuation:
    """Build a continuation package from a T0 / escalation prior result."""
    prior = prior if isinstance(prior, dict) else {}
    preview = context_preview if isinstance(context_preview, dict) else {}
    packet = prior.get("evidence_packet") if isinstance(prior.get("evidence_packet"), dict) else {}
    if not packet:
        packet = preview.get("evidence_packet") if isinstance(preview.get("evidence_packet"), dict) else {}
    tool_results = list(prior.get("tool_results") or packet.get("tool_results") or [])
    contract = prior.get("completion_contract") if isinstance(prior.get("completion_contract"), dict) else {}
    if not contract:
        contract = (
            preview.get("completion_contract")
            if isinstance(preview.get("completion_contract"), dict)
            else {}
        )
    ri = prior.get("repository_intelligence") if isinstance(prior.get("repository_intelligence"), dict) else {}
    if not ri:
        ri = (
            preview.get("repository_intelligence")
            if isinstance(preview.get("repository_intelligence"), dict)
            else {}
        )
    filters = prior.get("detected_filters") if isinstance(prior.get("detected_filters"), dict) else {}
    if not filters:
        filters = (
            preview.get("detected_filters") if isinstance(preview.get("detected_filters"), dict) else {}
        )
    fp = str(
        prior.get("context_fingerprint")
        or preview.get("context_fingerprint")
        or ""
    ).strip()
    observations = observations_from_tool_results(tool_results)
    return RuntimeContinuation(
        evidence_packet=dict(packet),
        tool_results=tool_results,
        completion_contract=dict(contract),
        context_fingerprint=fp,
        repository_intelligence=dict(ri),
        detected_filters=dict(filters),
        observations=observations,
        t0_failure_reason=str(prior.get("t0_failure_reason") or preview.get("t0_failure_reason") or ""),
        unchanged_context=True,
    )


def observations_from_tool_results(tool_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert T0 tool_results into compact runtime observations (no rebuild)."""
    out: list[dict[str, Any]] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if not tool:
            continue
        summary = str(item.get("summary") or item.get("detail") or ("ok" if item.get("ok") else "error"))[
            :200
        ]
        # Prefer already-bounded fields; never re-execute.
        obs = item.get("observation")
        if not obs:
            compact = {
                k: item.get(k)
                for k in ("tool", "ok", "summary", "source", "query_id", "row_count", "error")
                if item.get(k) is not None
            }
            obs = json.dumps(compact, ensure_ascii=False, default=str)
        out.append(
            {
                "tool": tool,
                "ok": bool(item.get("ok", True)),
                "summary": summary,
                "observation": str(obs)[:4000],
                "from_t0": True,
            }
        )
    return out


def continuation_from_payload(payload: dict[str, Any] | None) -> RuntimeContinuation | None:
    raw = payload.get("t0_continuation") if isinstance(payload, dict) else None
    if not isinstance(raw, dict) or not raw:
        return None
    return RuntimeContinuation(
        evidence_packet=dict(raw.get("evidence_packet") or {})
        if isinstance(raw.get("evidence_packet"), dict)
        else {},
        tool_results=list(raw.get("tool_results") or []),
        completion_contract=dict(raw.get("completion_contract") or {})
        if isinstance(raw.get("completion_contract"), dict)
        else {},
        context_fingerprint=str(raw.get("context_fingerprint") or "").strip(),
        repository_intelligence=dict(raw.get("repository_intelligence") or {})
        if isinstance(raw.get("repository_intelligence"), dict)
        else {},
        detected_filters=dict(raw.get("detected_filters") or {})
        if isinstance(raw.get("detected_filters"), dict)
        else {},
        observations=list(raw.get("observations") or []),
        t0_failure_reason=str(raw.get("t0_failure_reason") or ""),
        unchanged_context=bool(raw.get("unchanged_context", True)),
    )
