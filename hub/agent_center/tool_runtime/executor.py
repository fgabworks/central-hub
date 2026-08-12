"""Unified Tool Runtime executor — one execute(tool, args, context) path."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from hub.agent_center.openai_tools import ALLOWED_TOOLS, AgentToolsContext, ToolActivity, execute_tool
from hub.agent_center.redact import redact_text
from hub.agent_center.tool_runtime.handlers import handle_extra_tool, observation_from_payload
from hub.agent_center.tool_runtime.policy import policy_gate
from hub.agent_center.tool_runtime.prune import cap_observation
from hub.agent_center.tool_runtime.results import ToolResult
from hub.agent_center.tool_runtime.specs import get_tool_spec

AuditFn = Callable[..., None]


class UnifiedToolExecutor:
    """Provider-neutral choke point for Hub tool execution (Phase 1: RO only)."""

    def __init__(
        self,
        *,
        audit: AuditFn | None = None,
        max_observation_chars: int = 6_000,
    ) -> None:
        self.audit = audit
        self.max_observation_chars = max_observation_chars

    def execute(
        self,
        tool: str,
        args: dict[str, Any] | str | None,
        context: AgentToolsContext,
        *,
        interaction_mode: str | None = None,
        active_names: set[str] | None = None,
        permissions: set[str] | frozenset[str] | None = None,
        source: str = "tool_runtime",
    ) -> ToolResult:
        name = str(tool or "").strip()
        started = time.perf_counter()
        arguments = _normalize_args(args)
        gate = policy_gate(
            name,
            interaction_mode=interaction_mode,
            active_names=active_names,
            permissions=permissions,
            allow_writes=False,
        )
        if not gate.get("allowed"):
            duration = (time.perf_counter() - started) * 1000.0
            reason = str(gate.get("reason") or "blocked")
            result = ToolResult(
                ok=False,
                summary=reason,
                observation=json.dumps({"error": reason, "tool": name}),
                source=source,
                duration_ms=duration,
                error=reason,
                tool=name,
                context_chars=0,
            )
            context.activity.append(
                ToolActivity(name=name, arguments=arguments, ok=False, detail=reason)
            )
            self._audit(name, arguments, result, interaction_mode=interaction_mode)
            return result

        # Ensure allowlist on AgentToolsContext for legacy execute_tool path.
        spec = get_tool_spec(name)
        if context.allowed_tools is not None:
            context.allowed_tools = set(context.allowed_tools) | {name}

        try:
            if name in {"repository_intelligence", "sql_query_execute", "data_explorer_lookup", "skill_recall"}:
                raw = handle_extra_tool(name, arguments, context) or {"error": "handler missing"}
                ok, summary, observation = observation_from_payload(raw)
                payload_dict = raw if isinstance(raw, dict) else {}
                context.activity.append(
                    ToolActivity(
                        name=name,
                        arguments={k: v for k, v in arguments.items() if k != "content"},
                        ok=ok,
                        detail=summary[:240],
                        chars=len(observation),
                    )
                )
            elif name in ALLOWED_TOOLS:
                # Wrap existing openai_tools handlers — do not rewrite them.
                raw_text = execute_tool(name, arguments, context)
                ok, summary, observation = observation_from_payload(raw_text)
                try:
                    payload_dict = json.loads(raw_text) if raw_text else {}
                except json.JSONDecodeError:
                    payload_dict = {"raw": raw_text}
            else:
                ok = False
                summary = "unknown_tool"
                observation = json.dumps({"error": "unknown_tool", "tool": name})
                payload_dict = {"error": "unknown_tool"}
                context.activity.append(
                    ToolActivity(name=name, arguments=arguments, ok=False, detail=summary)
                )
        except Exception as exc:  # noqa: BLE001
            ok = False
            summary = "tool_exception"
            observation = json.dumps({"error": redact_text(str(exc), limit=500)})
            payload_dict = {"error": str(exc)}
            context.activity.append(
                ToolActivity(
                    name=name,
                    arguments=arguments,
                    ok=False,
                    detail=redact_text(str(exc), limit=240),
                )
            )

        observation = cap_observation(observation, max_chars=self.max_observation_chars)
        duration = (time.perf_counter() - started) * 1000.0
        error = "" if ok else str(
            (payload_dict or {}).get("error") or summary or "tool_failed"
        )
        result = ToolResult(
            ok=ok,
            summary=summary,
            observation=observation,
            source=source if not spec else f"{source}:{spec.domain}",
            duration_ms=duration,
            error=error,
            tool=name,
            raw=payload_dict if isinstance(payload_dict, dict) else {},
            context_chars=len(observation),
        )
        self._audit(name, arguments, result, interaction_mode=interaction_mode)
        return result

    def _audit(
        self,
        name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        *,
        interaction_mode: str | None,
    ) -> None:
        if not self.audit:
            return
        try:
            self.audit(
                action="AIRIX_TOOL_RUNTIME_EXECUTE",
                detail={
                    "tool": name,
                    "ok": result.ok,
                    "summary": result.summary[:200],
                    "duration_ms": result.duration_ms,
                    "error": result.error[:200],
                    "interaction_mode": interaction_mode or "",
                    "arg_keys": sorted(str(k) for k in arguments.keys())[:20],
                },
            )
        except Exception:  # noqa: BLE001
            pass


def _normalize_args(args: dict[str, Any] | str | None) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# Module-level helper matching the Phase-1 contract signature.
_DEFAULT_EXECUTOR = UnifiedToolExecutor()


def execute(
    tool: str,
    args: dict[str, Any] | str | None,
    context: AgentToolsContext,
    **kwargs: Any,
) -> ToolResult:
    return _DEFAULT_EXECUTOR.execute(tool, args, context, **kwargs)
