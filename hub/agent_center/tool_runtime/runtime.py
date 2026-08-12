"""Iterative provider-neutral Tool Runtime loop (Phase 2)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from hub.agent_center.completion import (
    CompletionContract,
    derive_completion_contract,
    validate_completion,
)
from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.tool_runtime.continuation import RuntimeContinuation
from hub.agent_center.tool_runtime.executor import UnifiedToolExecutor
from hub.agent_center.tool_runtime.feed import GLOBAL_TOOL_RUNTIME_FEED, ToolRuntimeFeed
from hub.agent_center.tool_runtime.policy import (
    active_tool_names,
    policy_gate,
    select_active_tools,
)
from hub.agent_center.tool_runtime.prune import (
    cap_observation,
    estimate_context_chars,
    prune_observations,
)
from hub.agent_center.tool_runtime.results import RuntimeOutcome, ToolStepRecord
from hub.agent_center.tool_runtime.settings import ToolRuntimeSettings, load_tool_runtime_settings
from hub.agent_center.tool_runtime.specs import ToolSpec, get_tool_spec
from hub.agent_center.tool_runtime.stuck import StuckGuard
from hub.agent_center.tool_runtime.telemetry import build_runtime_telemetry

CancelCheck = Callable[[], bool]


class ModelDriver(Protocol):
    """Provider-neutral model step: request tools or return a final answer."""

    def step(
        self,
        *,
        prompt: str,
        observations: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str,
        model: str,
        provider: str,
    ) -> dict[str, Any]:
        """
        Return one of:
          {"kind": "tool_request", "tool": name, "arguments": {...}, "usage": {...}}
          {"kind": "final_answer", "answer": "...", "usage": {...}}
          {"kind": "error", "error": "..."}
        """
        ...


@dataclass
class RuntimeContext:
    prompt: str
    tools_ctx: AgentToolsContext
    interaction_mode: str = "smart"
    provider: str = ""
    model: str = ""
    run_id: str = ""
    classification: Any | None = None
    context_sources: list[str] = field(default_factory=list)
    profile_allowed: set[str] = field(default_factory=set)
    permissions: set[str] = field(default_factory=set)
    evidence_packet: dict[str, Any] = field(default_factory=dict)
    cancel_check: CancelCheck | None = None
    continuation: RuntimeContinuation | None = None
    repository_intelligence: dict[str, Any] = field(default_factory=dict)
    session_reused: bool = False
    conversation_id: str = ""
    context_fingerprint: str = ""


class ToolRuntime:
    """model → tool request → policy → execute → observation → repeat."""

    def __init__(
        self,
        *,
        executor: UnifiedToolExecutor | None = None,
        settings: ToolRuntimeSettings | None = None,
        feed: ToolRuntimeFeed | None = None,
    ) -> None:
        self.settings = settings or load_tool_runtime_settings()
        self.executor = executor or UnifiedToolExecutor(
            max_observation_chars=self.settings.max_observation_chars
        )
        self.feed = feed or GLOBAL_TOOL_RUNTIME_FEED

    def run(self, driver: ModelDriver, ctx: RuntimeContext) -> RuntimeOutcome:
        settings = self.settings
        started_at = time.monotonic()
        provider = str(ctx.provider or "").strip()
        model = str(ctx.model or "").strip()
        if not provider or not model:
            return RuntimeOutcome(
                status="failed",
                answer="",
                provider=provider,
                model=model,
                stop_reason="provider_or_model_required",
                error="Tool Runtime requires an exact provider and model (no silent fallback).",
            )

        continuation_used = False
        cont = ctx.continuation
        ri_state: dict[str, Any] = dict(ctx.repository_intelligence or {})
        observations: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        if cont is not None:
            continuation_used = True
            observations = list(cont.observations or [])
            tool_results = list(cont.tool_results or [])
            if cont.evidence_packet:
                ctx.evidence_packet = dict(cont.evidence_packet)
            if cont.repository_intelligence:
                ri_state = _merge_ri(ri_state, cont.repository_intelligence)
                ctx.repository_intelligence = ri_state
            if not ctx.context_fingerprint and cont.context_fingerprint:
                ctx.context_fingerprint = str(cont.context_fingerprint)

        contract = _contract_from_continuation(cont, ctx.prompt)
        if ctx.run_id:
            self.feed.reset(ctx.run_id)

        stuck = StuckGuard(
            duplicate_limit=settings.stuck_duplicate_limit,
            max_recoveries=settings.stuck_max_recoveries,
        )
        steps: list[ToolStepRecord] = []
        usage_acc: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        deadline = time.monotonic() + float(settings.timeout_seconds)
        max_steps = min(int(settings.max_steps), int(settings.hard_runaway_cap))
        hard_cap = int(settings.hard_runaway_cap)

        prior_tool_names: list[str] = [
            str(r.get("tool") or "").strip()
            for r in tool_results
            if str(r.get("tool") or "").strip()
        ]
        active, active_names = self._select_active(ctx, settings, contract, ri_state, prior_tool_names)
        system = _system_prompt(ctx.interaction_mode, active)
        required_tools = _required_tools(contract, ctx.evidence_packet, ri_state)

        answer = ""
        stop_reason = ""
        status = "completed"
        retries = 0
        grounding_nudge_sent = False
        last_context_chars = 0

        for step_idx in range(1, hard_cap + 1):
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                stop_reason = "cancelled"
                break
            if time.monotonic() > deadline:
                status = "timed_out"
                stop_reason = "timeout"
                break
            if step_idx > max_steps:
                status = "max_steps"
                stop_reason = "max_steps"
                break

            prior_tool_names = [
                str(r.get("tool") or "").strip()
                for r in tool_results
                if str(r.get("tool") or "").strip()
            ]
            active, active_names = self._select_active(
                ctx, settings, contract, ri_state, prior_tool_names
            )
            system = _system_prompt(ctx.interaction_mode, active)
            required_tools = _required_tools(contract, ctx.evidence_packet, ri_state)

            pruned = prune_observations(
                observations,
                keep=settings.max_kept_observations,
                max_chars=settings.max_observation_chars,
                preserve_grounded=True,
                required_tools=required_tools,
            )
            last_context_chars = estimate_context_chars(
                system=system,
                prompt=ctx.prompt,
                observations=pruned,
                tools=active,
            )

            try:
                decision = driver.step(
                    prompt=ctx.prompt,
                    observations=pruned,
                    tools=active,
                    system=system,
                    model=model,
                    provider=provider,
                )
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool="",
                    ok=False,
                    summary="model_error",
                    duration_ms=0,
                    result="error",
                    context_chars=last_context_chars,
                    error=err[:240],
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)
                return self._finish(
                    status="failed",
                    answer="",
                    steps=steps,
                    tool_results=tool_results,
                    ctx=ctx,
                    provider=provider,
                    model=model,
                    stop_reason="model_error",
                    usage_acc=usage_acc,
                    error=err,
                    retries=retries,
                    active_names=active_names,
                    ri_state=ri_state,
                    context_chars=last_context_chars,
                    started_at=started_at,
                    continuation_used=continuation_used,
                )

            if time.monotonic() > deadline:
                status = "timed_out"
                stop_reason = "timeout"
                break
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                stop_reason = "cancelled"
                break

            returned_model = str(decision.get("model") or model).strip()
            if returned_model and returned_model != model:
                return self._finish(
                    status="failed",
                    answer="",
                    steps=steps,
                    tool_results=tool_results,
                    ctx=ctx,
                    provider=provider,
                    model=model,
                    stop_reason="model_mismatch",
                    usage_acc=usage_acc,
                    error=(
                        f"Driver returned model {returned_model!r} but runtime is fixed "
                        f"to {model!r}; refusing silent substitute."
                    ),
                    retries=retries,
                    active_names=active_names,
                    ri_state=ri_state,
                    context_chars=last_context_chars,
                    started_at=started_at,
                    continuation_used=continuation_used,
                )

            _accumulate_usage(usage_acc, decision.get("usage"))

            kind = str(decision.get("kind") or "").strip()
            if kind == "error":
                status = "failed"
                stop_reason = "model_error"
                err = str(decision.get("error") or "model error")
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool="",
                    ok=False,
                    summary="model_error",
                    duration_ms=0,
                    result="error",
                    context_chars=last_context_chars,
                    error=err[:240],
                    **_token_fields(decision.get("usage")),
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)
                break

            if kind == "final_answer":
                answer = str(decision.get("answer") or "").strip()
                completion = validate_completion(
                    contract,
                    prompt=ctx.prompt,
                    answer=answer,
                    evidence=ctx.evidence_packet,
                )
                grounding = {
                    "task_solved": completion.task_solved,
                    "answer_grounded": completion.answer_grounded,
                    "evidence_found": completion.evidence_found,
                    "reason": completion.reason,
                    "completion_intent": completion.intent,
                }
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool="(final_answer)",
                    ok=True,
                    summary="final_answer",
                    duration_ms=0,
                    result="ok",
                    context_chars=last_context_chars,
                    **_token_fields(decision.get("usage")),
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)

                if completion.task_solved and completion.answer_grounded:
                    return self._finish(
                        status="completed",
                        answer=answer,
                        steps=steps,
                        tool_results=tool_results,
                        ctx=ctx,
                        provider=provider,
                        model=model,
                        stop_reason="completion_contract_solved",
                        usage_acc=usage_acc,
                        grounding=grounding,
                        retries=retries,
                        active_names=active_names,
                        ri_state=ri_state,
                        context_chars=last_context_chars,
                        started_at=started_at,
                        continuation_used=continuation_used,
                    )

                if (
                    completion.task_solved
                    and not completion.answer_grounded
                    and not grounding_nudge_sent
                    and step_idx < max_steps
                ):
                    grounding_nudge_sent = True
                    retries += 1
                    observations.append(
                        {
                            "tool": "(grounding_nudge)",
                            "ok": False,
                            "summary": "answer_not_grounded",
                            "observation": json.dumps(
                                {
                                    "message": (
                                        "Final answer is not grounded in authoritative evidence. "
                                        "Use repository_intelligence or skill_recall on demand, "
                                        "then answer again with cited sources."
                                    ),
                                    "suggest_tools": [
                                        "repository_intelligence",
                                        "skill_recall",
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                            "preserve": True,
                        }
                    )
                    active, active_names = _merge_suggested_tools(
                        active,
                        active_names,
                        ["repository_intelligence", "skill_recall"],
                        ctx,
                    )
                    system = _system_prompt(ctx.interaction_mode, active)
                    continue

                return self._finish(
                    status="completed",
                    answer=answer,
                    steps=steps,
                    tool_results=tool_results,
                    ctx=ctx,
                    provider=provider,
                    model=model,
                    stop_reason="final_answer",
                    usage_acc=usage_acc,
                    grounding=grounding,
                    retries=retries,
                    active_names=active_names,
                    ri_state=ri_state,
                    context_chars=last_context_chars,
                    started_at=started_at,
                    continuation_used=continuation_used,
                )

            if kind != "tool_request":
                status = "failed"
                stop_reason = "invalid_model_decision"
                break

            tool_name = str(decision.get("tool") or "").strip()
            tool_args = (
                decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
            )
            guard = stuck.note(tool_name, tool_args)
            if guard.get("recover"):
                retries += 1
                suggest = list(guard.get("suggest_tools") or [])
                observations.append(
                    {
                        "tool": tool_name,
                        "ok": False,
                        "summary": "duplicate_recover",
                        "observation": json.dumps(
                            {
                                "error": "duplicate_tool_call",
                                "recover": True,
                                "suggest_tools": suggest,
                                "message": (
                                    "Duplicate tool call detected. Try a different tool "
                                    "with varied arguments instead of repeating the same call."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        "preserve": True,
                    }
                )
                active, active_names = _merge_suggested_tools(
                    active, active_names, suggest, ctx
                )
                system = _system_prompt(ctx.interaction_mode, active)
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool=tool_name,
                    ok=False,
                    summary="duplicate_recover",
                    duration_ms=0,
                    result="duplicate",
                    context_chars=last_context_chars,
                    error="duplicate_recover",
                    **_token_fields(decision.get("usage")),
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)
                continue

            if guard.get("blocked"):
                status = "stuck"
                stop_reason = "duplicate_tool_call"
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool=tool_name,
                    ok=False,
                    summary="duplicate_tool_call",
                    duration_ms=0,
                    result="duplicate",
                    context_chars=last_context_chars,
                    error="duplicate_tool_call",
                    **_token_fields(decision.get("usage")),
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)
                break

            gate = policy_gate(
                tool_name,
                interaction_mode=ctx.interaction_mode,
                active_names=active_names,
                permissions=ctx.permissions or None,
            )
            if not gate.get("allowed"):
                obs = {
                    "tool": tool_name,
                    "ok": False,
                    "summary": gate.get("reason"),
                    "observation": json.dumps({"error": gate.get("reason")}),
                }
                observations.append(obs)
                step_rec = ToolStepRecord(
                    step=step_idx,
                    provider=provider,
                    model=model,
                    tool=tool_name,
                    ok=False,
                    summary=str(gate.get("reason") or "blocked"),
                    duration_ms=0,
                    result="blocked",
                    context_chars=last_context_chars,
                    error=str(gate.get("reason") or "blocked"),
                    **_token_fields(decision.get("usage")),
                )
                steps.append(step_rec)
                if ctx.run_id:
                    self.feed.append(ctx.run_id, step_rec)
                continue

            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                stop_reason = "cancelled"
                break

            result = self.executor.execute(
                tool_name,
                tool_args,
                ctx.tools_ctx,
                interaction_mode=ctx.interaction_mode,
                active_names=active_names,
                permissions=ctx.permissions or None,
            )
            if tool_name == "repository_intelligence" and result.ok:
                raw = result.raw if isinstance(result.raw, dict) else {}
                ri_state = _merge_ri(ri_state, raw)
                ctx.repository_intelligence = ri_state

            obs_text = cap_observation(
                result.observation, max_chars=settings.max_observation_chars
            )
            observations.append(
                {
                    "tool": tool_name,
                    "ok": result.ok,
                    "summary": result.summary,
                    "observation": obs_text,
                }
            )
            tool_results.append(
                {
                    "tool": tool_name,
                    "ok": result.ok,
                    "summary": result.summary,
                    "source": result.source,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
            )
            packet = dict(ctx.evidence_packet or {})
            sources = list(packet.get("sources") or [])
            sources.append(f"tool:{tool_name}")
            packet["sources"] = list(dict.fromkeys(sources))
            tr = list(packet.get("tool_results") or [])
            tr.append({"tool": tool_name, "ok": result.ok, "summary": result.summary})
            packet["tool_results"] = tr
            if result.ok:
                packet["usable"] = True
            ctx.evidence_packet = packet

            step_rec = ToolStepRecord(
                step=step_idx,
                provider=provider,
                model=model,
                tool=tool_name,
                ok=result.ok,
                summary=result.summary[:160],
                duration_ms=result.duration_ms,
                result="ok" if result.ok else "error",
                context_chars=last_context_chars,
                observation_chars=len(obs_text),
                error=result.error[:240],
                **_token_fields(decision.get("usage")),
            )
            steps.append(step_rec)
            if ctx.run_id:
                self.feed.append(ctx.run_id, step_rec)

            if result.ok and result.raw.get("answer"):
                candidate = str(result.raw.get("answer") or "").strip()
                completion = validate_completion(
                    contract,
                    prompt=ctx.prompt,
                    answer=candidate,
                    evidence=ctx.evidence_packet,
                )
                if completion.task_solved and completion.answer_grounded:
                    return self._finish(
                        status="completed",
                        answer=candidate,
                        steps=steps,
                        tool_results=tool_results,
                        ctx=ctx,
                        provider=provider,
                        model=model,
                        stop_reason="completion_contract_solved",
                        usage_acc=usage_acc,
                        grounding={
                            "task_solved": True,
                            "answer_grounded": True,
                            "evidence_found": True,
                            "reason": completion.reason,
                            "completion_intent": completion.intent,
                        },
                        retries=retries,
                        active_names=active_names,
                        ri_state=ri_state,
                        context_chars=last_context_chars,
                        started_at=started_at,
                        continuation_used=continuation_used,
                    )

        return self._finish(
            status=status,
            answer=answer,
            steps=steps,
            tool_results=tool_results,
            ctx=ctx,
            provider=provider,
            model=model,
            stop_reason=stop_reason or status,
            usage_acc=usage_acc,
            error="" if status in {"completed", "max_steps", "stuck"} else stop_reason,
            retries=retries,
            active_names=active_names,
            ri_state=ri_state,
            context_chars=last_context_chars,
            started_at=started_at,
            continuation_used=continuation_used,
        )

    def _select_active(
        self,
        ctx: RuntimeContext,
        settings: ToolRuntimeSettings,
        contract: CompletionContract,
        ri_state: dict[str, Any],
        prior_tool_names: list[str],
    ) -> tuple[list[ToolSpec], set[str]]:
        active = select_active_tools(
            interaction_mode=ctx.interaction_mode,
            classification=ctx.classification,
            context_sources=ctx.context_sources,
            profile_allowed=ctx.profile_allowed or None,
            permissions=ctx.permissions or None,
            max_tools=settings.max_active_tools,
            prompt=ctx.prompt,
            completion_intent=contract.intent,
            repository_intelligence=ri_state,
            prior_tool_names=prior_tool_names,
        )
        active_names = active_tool_names(active)
        if ctx.tools_ctx.allowed_tools:
            allowed = set(ctx.tools_ctx.allowed_tools) | active_names
            active = [s for s in active if s.name in allowed or s.name in active_names]
            active_names = active_tool_names(active)
            ctx.tools_ctx.allowed_tools = set(active_names)
        return active, active_names

    def _finish(
        self,
        *,
        status: str,
        answer: str,
        steps: list[ToolStepRecord],
        tool_results: list[dict[str, Any]],
        ctx: RuntimeContext,
        provider: str,
        model: str,
        stop_reason: str,
        usage_acc: dict[str, int],
        grounding: dict[str, Any] | None = None,
        error: str = "",
        retries: int,
        active_names: set[str],
        ri_state: dict[str, Any],
        context_chars: int,
        started_at: float,
        continuation_used: bool,
    ) -> RuntimeOutcome:
        if ctx.run_id:
            self.feed.finish(ctx.run_id, status=status)
        usage = _usage_dict(usage_acc)
        telemetry = build_runtime_telemetry(
            steps=steps,
            tool_results=tool_results,
            context_chars=context_chars,
            usage=usage,
            repository_intelligence=ri_state,
            session_reused=bool(ctx.session_reused),
            retries=retries,
            provider=provider,
            model=model,
            runtime_ms=(time.monotonic() - started_at) * 1000.0,
            grounding=grounding or {},
            stop_reason=stop_reason,
            active_tools=sorted(active_names),
            continuation_used=continuation_used,
        )
        return RuntimeOutcome(
            status=status,
            answer=answer,
            steps=steps,
            tool_results=tool_results,
            grounding=grounding or {},
            evidence_packet=ctx.evidence_packet,
            usage=usage,
            provider=provider,
            model=model,
            stop_reason=stop_reason,
            error=error,
            telemetry=telemetry,
            session_reused=bool(ctx.session_reused),
            retries=retries,
            active_tools=sorted(active_names),
        )


def _system_prompt(mode: str, tools: list[ToolSpec]) -> str:
    names = ", ".join(t.name for t in tools) or "(none)"
    return (
        f"You are AiriX Tool Runtime in {mode} mode (read-only). "
        "Use Hub tools to gather evidence. Never invent repository, SQL, or DHIS2 facts. "
        "Call repository_intelligence or skill_recall on demand when you need project "
        "knowledge, architecture context, or instruction/skill markdown. "
        f"Available tools: {names}. "
        "When the task is solved from tool evidence, return a final answer citing sources."
    )


def _contract_from_continuation(
    cont: RuntimeContinuation | None,
    prompt: str,
) -> CompletionContract:
    raw = cont.completion_contract if cont is not None else {}
    if isinstance(raw, dict) and raw.get("intent"):
        return CompletionContract(
            intent=str(raw.get("intent") or ""),
            required_output=str(raw.get("required_output") or ""),
            filters=dict(raw.get("filters") or {}),
            authoritative_sources=tuple(raw.get("authoritative_sources") or ()),
            completion_criteria=tuple(raw.get("completion_criteria") or ()),
            reason=str(raw.get("reason") or ""),
        )
    return derive_completion_contract(prompt)


def _merge_ri(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if not incoming:
        return dict(existing or {})
    merged = dict(existing or {})
    for key in ("profiles", "items", "diagnostics"):
        if incoming.get(key) is not None:
            merged[key] = incoming.get(key)
    return merged


def _required_tools(
    contract: CompletionContract,
    evidence: dict[str, Any] | None,
    ri_state: dict[str, Any],
) -> set[str]:
    required = {"repository_intelligence", "skill_recall"}
    for src in contract.authoritative_sources:
        name = str(src or "").strip()
        if name:
            required.add(name)
    packet = evidence if isinstance(evidence, dict) else {}
    for src in packet.get("sources") or []:
        text = str(src or "")
        if text.startswith("tool:"):
            required.add(text.split(":", 1)[1])
    if ri_state.get("items"):
        required.add("repository_intelligence")
    return required


def _merge_suggested_tools(
    active: list[ToolSpec],
    active_names: set[str],
    suggest: list[str],
    ctx: RuntimeContext,
) -> tuple[list[ToolSpec], set[str]]:
    out = list(active)
    names = set(active_names)
    cap = max(len(out), 8)
    for alt in suggest:
        alt_name = str(alt or "").strip()
        if not alt_name or alt_name in names:
            continue
        spec = get_tool_spec(alt_name)
        if spec is None or not spec.is_read_only:
            continue
        if ctx.profile_allowed and alt_name not in ctx.profile_allowed:
            continue
        out.append(spec)
        names.add(alt_name)
        if len(out) >= cap + 2:
            break
    if ctx.tools_ctx.allowed_tools is not None:
        ctx.tools_ctx.allowed_tools = set(ctx.tools_ctx.allowed_tools) | names
    return out, names


def _accumulate_usage(acc: dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        try:
            acc[key] = int(acc.get(key) or 0) + int(usage.get(key) or 0)
        except (TypeError, ValueError):
            continue


def _usage_dict(acc: dict[str, int]) -> dict[str, Any]:
    return {
        "input_tokens": int(acc.get("input_tokens") or 0),
        "output_tokens": int(acc.get("output_tokens") or 0),
        "total_tokens": int(acc.get("total_tokens") or 0),
        "usage_source": "actual",
    }


def _token_fields(usage: Any) -> dict[str, int | None]:
    if not isinstance(usage, dict):
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    def _n(key: str) -> int | None:
        try:
            if usage.get(key) is None:
                return None
            return int(usage.get(key))
        except (TypeError, ValueError):
            return None

    return {
        "input_tokens": _n("input_tokens"),
        "output_tokens": _n("output_tokens"),
        "total_tokens": _n("total_tokens"),
    }


@dataclass
class ScriptedModelDriver:
    """Deterministic driver for tests — yields queued decisions."""

    decisions: list[dict[str, Any]]
    _idx: int = 0

    def step(self, **_: Any) -> dict[str, Any]:
        if self._idx >= len(self.decisions):
            return {"kind": "final_answer", "answer": ""}
        item = self.decisions[self._idx]
        self._idx += 1
        return dict(item)
