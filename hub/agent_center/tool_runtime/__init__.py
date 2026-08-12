"""AiriX Unified Tool Runtime — Phase 2 (dynamic tools + continuation)."""

from __future__ import annotations

# Keep this package import light: avoid pulling routing.service via policy at
# import time (openai_runner → executor → policy must stay cycle-free).

from hub.agent_center.tool_runtime.executor import UnifiedToolExecutor, execute
from hub.agent_center.tool_runtime.feed import GLOBAL_TOOL_RUNTIME_FEED, ToolRuntimeFeed
from hub.agent_center.tool_runtime.results import RuntimeOutcome, ToolResult, ToolStepRecord
from hub.agent_center.tool_runtime.settings import ToolRuntimeSettings, load_tool_runtime_settings
from hub.agent_center.tool_runtime.specs import (
    PHASE1_CORE_TOOLS,
    TOOL_SPECS,
    ToolSpec,
    get_tool_spec,
    list_tool_specs,
    openai_tool_definitions,
)
from hub.agent_center.tool_runtime.stuck import StuckGuard

__all__ = [
    "GLOBAL_TOOL_RUNTIME_FEED",
    "PHASE1_CORE_TOOLS",
    "RuntimeOutcome",
    "TOOL_SPECS",
    "ToolResult",
    "ToolRuntimeFeed",
    "ToolRuntimeSettings",
    "ToolSpec",
    "ToolStepRecord",
    "StuckGuard",
    "UnifiedToolExecutor",
    "execute",
    "get_tool_spec",
    "list_tool_specs",
    "load_tool_runtime_settings",
    "openai_tool_definitions",
    "ToolRuntime",
    "RuntimeContext",
    "ScriptedModelDriver",
    "select_dynamic_tools",
    "build_continuation_from_t0",
    "select_runtime_provider_model",
    "GLOBAL_PROVIDER_SESSION_CACHE",
    "build_runtime_telemetry",
    "policy_gate",
    "select_active_tools",
    "tool_runtime_needed",
]


def __getattr__(name: str):
    if name in {"ToolRuntime", "RuntimeContext", "ScriptedModelDriver"}:
        from hub.agent_center.tool_runtime import runtime as _runtime

        return getattr(_runtime, name)
    if name == "select_dynamic_tools":
        from hub.agent_center.tool_runtime.intelligence import select_dynamic_tools

        return select_dynamic_tools
    if name == "build_continuation_from_t0":
        from hub.agent_center.tool_runtime.continuation import build_continuation_from_t0

        return build_continuation_from_t0
    if name == "select_runtime_provider_model":
        from hub.agent_center.tool_runtime.model_policy import select_runtime_provider_model

        return select_runtime_provider_model
    if name == "GLOBAL_PROVIDER_SESSION_CACHE":
        from hub.agent_center.tool_runtime.session import GLOBAL_PROVIDER_SESSION_CACHE

        return GLOBAL_PROVIDER_SESSION_CACHE
    if name == "build_runtime_telemetry":
        from hub.agent_center.tool_runtime.telemetry import build_runtime_telemetry

        return build_runtime_telemetry
    if name in {"policy_gate", "select_active_tools", "tool_runtime_needed"}:
        from hub.agent_center.tool_runtime import policy as _policy

        return getattr(_policy, name)
    raise AttributeError(name)
