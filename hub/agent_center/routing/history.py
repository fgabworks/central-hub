"""AiriX Smart Routing Phase 3 — execution history, stats, and analytics."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.redact import redact_text
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
        t0_avoided = 1 if payload.get("t0_llm_avoided") else 0
        fallback_from = str(payload.get("fallback_from") or "").strip()
        escalated_to = str(payload.get("escalated_to") or "").strip()
        event_id = str(payload.get("id") or _uid())
        partial = redact_text(str(payload.get("partial_summary") or ""), limit=240)
        row = {
            "id": event_id,
            "workspace": workspace,
            "created_at": str(payload.get("created_at") or _now()),
            "provider_id": provider_id,
            "adapter_id": str(payload.get("adapter_id") or ""),
            "tier": str(payload.get("tier") or ""),
            "task_type": task_type,
            "status": status,
            "outcome": outcome,
            "retries": retries,
            "runtime_ms": runtime_ms,
            "estimated_usage": str(payload.get("estimated_usage") or "")[:40],
            "actual_tokens": actual_tokens_i,
            "usage_source": str(payload.get("usage_source") or "estimate")[:20],
            "t0_llm_avoided": t0_avoided,
            "fallback_from": fallback_from,
            "escalated_to": escalated_to,
            "prompt_fingerprint": str(payload.get("prompt_fingerprint") or "")[:64],
            "error_code": str(payload.get("error_code") or "")[:80],
            "partial_summary": partial,
        }
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO airix_routing_events(
                    id, workspace, created_at, provider_id, adapter_id, tier, task_type,
                    status, outcome, retries, runtime_ms, estimated_usage, actual_tokens,
                    usage_source, t0_llm_avoided, fallback_from, escalated_to,
                    prompt_fingerprint, error_code, partial_summary
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["id"],
                    row["workspace"],
                    row["created_at"],
                    row["provider_id"],
                    row["adapter_id"],
                    row["tier"],
                    row["task_type"],
                    row["status"],
                    row["outcome"],
                    row["retries"],
                    row["runtime_ms"],
                    row["estimated_usage"],
                    row["actual_tokens"],
                    row["usage_source"],
                    row["t0_llm_avoided"],
                    row["fallback_from"],
                    row["escalated_to"],
                    row["prompt_fingerprint"],
                    row["error_code"],
                    row["partial_summary"],
                ),
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
                self.save_finding(finding, workspace=workspace)
        return row

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

    def save_finding(self, finding: dict[str, Any], *, workspace: str = "work") -> dict[str, Any]:
        workspace = normalize_workspace(workspace)
        summary = redact_text(str(finding.get("summary") or "").strip(), limit=200)
        if not summary:
            return {}
        row = {
            "id": _uid(),
            "workspace": workspace,
            "created_at": _now(),
            "task_type": str(finding.get("task_type") or "general"),
            "keywords_json": json.dumps(list(finding.get("keywords") or [])[:12]),
            "summary": summary,
            "provider_id": str(finding.get("provider_id") or ""),
            "source_event_id": str(finding.get("source_event_id") or ""),
            "hit_count": 0,
        }
        with self.db.connect() as conn:
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
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        workspace = normalize_workspace(workspace)
        limit = max(1, min(100, int(limit)))
        with self.db.connect() as conn:
            if task_type:
                rows = conn.execute(
                    """
                    SELECT * FROM airix_routing_findings
                    WHERE workspace=? AND task_type=?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (workspace, task_type, limit),
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
        successes = failures = cancels = retries = 0
        runtime_total = 0
        tokens_est = 0
        tokens_actual = 0
        t0_avoided = 0
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
            tok = ev.get("actual_tokens")
            if tok is not None:
                tokens_actual += int(tok)
            else:
                # Rough band → token estimate for diagnostics only.
                band = str(ev.get("estimated_usage") or "")
                tokens_est += {"Very Low": 200, "Low": 800, "Moderate": 2500, "High": 8000}.get(
                    band, 1000
                )
        total = len(events)
        return {
            "phase": 3,
            "workspace": workspace,
            "executions_total": total,
            "executions_by_tier": by_tier,
            "executions_by_provider": by_provider,
            "success_rate": round(successes / total, 3) if total else None,
            "successes": successes,
            "failures": failures,
            "cancels": cancels,
            "retries_total": retries,
            "average_runtime_ms": int(runtime_total / total) if total else None,
            "estimated_tokens_total": tokens_est,
            "actual_tokens_total": tokens_actual,
            "t0_llm_avoided": t0_avoided,
            "provider_stats": self.provider_stats(workspace=workspace),
            "findings_count": len(self.list_findings(workspace=workspace, limit=100)),
        }
