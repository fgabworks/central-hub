"""Environment-driven settings for Central Hub."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    app_name: str
    env_profile: str
    host: str
    port: int
    debug: bool
    repositories_config: Path
    request_timeout_seconds: float


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from `.env` (if present) and process environment."""
    load_dotenv(env_file or (ROOT_DIR / ".env"), override=False)

    config_path = os.getenv(
        "CENTRAL_HUB_REPOSITORIES_CONFIG",
        str(ROOT_DIR / "config" / "repositories.yaml"),
    )

    return Settings(
        app_name=os.getenv("CENTRAL_HUB_APP_NAME", "Central Hub"),
        env_profile=os.getenv("CENTRAL_HUB_ENV", "dev"),
        host=os.getenv("CENTRAL_HUB_HOST", "127.0.0.1"),
        port=int(os.getenv("CENTRAL_HUB_PORT", "8080")),
        debug=os.getenv("CENTRAL_HUB_DEBUG", "true").lower() in {"1", "true", "yes"},
        repositories_config=Path(config_path).expanduser().resolve(),
        request_timeout_seconds=float(os.getenv("CENTRAL_HUB_REQUEST_TIMEOUT", "5")),
    )
