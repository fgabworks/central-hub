"""CRUD helpers for agent prompts and runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.models import MAX_ANSWER_CHARS, MAX_LOG_CHARS, public_run
from hub.agent_center.provider_secrets import redact_known_secrets
from hub.agent_center.redact import redact_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


class AgentCenterStore:
    def __init__(self, db: AgentCenterDb | None = None) -> None:
        self.db = db or AgentCenterDb()

    def get_connection(self, agent_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_connections WHERE agent_id=?", (agent_id,)
            ).fetchone()
        return dict(row) if row else {"agent_id": agent_id, "disconnected": 0}

    def save_connection(
        self,
        agent_id: str,
        *,
        disconnected: bool | None = None,
        last_check: str | None = None,
        last_successful_check: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_connection(agent_id)
        values = {
            "disconnected": int(disconnected if disconnected is not None else bool(current.get("disconnected"))),
            "last_check": last_check if last_check is not None else str(current.get("last_check") or ""),
            "last_successful_check": last_successful_check if last_successful_check is not None else str(current.get("last_successful_check") or ""),
            "last_error": redact_known_secrets(last_error, limit=500) if last_error is not None else str(current.get("last_error") or ""),
        }
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_connections(agent_id, disconnected, last_check, last_successful_check, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET disconnected=excluded.disconnected,
                    last_check=excluded.last_check, last_successful_check=excluded.last_successful_check,
                    last_error=excluded.last_error, updated_at=excluded.updated_at
                """,
                (agent_id, values["disconnected"], values["last_check"], values["last_successful_check"], values["last_error"], _now()),
            )
        return self.get_connection(agent_id)

    def get_pref(self, key: str, default: str = "") -> str:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM agent_prefs WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return str(row["value"] or default)

    def set_pref(self, key: str, value: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_prefs(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value or ""), _now()),
            )

    # --- prompts ---
    def list_prompts(self, *, profile_id: str = "okarun") -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_prompts WHERE profile_id = ? ORDER BY favorite DESC, updated_at DESC",
                (profile_id,),
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
        profile_id: str = "okarun",
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
                    WHERE id=? AND profile_id=?
                    """,
                    (title[:200], body, mode, tags_json, 1 if favorite else 0, now, pid, profile_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO agent_prompts(id, title, body, mode, tags_json, favorite, created_at, updated_at, profile_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pid, title[:200] or "Untitled prompt", body, mode, tags_json, 1 if favorite else 0, now, now, profile_id),
                )
        return self.get_prompt(pid) or {}

    def delete_prompt(self, prompt_id: str, *, profile_id: str = "okarun") -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM agent_prompts WHERE id = ? AND profile_id = ?",
                (prompt_id, profile_id),
            )
            return cur.rowcount > 0

    # --- conversations ---
    def create_conversation(self, *, profile_id: str, title: str) -> dict[str, Any]:
        cid, now = _uid(), _now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_conversations(id, profile_id, title, summary, created_at, updated_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (cid, profile_id, (title or "New conversation")[:160], now, now),
            )
        return self.get_conversation(cid, profile_id=profile_id) or {}

    def get_conversation(self, conversation_id: str, *, profile_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_conversations WHERE id=? AND profile_id=?",
                (conversation_id, profile_id),
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, *, profile_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_conversations
                WHERE profile_id=? ORDER BY updated_at DESC LIMIT ?
                """,
                (profile_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_conversation(
        self, conversation_id: str, *, profile_id: str, title: str
    ) -> dict[str, Any] | None:
        clean = (title or "").strip()[:160]
        if not clean:
            return self.get_conversation(conversation_id, profile_id=profile_id)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE agent_conversations SET title=?, updated_at=?
                WHERE id=? AND profile_id=?
                """,
                (clean, _now(), conversation_id, profile_id),
            )
        return self.get_conversation(conversation_id, profile_id=profile_id)

    def list_conversation_runs(
        self,
        conversation_id: str,
        *,
        profile_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_runs
                WHERE conversation_id=? AND profile_id=?
                ORDER BY created_at ASC LIMIT ?
                """,
                (conversation_id, profile_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    def update_conversation_summary(
        self, conversation_id: str, *, profile_id: str, summary: str
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE agent_conversations SET summary=?, updated_at=?
                WHERE id=? AND profile_id=?
                """,
                (redact_text(summary, limit=4000), _now(), conversation_id, profile_id),
            )

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
                    tool_activity_json, usage_json, profile_id, conversation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, '', 0, NULL, ?, NULL, NULL, '[]', '{}', ?, ?)
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
                    payload.get("profile_id") or "okarun",
                    payload.get("conversation_id") or "",
                ),
            )
        return self.get_run(rid) or {}

    def get_run(self, run_id: str, *, profile_id: str | None = None) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            if profile_id:
                row = conn.execute(
                    "SELECT * FROM agent_runs WHERE id = ? AND profile_id = ?",
                    (run_id, profile_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, *, limit: int = 50, profile_id: str = "okarun") -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
                (profile_id, max(1, min(limit, 200))),
            ).fetchall()
        return [public_run(self._run_row(r)) for r in rows]

    def latest_provider_session(
        self,
        *,
        conversation_id: str,
        profile_id: str,
        agent_id: str,
        model: str,
        repository_ids: list[str],
    ) -> str:
        """Return the latest persisted CLI session for the exact conversation/scope."""
        if not conversation_id:
            return ""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, agent_id, model, usage_json, repository_ids_json
                FROM agent_runs
                WHERE conversation_id=? AND profile_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id, profile_id),
            ).fetchall()
        expected = list(repository_ids or [])
        for row in rows:
            try:
                if row["status"] != "completed" or row["agent_id"] != agent_id or row["model"] != model:
                    return ""
                if json.loads(row["repository_ids_json"] or "[]") != expected:
                    return ""
                usage = json.loads(row["usage_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return ""
            session_id = str((usage or {}).get("provider_session_id") or "").strip()
            if session_id:
                return session_id
        return ""

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
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return {
            "id": row["id"],
            "title": row["title"],
            "body": row["body"],
            "mode": row["mode"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "favorite": bool(row["favorite"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "profile_id": row["profile_id"] if "profile_id" in keys else "okarun",
        }

    def _run_row(self, row: Any) -> dict[str, Any]:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        tool_raw = row["tool_activity_json"] if "tool_activity_json" in keys else "[]"
        usage_raw = row["usage_json"] if "usage_json" in keys else "{}"
        return {
            "id": row["id"],
            "profile_id": row["profile_id"] if "profile_id" in keys else "okarun",
            "conversation_id": row["conversation_id"] if "conversation_id" in keys else "",
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
