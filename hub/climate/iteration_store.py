"""Persistent user-driven coding proposal/test iteration chains."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CodingIterationStore:
    def __init__(self, db: AgentCenterDb) -> None:
        self.db = db

    def ensure_chain(self, *, root_proposal_id: str, root_run_id: str, workspace: str, repository_id: str, max_depth: int) -> None:
        now = _now()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO coding_iteration_chains "
                "(root_proposal_id,root_run_id,workspace,repository_id,max_depth,current_depth,status,warning,created_at,updated_at) "
                "VALUES (?,?,?,?,?,0,'awaiting_decision','',?,?)",
                (root_proposal_id, root_run_id, workspace, repository_id, max_depth, now, now),
            )

    def update_chain(self, root_proposal_id: str, **changes: Any) -> None:
        allowed = {"current_depth", "status", "warning", "max_depth"}
        clean = {key: value for key, value in changes.items() if key in allowed}
        if not clean:
            return
        clean["updated_at"] = _now()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE coding_iteration_chains SET " + ",".join(f"{key}=?" for key in clean) + " WHERE root_proposal_id=?",
                (*clean.values(), root_proposal_id),
            )

    def event(self, *, root_proposal_id: str, depth: int, event_type: str, proposal_id: str = "", test_run_id: str = "", status: str = "", detail: dict[str, Any] | None = None) -> None:
        with self.db.connect() as conn:
            sequence = int(conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM coding_iteration_events WHERE root_proposal_id=?",
                (root_proposal_id,),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO coding_iteration_events "
                "(id,root_proposal_id,sequence,depth,event_type,proposal_id,test_run_id,status,detail_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, root_proposal_id, sequence, depth, event_type, proposal_id, test_run_id, status, json.dumps(detail or {}), _now()),
            )

    def proposal_fingerprint_exists(self, root_proposal_id: str, fingerprint: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM coding_edit_proposals WHERE root_proposal_id=? AND proposal_fingerprint=? LIMIT 1",
                (root_proposal_id, fingerprint),
            ).fetchone()
        return row is not None

    def status(self, root_proposal_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            chain = conn.execute(
                "SELECT * FROM coding_iteration_chains WHERE root_proposal_id=?", (root_proposal_id,)
            ).fetchone()
            events = conn.execute(
                "SELECT * FROM coding_iteration_events WHERE root_proposal_id=? ORDER BY sequence",
                (root_proposal_id,),
            ).fetchall()
        if chain is None:
            return None
        rows = []
        labels = []
        for raw in events:
            row = dict(raw)
            try:
                row["detail"] = json.loads(row.pop("detail_json") or "{}")
            except (TypeError, ValueError):
                row["detail"] = {}
            label = self._label(row)
            if label:
                labels.append(label)
            rows.append(row)
        return {**dict(chain), "events": rows, "timeline": labels}

    @staticmethod
    def _label(event: dict[str, Any]) -> str:
        depth = int(event.get("depth") or 0)
        kind = str(event.get("event_type") or "")
        if kind == "proposal_created":
            return "Change 1" if depth == 0 else f"Fix {depth}"
        if kind == "proposal_rejected":
            return "Change rejected" if depth == 0 else f"Fix {depth} rejected"
        if kind == "proposal_conflict":
            return "Change is stale" if depth == 0 else f"Fix {depth} is stale"
        if kind == "tests_passed":
            return "Tests passed"
        if kind == "tests_failed":
            return "Tests failed"
        if kind == "tests_cancelled":
            return "Tests cancelled"
        if kind == "tests_timed_out":
            return "Tests timed out"
        if kind == "tests_skipped":
            return "Tests skipped"
        if kind == "guard_blocked":
            return str((event.get("detail") or {}).get("warning") or "Iteration stopped")
        return ""
