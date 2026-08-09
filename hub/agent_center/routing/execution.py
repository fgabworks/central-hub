"""AiriX Smart Routing Phase 3 — execute recommended routes via existing adapters."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from hub.agent_center.openai_tools import AgentToolsContext, execute_tool
from hub.agent_center.profiles import get_profile, normalize_tools
from hub.agent_center.redact import redact_text
from hub.agent_center.routing.context import (
    build_minimal_context_preview,
    provider_to_adapter_id,
    select_minimal_tools,
    select_repository_ids,
)
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import RouteRecommendation, RoutingSettings
from hub.agent_center.routing.profile import INTERNAL_WORK_PROFILE
from hub.agent_center.service import AgentCenterError, AgentCenterService


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_fingerprint(prompt: str, agent: str = "") -> str:
    base = (prompt or "").strip().lower()
    raw = f"{base}::{(agent or '').strip().lower()}" if agent else base
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def prompt_only_fingerprint(prompt: str) -> str:
    return _prompt_fingerprint(prompt, "")


def _extract_query(prompt: str) -> str:
    text = (prompt or "").strip()
    m = re.search(r"[\"']([^\"']{2,120})[\"']", text)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(
        r"^(look\s*up|find|show\s+me|list|search|what\s+is|status\s+of)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    return (cleaned or text)[:200]


def _parse_runtime_ms(started_at: str | None, finished_at: str | None) -> int:
    if not started_at or not finished_at:
        return 0
    try:
        a = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        b = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        return max(0, int((b - a).total_seconds() * 1000))
    except ValueError:
        return 0


def _tokens_from_usage(usage: Any) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in ("total_tokens", "total", "output_tokens", "completion_tokens"):
        if usage.get(key) is not None:
            try:
                return int(usage[key])
            except (TypeError, ValueError):
                continue
    inp = usage.get("input_tokens") or usage.get("prompt_tokens")
    out = usage.get("output_tokens") or usage.get("completion_tokens")
    try:
        if inp is not None or out is not None:
            return int(inp or 0) + int(out or 0)
    except (TypeError, ValueError):
        return None
    return None


class RouteExecutor:
    """
    Executes Smart Routing plans.

    T0 → deterministic Hub tools (no LLM)
    T1/T2/T3 → existing AgentCenterService.start_run / cancel_run
    """

    def __init__(
        self,
        agent_center: AgentCenterService,
        *,
        availability_loader: Callable[[], dict[str, dict[str, Any]]] | None = None,
        history: RoutingHistoryStore | None = None,
    ) -> None:
        self.agent_center = agent_center
        self._availability_loader = availability_loader
        self.history = history
        self._lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}
        self._fingerprints: dict[str, str] = {}

    def get_status(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._active.get(execution_id)
            return dict(row) if row is not None else None

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._active.values() if v.get("status") in {"queued", "running"}]

    def cancel(self, execution_id: str, *, actor: str = "owner") -> dict[str, Any]:
        with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                raise AgentCenterError("Execution not found", code="execution_not_found")
            if row.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
                return dict(row)
            row["cancel_requested"] = True
            row["status"] = "cancelled"
            row["finished_at"] = _utcnow()
            row["error"] = "Cancelled by user"
            fp = row.get("fingerprint")
            if fp and self._fingerprints.get(fp) == execution_id:
                self._fingerprints.pop(fp, None)
            agent_run_id = row.get("agent_run_id") or ""
        if agent_run_id:
            try:
                self.agent_center.cancel_run(
                    agent_run_id, profile_id=INTERNAL_WORK_PROFILE
                )
            except AgentCenterError:
                pass
        with self._lock:
            final = dict(self._active.get(execution_id) or row)
        self._record_history(final)
        return final

    def execute(
        self,
        *,
        prompt: str,
        recommendation: RouteRecommendation,
        settings: RoutingSettings,
        agent_override: str | None = None,
        repository_ids: list[str] | None = None,
        approve_codex: bool = False,
        force: bool = False,
        workspace: str = "work",
        attempt: int = 0,
        candidate_findings: list[dict[str, Any]] | None = None,
        previous_partial: str = "",
    ) -> dict[str, Any]:
        prompt_n = (prompt or "").strip()
        if not prompt_n:
            raise AgentCenterError("Prompt is required", code="prompt_required")

        provider_id = (agent_override or recommendation.recommended_agent or "").strip()
        if not provider_id:
            raise AgentCenterError("No agent recommended", code="agent_required")

        attempt_n = max(0, int(attempt or 0))
        if attempt_n > int(settings.max_retries):
            raise AgentCenterError(
                f"Retry limit reached (max_retries={settings.max_retries})",
                code="retry_limit",
            )

        prompt_fp = prompt_only_fingerprint(prompt_n)
        if self.history is not None and attempt_n > 0 and not force:
            identical = self.history.identical_failure_count(
                prompt_fp, provider_id, workspace=workspace
            )
            if identical > 0:
                raise AgentCenterError(
                    "Identical failed execution blocked — choose a stronger route or force retry",
                    code="identical_retry_blocked",
                )

        advanced = provider_id in {"codex", "claude-code", "cursor-agent"}
        requires_approval = bool(recommendation.approval_required) or advanced
        if settings.require_approval_before_codex and advanced and not approve_codex:
            raise AgentCenterError(
                "Codex/advanced agent requires explicit approval before execution",
                code="approval_required",
            )

        adapter_id = provider_to_adapter_id(provider_id)
        fingerprint = _prompt_fingerprint(prompt_n, provider_id)
        context_preview = build_minimal_context_preview(
            prompt=prompt_n,
            classification=recommendation.classification,
            recommendation=recommendation,
            repository_ids=repository_ids,
            agent_override=provider_id,
            candidate_findings=candidate_findings,
        )
        if previous_partial:
            context_preview = {
                **context_preview,
                "partial_results": redact_text(previous_partial, limit=240),
            }

        with self._lock:
            existing_id = self._fingerprints.get(fingerprint)
            if existing_id and not force:
                existing = self._active.get(existing_id)
                if existing and existing.get("status") in {"queued", "running"}:
                    raise AgentCenterError(
                        "An execution for this prompt is already active",
                        code="duplicate_execution",
                    )
            execution_id = uuid.uuid4().hex
            row = {
                "id": execution_id,
                "fingerprint": fingerprint,
                "prompt_fingerprint": prompt_fp,
                "status": "queued",
                "phase": 3,
                "prompt": prompt_n,
                "provider_id": provider_id,
                "adapter_id": adapter_id,
                "tier": recommendation.recommended_tier,
                "task_type": recommendation.task_type,
                "estimated_usage": recommendation.estimated_usage,
                "approval_required": requires_approval,
                "approved": bool(approve_codex),
                "manual_override": bool(agent_override),
                "context": context_preview,
                "agent_run_id": None,
                "answer": "",
                "tool_results": [],
                "error": "",
                "cancel_requested": False,
                "created_at": _utcnow(),
                "started_at": None,
                "finished_at": None,
                "fallback_from": None,
                "escalated_to": recommendation.escalation_reason or "",
                "attempt": attempt_n,
                "workspace": workspace,
                "history_recorded": False,
                "partial_summary": redact_text(previous_partial, limit=240) if previous_partial else "",
            }
            self._active[execution_id] = row
            self._fingerprints[fingerprint] = execution_id

        try:
            if adapter_id is None:
                result = self._execute_t0(execution_id, prompt_n, recommendation, context_preview)
            else:
                result = self._execute_agent(
                    execution_id,
                    prompt_n,
                    recommendation,
                    context_preview,
                    adapter_id=adapter_id,
                    repository_ids=repository_ids,
                    settings=settings,
                )
        except AgentCenterError as exc:
            result = self._fail(execution_id, str(exc), code=exc.code)
        except Exception as exc:  # noqa: BLE001
            result = self._fail(execution_id, str(exc), code="execution_failed")

        if result.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
            self._record_history(result)
        return result

    def _record_history(self, row: dict[str, Any]) -> None:
        if self.history is None or row.get("history_recorded"):
            return
        status = str(row.get("status") or "")
        if status == "completed":
            outcome = "success"
        elif status == "cancelled":
            outcome = "cancel"
        elif status == "unavailable":
            outcome = "unavailable"
        else:
            outcome = "failure"
        usage = {}
        agent_run = row.get("agent_run") or {}
        if isinstance(agent_run, dict):
            usage = agent_run.get("usage") or {}
        tokens = _tokens_from_usage(usage)
        partial = row.get("partial_summary") or ""
        if not partial and row.get("answer"):
            partial = redact_text(str(row.get("answer") or ""), limit=240)
        elif row.get("tool_results") and not partial:
            bits = []
            for item in row.get("tool_results") or []:
                if item.get("ok"):
                    res = item.get("result") or {}
                    bits.append(str(res.get("summary") or item.get("tool") or "")[:80])
            partial = redact_text("; ".join(bits), limit=240)
        try:
            self.history.record_event(
                {
                    "id": row.get("id"),
                    "workspace": row.get("workspace") or "work",
                    "provider_id": row.get("provider_id"),
                    "adapter_id": row.get("adapter_id") or "",
                    "tier": row.get("tier") or "",
                    "task_type": row.get("task_type") or "general",
                    "status": status,
                    "outcome": outcome,
                    "retries": int(row.get("attempt") or 0),
                    "runtime_ms": _parse_runtime_ms(row.get("started_at"), row.get("finished_at")),
                    "estimated_usage": row.get("estimated_usage") or "",
                    "actual_tokens": tokens,
                    "usage_source": "actual" if tokens is not None else "estimate",
                    "t0_llm_avoided": row.get("mode") == "deterministic" and outcome == "success",
                    "fallback_from": row.get("fallback_from") or "",
                    "escalated_to": row.get("escalated_to") or "",
                    "prompt_fingerprint": row.get("prompt_fingerprint") or "",
                    "error_code": row.get("error_code") or "",
                    "partial_summary": partial,
                    "answer": row.get("answer") or "",
                    "prompt_for_keywords": row.get("prompt") or "",
                }
            )
            with self._lock:
                if row.get("id") in self._active:
                    self._active[row["id"]]["history_recorded"] = True
                    self._active[row["id"]]["partial_summary"] = partial
        except Exception:  # noqa: BLE001
            pass

    def _fail(self, execution_id: str, error: str, *, code: str = "execution_failed") -> dict[str, Any]:
        with self._lock:
            row = self._active.get(execution_id)
            if not row:
                raise AgentCenterError(error, code=code)
            row["status"] = "unavailable" if code == "unavailable" else "failed"
            row["error"] = error
            row["error_code"] = code
            row["finished_at"] = _utcnow()
            fp = row.get("fingerprint")
            if fp and self._fingerprints.get(fp) == execution_id:
                self._fingerprints.pop(fp, None)
            return dict(row)

    def _update(self, execution_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            row = self._active[execution_id]
            if row.get("cancel_requested") and fields.get("status") not in {
                "cancelled",
                "failed",
                "completed",
            }:
                row["status"] = "cancelled"
                row["finished_at"] = _utcnow()
                return dict(row)
            row.update(fields)
            if fields.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
                fp = row.get("fingerprint")
                if fp and self._fingerprints.get(fp) == execution_id:
                    self._fingerprints.pop(fp, None)
            return dict(row)

    def _tools_context(self, tool_ids: list[str], repository_ids: list[str]) -> AgentToolsContext:
        svc = self.agent_center
        profile = get_profile(INTERNAL_WORK_PROFILE)
        tools = normalize_tools(profile, tool_ids)
        return AgentToolsContext(
            registry=svc.registry,
            repository_ids=list(repository_ids or []),
            notebook=svc.notebook,
            sql_store=svc.sql_store,
            uid_index=svc.uid_index,
            email=svc.email,
            calendar=svc.calendar,
            job_store=svc.job_store,
            audit_store=svc.audit_store,
            dhis2_reports=svc.dhis2_reports,
            notepad_factory=svc.notepad_factory,
            profile_id=profile.id,
            workspace=profile.workspace,
            allowed_tools=set(tools),
        )

    def _execute_t0(
        self,
        execution_id: str,
        prompt: str,
        recommendation: RouteRecommendation,
        context_preview: dict[str, Any],
    ) -> dict[str, Any]:
        self._update(execution_id, status="running", started_at=_utcnow())
        tools = list(context_preview.get("tool_ids") or select_minimal_tools(recommendation.classification))
        ctx = self._tools_context(tools, [])
        query = _extract_query(prompt)
        results: list[dict[str, Any]] = []
        for name in tools:
            if self.get_status(execution_id) and self.get_status(execution_id).get("cancel_requested"):
                return self._update(execution_id, status="cancelled", finished_at=_utcnow())
            args: dict[str, Any] = {"query": query, "limit": 10}
            if name == "uid_lookup":
                args = {"query": query, "limit": 10}
            elif name == "sql_lookup":
                args = {"query": query, "limit": 10}
            elif name in {"jobs_lookup", "audit_lookup"}:
                args = {"limit": 15}
            raw = execute_tool(name, args, ctx)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            results.append({"tool": name, "ok": "error" not in parsed, "result": parsed})

        lines = [f"Deterministic lookup for: {query}"]
        for item in results:
            summary = ""
            res = item.get("result") or {}
            if isinstance(res, dict):
                summary = str(res.get("summary") or res.get("error") or "")[:400]
            lines.append(f"- {item['tool']}: {summary or ('ok' if item['ok'] else 'failed')}")
        answer = "\n".join(lines)
        priors = context_preview.get("prior_findings") or []
        if priors:
            answer += "\n\nPrior findings used: " + "; ".join(
                str(p.get("summary") or "")[:120] for p in priors[:3]
            )
        return self._update(
            execution_id,
            status="completed",
            answer=answer,
            tool_results=results,
            finished_at=_utcnow(),
            mode="deterministic",
        )

    def _provider_available(self, adapter_id: str) -> tuple[bool, str]:
        if self._availability_loader is None:
            adapter = self.agent_center.get_agent(adapter_id)
            if adapter is None:
                return False, f"Unknown agent: {adapter_id}"
            av = adapter.availability()
            ok = av.status in {"available", "degraded"}
            return ok, av.detail or av.status
        try:
            raw = self._availability_loader() or {}
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        info = raw.get(adapter_id) or {}
        status = str(info.get("status") or "")
        runnable = bool(info.get("runnable")) or status in {"available", "degraded"}
        return runnable, str(info.get("detail") or status or "unavailable")

    def _execute_agent(
        self,
        execution_id: str,
        prompt: str,
        recommendation: RouteRecommendation,
        context_preview: dict[str, Any],
        *,
        adapter_id: str,
        repository_ids: list[str] | None,
        settings: RoutingSettings,
    ) -> dict[str, Any]:
        available, detail = self._provider_available(adapter_id)
        chosen = adapter_id
        fallback_from = None
        if not available:
            alt = recommendation.alternative_agent
            candidates: list[str] = []
            if alt:
                mapped = provider_to_adapter_id(alt)
                if mapped:
                    candidates.append(mapped)
            if "grok" not in candidates and adapter_id != "grok":
                candidates.append("grok")
            candidates = [c for c in candidates if c != "codex"]
            if settings.allow_escalation is False:
                candidates = [c for c in candidates if c not in {"claude-code", "cursor-agent"}]
            found = None
            for cand in candidates:
                ok, _d = self._provider_available(cand)
                if ok:
                    found = cand
                    break
            if found is None:
                return self._fail(
                    execution_id,
                    detail or f"Provider {adapter_id} unavailable",
                    code="unavailable",
                )
            fallback_from = adapter_id
            chosen = found

        self._update(
            execution_id,
            status="running",
            started_at=_utcnow(),
            adapter_id=chosen,
            fallback_from=fallback_from,
        )

        classification = recommendation.classification
        tools = list(context_preview.get("tool_ids") or select_minimal_tools(classification))
        repos = select_repository_ids(
            classification, list(context_preview.get("repository_ids") or repository_ids or [])
        )
        if chosen == "codex" and not repos:
            try:
                selectable = self.agent_center.repositories(profile_id=INTERNAL_WORK_PROFILE)
                repos = [str(r["id"]) for r in selectable[:1] if r.get("id")]
            except Exception:  # noqa: BLE001
                repos = []
            if not repos:
                return self._fail(
                    execution_id,
                    "Codex requires a selected connected repository",
                    code="repository_required",
                )

        hints = list(context_preview.get("hints") or [])[:6]
        for finding in (context_preview.get("prior_findings") or [])[:3]:
            summary = str(finding.get("summary") or "").strip()
            if summary:
                hints.append(f"prior_finding: {summary[:160]}")
        if context_preview.get("partial_results"):
            hints.append(f"partial_results: {str(context_preview.get('partial_results'))[:160]}")

        payload = {
            "profile_id": INTERNAL_WORK_PROFILE,
            "mode": "ask",
            "prompt": prompt,
            "agent_id": chosen,
            "tool_ids": tools,
            "repository_ids": repos,
            "hints": hints[:10],
            "files": {},
        }
        run = self.agent_center.start_run(payload)
        run_id = str(run.get("id") or "")
        status = str(run.get("status") or "")
        self._update(execution_id, agent_run_id=run_id)

        if status == "unavailable":
            return self._fail(
                execution_id,
                str(run.get("error") or detail or "Agent unavailable"),
                code="unavailable",
            )

        if status in {"succeeded", "completed"} or run.get("answer"):
            return self._update(
                execution_id,
                status="completed",
                answer=str(run.get("answer") or ""),
                finished_at=_utcnow(),
                agent_run=run,
            )

        return self._update(
            execution_id,
            status="running",
            agent_run=run,
            answer=str(run.get("answer") or ""),
        )

    def refresh(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                return None
            agent_run_id = row.get("agent_run_id")
            if not agent_run_id or row.get("status") not in {"queued", "running"}:
                return dict(row)
        try:
            run = self.agent_center.get_run(
                str(agent_run_id), profile_id=INTERNAL_WORK_PROFILE
            )
        except AgentCenterError:
            return self.get_status(execution_id)
        status = str(run.get("status") or "")
        if status in {"succeeded", "completed"}:
            result = self._update(
                execution_id,
                status="completed",
                answer=str(run.get("answer") or ""),
                finished_at=str(run.get("finished_at") or _utcnow()),
                agent_run=run,
            )
            self._record_history(result)
            return result
        if status in {"failed", "cancelled", "unavailable"}:
            result = self._update(
                execution_id,
                status=status,
                error=str(run.get("error") or ""),
                finished_at=str(run.get("finished_at") or _utcnow()),
                agent_run=run,
                answer=str(run.get("answer") or ""),
            )
            self._record_history(result)
            return result
        return self._update(execution_id, agent_run=run, answer=str(run.get("answer") or ""))
