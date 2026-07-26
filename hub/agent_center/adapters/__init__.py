"""Load agent adapter config and instantiate adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hub.agent_center.adapters.base import AgentAdapter, AgentDescriptor
from hub.agent_center.adapters.claude_code import ClaudeCodeAdapter
from hub.agent_center.adapters.codex import CodexAdapter
from hub.agent_center.adapters.cursor_agent import CursorAgentAdapter
from hub.agent_center.adapters.hub_simulator import HubSimulatorAdapter
from hub.agent_center.adapters.openai_api import OpenAIApiAdapter
from hub.agent_center.models import MODES
from hub.settings import ROOT_DIR

_ADAPTER_TYPES = {
    "hub_simulator": HubSimulatorAdapter,
    "openai_api": OpenAIApiAdapter,
    "claude_code": ClaudeCodeAdapter,
    "cursor_agent": CursorAgentAdapter,
    "codex": CodexAdapter,
}


def default_agents_config_path() -> Path:
    return ROOT_DIR / "config" / "agents.yaml"


def load_agent_descriptors(path: Path | None = None) -> list[AgentDescriptor]:
    cfg_path = path or default_agents_config_path()
    if not cfg_path.is_file():
        return _builtin_descriptors()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    items = raw.get("agents") or []
    out: list[AgentDescriptor] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        modes = [m for m in (item.get("modes") or list(MODES)) if m in MODES]
        templates = item.get("command_templates") or {}
        safe_templates: dict[str, list[str]] = {}
        if isinstance(templates, dict):
            for key, argv in templates.items():
                if key in MODES and isinstance(argv, list) and all(isinstance(x, str) for x in argv):
                    safe_templates[key] = list(argv)
        out.append(
            AgentDescriptor(
                id=str(item.get("id") or "").strip(),
                label=str(item.get("label") or item.get("id") or "Agent").strip(),
                provider=str(item.get("provider") or item.get("adapter") or "").strip(),
                executable=str(item.get("executable") or "").strip(),
                modes=modes or list(MODES),
                models_managed=[str(x) for x in (item.get("models_managed") or []) if str(x).strip()],
                command_templates=safe_templates,
                enabled=bool(item.get("enabled", True)),
                notes=str(item.get("notes") or ""),
            )
        )
    return [d for d in out if d.id]


def build_adapters(descriptors: list[AgentDescriptor] | None = None) -> list[AgentAdapter]:
    descriptors = descriptors if descriptors is not None else load_agent_descriptors()
    adapters: list[AgentAdapter] = []
    for desc in descriptors:
        cls = _ADAPTER_TYPES.get(desc.provider) or _ADAPTER_TYPES.get(desc.id.replace("-", "_"))
        if cls is None:
            # Future agents: still surface as unavailable generic adapters.
            from hub.agent_center.adapters.generic import GenericCliAdapter

            adapters.append(GenericCliAdapter(desc))
        else:
            adapters.append(cls(desc))
    return adapters


def _builtin_descriptors() -> list[AgentDescriptor]:
    return [
        AgentDescriptor(
            id="hub-simulator",
            label="Hub Simulator (demo)",
            provider="hub_simulator",
            executable="python",
            modes=list(MODES),
            models_managed=["simulator"],
        ),
        AgentDescriptor(
            id="openai-api",
            label="OpenAI API",
            provider="openai_api",
            executable="",
            modes=list(MODES),
            models_managed=[],
            notes="OpenAI Responses API; models loaded dynamically when enabled.",
        ),
        AgentDescriptor(
            id="claude-code",
            label="Claude Code",
            provider="claude_code",
            executable="claude",
            modes=list(MODES),
            models_managed=["claude-opus-4", "claude-sonnet-4", "claude-haiku-4"],
        ),
        AgentDescriptor(
            id="cursor-agent",
            label="Cursor Agent",
            provider="cursor_agent",
            executable="agent",
            modes=list(MODES),
            models_managed=["inherit", "composer-2.5-fast", "gpt-5.6-sol-medium"],
        ),
        AgentDescriptor(
            id="codex",
            label="Codex",
            provider="codex",
            executable="codex",
            modes=list(MODES),
            models_managed=["o4-mini", "gpt-5", "codex-mini"],
        ),
    ]
