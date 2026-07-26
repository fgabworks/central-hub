"""CRUD helpers for agent prompts and runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.models import MAX_ANSWER_CHARS, MAX_LOG_CHARS, public_run
from hub.agent_center.redact import redact_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


class AgentCenterStore:
    def __init__(self, db: AgentCenterDb | None = None) -> None:
        self.db = db or AgentCenterDb()

    # --- prompts ---
    def list_prompts(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_prompts ORDER BY favorite DESC, updated_at DESC"
            ).fetchall()
        return [self._prompt_row(r) for r in rows]

    def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_prompts WHERE id = ?", (prompt_id,)).fetchone()
        return self._prompt_row(row) if row else None

    def save_prompt(
        self,
        *,
        title: str,
        body: str,
        mode: str = "ask",
        tags: list[str] | None = None,
        favorite: bool = False,
        prompt_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        pid = prompt_id or _uid()
        tags_json = json.dumps(list(tags or []), ensure_ascii=False)
        with self.db.connect() as conn:
            existing = conn.execute("SELECT id FROM agent_prompts WHERE id = ?", (pid,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE agent_prompts
                    SET title=?, body=?, mode=?, tags_json=?, favorite=?, updated_at=?
                    WHERE id=?
                    """,
                    (title[:200], body, mode, tags_json, 1 if favorite else 0, now, pid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO agent_prompts(id, title, body, mode, tags_json, favorite, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pid, title[:200] or "Untitled prompt", body, mode, tags_json, 1 if favorite else 0, now, now),
                )
        return self.get_prompt(pid) or {}

    def delete_prompt(self, prompt_id: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM agent_prompts WHERE id = ?", (prompt_id,))
            return cur.rowcount > 0

    # --- runs ---
    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        rid = _uid()
        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs(
                    id, status, mode, agent_id, agent_label, model, repository_ids_json,
                    prompt, packed_prompt, context_json, answer, logs, referenced_files_json,
                    error, cancel_requested, pid, created_at, started_at, finished_at,
                    tool_activity_json, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, '', 0, NULL, ?, NULL, NULL, '[]', '{}')
                """,
                (
                    rid,
                    payload.get("status") or "queued",
                    payload["mode"],
                    payload["agent_id"],
                    payload.get("agent_label") or "",
                    payload.get("model") or "",
                    json.dumps(payload.get("repository_ids") or [], ensure_ascii=False),
                    payload.get("prompt") or "",
                    payload.get("packed_prompt") or "",
                    json.dumps(payload.get("context") or {}, ensure_ascii=False),
                    json.dumps(payload.get("referenced_files") or [], ensure_ascii=False),
                    now,
                ),
            )
        return self.get_run(rid) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [public_run(self._run_row(r)) for r in rows]

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "answer",
            "logs",
            "error",
            "cancel_requested",
            "pid",
            "started_at",
            "finished_at",
            "referenced_files_json",
            "tool_activity_json",
            "usage_json",
            "model",
        }
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key == "referenced_files":
                key = "referenced_files_json"
                value = json.dumps(value or [], ensure_ascii=False)
            elif key == "tool_activity":
                key = "tool_activity_json"
                value = json.dumps(value or [], ensure_ascii=False)
            elif key == "usage":
                key = "usage_json"
                value = json.dumps(value or {}, ensure_ascii=False)
            if key not in allowed:
                continue
            if key in {"answer", "logs", "error"} and isinstance(value, str):
                if key == "answer":
                    value = redact_text(value, limit=MAX_ANSWER_CHARS)
                elif key == "logs":
                    value = redact_text(value, limit=MAX_LOG_CHARS)
                else:
                    value = redact_text(value, limit=4000)
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_run(run_id)
        values.append(run_id)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_run(run_id)

    def append_log(self, run_id: str, chunk: str) -> None:
        if not chunk:
            return
        with self.db.connect() as conn:
            row = conn.execute("SELECT logs FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return
            logs = redact_text((row["logs"] or "") + chunk, limit=MAX_LOG_CHARS)
            conn.execute("UPDATE agent_runs SET logs = ? WHERE id = ?", (logs, run_id))

    def request_cancel(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            conn.execute("UPDATE agent_runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
        return self.get_run(run_id)

    def _prompt_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "mode": row["mode"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "favorite": bool(row["favorite"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _run_row(self, row: Any) -> dict[str, Any]:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        tool_raw = row["tool_activity_json"] if "tool_activity_json" in keys else "[]"
        usage_raw = row["usage_json"] if "usage_json" in keys else "{}"
        return {
            "id": row["id"],
            "status": row["status"],
            "mode": row["mode"],
            "agent_id": row["agent_id"],
            "agent_label": row["agent_label"],
            "model": row["model"],
            "repository_ids": json.loads(row["repository_ids_json"] or "[]"),
            "prompt": row["prompt"],
            "packed_prompt": row["packed_prompt"],
            "context": json.loads(row["context_json"] or "{}"),
            "answer": row["answer"] or "",
            "logs": row["logs"] or "",
            "referenced_files": json.loads(row["referenced_files_json"] or "[]"),
            "tool_activity": json.loads(tool_raw or "[]"),
            "usage": json.loads(usage_raw or "{}"),
            "error": row["error"] or "",
            "cancel_requested": bool(row["cancel_requested"]),
            "pid": row["pid"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
