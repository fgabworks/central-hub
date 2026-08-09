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
    tools: list[str] = []
    for item in row.get("tool_results") or []:
        if isinstance(item, dict) and item.get("tool"):
            tools.append(str(item.get("tool")))
    ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
    for tid in ctx.get("tool_ids") or []:
        tools.append(str(tid))
    # Preserve order, unique.
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


def empty_t0_telemetry(
    *,
    tier: str = "T0",
    tools_used: list[str] | None = None,
    runtime_ms: int = 0,
) -> dict[str, Any]:
    """Canonical pure-T0 telemetry (all AI fields zero / None)."""
    return {
        "routing_tier": tier or "T0",
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
    }


def build_execution_telemetry(row: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive telemetry from an execution row / public fields dict.

    Uses mode, adapter_id, agent_run_id, usage, and tool_results — not UI labels.
    """
    row = dict(row or {})
    mode = str(row.get("mode") or "").strip().lower()
    tier = str(row.get("tier") or "").strip() or "T?"
    provider_id = str(row.get("provider_id") or "").strip()
    adapter_id = str(row.get("adapter_id") or "").strip()
    fallback_from = str(row.get("fallback_from") or "").strip()
    child_id = str(row.get("agent_run_id") or "").strip() or None
    if not child_id:
        agent_run = row.get("agent_run") if isinstance(row.get("agent_run"), dict) else {}
        child_id = str(agent_run.get("id") or "").strip() or None

    model = ""
    if isinstance(row.get("agent_run"), dict):
        model = str(row["agent_run"].get("model") or "").strip()
    if not model:
        model = str(row.get("resolved_model") or row.get("model") or "").strip()
    model = model or None

    tools = _tools_from_row(row)
    runtime_ms = row.get("runtime_ms")
    if runtime_ms is None:
        runtime_ms = _runtime_ms_from_iso(row.get("started_at"), row.get("finished_at"))
    runtime_ms = _int0(runtime_ms)

    # Detect AI invocation from actual fields — not from recommended_tier alone.
    llm_adapter = is_ai_provider(adapter_id) or is_ai_provider(provider_id)
    started_ai = bool(child_id) or (llm_adapter and mode not in {"deterministic", "grounding_gate"})
    hybrid = bool(
        fallback_from in {"deterministic", "t0"}
        and (started_ai or llm_adapter)
    ) or (mode == "orchestrated" and started_ai and "deterministic" in (fallback_from or ""))

    # Pure T0 / grounding gate: no child run, no AI adapter execution.
    pure_t0 = (
        mode in {"deterministic", "grounding_gate"}
        and not child_id
        and not started_ai
        and not hybrid
        and not (llm_adapter and mode not in {"deterministic", "grounding_gate"})
    )
    # Also treat completed deterministic without adapter as T0 even if provider_id says deterministic.
    if mode in {"deterministic", "grounding_gate"} and not child_id and not is_ai_provider(adapter_id):
        pure_t0 = True
        # If somehow an AI provider_id leaked on a deterministic row, still force T0 purity.
        if is_ai_provider(provider_id) and not adapter_id:
            # provider_id alone on deterministic without adapter/child is routing label noise.
            pass

    if pure_t0 and not hybrid:
        tel = empty_t0_telemetry(
            tier=tier if tier.startswith("T") else "T0",
            tools_used=tools,
            runtime_ms=runtime_ms,
        )
        # Enforce zeros even if usage leaked onto the row.
        return enforce_t0_telemetry(tel)

    # AI or Hybrid path — read provider-reported usage when present.
    usage = {}
    if isinstance(row.get("usage"), dict) and row.get("usage"):
        usage = row["usage"]
    agent_run = row.get("agent_run") if isinstance(row.get("agent_run"), dict) else {}
    if not usage and isinstance(agent_run.get("usage"), dict):
        usage = agent_run["usage"]

    parsed = parse_usage(usage)
    cached = parsed.get("cached_tokens")
    if cached is None:
        cached = 0 if parsed.get("usage_source") == "actual" else None

    usage_source = str(parsed.get("usage_source") or "estimate")
    # If AI ran but no usage reported, mark estimated explicitly (do not invent totals).
    if started_ai and parsed.get("total_tokens") is None:
        usage_source = "estimate"

    display_provider = adapter_id or provider_id or None
    if display_provider in {"deterministic", ""}:
        display_provider = adapter_id if is_ai_provider(adapter_id) else None

    exec_type = EXEC_HYBRID if hybrid else EXEC_AI
    # Orchestrated multi-step with AI still AI/Hybrid based on fallback.
    if mode == "orchestrated" and started_ai and not hybrid:
        exec_type = EXEC_AI

    return {
        "routing_tier": tier,
        "execution_type": exec_type,
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
    if not str(out.get("routing_tier") or "").startswith("T"):
        out["routing_tier"] = "T0"
    return out


def assert_t0_telemetry_pure(telemetry: dict[str, Any]) -> None:
    """Raise AssertionError if a claimed T0/Deterministic telemetry is impure."""
    if telemetry.get("llm_invoked"):
        raise AssertionError("T0 telemetry must not set llm_invoked=True")
    if telemetry.get("provider") not in (None, "", "deterministic"):
        # Display provider must be None for pure T0.
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
    lines = [
        f"Tier: {t.get('routing_tier') or '?'} · "
        f"Type: {t.get('execution_type') or '?'} · "
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
    }


def attach_execution_telemetry(row: dict[str, Any]) -> dict[str, Any]:
    """Compute and stamp usage telemetry onto an execution row (mutates + returns)."""
    mode = str(row.get("mode") or "").strip().lower()
    runtime_ms = _runtime_ms_from_iso(row.get("started_at"), row.get("finished_at"))
    tools_preview = [
        str(t.get("tool"))
        for t in (row.get("tool_results") or [])
        if isinstance(t, dict) and t.get("tool")
    ]

    if mode in {"deterministic", "grounding_gate"} and not row.get("agent_run_id"):
        if row.get("t0_fallthrough"):
            tel = empty_t0_telemetry(
                tier=str(row.get("tier") or "T0"),
                tools_used=tools_preview,
                runtime_ms=runtime_ms,
            )
        else:
            tel = enforce_t0_telemetry(
                build_execution_telemetry({**row, "runtime_ms": runtime_ms})
            )
    else:
        enriched = {**row, "runtime_ms": runtime_ms}
        if str(row.get("fallback_from") or "") in {"deterministic", "t0"}:
            enriched["tier"] = row.get("tier") or enriched.get("tier") or "T1"
        tel = build_execution_telemetry(enriched)
        if str(row.get("fallback_from") or "") in {"deterministic", "t0"} and tel.get("llm_invoked"):
            tel["execution_type"] = EXEC_HYBRID
            tel["t0_pure"] = False

    row["telemetry"] = public_telemetry(tel)
    usage = dict(row.get("usage") or {})
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
    return row
