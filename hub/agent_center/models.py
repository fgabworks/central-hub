"""Constants and helpers for Prompting & Agent Center."""

from __future__ import annotations

from typing import Any

MODES = ("find", "ask", "plan", "review")
DISABLED_MODES = ("edit", "test")
DEFAULT_MODE = "ask"

RUN_STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "unavailable",
)

AGENT_STATUSES = ("available", "unavailable", "degraded", "disabled")

INSTRUCTION_FILENAMES = (
    "AGENTS.md",
    "AI_START_HERE.md",
    "AI_REFERENCE.md",
    "CLAUDE.md",
    "CODEX.md",
    ".cursorrules",
    "SECURITY.md",
)

# Path segments / names that must never be included in agent context.
SECRET_NAME_PATTERNS = (
    "ai_provider_secrets.env",
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "token",
    "tokens",
    "password",
    "passwd",
    "private_key",
    "private-key",
    "id_rsa",
    "id_ed25519",
    "kubeconfig",
    ".pem",
    ".p12",
    ".pfx",
    "service-account",
    "service_account",
)

SECRET_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

MAX_INSTRUCTION_CHARS = 40_000
MAX_CONTEXT_FILE_CHARS = 12_000
MAX_CONTEXT_FILES = 40
MAX_PROMPT_CHARS = 20_000
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
MAX_LOG_CHARS = 200_000
MAX_ANSWER_CHARS = 200_000


def normalize_mode(value: str | None, *, default: str = DEFAULT_MODE) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in MODES else default


def mode_label(mode: str) -> str:
    return {
        "find": "Find",
        "ask": "Ask",
        "plan": "Plan",
        "review": "Review",
        "edit": "Edit",
        "test": "Test",
    }.get(mode, mode.title())


def public_run(row: dict[str, Any]) -> dict[str, Any]:
    """Strip bulky fields for list views."""
    return {
        "id": row.get("id"),
        "profile_id": row.get("profile_id"),
        "conversation_id": row.get("conversation_id"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "status": row.get("status"),
        "mode": row.get("mode"),
        "agent_id": row.get("agent_id"),
        "agent_label": row.get("agent_label"),
        "model": row.get("model"),
        "repository_ids": row.get("repository_ids") or [],
        "prompt_preview": (row.get("prompt") or "")[:160],
        "error": row.get("error") or "",
        "ok": row.get("status") == "completed",
    }
