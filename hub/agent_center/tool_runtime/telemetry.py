"""Per-run Tool Runtime intelligence telemetry (Phase 2)."""

from __future__ import annotations

from typing import Any


def build_runtime_telemetry(
    *,
    steps: list[Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    context_chars: int = 0,
    usage: dict[str, Any] | None = None,
    repository_intelligence: dict[str, Any] | None = None,
    session_reused: bool = False,
    retries: int = 0,
    provider: str = "",
    model: str = "",
    runtime_ms: float = 0.0,
    grounding: dict[str, Any] | None = None,
    stop_reason: str = "",
    active_tools: list[str] | None = None,
    continuation_used: bool = False,
) -> dict[str, Any]:
    step_rows = list(steps or [])
    tool_calls = 0
    for step in step_rows:
        tool = ""
        if hasattr(step, "tool"):
            tool = str(getattr(step, "tool") or "")
        elif isinstance(step, dict):
            tool = str(step.get("tool") or "")
        if tool and tool not in {"", "(final_answer)"}:
            tool_calls += 1
    if not tool_calls and tool_results:
        tool_calls = len(tool_results)

    ri = repository_intelligence if isinstance(repository_intelligence, dict) else {}
    diag = ri.get("diagnostics") if isinstance(ri.get("diagnostics"), dict) else {}
    ri_entries = int(
        diag.get("knowledge_entries_used")
        if diag.get("knowledge_entries_used") is not None
        else len(ri.get("items") or [])
    )
    usage = usage if isinstance(usage, dict) else {}
    grounding = grounding if isinstance(grounding, dict) else {}

    return {
        "steps": len(step_rows),
        "tool_calls": tool_calls,
        "context_chars": int(context_chars or 0),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "tokens": usage.get("total_tokens"),
        "ri_entries_used": ri_entries,
        "session_reused": bool(session_reused),
        "retries": int(retries or 0),
        "provider": provider or None,
        "model": model or None,
        "runtime_ms": round(float(runtime_ms or 0.0), 2),
        "task_solved": bool(grounding.get("task_solved")),
        "grounded": bool(grounding.get("answer_grounded")),
        "stop_reason": stop_reason or "",
        "active_tools": list(active_tools or []),
        "continuation_used": bool(continuation_used),
        "usage_source": usage.get("usage_source") or "actual",
    }
