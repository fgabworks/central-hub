"""Result contracts for the Unified Tool Runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    summary: str
    observation: str
    source: str
    duration_ms: float = 0.0
    error: str = ""
    tool: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    context_chars: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "observation": self.observation,
            "source": self.source,
            "duration_ms": round(float(self.duration_ms), 2),
            "error": self.error,
            "tool": self.tool,
            "context_chars": int(self.context_chars),
        }


@dataclass
class ToolStepRecord:
    step: int
    provider: str
    model: str
    tool: str
    ok: bool
    summary: str
    duration_ms: float
    result: str = ""  # ok | error | blocked | duplicate | cancelled | timeout
    context_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    observation_chars: int = 0
    error: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeOutcome:
    status: str  # completed | cancelled | timed_out | stuck | max_steps | failed
    answer: str
    steps: list[ToolStepRecord] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    evidence_packet: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    stop_reason: str = ""
    error: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)
    session_reused: bool = False
    retries: int = 0
    active_tools: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "steps": [s.public() for s in self.steps],
            "tool_results": list(self.tool_results),
            "grounding": dict(self.grounding),
            "evidence_packet": dict(self.evidence_packet),
            "usage": dict(self.usage),
            "provider": self.provider,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "telemetry": dict(self.telemetry),
            "session_reused": bool(self.session_reused),
            "retries": int(self.retries),
            "active_tools": list(self.active_tools),
        }
