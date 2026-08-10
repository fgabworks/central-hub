"""AiriX execution AI-usage telemetry.

Built from actual provider/tool execution events — never inferred from UI labels.
T0 / Deterministic runs must record zero LLM tokens and no child AI run.
"""

from __future__ import annotations

from typing import Any

from hub.agent_center.routing.cost import parse_usage

# Adapters/providers that count as an LLM (or LLM-backed) invocation.
AI_PROVIDERS = frozenset(
    {
        "codex",
        "grok",
        "openai-api",
        "claude-code",
        "cursor-agent",
        "hub-simulator",
        "low-cost",
        "openai",
        "xai",
        "anthropic",
        "cursor",
    }
)

EXEC_DETERMINISTIC = "Deterministic"
EXEC_AI = "AI"
EXEC_HYBRID = "Hybrid"


def is_ai_provider(provider_or_adapter: str | None) -> bool:
    key = (provider_or_adapter or "").strip().lower()
    if not key or key in {"deterministic", "none", "null"}:
        return False
    if key in AI_PROVIDERS:
        return True
    # Map routing provider ids that share adapter names.
    return key.replace("_", "-") in AI_PROVIDERS


def _tools_from_row(row: dict[str, Any]) -> list[str]:
    """Collect Hub tools actually executed (tool_results, evidence packet, sources)."""
    tools: list[str] = []

    def _add(name: Any) -> None:
        text = str(name or "").strip()
        if not text:
            return
        if text.startswith("tool:"):
            text = text.split(":", 1)[1].strip()
        if text and text not in {"selected context", "none", "hub tools"}:
            tools.append(text)

    for item in row.get("tool_results") or []:
        if isinstance(item, dict):
            _add(item.get("tool"))

    packet = row.get("evidence_packet") if isinstance(row.get("evidence_packet"), dict) else {}
    for item in packet.get("tool_results") or []:
        if isinstance(item, dict):
            _add(item.get("tool"))
    for src in packet.get("sources") or []:
        text = str(src or "").strip()
        if text.startswith("tool:"):
            _add(text)

    grounding = row.get("grounding") if isinstance(row.get("grounding"), dict) else {}
    for part in str(grounding.get("source") or "").split(","):
        part = part.strip()
        if part.startswith("tool:") or part.endswith("_lookup") or part in {
            "repo_search",
            "read_file",
            "uid_lookup",
            "org_unit_lookup",
            "sql_lookup",
            "sql_query_execute",
            "notebook_lookup",
            "jobs_lookup",
            "audit_lookup",
            "dhis2_reports_lookup",
        }:
            _add(part)

    ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
    # Only include packed tool_ids when we already have executed tool evidence —
    # otherwise planned-but-not-run tools would look like they executed.
    if tools:
        pass
    else:
        for tid in ctx.get("tool_ids") or []:
            _add(tid)

    return list(dict.fromkeys(t for t in tools if t.strip()))


def _int0(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _runtime_ms_from_iso(started_at: Any, finished_at: Any) -> int:
    if not started_at or not finished_at:
        return 0
    try:
        from datetime import datetime

        a = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() * 1000))
    except ValueError:
        return 0


def _child_ai_run_id(row: dict[str, Any]) -> str | None:
    child_id = str(row.get("agent_run_id") or "").strip() or None
    if child_id:
        return child_id
    agent_run = row.get("agent_run") if isinstance(row.get("agent_run"), dict) else {}
    return str(agent_run.get("id") or "").strip() or None


def _resolve_tier(row: dict[str, Any], *, pure_t0: bool = False) -> str:
    """Never emit 'T?' when tier is knowable from the execution."""
    tier = str(row.get("tier") or "").strip()
    if tier.startswith("T"):
        return tier
    tel = row.get("telemetry") if isinstance(row.get("telemetry"), dict) else {}
    prior = str(tel.get("routing_tier") or "").strip()
    if prior.startswith("T"):
        return prior
    if pure_t0:
        return "T0"
    provider = str(row.get("provider_id") or "").strip().lower()
    adapter = str(row.get("adapter_id") or "").strip().lower()
    mode = str(row.get("mode") or "").strip().lower()
    if (
        mode in {"deterministic", "grounding_gate"}
        or provider in {"deterministic", ""}
        or adapter in {"", "deterministic"}
    ):
        return "T0"
    if adapter in {"hub-simulator", "low-cost"} or provider in {"low-cost", "hub-simulator"}:
        return "T1"
    if adapter in {"grok", "openai-api"} or provider in {"grok", "openai-api"}:
        return "T2"
    if adapter in {"codex", "claude-code", "cursor-agent"} or provider in {
        "codex",
        "claude-code",
        "cursor-agent",
    }:
        return "T3"
    return "T0" if not is_ai_provider(adapter or provider) else "T2"


def empty_t0_telemetry(
    *,
    tier: str = "T0",
    tools_used: list[str] | None = None,
    runtime_ms: int = 0,
    t0_failure_reason: str | None = None,
    next_capability: str | None = None,
    db_query_attempted: bool = False,
    ai_escalation_occurred: bool = False,
    routing_mode: str | None = None,
    session_reused: bool = False,
    context_items: list[str] | None = None,
    context_chars: int | None = None,
    repository_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical pure-T0 telemetry (all AI fields zero / None)."""
    resolved_tier = tier if str(tier or "").startswith("T") else "T0"
    return {
        "routing_tier": resolved_tier,
        "execution_type": EXEC_DETERMINISTIC,
        "llm_invoked": False,
        "provider": None,
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_ai_tokens": 0,
        "usage_source": "actual",
        "tools_used": list(tools_used or []),
        "runtime_ms": max(0, int(runtime_ms or 0)),
        "child_ai_run_id": None,
        "t0_pure": True,
        "t0_failure_reason": t0_failure_reason or None,
        "next_capability": next_capability or None,
        "db_query_attempted": bool(db_query_attempted),
        "ai_escalation_occurred": bool(ai_escalation_occurred),
        "routing_mode": routing_mode or None,
        "session_reused": bool(session_reused),
        "context_items": list(context_items or []),
        "context_chars": context_chars,
        "repository_intelligence": dict(repository_intelligence or {}),
    }


def build_execution_telemetry(row: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive telemetry from an execution row / public fields dict.

    Uses mode, adapter_id, agent_run_id, usage, and tool_results — not UI labels.
    llm_invoked is True only when an actual provider child run was started.
    """
    row = dict(row or {})
    mode = str(row.get("mode") or "").strip().lower()
    provider_id = str(row.get("provider_id") or "").strip()
    adapter_id = str(row.get("adapter_id") or "").strip()
    fallback_from = str(row.get("fallback_from") or "").strip().lower()
    child_id = _child_ai_run_id(row)

    model = ""
    agent_run = row.get("agent_run") if isinstance(row.get("agent_run"), dict) else {}
    if agent_run:
        model = str(agent_run.get("model") or "").strip()
    if not model:
        model = str(row.get("resolved_model") or row.get("model") or "").strip()
    model = model or None

    tools = _tools_from_row(row)
    runtime_ms = row.get("runtime_ms")
    if runtime_ms is None:
        runtime_ms = _runtime_ms_from_iso(row.get("started_at"), row.get("finished_at"))
    runtime_ms = _int0(runtime_ms)

    looks_deterministic = (
        mode in {"deterministic", "grounding_gate"}
        or provider_id in {"deterministic", ""}
        or adapter_id in {"", "deterministic"}
        or (not is_ai_provider(adapter_id) and not is_ai_provider(provider_id))
    )

    esc_fields = {
        "t0_failure_reason": row.get("t0_failure_reason") or None,
        "next_capability": row.get("next_capability") or None,
        "db_query_attempted": bool(row.get("db_query_attempted")),
        "ai_escalation_occurred": bool(
            row.get("ai_escalation_occurred")
            or (fallback_from in {"deterministic", "t0"} and child_id)
        ),
        "routing_mode": row.get("routing_mode") or (row.get("context") or {}).get("routing_mode"),
        "session_reused": bool(row.get("session_reused")),
        "context_items": list(row.get("context_items") or []),
        "context_chars": row.get("context_chars"),
        "repository_intelligence": dict(
            row.get("repository_intelligence_diagnostics")
            or ((row.get("context") or {}).get("repository_intelligence") or {}).get("diagnostics")
            or {}
        ),
    }

    # No child AI run ⇒ never claim LLM invocation.
    if not child_id:
        if looks_deterministic:
            return enforce_t0_telemetry(
                empty_t0_telemetry(
                    tier=_resolve_tier(row, pure_t0=True),
                    tools_used=tools,
                    runtime_ms=runtime_ms,
                    **esc_fields,
                )
            )
        return {
            "routing_tier": _resolve_tier(row, pure_t0=False),
            "execution_type": EXEC_AI,
            "llm_invoked": False,
            "provider": adapter_id or provider_id or None,
            "model": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_ai_tokens": 0,
            "usage_source": "actual",
            "tools_used": tools,
            "runtime_ms": runtime_ms,
            "child_ai_run_id": None,
            "t0_pure": False,
            **esc_fields,
        }

    # Child AI run started → AI or Hybrid.
    hybrid = fallback_from in {"deterministic", "t0"}
    usage: dict[str, Any] = {}
    if isinstance(row.get("usage"), dict) and row.get("usage"):
        usage = row["usage"]
    if not usage and isinstance(agent_run.get("usage"), dict):
        usage = agent_run["usage"]

    parsed = parse_usage(usage)
    cached = parsed.get("cached_tokens")
    if cached is None:
        cached = 0 if parsed.get("usage_source") == "actual" else None

    usage_source = str(parsed.get("usage_source") or "estimate")
    if parsed.get("total_tokens") is None:
        usage_source = "estimate"

    display_provider = adapter_id or provider_id or None
    if display_provider in {"deterministic", ""}:
        display_provider = None
    if not display_provider and agent_run.get("agent_id"):
        display_provider = str(agent_run.get("agent_id"))

    return {
        "routing_tier": _resolve_tier(row, pure_t0=False),
        "execution_type": EXEC_HYBRID if hybrid else EXEC_AI,
        "llm_invoked": True,
        "provider": display_provider,
        "model": model,
        "input_tokens": parsed.get("input_tokens"),
        "output_tokens": parsed.get("output_tokens"),
        "cached_tokens": cached,
        "total_ai_tokens": parsed.get("total_tokens"),
        "usage_source": usage_source,
        "tools_used": tools,
        "runtime_ms": runtime_ms,
        "child_ai_run_id": child_id,
        "t0_pure": False,
        "t0_failure_reason": row.get("t0_failure_reason") or None,
        "next_capability": row.get("next_capability") or None,
        "db_query_attempted": bool(row.get("db_query_attempted")),
        "ai_escalation_occurred": True
        if hybrid or row.get("ai_escalation_occurred")
        else bool(row.get("ai_escalation_occurred")),
        "routing_mode": row.get("routing_mode") or (row.get("context") or {}).get("routing_mode"),
        "session_reused": bool(row.get("session_reused")),
        "context_items": list(row.get("context_items") or []),
        "context_chars": row.get("context_chars"),
        "repository_intelligence": dict(
            row.get("repository_intelligence_diagnostics")
            or ((row.get("context") or {}).get("repository_intelligence") or {}).get("diagnostics")
            or {}
        ),
    }


def enforce_t0_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Force T0 purity fields. Used when finalizing deterministic executions."""
    out = dict(telemetry)
    out["execution_type"] = EXEC_DETERMINISTIC
    out["llm_invoked"] = False
    out["provider"] = None
    out["model"] = None
    out["input_tokens"] = 0
    out["output_tokens"] = 0
    out["cached_tokens"] = 0
    out["total_ai_tokens"] = 0
    out["usage_source"] = "actual"
    out["child_ai_run_id"] = None
    out["t0_pure"] = True
    out["ai_escalation_occurred"] = False
    if not str(out.get("routing_tier") or "").startswith("T"):
        out["routing_tier"] = "T0"
    return out


def assert_t0_telemetry_pure(telemetry: dict[str, Any]) -> None:
    """Raise AssertionError if a claimed T0/Deterministic telemetry is impure."""
    if telemetry.get("llm_invoked"):
        raise AssertionError("T0 telemetry must not set llm_invoked=True")
    if telemetry.get("provider") not in (None, "", "deterministic"):
        if telemetry.get("provider") is not None:
            raise AssertionError(f"T0 telemetry provider must be None, got {telemetry.get('provider')!r}")
    if telemetry.get("model") not in (None, ""):
        raise AssertionError(f"T0 telemetry model must be None, got {telemetry.get('model')!r}")
    if telemetry.get("child_ai_run_id") not in (None, ""):
        raise AssertionError(
            f"T0 telemetry child_ai_run_id must be None, got {telemetry.get('child_ai_run_id')!r}"
        )
    for key in ("input_tokens", "output_tokens", "cached_tokens", "total_ai_tokens"):
        if _int0(telemetry.get(key)) != 0:
            raise AssertionError(f"T0 telemetry {key} must be 0, got {telemetry.get(key)!r}")
    if telemetry.get("execution_type") not in (EXEC_DETERMINISTIC, None, ""):
        raise AssertionError(
            f"T0 telemetry execution_type must be Deterministic, got {telemetry.get('execution_type')!r}"
        )
    if not str(telemetry.get("routing_tier") or "").startswith("T"):
        raise AssertionError(
            f"T0 telemetry routing_tier must be a T* value, got {telemetry.get('routing_tier')!r}"
        )


def format_telemetry_block(telemetry: dict[str, Any] | None) -> str:
    """Compact HTML-safe-ish plain text diagnostics line(s)."""
    if not isinstance(telemetry, dict) or not telemetry:
        return ""
    t = telemetry
    src = str(t.get("usage_source") or "")
    src_note = f" ({src})" if src and src != "actual" else ""
    total = t.get("total_ai_tokens")
    total_s = "—" if total is None else str(total)
    if total is None and src == "estimate":
        total_s = "est. unavailable"
    elif src == "estimate" and total is not None:
        total_s = f"{total} (est.)"
    tier = t.get("routing_tier") or "T0"
    lines = [
        f"Tier: {tier} · "
        f"Type: {t.get('execution_type') or EXEC_DETERMINISTIC} · "
        f"LLM: {'Yes' if t.get('llm_invoked') else 'No'}",
        f"Provider: {t.get('provider') or 'None'} · Model: {t.get('model') or 'None'}",
        f"Tokens in/out/cached/total: "
        f"{t.get('input_tokens') if t.get('input_tokens') is not None else 0}/"
        f"{t.get('output_tokens') if t.get('output_tokens') is not None else 0}/"
        f"{t.get('cached_tokens') if t.get('cached_tokens') is not None else 0}/"
        f"{total_s}{src_note}",
        f"Tools: {', '.join(t.get('tools_used') or []) or 'None'} · "
        f"Runtime: {t.get('runtime_ms') or 0} ms · "
        f"Child run: {t.get('child_ai_run_id') or 'None'}",
    ]
    if t.get("t0_failure_reason") or t.get("next_capability") or t.get("db_query_attempted") or t.get(
        "ai_escalation_occurred"
    ):
        lines.append(
            f"T0 failure: {t.get('t0_failure_reason') or 'None'} · "
            f"Next: {t.get('next_capability') or 'None'} · "
            f"DB query: {'Yes' if t.get('db_query_attempted') else 'No'} · "
            f"AI escalate: {'Yes' if t.get('ai_escalation_occurred') else 'No'}"
        )
    if t.get("routing_mode") or t.get("session_reused") or t.get("context_items") or t.get("context_chars") is not None:
        items = ", ".join(list(t.get("context_items") or [])[:6]) or "None"
        chars = t.get("context_chars")
        lines.append(
            f"Mode: {t.get('routing_mode') or 'smart'} · "
            f"Session reused: {'Yes' if t.get('session_reused') else 'No'} · "
            f"Context items: {items}"
            + (f" · Context chars: {chars}" if chars is not None else "")
        )
    ri = t.get("repository_intelligence") if isinstance(t.get("repository_intelligence"), dict) else {}
    if ri:
        repos = ", ".join(str(value) for value in (ri.get("repository_ids") or [])) or "None"
        lines.append(
            f"Repository Intelligence used: {'Yes' if ri.get('used') else 'No'} Â· "
            f"Repository: {repos} Â· Entries: {ri.get('knowledge_entries_used') or 0} Â· "
            f"Freshness: {ri.get('freshness') or 'not_learned'} Â· "
            f"Context chars: {ri.get('context_chars_contributed') or 0}"
        )
    return "\n".join(lines)


def public_telemetry(telemetry: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(telemetry, dict):
        return {}
    return {
        "routing_tier": telemetry.get("routing_tier"),
        "execution_type": telemetry.get("execution_type"),
        "llm_invoked": bool(telemetry.get("llm_invoked")),
        "provider": telemetry.get("provider"),
        "model": telemetry.get("model"),
        "input_tokens": telemetry.get("input_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "cached_tokens": telemetry.get("cached_tokens"),
        "total_ai_tokens": telemetry.get("total_ai_tokens"),
        "usage_source": telemetry.get("usage_source"),
        "tools_used": list(telemetry.get("tools_used") or []),
        "runtime_ms": telemetry.get("runtime_ms") or 0,
        "child_ai_run_id": telemetry.get("child_ai_run_id"),
        "t0_pure": bool(telemetry.get("t0_pure")),
        "t0_failure_reason": telemetry.get("t0_failure_reason") or None,
        "next_capability": telemetry.get("next_capability") or None,
        "db_query_attempted": bool(telemetry.get("db_query_attempted")),
        "ai_escalation_occurred": bool(telemetry.get("ai_escalation_occurred")),
        "routing_mode": telemetry.get("routing_mode") or None,
        "session_reused": bool(telemetry.get("session_reused")),
        "context_items": list(telemetry.get("context_items") or []),
        "context_chars": telemetry.get("context_chars"),
        "repository_intelligence": dict(telemetry.get("repository_intelligence") or {}),
    }


def attach_execution_telemetry(row: dict[str, Any]) -> dict[str, Any]:
    """Compute and stamp usage telemetry onto an execution row (mutates + returns)."""
    runtime_ms = _runtime_ms_from_iso(row.get("started_at"), row.get("finished_at"))
    enriched = {**row, "runtime_ms": runtime_ms if runtime_ms or row.get("runtime_ms") is None else row.get("runtime_ms")}
    tel = build_execution_telemetry(enriched)

    # Hybrid only when deterministic tools ran first AND an AI child run started.
    if (
        str(row.get("fallback_from") or "").lower() in {"deterministic", "t0"}
        and tel.get("llm_invoked")
        and tel.get("child_ai_run_id")
    ):
        tel["execution_type"] = EXEC_HYBRID
        tel["t0_pure"] = False

    row["telemetry"] = public_telemetry(tel)
    usage = dict(row.get("usage") or {})
    # Drop stale AI-ish usage fields when this execution is pure T0.
    if tel.get("t0_pure") or not tel.get("llm_invoked"):
        usage = {}
    usage.update(
        {
            "input_tokens": tel.get("input_tokens"),
            "output_tokens": tel.get("output_tokens"),
            "cached_tokens": tel.get("cached_tokens"),
            "total_tokens": tel.get("total_ai_tokens"),
            "usage_source": tel.get("usage_source"),
            "execution_type": tel.get("execution_type"),
            "llm_invoked": tel.get("llm_invoked"),
            "routing_tier": tel.get("routing_tier"),
            "child_ai_run_id": tel.get("child_ai_run_id"),
            "tools_used": list(tel.get("tools_used") or []),
            "provider": tel.get("provider"),
            "model": tel.get("model"),
            "runtime_ms": tel.get("runtime_ms"),
        }
    )
    row["usage"] = usage
    execution_type = str(tel.get("execution_type") or "")
    row["execution_summary"] = {
        "interaction_mode": str(
            row.get("interaction_mode")
            or (row.get("context") or {}).get("interaction_mode")
            or ("agent" if row.get("routing_mode") == "direct" else "smart")
        ),
        "resolved_provider": row.get("resolved_provider") or row.get("adapter_id") or row.get("provider_id"),
        "resolved_model": row.get("resolved_model") or row.get("model"),
        "t0_used": execution_type in {EXEC_DETERMINISTIC, EXEC_HYBRID},
        "llm_used": bool(tel.get("llm_invoked")),
        "tokens": int(tel.get("total_ai_tokens") or 0),
        "tools": list(tel.get("tools_used") or []),
        "task_solved": (row.get("grounding") or {}).get("task_solved"),
        "grounded": (row.get("grounding") or {}).get("grounded"),
    }
    return row
