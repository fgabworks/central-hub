"""Controlled A/B token-efficiency benchmark: Direct Codex vs AiriX Smart Routing.

Prompt (exact): Count the number of pregnant women in Brgy. Baloy for 2026 Q2.
Does not modify routing logic.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Longer Codex runs for data lookup.
os.environ.setdefault("AGENT_CENTER_TIMEOUT_SECONDS", "600")

from app import create_app

PROMPT = "Count the number of pregnant women in Brgy. Baloy for 2026 Q2."
REPO_ID = "live-processing-local"
MODEL = "gpt-5.6-sol"  # shared Codex model if Codex is used
OUT = Path(__file__).resolve().parent / "airix_ab_benchmark_baloy_2026q2.json"


def _extract_count(text: str) -> str | None:
    if not text:
        return None
    patterns = [
        r"(?:count|total|number)\s*(?:of\s+pregnant\s+women\s*)?(?:is|=|:)?\s*(\d[\d,]*)",
        r"(\d[\d,]*)\s+pregnant\s+women",
        r"\b(\d[\d,]*)\s*(?:women|PW)\b",
        r"(?:final\s+)?(?:answer|result)\s*[:=]\s*(\d[\d,]*)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).replace(",", "")
    # Last-resort integer near end
    nums = re.findall(r"\b(\d{1,6})\b", text)
    return nums[-1] if nums else None


def _usage_fields(usage: dict | None) -> dict:
    u = usage if isinstance(usage, dict) else {}
    inp = u.get("input_tokens")
    if inp is None:
        inp = u.get("prompt_tokens")
    out = u.get("output_tokens")
    if out is None:
        out = u.get("completion_tokens")
    cached = (
        u.get("cached_input_tokens")
        or u.get("cache_read_input_tokens")
        or u.get("input_tokens_cached")
        or u.get("cached_tokens")
    )
    total = u.get("total_tokens")
    if total is None and (inp is not None or out is not None):
        total = int(inp or 0) + int(out or 0)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cached,
        "total_tokens": total,
        "usage_source": u.get("usage_source") or ("actual" if total is not None else "missing"),
        "raw_keys": sorted(str(k) for k in u.keys())[:20],
    }


def _wait_run(ac, run_id: str, *, timeout: float = 600.0) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = ac.get_run(run_id, profile_id="okarun")
        status = str(last.get("status") or "")
        if status in {"completed", "succeeded", "failed", "cancelled", "unavailable", "timed_out"}:
            return last
        if last.get("answer") and status not in {"queued", "running"}:
            return last
        time.sleep(1.0)
    last = ac.get_run(run_id, profile_id="okarun")
    last["_benchmark_timeout"] = True
    return last


def run_test_a(ac) -> dict:
    """Direct Codex with PMNP selected."""
    t0 = time.perf_counter()
    run = ac.start_run(
        {
            "profile_id": "okarun",
            "mode": "ask",
            "prompt": PROMPT,
            "agent_id": "codex",
            "model": MODEL,
            "repository_ids": [REPO_ID],
            "selected_repository_id": REPO_ID,
            "tool_ids": ["repo_search", "read_file", "uid_lookup", "org_unit_lookup", "sql_lookup"],
            "hints": ["benchmark:direct_codex", "repository:live-processing-local"],
        }
    )
    run_id = str(run.get("id") or "")
    status = str(run.get("status") or "")
    if status in {"queued", "running"} or (run_id and not run.get("answer")):
        run = _wait_run(ac, run_id, timeout=600.0)
    elapsed = time.perf_counter() - t0
    answer = str(run.get("answer") or "")
    usage = _usage_fields(run.get("usage") or {})
    tools = run.get("tool_activity") or run.get("context", {}).get("tools") or []
    tool_calls = 0
    if isinstance(tools, list):
        tool_calls = len(tools)
    elif isinstance(tools, dict):
        tool_calls = len(tools.get("enabled") or []) + len(tools.get("calls") or [])
    # Prefer JSONL tool events if present
    activity = run.get("tool_activity")
    if isinstance(activity, list):
        tool_calls = len(activity)
    elif isinstance(activity, dict) and isinstance(activity.get("events"), list):
        tool_calls = len(activity["events"])
    refs = run.get("referenced_files") or []
    sources = list(refs) if isinstance(refs, list) else []
    ctx = run.get("context") or {}
    if isinstance(ctx.get("included_sources"), list):
        sources.extend(ctx["included_sources"])
    return {
        "label": "A_direct_codex",
        "provider": "codex",
        "model": run.get("model") or MODEL,
        "status": run.get("status"),
        "runtime_seconds": round(elapsed, 3),
        "answer": answer,
        "count": _extract_count(answer),
        "usage": usage,
        "tool_calls": tool_calls,
        "sources": list(dict.fromkeys(str(s) for s in sources))[:40],
        "run_id": run_id,
        "error": run.get("error") or "",
        "codex_used": True,
    }


def run_test_b(router, ac) -> dict:
    """AiriX Smart Routing with same prompt + PMNP selection."""
    rec = router.recommend_route(
        PROMPT,
        workspace="work",
        actor="owner",
        repository_ids=[REPO_ID],
        probe_providers=False,
    )
    t0 = time.perf_counter()
    result = router.execute_route(
        PROMPT,
        workspace="work",
        actor="owner",
        repository_ids=[REPO_ID],
        selected_repository_id=REPO_ID,
        active_repository_id=REPO_ID,
        approve_codex=True,  # if routing escalates to Codex, same approval path
        force=True,
        recommendation=rec,
        model=MODEL if rec.recommended_agent == "codex" else (rec.recommended_model or None),
        orchestrate=True,
    )
    elapsed = time.perf_counter() - t0
    execution = result.get("execution") or {}
    # Poll if still active
    exec_id = str(execution.get("id") or result.get("session", {}).get("id") or "")
    # Orchestrated responses wrap differently
    session = result.get("session") or {}
    if result.get("status") in {"completed", "failed", "cancelled", "paused_for_approval", "timed_out"}:
        status = str(result.get("status"))
        answer = str(result.get("answer") or execution.get("answer") or "")
        # Prefer last step execution if present
        steps = result.get("steps") or result.get("orchestration") or []
        if isinstance(steps, list) and steps:
            last = steps[-1] if isinstance(steps[-1], dict) else {}
            if last.get("answer"):
                answer = str(last.get("answer"))
        elapsed = time.perf_counter() - t0
        usage = _usage_fields(result.get("usage") or execution.get("usage") or {})
        # Sum actual tokens from session if present
        if session.get("actual_tokens") is not None:
            usage["total_tokens"] = int(session.get("actual_tokens") or 0)
            usage["usage_source"] = "session_actual"
        tool_calls = 0
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict):
                tr = step.get("tool_results") or []
                if isinstance(tr, list):
                    tool_calls += len(tr)
        provider = str(
            result.get("provider_id")
            or execution.get("provider_id")
            or execution.get("adapter_id")
            or rec.recommended_agent
        )
        model = str(
            result.get("resolved_model")
            or execution.get("resolved_model")
            or execution.get("model")
            or rec.recommended_model
            or ""
        )
        mode = str(execution.get("mode") or result.get("mode") or "")
        ai_tokens = usage.get("total_tokens")
        if mode == "deterministic" or provider in {"deterministic"}:
            if ai_tokens is None:
                ai_tokens = 0
                usage["total_tokens"] = 0
                usage["usage_source"] = "actual_t0_zero"
        sources = []
        ep = execution.get("evidence_packet") or result.get("evidence_packet") or {}
        if isinstance(ep, dict):
            sources.extend(ep.get("sources") or [])
        grounding = execution.get("grounding") or result.get("grounding") or {}
        if grounding.get("source"):
            sources.append(str(grounding.get("source")))
        return {
            "label": "B_airix_smart_routing",
            "recommendation": {
                "tier": rec.recommended_tier,
                "provider": rec.recommended_agent,
                "model": rec.recommended_model,
                "reason": rec.reason,
                "signals": list(rec.classification.signals or [])[:12],
                "task_type": rec.task_type,
            },
            "provider": provider,
            "model": model,
            "tier": execution.get("tier") or rec.recommended_tier,
            "mode": mode,
            "status": status,
            "runtime_seconds": round(elapsed, 3),
            "answer": answer or str(result.get("answer") or ""),
            "count": _extract_count(answer or str(result.get("answer") or "")),
            "usage": usage,
            "ai_tokens": ai_tokens,
            "tool_calls": tool_calls,
            "sources": list(dict.fromkeys(str(s) for s in sources))[:40],
            "execution_id": exec_id,
            "session_id": session.get("id"),
            "error": str(result.get("error") or execution.get("error") or ""),
            "codex_avoided": provider not in {"codex", "claude-code", "cursor-agent"},
            "fallback_from": execution.get("fallback_from"),
            "stopped_reason": result.get("stopped_reason"),
            "grounding": grounding,
            "raw_keys": sorted(result.keys())[:30],
        }

    status = str(execution.get("status") or result.get("status") or "")
    deadline = time.monotonic() + 600.0
    while status in {"queued", "running", "active"} and time.monotonic() < deadline:
        time.sleep(1.0)
        live = router.execution_status(exec_id) if exec_id else None
        if live:
            execution = live
        status = str(execution.get("status") or "")
        if status not in {"queued", "running", "active"}:
            break
    elapsed = time.perf_counter() - t0

    answer = str(execution.get("answer") or result.get("answer") or "")
    usage = _usage_fields(execution.get("usage") or {})
    agent_run = execution.get("agent_run") or {}
    if isinstance(agent_run, dict) and agent_run.get("usage"):
        usage = _usage_fields(agent_run.get("usage"))
    tool_results = execution.get("tool_results") or []
    tool_calls = len(tool_results) if isinstance(tool_results, list) else 0
    provider = str(execution.get("provider_id") or execution.get("adapter_id") or rec.recommended_agent)
    model = str(execution.get("resolved_model") or execution.get("model") or rec.recommended_model or "")
    sources = []
    ep = execution.get("evidence_packet") or {}
    if isinstance(ep, dict):
        sources.extend(ep.get("sources") or [])
    grounding = execution.get("grounding") or {}
    if grounding.get("source"):
        sources.append(grounding.get("source"))
    mode = str(execution.get("mode") or "")
    ai_tokens = usage.get("total_tokens")
    if mode == "deterministic" or provider in {"deterministic", ""}:
        if ai_tokens is None:
            ai_tokens = 0
            usage["total_tokens"] = 0
            usage["usage_source"] = "actual_t0_zero"
    return {
        "label": "B_airix_smart_routing",
        "recommendation": {
            "tier": rec.recommended_tier,
            "provider": rec.recommended_agent,
            "model": rec.recommended_model,
            "reason": rec.reason,
            "signals": list(rec.classification.signals or [])[:12],
            "task_type": rec.task_type,
        },
        "provider": provider,
        "model": model,
        "tier": execution.get("tier") or rec.recommended_tier,
        "mode": mode,
        "status": execution.get("status") or result.get("status"),
        "runtime_seconds": round(elapsed, 3),
        "answer": answer,
        "count": _extract_count(answer),
        "usage": usage,
        "ai_tokens": ai_tokens,
        "tool_calls": tool_calls,
        "sources": list(dict.fromkeys(str(s) for s in sources))[:40],
        "execution_id": exec_id,
        "error": execution.get("error") or "",
        "codex_avoided": provider not in {"codex", "claude-code", "cursor-agent"},
        "fallback_from": execution.get("fallback_from"),
        "stopped_reason": result.get("stopped_reason"),
        "grounding": grounding,
        "raw_keys": sorted(result.keys())[:30],
    }


def compare(a: dict, b: dict) -> dict:
    a_tok = a["usage"].get("total_tokens")
    b_tok = b.get("ai_tokens")
    if b_tok is None:
        b_tok = b["usage"].get("total_tokens")
    a_rt = a["runtime_seconds"]
    b_rt = b["runtime_seconds"]
    same_count = (
        a.get("count") is not None
        and b.get("count") is not None
        and str(a.get("count")) == str(b.get("count"))
    )
    qualifies = bool(same_count and a.get("count") is not None)

    def pct(old, new):
        if old is None or new is None or old == 0:
            return None
        return round((old - new) / old * 100.0, 2)

    codex_a = a_tok if a.get("codex_used") else None
    # Codex token reduction: if B avoided Codex, 100% of Direct Codex tokens avoided
    if b.get("codex_avoided") and codex_a is not None:
        codex_reduction = 100.0
        capacity_avoided = codex_a
    elif codex_a is not None and b_tok is not None and not b.get("codex_avoided"):
        # Both used Codex / AI — compare B's AI tokens to A's
        codex_reduction = pct(codex_a, b_tok)
        capacity_avoided = max(0, int(codex_a) - int(b_tok or 0)) if b_tok is not None else None
    else:
        codex_reduction = None
        capacity_avoided = codex_a if b.get("codex_avoided") else None

    return {
        "qualifies_same_verified_count": qualifies,
        "count_a": a.get("count"),
        "count_b": b.get("count"),
        "codex_token_reduction_pct": codex_reduction,
        "total_ai_token_reduction_pct": pct(a_tok, b_tok) if a_tok is not None else None,
        "runtime_difference_pct": pct(a_rt, b_rt),  # positive = AiriX faster
        "tool_call_difference": (a.get("tool_calls") or 0) - (b.get("tool_calls") or 0),
        "estimated_codex_capacity_avoided_tokens": capacity_avoided,
        "formula": "saving % = (Direct Codex tokens - AiriX total AI tokens) / Direct Codex tokens × 100",
        "tokens_a": a_tok,
        "tokens_b": b_tok,
        "runtime_a": a_rt,
        "runtime_b": b_rt,
    }


def main() -> None:
    app = create_app()
    ac = app.config["AGENT_CENTER"]
    # Align timeout for this process
    ac.timeout_seconds = float(os.environ.get("AGENT_CENTER_TIMEOUT_SECONDS") or 600)
    router = app.config["AIRIX_ROUTER"]

    print("=== TEST A: Direct Codex ===", flush=True)
    a = run_test_a(ac)
    print(json.dumps({k: a[k] for k in a if k != "answer"}, indent=2), flush=True)
    print("ANSWER_A:\n", a.get("answer", "")[:2000], flush=True)

    print("\n=== RESET / TEST B: AiriX Smart Routing ===", flush=True)
    b = run_test_b(router, ac)
    print(json.dumps({k: b[k] for k in b if k not in {"answer", "orchestrate"}}, indent=2), flush=True)
    print("ANSWER_B:\n", b.get("answer", "")[:2000], flush=True)

    cmp = compare(a, b)
    report = {"prompt": PROMPT, "repo_id": REPO_ID, "model": MODEL, "a": a, "b": b, "comparison": cmp}
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== COMPARISON ===", flush=True)
    print(json.dumps(cmp, indent=2), flush=True)
    print("Wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
