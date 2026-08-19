"""Bounded same-provider conversation turns for API chat runners.

Never includes the current run. Secrets stay in stored prompt/answer text that
has already been redacted by the runner that persisted them.
"""

from __future__ import annotations

from typing import Any


def prior_completed_turns(
    store: Any,
    *,
    run_id: str,
    conversation_id: str,
    agent_id: str,
    model: str,
    limit: int = 12,
    char_budget: int = 80_000,
) -> list[tuple[str, str]]:
    """Return (prompt, answer) pairs for restored same-provider, same-model turns."""
    current = store.get_run(run_id) or {}
    profile_id = str(current.get("profile_id") or "okarun")
    rows = (
        store.list_conversation_runs(conversation_id, profile_id=profile_id, limit=limit)
        if conversation_id
        else []
    )
    turns: list[tuple[str, str]] = []
    remaining = char_budget
    for row in rows:
        if row.get("id") == run_id or row.get("status") != "completed":
            continue
        if row.get("agent_id") != agent_id or row.get("model") != model:
            continue
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        if not prompt or not answer:
            continue
        pair_chars = len(prompt) + len(answer)
        if pair_chars > remaining:
            continue
        turns.append((prompt, answer))
        remaining -= pair_chars
    return turns
