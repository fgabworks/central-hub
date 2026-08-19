"""OpenAI API settings for Prompting & Agent Center (secrets from env or local store)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from hub.agent_center.openai_catalog import parse_allowed_models
from hub.settings import _as_bool, _as_float, _as_int


@dataclass(frozen=True)
class OpenAISettings:
    enabled: bool
    api_key: str | None
    default_model: str
    allowed_models: frozenset[str] | None
    model_cache_ttl_seconds: float
    pro_model_timeout_seconds: float
    base_url: str
    timeout_seconds: float
    max_output_tokens: int
    max_tool_rounds: int
    max_tool_result_chars: int

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.api_key)

    def public_status(self) -> dict[str, str | bool | int]:
        """Redacted status — never includes the API key."""
        return {
            "enabled": self.enabled,
            "api_key": "set" if self.api_key else "missing",
            "default_model": self.default_model or "",
            "allowed_models_count": len(self.allowed_models) if self.allowed_models is not None else 0,
            "allowed_models_restricted": self.allowed_models is not None,
            "model_cache_ttl_seconds": int(self.model_cache_ttl_seconds),
            "pro_model_timeout_seconds": int(self.pro_model_timeout_seconds),
            "configured": self.is_configured,
        }


def load_openai_settings() -> OpenAISettings:
    key = (os.getenv("OPENAI_API_KEY") or "").strip() or None
    return OpenAISettings(
        enabled=_as_bool(os.getenv("OPENAI_ENABLED"), default=bool(key)),
        api_key=key,
        default_model=(os.getenv("OPENAI_DEFAULT_MODEL") or "").strip(),
        allowed_models=parse_allowed_models(os.getenv("OPENAI_ALLOWED_MODELS")),
        model_cache_ttl_seconds=_as_float(os.getenv("OPENAI_MODEL_CACHE_TTL_SECONDS"), 300.0),
        pro_model_timeout_seconds=_as_float(os.getenv("OPENAI_PRO_MODEL_TIMEOUT_SECONDS"), 600.0),
        base_url=(os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/"),
        timeout_seconds=_as_float(os.getenv("OPENAI_TIMEOUT_SECONDS"), 120.0),
        max_output_tokens=_as_int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS"), 4096, minimum=256, maximum=128_000),
        max_tool_rounds=_as_int(os.getenv("OPENAI_MAX_TOOL_ROUNDS"), 8, minimum=0, maximum=20),
        max_tool_result_chars=_as_int(
            os.getenv("OPENAI_MAX_TOOL_RESULT_CHARS"), 12_000, minimum=1000, maximum=100_000
        ),
    )
