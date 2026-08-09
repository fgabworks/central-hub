"""AiriX Smart Routing — execution history, stats, and analytics (Phase 5)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import redact_text
from hub.agent_center.routing.budget import band_to_tokens
from hub.agent_center.routing.findings import extract_findings_from_answer
from hub.notebook.models import normalize_workspace

# Enough samples before success-rate bias affects recommendations.
MIN_HISTORY_SAMPLES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


class RoutingHistoryStore:
    """Persists sanitized routing metrics and compact findings (no secrets/prompts)."""

    def __init__(self, db: AgentCenterDb | None = None) -> None:
        self.db = db or AgentCenterDb()

    def record_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = normalize_workspace(str(payload.get("workspace") or "work"))
        provider_id = str(payload.get("provider_id") or "").strip() or "unknown"
        task_type = str(payload.get("task_type") or "general").strip() or "general"
        status = str(payload.get("status") or "failed").strip()
        outcome = str(payload.get("outcome") or status).strip()
        retries = max(0, int(payload.get("retries") or 0))
        runtime_ms = max(0, int(payload.get("runtime_ms") or 0))
        actual_tokens = payload.get("actual_tokens")
        try:
            actual_tokens_i = int(actual_tokens) if actual_tokens is not None else None
        except (TypeError, ValueError):
            actual_tokens_i = None
        input_tokens = payload.get("input_tokens")
        output_tokens = payload.get("output_tokens")
        try:
            input_tokens_i = int(input_tokens) if input_tokens is not None else None
        except (TypeError, ValueError):
            input_tokens_i = None
        try:
            output_tokens_i = int(output_tokens) if output_tokens is not None else None
        except (TypeError, ValueError):
            output_tokens_i = None
        estimated_usage = str(payload.get("estimated_usage") or "")[:40]
        estimated_tokens = payload.get("estimated_tokens")
        try:
            estimated_tokens_i = (
                int(estimated_tokens)
                if estimated_tokens is not None
                else band_to_tokens(estimated_usage)
            )
        except (TypeError, ValueError):
            estimated_tokens_i = band_to_tokens(estimated_usage)

        def _cost(val: Any) -> float | None:
            if val is None:
                return None
            try:
                return round(float(val), 6)
            except (TypeError, ValueError):
                return None

        estimated_cost = _cost(payload.get("estimated_cost_usd"))
        actual_cost = _cost(payload.get("actual_cost_usd"))
        findings_reused = payload.get("findings_reused") or payload.get("findings_reused_json") or []
        if isinstance(findings_reused, str):
            try:
                findings_reused = json.loads(findings_reused)
            except (TypeError, json.JSONDecodeError):
                findings_reused = []
        if not isinstance(findings_reused, list):
            findings_reused = []
        findings_reused = findings_reused[:8]
        t0_avoided = 1 if payload.get("t0_llm_avoided") else 0
        fallback_from = str(payload.get("fallback_from") or "").strip()
        escalated_to = str(payload.get("escalated_to") or "").strip()
        event_id = str(payload.get("id") or _uid())
        partial = redact_text(str(payload.get("partial_summary") or ""), limit=240)
        actor = str(payload.get("actor") or "owner").strip() or "owner"
        rbac_role = str(payload.get("rbac_role") or "")[:40]
        permission_denied = 1 if payload.get("permission_denied") or outcome == "permission_denied" else 0
        row = {
            "id": event_id,
            "workspace": workspace,
            "actor": actor,
            "created_at": str(payload.get("created_at") or _now()),
            "provider_id": provider_id,
            "adapter_id": str(payload.get("adapter_id") or ""),
            "tier": str(payload.get("tier") or ""),
            "task_type": task_type,
            "status": status,
            "outcome": outcome,
            "retries": retries,
            "runtime_ms": runtime_ms,
            "estimated_usage": estimated_usage,
            "actual_tokens": actual_tokens_i,
            "input_tokens": input_tokens_i,
            "output_tokens": output_tokens_i,
            "estimated_tokens": estimated_tokens_i,
            "estimated_cost_usd": estimated_cost,
            "actual_cost_usd": actual_cost,
            "findings_reused_json": json.dumps(
                [
                    {
                        "id": str(f.get("id") or ""),
                        "summary": redact_text(str(f.get("summary") or ""), limit=120),
                        "relevance_score": f.get("relevance_score"),
                    }
                    for f in findings_reused
                    if isinstance(f, dict)
                ]
            ),
            "rbac_role": rbac_role,
            "permission_denied": permission_denied,
            "usage_source": str(payload.get("usage_source") or ("actual" if actual_tokens_i is not None else "estimate"))[
                :20
            ],
            "t0_llm_avoided": t0_avoided,
            "fallback_from": fallback_from,
            "escalated_to": escalated_to,
            "prompt_fingerprint": str(payload.get("prompt_fingerprint") or "")[:64],
            "error_code": str(payload.get("error_code") or "")[:80],
            "partial_summary": partial,
        }
        with self.db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(airix_routing_events)").fetchall()}
            base_cols = [
                "id",
                "workspace",
                "created_at",
                "provider_id",
                "adapter_id",
                "tier",
                "task_type",
                "status",
                "outcome",
                "retries",
                "runtime_ms",
                "estimated_usage",
                "actual_tokens",
                "usage_source",
                "t0_llm_avoided",
                "fallback_from",
                "escalated_to",
                "prompt_fingerprint",
                "error_code",
                "partial_summary",
            ]
            optional = [
                "actor",
                "input_tokens",
                "output_tokens",
                "estimated_tokens",
                "estimated_cost_usd",
                "actual_cost_usd",
                "findings_reused_json",
                "rbac_role",
                "permission_denied",
            ]
            insert_cols = [c for c in base_cols if c in cols] + [c for c in optional if c in cols]
            placeholders = ",".join("?" for _ in insert_cols)
            conn.execute(
                f"INSERT INTO airix_routing_events({', '.join(insert_cols)}) VALUES ({placeholders})",
                tuple(row.get(c) for c in insert_cols),
            )
            self._bump_stats(
                conn,
                workspace=workspace,
                provider_id=provider_id,
                task_type=task_type,
                outcome=outcome,
                retries=retries,
                runtime_ms=runtime_ms,
                tokens=actual_tokens_i or 0,
                t0_avoided=t0_avoided,
                fallback=bool(fallback_from),
                escalation=bool(escalated_to),
            )
        # Store compact findings only for successes.
        if outcome == "success" and payload.get("answer"):
            for finding in extract_findings_from_answer(
                answer=str(payload.get("answer") or ""),
                task_type=task_type,
                prompt=str(payload.get("prompt_for_keywords") or ""),
                provider_id=provider_id,
                source_event_id=event_id,
            ):
                self.save_finding(finding, workspace=workspace, actor=actor)
        # Bump hit counts for reused findings.
        reused_ids = [str(f.get("id") or "") for f in findings_reused if isinstance(f, dict) and f.get("id")]
        if reused_ids:
            self.mark_findings_reused(reused_ids, workspace=workspace, actor=actor)
        return row

    def mark_findings_reused(
        self,
        finding_ids: list[str],
        *,
        workspace: str = "work",
        actor: str | None = None,
    ) -> int:
        workspace = normalize_workspace(workspace)
        ids = [str(x).strip() for x in finding_ids if str(x).strip()]
        if not ids:
            return 0
        bumped = 0
        with self.db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(airix_routing_findings)").fetchall()}
            for fid in ids[:12]:
                if actor and "actor" in cols:
                    cur = conn.execute(
                        """
                        UPDATE airix_routing_findings
                        SET hit_count = hit_count + 1
                        WHERE id=? AND workspace=? AND actor=?
                        """,
                        (fid, workspace, actor),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE airix_routing_findings
                        SET hit_count = hit_count + 1
                        WHERE id=? AND workspace=?
                        """,
                        (fid, workspace),
                    )
                bumped += int(cur.rowcount or 0)
        return bumped

    def _bump_stats(
        self,
        conn: Any,
        *,
        workspace: str,
        provider_id: str,
        task_type: str,
        outcome: str,
        retries: int,
        runtime_ms: int,
        tokens: int,
        t0_avoided: int,
        fallback: bool,
        escalation: bool,
    ) -> None:
        now = _now()
        existing = conn.execute(
            """
            SELECT * FROM airix_routing_provider_stats
            WHERE workspace=? AND provider_id=? AND task_type=?
            """,
            (workspace, provider_id, task_type),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO airix_routing_provider_stats(
                    workspace, provider_id, task_type, successes, failures, cancels,
                    retries_total, runtime_ms_total, tokens_total, t0_avoided,
                    fallbacks, escalations, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    workspace,
                    provider_id,
                    task_type,
                    1 if outcome == "success" else 0,
                    1 if outcome in {"failure", "unavailable"} else 0,
                    1 if outcome == "cancel" else 0,
                    retries,
                    runtime_ms,
                    tokens,
                    t0_avoided,
                    1 if fallback else 0,
                    1 if escalation else 0,
                    now,
                ),
            )
            return
        succ = int(existing["successes"] or 0) + (1 if outcome == "success" else 0)
        fail = int(existing["failures"] or 0) + (1 if outcome in {"failure", "unavailable"} else 0)
        canc = int(existing["cancels"] or 0) + (1 if outcome == "cancel" else 0)
        conn.execute(
            """
            UPDATE airix_routing_provider_stats SET
                successes=?, failures=?, cancels=?,
                retries_total=retries_total+?,
                runtime_ms_total=runtime_ms_total+?,
                tokens_total=tokens_total+?,
                t0_avoided=t0_avoided+?,
                fallbacks=fallbacks+?,
                escalations=escalations+?,
                updated_at=?
            WHERE workspace=? AND provider_id=? AND task_type=?
            """,
            (
                succ,
                fail,
                canc,
                retries,
                runtime_ms,
                tokens,
                t0_avoided,
                1 if fallback else 0,
                1 if escalation else 0,
                now,
                workspace,
                provider_id,
                task_type,
            ),
        )

    def save_finding(
        self,
        finding: dict[str, Any],
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> dict[str, Any]:
        workspace = normalize_workspace(workspace)
        actor = (actor or "owner").strip() or "owner"
        summary = redact_text(str(finding.get("summary") or "").strip(), limit=200)
        if not summary:
            return {}
        row = {
            "id": _uid(),
            "workspace": workspace,
            "actor": actor,
            "created_at": _now(),
            "task_type": str(finding.get("task_type") or "general"),
            "keywords_json": json.dumps(list(finding.get("keywords") or [])[:12]),
            "summary": summary,
            "provider_id": str(finding.get("provider_id") or ""),
            "source_event_id": str(finding.get("source_event_id") or ""),
            "hit_count": 0,
        }
        with self.db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(airix_routing_findings)").fetchall()}
            if "actor" in cols:
                conn.execute(
                    """
                    INSERT INTO airix_routing_findings(
                        id, workspace, created_at, task_type, keywords_json, summary,
                        provider_id, source_event_id, hit_count, actor
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["id"],
                        row["workspace"],
                        row["created_at"],
                        row["task_type"],
                        row["keywords_json"],
                        row["summary"],
                        row["provider_id"],
                        row["source_event_id"],
                        row["hit_count"],
                        row["actor"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO airix_routing_findings(
                        id, workspace, created_at, task_type, keywords_json, summary,
                        provider_id, source_event_id, hit_count
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["id"],
                        row["workspace"],
                        row["created_at"],
                        row["task_type"],
                        row["keywords_json"],
                        row["summary"],
                        row["provider_id"],
                        row["source_event_id"],
                        row["hit_count"],
                    ),
                )
        return {**row, "keywords": list(finding.get("keywords") or [])}

    def list_findings(
        self,
        *,
        workspace: str = "work",
        task_type: str | None = None,
        actor: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        limit = max(1, min(100, int(limit)))
        with self.db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(airix_routing_findings)").fetchall()}
            if task_type and actor and "actor" in cols:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_findings
                    WHERE workspace=? AND task_type=? AND actor=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, task_type, actor, limit),
                ).fetchall()
            elif task_type:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_findings
                    WHERE workspace=? AND task_type=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, task_type, limit),
                ).fetchall()
            elif actor and "actor" in cols:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_findings
                    WHERE workspace=? AND actor=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, actor, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_findings
                    WHERE workspace=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, limit),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["keywords"] = json.loads(d.get("keywords_json") or "[]")
            except json.JSONDecodeError:
                d["keywords"] = []
            out.append(d)
        return out

    def list_events(
        self,
        *,
        workspace: str = "work",
        actor: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        limit = max(1, min(2000, int(limit)))
        with self.db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(airix_routing_events)").fetchall()}
            if actor and "actor" in cols:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_events
                    WHERE workspace=? AND actor=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, actor, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_events
                    WHERE workspace=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def save_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = normalize_workspace(str(payload.get("workspace") or "work"))
        actor = str(payload.get("actor") or "owner").strip() or "owner"
        sid = str(payload.get("id") or _uid())
        now = _now()
        row = {
            "id": sid,
            "workspace": workspace,
            "actor": actor,
            "prompt_fingerprint": str(payload.get("prompt_fingerprint") or "")[:64],
            "prompt_preview": redact_text(str(payload.get("prompt_preview") or ""), limit=160),
            "role_id": str(payload.get("role_id") or ""),
            "status": str(payload.get("status") or "active"),
            "plan_json": json.dumps(payload.get("plan") or []),
            "completed_steps_json": json.dumps(payload.get("completed_steps") or []),
            "findings_json": json.dumps(payload.get("findings") or []),
            "partial_summary": redact_text(str(payload.get("partial_summary") or ""), limit=240),
            "estimated_tokens": int(payload.get("estimated_tokens") or 0),
            "actual_tokens": int(payload.get("actual_tokens") or 0),
            "created_at": str(payload.get("created_at") or now),
            "updated_at": now,
        }
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM airix_routing_sessions WHERE id=?", (sid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE airix_routing_sessions SET
                        status=?, plan_json=?, completed_steps_json=?, findings_json=?,
                        partial_summary=?, estimated_tokens=?, actual_tokens=?,
                        role_id=?, updated_at=?
                    WHERE id=? AND workspace=? AND actor=?
                    """,
                    (
                        row["status"],
                        row["plan_json"],
                        row["completed_steps_json"],
                        row["findings_json"],
                        row["partial_summary"],
                        row["estimated_tokens"],
                        row["actual_tokens"],
                        row["role_id"],
                        row["updated_at"],
                        sid,
                        workspace,
                        actor,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO airix_routing_sessions(
                        id, workspace, actor, prompt_fingerprint, prompt_preview, role_id,
                        status, plan_json, completed_steps_json, findings_json, partial_summary,
                        estimated_tokens, actual_tokens, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row["id"],
                        row["workspace"],
                        row["actor"],
                        row["prompt_fingerprint"],
                        row["prompt_preview"],
                        row["role_id"],
                        row["status"],
                        row["plan_json"],
                        row["completed_steps_json"],
                        row["findings_json"],
                        row["partial_summary"],
                        row["estimated_tokens"],
                        row["actual_tokens"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
        return self.get_session(sid, workspace=workspace, actor=actor) or row

    def get_session(
        self,
        session_id: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        workspace = normalize_workspace(workspace)
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM airix_routing_sessions
                WHERE id=? AND workspace=? AND actor=?
                """,
                (session_id, workspace, actor),
            ).fetchone()
        if row is None:
            return None
        return self._public_session(dict(row))

    def find_resumable_session(
        self,
        prompt_fingerprint: str,
        *,
        workspace: str = "work",
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        workspace = normalize_workspace(workspace)
        fp = (prompt_fingerprint or "").strip()
        if not fp:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM airix_routing_sessions
                WHERE workspace=? AND actor=? AND prompt_fingerprint=?
                  AND status IN ('active', 'paused', 'paused_for_approval')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (workspace, actor, fp),
            ).fetchone()
        return self._public_session(dict(row)) if row else None

    def _public_session(self, row: dict[str, Any]) -> dict[str, Any]:
        def _loads(key: str) -> Any:
            try:
                return json.loads(row.get(key) or ("[]" if key.endswith("_json") else "{}"))
            except json.JSONDecodeError:
                return []

        return {
            "id": row.get("id"),
            "workspace": row.get("workspace"),
            "actor": row.get("actor"),
            "prompt_fingerprint": row.get("prompt_fingerprint"),
            "prompt_preview": row.get("prompt_preview"),
            "role_id": row.get("role_id"),
            "status": row.get("status"),
            "plan": _loads("plan_json"),
            "completed_steps": _loads("completed_steps_json"),
            "findings": _loads("findings_json"),
            "partial_summary": row.get("partial_summary") or "",
            "estimated_tokens": int(row.get("estimated_tokens") or 0),
            "actual_tokens": int(row.get("actual_tokens") or 0),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def mark_finding_hit(self, finding_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE airix_routing_findings SET hit_count=hit_count+1 WHERE id=?",
                (finding_id,),
            )

    def provider_stats(
        self,
        *,
        workspace: str = "work",
        task_type: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        with self.db.connect() as conn:
            if task_type:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_provider_stats
                    WHERE workspace=? AND task_type=?
                    """,
                    (workspace, task_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_provider_stats
                    WHERE workspace=?
                    """,
                    (workspace,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            samples = int(d.get("successes") or 0) + int(d.get("failures") or 0)
            d["samples"] = samples
            d["success_rate"] = (
                round(float(d.get("successes") or 0) / samples, 3) if samples else None
            )
            d["avg_runtime_ms"] = (
                int(d["runtime_ms_total"] / samples) if samples and d.get("runtime_ms_total") else None
            )
            out.append(d)
        return out

    def stats_for_provider_task(
        self,
        provider_id: str,
        task_type: str,
        *,
        workspace: str = "work",
    ) -> dict[str, Any] | None:
        for row in self.provider_stats(workspace=workspace, task_type=task_type):
            if row.get("provider_id") == provider_id:
                return row
        return None

    def recent_failures_for_fingerprint(
        self,
        fingerprint: str,
        *,
        workspace: str = "work",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        fp = (fingerprint or "").strip()
        if not fp:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM airix_routing_events
                WHERE workspace=? AND prompt_fingerprint=?
                  AND outcome IN ('failure', 'unavailable')
                ORDER BY created_at DESC LIMIT ?
                """,
                (workspace, fp, max(1, min(50, limit))),
            ).fetchall()
        return [dict(r) for r in rows]

    def identical_failure_count(
        self,
        fingerprint: str,
        provider_id: str,
        *,
        workspace: str = "work",
        error_code: str | None = None,
    ) -> int:
        fails = self.recent_failures_for_fingerprint(fingerprint, workspace=workspace, limit=20)
        n = 0
        for row in fails:
            if row.get("provider_id") != provider_id:
                continue
            if error_code and row.get("error_code") and row.get("error_code") != error_code:
                continue
            n += 1
        return n

    def analytics(self, *, workspace: str = "work") -> dict[str, Any]:
        workspace = normalize_workspace(workspace)
        with self.db.connect() as conn:
            events = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT * FROM airix_routing_events
                    WHERE workspace=?
                    ORDER BY created_at DESC LIMIT 500
                    """,
                    (workspace,),
                ).fetchall()
            ]
        by_tier: dict[str, int] = {}
        by_provider: dict[str, int] = {}
        successes = failures = cancels = retries = escalations = 0
        runtime_total = 0
        tokens_est = 0
        tokens_actual = 0
        t0_avoided = 0
        permission_blocked = 0
        findings_reused = 0
        for ev in events:
            tier = str(ev.get("tier") or "?")
            prov = str(ev.get("provider_id") or "?")
            by_tier[tier] = by_tier.get(tier, 0) + 1
            by_provider[prov] = by_provider.get(prov, 0) + 1
            outcome = str(ev.get("outcome") or "")
            if outcome == "success":
                successes += 1
            elif outcome == "cancel":
                cancels += 1
            else:
                failures += 1
            retries += int(ev.get("retries") or 0)
            runtime_total += int(ev.get("runtime_ms") or 0)
            if ev.get("t0_llm_avoided"):
                t0_avoided += 1
            if ev.get("permission_denied") or ev.get("error_code") == "permission_denied":
                permission_blocked += 1
            if ev.get("escalated_to"):
                escalations += 1
            reused_raw = ev.get("findings_reused_json") or "[]"
            if isinstance(reused_raw, str):
                try:
                    reused_list = json.loads(reused_raw)
                except (TypeError, json.JSONDecodeError):
                    reused_list = []
            else:
                reused_list = reused_raw if isinstance(reused_raw, list) else []
            findings_reused += len(reused_list)
            tok = ev.get("actual_tokens")
            if tok is not None:
                tokens_actual += int(tok)
            else:
                est = ev.get("estimated_tokens")
                if est is not None:
                    tokens_est += int(est)
                else:
                    band = str(ev.get("estimated_usage") or "")
                    tokens_est += {"Very Low": 200, "Low": 800, "Moderate": 2500, "High": 8000}.get(
                        band, 1000
                    )
        total = len(events)
        return {
            "phase": 5,
            "workspace": workspace,
            "executions_total": total,
            "executions_by_tier": by_tier,
            "executions_by_provider": by_provider,
            "success_rate": round(successes / total, 3) if total else None,
            "successes": successes,
            "failures": failures,
            "cancels": cancels,
            "retries_total": retries,
            "escalations_total": escalations,
            "average_runtime_ms": int(runtime_total / total) if total else None,
            "estimated_tokens_total": tokens_est,
            "actual_tokens_total": tokens_actual,
            "t0_llm_avoided": t0_avoided,
            "permission_blocked": permission_blocked,
            "prior_findings_reused": findings_reused,
            "provider_stats": self.provider_stats(workspace=workspace),
            "findings_count": len(self.list_findings(workspace=workspace, limit=100)),
        }
