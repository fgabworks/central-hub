"""Environment-only settings for the Gemini API provider."""

from __future__ import annotations

import os
from dataclasses import dataclass

from hub.settings import _as_bool, _as_float, _as_int


@dataclass(frozen=True)
class GeminiSettings:
    enabled: bool
    api_key: str | None
    base_url: str
    default_model: str
    allowed_models: tuple[str, ...] | None
    timeout_seconds: float
    model_cache_ttl_seconds: float
    max_output_tokens: int

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.api_key)


def load_gemini_settings() -> GeminiSettings:
    # Google's client behavior gives GOOGLE_API_KEY precedence when both exist.
    api_key = (
        os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
    ).strip() or None
    allowed_raw = (os.getenv("GEMINI_ALLOWED_MODELS") or "").strip()
    allowed = tuple(
        item.strip() for item in allowed_raw.split(",") if item.strip()
    ) or None
    return GeminiSettings(
        enabled=_as_bool(os.getenv("GEMINI_ENABLED"), default=bool(api_key)),
        api_key=api_key,
        base_url=(
            os.getenv("GEMINI_BASE_URL")
            or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/"),
        default_model=(os.getenv("GEMINI_DEFAULT_MODEL") or "").strip(),
        allowed_models=allowed,
        timeout_seconds=max(
            5.0, _as_float(os.getenv("GEMINI_TIMEOUT_SECONDS"), 120.0)
        ),
        model_cache_ttl_seconds=max(
            0.0, _as_float(os.getenv("GEMINI_MODEL_CACHE_TTL_SECONDS"), 300.0)
        ),
        max_output_tokens=_as_int(
            os.getenv("GEMINI_MAX_OUTPUT_TOKENS"),
            4096,
            minimum=256,
            maximum=65_536,
        ),
    )
