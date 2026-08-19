"""Environment-backed settings for the Anthropic Messages API provider."""

from __future__ import annotations

import os
from dataclasses import dataclass

from hub.settings import _as_bool, _as_float, _as_int


@dataclass(frozen=True)
class AnthropicSettings:
    enabled: bool
    api_key: str | None
    base_url: str
    api_version: str
    default_model: str
    allowed_models: tuple[str, ...] | None
    timeout_seconds: float
    model_cache_ttl_seconds: float
    max_output_tokens: int

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.api_key)


def load_anthropic_settings() -> AnthropicSettings:
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None
    allowed_raw = (os.getenv("ANTHROPIC_ALLOWED_MODELS") or "").strip()
    allowed = tuple(item.strip() for item in allowed_raw.split(",") if item.strip()) or None
    return AnthropicSettings(
        enabled=_as_bool(os.getenv("ANTHROPIC_ENABLED"), default=bool(api_key)),
        api_key=api_key,
        base_url=(os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/"),
        api_version=(os.getenv("ANTHROPIC_API_VERSION") or "2023-06-01").strip() or "2023-06-01",
        default_model=(os.getenv("ANTHROPIC_DEFAULT_MODEL") or "").strip(),
        allowed_models=allowed,
        timeout_seconds=max(5.0, _as_float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS"), 120.0)),
        model_cache_ttl_seconds=max(
            0.0, _as_float(os.getenv("ANTHROPIC_MODEL_CACHE_TTL_SECONDS"), 300.0)
        ),
        max_output_tokens=_as_int(
            os.getenv("ANTHROPIC_MAX_OUTPUT_TOKENS"),
            4096,
            minimum=256,
            maximum=128_000,
        ),
    )
