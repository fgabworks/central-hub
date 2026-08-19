"""Durable storage for controlled Code Workspace edit proposals."""

from __future__ import annotations

import json
from typing import Any

from hub.agent_center.db import AgentCenterDb


_JSON_FIELDS = {
    "plan": "plan_json",
    "affected_files": "affected_files_json",
    "inspected_files": "inspected_files_json",
    "edits": "edits_json",
    "evidence_provenance": "evidence_provenance_json",
    "rollback_snapshot": "rollback_snapshot_json",
    "files_changed": "files_changed_json",
    "resulting_state": "resulting_state_json",
}


class CodingProposalStore:
    def __init__(self, db: AgentCenterDb) -> None:
        self.db = db

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        columns = [
            "id", "run_id", "conversation_id", "workspace", "repository_id",
            "requested_change", "plan_json", "affected_files_json",
            "inspected_files_json", "edits_json", "state", "decision",
            "provider", "model", "execution_mode", "context_scope",
            "evidence_provenance_json", "rollback_snapshot_json",
            "files_changed_json", "resulting_state_json", "error", "created_at",
            "updated_at", "decided_at", "applied_at", "parent_proposal_id",
            "source_test_run_id",
        ]
        values: dict[str, Any] = dict(record)
        for public, stored in _JSON_FIELDS.items():
            values[stored] = json.dumps(values.get(public) or ([] if public != "evidence_provenance" else {}))
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO coding_edit_proposals ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(run_id) DO UPDATE SET "
                + ",".join(f"{name}=excluded.{name}" for name in columns if name not in {"id", "run_id", "created_at"}),
                tuple(values.get(name) for name in columns),
            )
        return self.get(str(record["run_id"])) or dict(record)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM coding_edit_proposals WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        for public, stored in _JSON_FIELDS.items():
            try:
                data[public] = json.loads(data.pop(stored) or "null")
            except (TypeError, ValueError):
                data[public] = {} if public == "evidence_provenance" else []
        return data
