"""AiriX Smart Routing — execute recommended routes via existing adapters."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from hub.agent_center.models import DEFAULT_TIMEOUT_SECONDS
from hub.agent_center.openai_tools import AgentToolsContext, execute_tool
from hub.agent_center.profiles import get_profile, normalize_tools
from hub.agent_center.redact import redact_text
from hub.agent_center.routing.context import (
    build_minimal_context_preview,
    provider_to_adapter_id,
    select_minimal_tools,
    select_repository_ids,
    normalize_context_sources,
    normalize_interaction_mode,
    tools_for_repository_knowledge,
)
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.lifecycle import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_STALE_SECONDS,
    DEFAULT_STEP_WAIT_SECONDS,
    is_stale,
    is_terminal,
    log_lifecycle,
    normalize_status,
    public_execution_fields,
)
from hub.agent_center.routing.models import RouteRecommendation, RoutingSettings
from hub.agent_center.routing.profile import INTERNAL_WORK_PROFILE
from hub.agent_center.service import AgentCenterError, AgentCenterService

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_provider_answer(run: dict[str, Any] | None) -> str:
    """Pull the terminal provider answer from a child run / public execution row."""
    if not isinstance(run, dict):
        return ""
    for key in ("answer", "content", "output_text", "final_answer"):
        text = str(run.get(key) or "").strip()
        if text:
            return text
    agent_run = run.get("agent_run") if isinstance(run.get("agent_run"), dict) else {}
    for key in ("answer", "content", "output_text", "final_answer"):
        text = str(agent_run.get(key) or "").strip()
        if text:
            return text
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    text = str(context.get("answer") or "").strip()
    return text


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
    from hub.agent_center.routing.cost import parse_usage

    parsed = parse_usage(usage)
    return parsed.get("total_tokens")


def _usage_breakdown(usage: Any) -> dict[str, Any]:
    from hub.agent_center.routing.cost import parse_usage

    return parse_usage(usage)


def _attach_telemetry(row: dict[str, Any]) -> dict[str, Any]:
    from hub.agent_center.routing.telemetry import attach_execution_telemetry

    return attach_execution_telemetry(row)

class RouteExecutor:
    """
    Executes Smart Routing plans.

    T0 → deterministic Hub tools (no LLM)
    T1/T2/T3 → existing AgentCenterService.start_run / cancel_run
    """

    # Overridable in tests; production uses DEFAULT_STEP_WAIT_SECONDS.
    step_wait_seconds: float = DEFAULT_STEP_WAIT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS

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
            return public_execution_fields(dict(row)) if row is not None else None

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                public_execution_fields(dict(v))
                for v in self._active.values()
                if str(v.get("status") or "") in {"queued", "running"}
            ]

    def cancel(self, execution_id: str, *, actor: str = "owner") -> dict[str, Any]:
        with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                raise AgentCenterError("Execution not found", code="execution_not_found")
            if is_terminal(str(row.get("status") or "")):
                return public_execution_fields(dict(row))
            row["cancel_requested"] = True
            row["status"] = "cancelled"
            row["finished_at"] = _utcnow()
            row["error"] = "Cancelled by user"
            row["error_code"] = "cancelled"
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
        final = public_execution_fields(final)
        log_lifecycle(
            event="step_cancelled",
            status="cancelled",
            provider_id=str(final.get("provider_id") or ""),
            started_at=str(final.get("started_at") or ""),
            finished_at=str(final.get("finished_at") or ""),
            execution_id=execution_id,
            failure_reason="Cancelled by user",
        )
        self._record_history(final)
        return final

    def cancel_all_active(self, *, workspace: str = "work", actor: str | None = None) -> list[dict[str, Any]]:
        """Cancel every in-flight step (used when parent orchestration is cancelled)."""
        cancelled: list[dict[str, Any]] = []
        for row in self.list_active():
            if workspace and row.get("workspace") not in {None, workspace}:
                continue
            if actor and row.get("actor") not in {None, actor}:
                continue
            try:
                cancelled.append(self.cancel(str(row["id"]), actor=actor or "owner"))
            except AgentCenterError:
                continue
        return cancelled

    def execute(
        self,
        *,
        prompt: str,
        recommendation: RouteRecommendation,
        settings: RoutingSettings,
        agent_override: str | None = None,
        repository_ids: list[str] | None = None,
        active_repository_id: str | None = None,
        selected_repository_id: str | None = None,
        approve_codex: bool = False,
        force: bool = False,
        workspace: str = "work",
        attempt: int = 0,
        candidate_findings: list[dict[str, Any]] | None = None,
        previous_partial: str = "",
        tool_ids_override: list[str] | None = None,
        actor: str = "owner",
        rbac_role: str = "",
        model: str | None = None,
        manual_override: bool = False,
        routing_mode: str | None = None,
        conversation_id: str | None = None,
        context_fingerprint: str | None = None,
        interaction_mode: str | None = None,
        context_sources: list[str] | None = None,
        dhis2_environment: str | None = None,
    ) -> dict[str, Any]:
        prompt_n = (prompt or "").strip()
        if not prompt_n:
            raise AgentCenterError("Prompt is required", code="prompt_required")

        from hub.agent_center.routing.context import normalize_routing_mode

        interaction = normalize_interaction_mode(interaction_mode or routing_mode)
        mode_key = normalize_routing_mode(interaction)
        # Explicit UI/API selection is authoritative; never silently swap providers.
        is_manual = bool(manual_override) or mode_key == "direct"
        recommended_provider = (recommendation.recommended_agent or "").strip()
        provider_id = (agent_override or recommended_provider).strip()
        if not provider_id:
            raise AgentCenterError("No agent recommended", code="agent_required")
        adapter_probe = provider_to_adapter_id(provider_id)
        # Real providers must never resolve to Hub Simulator via mapping bugs.
        if (
            is_manual
            and provider_id not in {"low-cost", "hub-simulator"}
            and adapter_probe == "hub-simulator"
        ):
            raise AgentCenterError(
                f"Selected provider {provider_id!r} must not resolve to Hub Simulator",
                code="provider_mismatch",
            )

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
        from hub.agent_center.repository_context import (
            normalize_repository_ids,
            resolve_repository_context,
        )

        try:
            selectable = self.agent_center.repositories(profile_id=INTERNAL_WORK_PROFILE)
        except Exception:  # noqa: BLE001
            selectable = []
        # One resolver for Smart/Ask/Inspect/Plan/Agent — never drop a selected learned repo.
        resolved_scope = resolve_repository_context(
            agent_id=adapter_id or provider_id or "deterministic",
            repository_ids=repository_ids,
            active_repository_id=active_repository_id,
            selected_repository_id=selected_repository_id,
            repositories=selectable,
        )
        resolved_repo_ids = list(resolved_scope.get("repository_ids") or [])
        if not resolved_repo_ids:
            # Soft fallback: keep raw explicit IDs when resolver had nothing selectable yet.
            resolved_repo_ids = normalize_repository_ids(repository_ids) or normalize_repository_ids(
                [selected_repository_id]
            )
        context_preview = build_minimal_context_preview(
            prompt=prompt_n,
            classification=recommendation.classification,
            recommendation=recommendation,
            repository_ids=resolved_repo_ids,
            agent_override=provider_id,
            candidate_findings=candidate_findings,
            context_sources=context_sources,
        )
        # Ensure packed repository_ids stay aligned with the canonical resolver.
        if resolved_repo_ids and not context_preview.get("repository_ids"):
            context_preview["repository_ids"] = resolved_repo_ids[:2]
        repository_knowledge: dict[str, Any] = {
            "profiles": [],
            "items": [],
            "item_count": 0,
            "include_full_index": False,
            "diagnostics": {
                "used": False,
                "knowledge_entries_used": 0,
                "freshness": "none",
                "repository_ids": [],
                "requested_repository_ids": resolved_repo_ids,
            },
        }
        if resolved_repo_ids:
            try:
                repository_knowledge = self.agent_center.repository_intelligence.retrieve(
                    list(resolved_repo_ids), prompt_n
                )
            except Exception:  # noqa: BLE001
                repository_knowledge = {
                    **repository_knowledge,
                    "diagnostics": {
                        **(repository_knowledge.get("diagnostics") or {}),
                        "used": False,
                        "freshness": "failed",
                        "requested_repository_ids": resolved_repo_ids,
                    },
                }
        context_preview["repository_intelligence"] = repository_knowledge
        context_preview["tool_ids"] = tools_for_repository_knowledge(
            list(context_preview.get("tool_ids") or []), repository_knowledge
        )
        # Relevant files / data explorer context sources add search tools — they must
        # never disable Repository Intelligence attachment.
        sources = normalize_context_sources(context_sources)
        if "files" in sources or "data_explorer" in sources:
            context_preview["tool_ids"] = list(
                dict.fromkeys(
                    list(context_preview.get("tool_ids") or []) + ["repo_search", "read_file"]
                )
            )[:6]
        for item in (repository_knowledge.get("items") or [])[:4]:
            summary = str(item.get("summary") or "").strip()
            if summary:
                context_preview.setdefault("hints", []).append(
                    f"repo_knowledge:{item.get('category')}:{item.get('path')}:{summary[:180]}"
                )
        repository_ids = resolved_repo_ids
        ri_diag = dict(repository_knowledge.get("diagnostics") or {})
        initial_context_items = [f"repository:{rid}" for rid in resolved_repo_ids]
        initial_context_items.extend(
            f"context_source:{source}" for source in normalize_context_sources(context_sources)
        )
        for item in (repository_knowledge.get("items") or [])[:6]:
            rid = str(item.get("repository_id") or "").strip()
            path = str(item.get("path") or "").strip()
            if path:
                initial_context_items.append(f"repository_intelligence:{rid}:{path}")
        initial_context_items = list(dict.fromkeys(initial_context_items))
        if active_repository_id:
            context_preview = {
                **context_preview,
                "active_repository_id": str(active_repository_id).strip(),
            }
        if selected_repository_id:
            context_preview = {
                **context_preview,
                "selected_repository_id": str(selected_repository_id).strip(),
            }
        if tool_ids_override:
            context_preview = {
                **context_preview,
                "tool_ids": list(tool_ids_override)[:6],
            }
        if previous_partial:
            context_preview = {
                **context_preview,
                "partial_results": redact_text(previous_partial, limit=240),
            }
        # Preserve explicit UI/API model through routing (validated later per provider).
        selected_model = (model or "").strip()
        if selected_model:
            context_preview = {**context_preview, "model": selected_model}
        if conversation_id:
            context_preview = {
                **context_preview,
                "conversation_id": str(conversation_id).strip(),
            }
        if context_fingerprint:
            context_preview = {
                **context_preview,
                "context_fingerprint": str(context_fingerprint).strip()[:128],
            }
        context_preview = {
            **context_preview,
            "routing_mode": mode_key,
            "interaction_mode": interaction,
            "context_sources": normalize_context_sources(context_sources),
            "dhis2_environment": (
                str(dhis2_environment or "").strip().lower()
                if str(dhis2_environment or "").strip().lower() in {"stage", "live"}
                else ""
            ),
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
                "phase": 5,
                "prompt": prompt_n,
                "provider_id": provider_id,
                "adapter_id": adapter_id,
                "tier": recommendation.recommended_tier,
                "task_type": recommendation.task_type,
                "estimated_usage": recommendation.estimated_usage,
                "approval_required": requires_approval,
                "approved": bool(approve_codex),
                "manual_override": is_manual,
                "routing_mode": mode_key,
                "interaction_mode": interaction,
                "selected_provider": (agent_override or "").strip() or provider_id,
                "recommended_provider": recommended_provider if mode_key != "direct" else provider_id,
                "resolved_provider": provider_id,
                "selected_model": selected_model,
                "recommended_model": str(
                    getattr(recommendation, "recommended_model", None) or ""
                ).strip()
                if mode_key != "direct"
                else selected_model,
                "resolved_model": selected_model,
                "fallback_reason": "",
                "session_reused": False,
                "context": context_preview,
                "repository_intelligence_diagnostics": dict(
                    repository_knowledge.get("diagnostics") or {}
                ),
                "context_items": initial_context_items,
                "context_chars": int(ri_diag.get("context_chars_contributed") or 0),
                "prior_findings": list(context_preview.get("prior_findings") or []),
                "rbac_role": (rbac_role or "").strip(),
                "_routing_settings": settings,
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
                "actor": (actor or "owner").strip() or "owner",
                "history_recorded": False,
                "partial_summary": redact_text(previous_partial, limit=240) if previous_partial else "",
            }
            self._active[execution_id] = row
            self._fingerprints[fingerprint] = execution_id
            logger.info(
                "airix_provider_resolution selected=%s recommended=%s resolved=%s "
                "selected_model=%s recommended_model=%s resolved_model=%s "
                "fallback_reason=%s manual_override=%s",
                row["selected_provider"] or "-",
                row["recommended_provider"] or "-",
                row["resolved_provider"] or "-",
                row["selected_model"] or "-",
                row["recommended_model"] or "-",
                row["resolved_model"] or "-",
                "-",
                str(is_manual).lower(),
            )

        try:
            signals = set(recommendation.classification.signals or [])
            authoritative_data = (
                "authoritative_data_query" in signals
                or "data_query" in signals
                or "structured_data_lookup" in signals
            )
            # Direct Agent: skip Smart Routing / T0 / automatic escalation entirely.
            if mode_key == "direct":
                if adapter_id is None:
                    return self._fail(
                        execution_id,
                        "Direct Agent requires an explicit AI provider (not T0/deterministic).",
                        code="agent_required",
                    )
                result = self._execute_agent(
                    execution_id,
                    prompt_n,
                    recommendation,
                    context_preview,
                    adapter_id=adapter_id,
                    repository_ids=repository_ids,
                    settings=settings,
                    manual_override=True,
                )
            else:
                # Prefer T0 + connected DB before any selected AI for deterministic-capable work.
                try_t0_first = interaction in {"inspect", "plan"} or (
                    not is_manual
                    and (
                        adapter_id is None
                        or (
                            recommendation.classification.deterministic_capable
                            and (
                                authoritative_data
                                or recommendation.recommended_agent == "deterministic"
                            )
                        )
                    )
                )

                def _escalate_to_ai(
                    *,
                    chosen: str,
                    reason: str,
                    prior: dict[str, Any],
                    preserve_model: bool,
                ) -> dict[str, Any]:
                    self._update(
                        execution_id,
                        status="running",
                        fallback_from="deterministic",
                        fallback_reason=reason,
                        escalated_to=chosen,
                        adapter_id=chosen,
                        resolved_provider=chosen,
                        finished_at=None,
                        answer="",
                        error="",
                        error_code="",
                        ai_escalation_occurred=True,
                        t0_failure_reason=prior.get("t0_failure_reason") or reason,
                        next_capability=prior.get("next_capability") or "ai_escalate",
                        db_query_attempted=bool(prior.get("db_query_attempted")),
                        synthesis_escalation=reason == "t0_explanation_synthesis",
                    )
                    esc_context = {
                        **context_preview,
                        "evidence_packet": prior.get("evidence_packet")
                        or context_preview.get("evidence_packet"),
                        "detected_filters": prior.get("detected_filters")
                        or context_preview.get("detected_filters"),
                        "completion_contract": prior.get("completion_contract")
                        or context_preview.get("completion_contract"),
                        "t0_failure_reason": prior.get("t0_failure_reason") or reason,
                        "allow_general_knowledge": reason == "t0_miss_general_knowledge",
                        "tool_results": prior.get("tool_results")
                        or context_preview.get("tool_results"),
                        "repository_intelligence_diagnostics": prior.get(
                            "repository_intelligence_diagnostics"
                        )
                        or (
                            (context_preview.get("repository_intelligence") or {}).get(
                                "diagnostics"
                            )
                            if isinstance(context_preview.get("repository_intelligence"), dict)
                            else {}
                        ),
                    }
                    if reason == "t0_explanation_synthesis":
                        # The model receives only the bounded, already-grounded packet
                        # and its retrieved RI entries; it does not reread the repo.
                        current_knowledge = context_preview.get("repository_intelligence")
                        current_knowledge = (
                            current_knowledge if isinstance(current_knowledge, dict) else {}
                        )
                        prior_knowledge = (
                            prior.get("repository_intelligence")
                            if isinstance(prior.get("repository_intelligence"), dict)
                            else {}
                        )
                        if not current_knowledge.get("items") and prior_knowledge.get("items"):
                            current_knowledge = prior_knowledge
                        esc_context["bounded_evidence_only"] = True
                        esc_context["synthesis_escalation"] = True
                        esc_context["repository_intelligence"] = {
                            **current_knowledge,
                            # Keep bounded items only for synthesis packing; diagnostics stay on the row.
                            "profiles": list(current_knowledge.get("profiles") or [])[:1],
                            "items": list(current_knowledge.get("items") or [])[:6],
                            "include_full_index": False,
                        }
                    if preserve_model and selected_model:
                        esc_context["model"] = selected_model
                    return self._execute_agent(
                        execution_id,
                        prompt_n,
                        recommendation,
                        esc_context,
                        adapter_id=chosen,
                        repository_ids=[]
                        if reason == "t0_miss_general_knowledge"
                        else repository_ids,
                        settings=settings,
                        manual_override=is_manual and preserve_model,
                    )

                result: dict[str, Any]
                if try_t0_first:
                    result = self._execute_t0(
                        execution_id, prompt_n, recommendation, context_preview
                    )
                    grounding = result.get("grounding") or {}
                    solved = bool(
                        grounding.get("task_solved") and grounding.get("answer_grounded")
                    )
                    if solved and result.get("status") == "completed" and result.get("answer"):
                        pass  # Deterministic success — do not escalate.
                    elif result.get("t0_capability_escalate"):
                        # AI can materially advance (e.g. query construction) after DB attempt.
                        preferred = adapter_id or provider_to_adapter_id(
                            str(
                                agent_override
                                or recommendation.alternative_agent
                                or recommendation.recommended_agent
                                or "grok"
                            )
                        )
                        candidates: list[str] = []
                        candidate_order = (
                            ("openai-api", "grok", preferred)
                            if str(result.get("t0_failure_reason") or "")
                            == "t0_explanation_synthesis"
                            else (
                                preferred,
                                provider_to_adapter_id(str(recommendation.alternative_agent or "")),
                                "openai-api",
                                "grok",
                            )
                        )
                        for cand in candidate_order:
                            if not cand or cand == "hub-simulator" or cand in candidates:
                                continue
                            candidates.append(cand)
                        if str(result.get("t0_failure_reason") or "") == "t0_explanation_synthesis":
                            from hub.agent_center.routing.cost import price_per_mtok

                            candidates.sort(
                                key=lambda candidate: (
                                    price_per_mtok(settings, candidate) <= 0,
                                    price_per_mtok(settings, candidate)
                                    if price_per_mtok(settings, candidate) > 0
                                    else float("inf"),
                                )
                            )
                        chosen_alt = None
                        for candidate in candidates:
                            ok, _detail = self._provider_available(candidate)
                            if ok:
                                chosen_alt = candidate
                                break
                        if chosen_alt is None:
                            # No capable AI — surface Cannot verify rather than inventing values.
                            from hub.agent_center.grounding import format_cannot_verify

                            cannot = format_cannot_verify(
                                repository_ids=repository_ids,
                                reason=str(
                                    result.get("t0_failure_reason")
                                    or "No capable AI provider available after deterministic tools."
                                ),
                            )
                            result = public_execution_fields(
                                self._update(
                                    execution_id,
                                    status="completed",
                                    answer=cannot,
                                    finished_at=_utcnow(),
                                    t0_capability_escalate=False,
                                    ai_escalation_occurred=False,
                                    next_capability="cannot_verify",
                                )
                            )
                        else:
                            result = _escalate_to_ai(
                                chosen=chosen_alt,
                                reason=str(
                                    result.get("t0_failure_reason")
                                    or "t0_capability_escalate"
                                ),
                                prior=result,
                                preserve_model=bool(adapter_id or selected_model),
                            )
                    elif result.get("t0_fallthrough") and not authoritative_data:
                        alt = recommendation.alternative_agent or "grok"
                        alt_adapter = provider_to_adapter_id(str(alt))
                        candidates = []
                        for cand in (adapter_id, alt_adapter, "openai-api", "grok"):
                            if not cand or cand == "hub-simulator" or cand in candidates:
                                continue
                            candidates.append(cand)
                        chosen_alt = None
                        for candidate in candidates:
                            ok, _detail = self._provider_available(candidate)
                            if ok:
                                chosen_alt = candidate
                                break
                        if chosen_alt is None:
                            result = self._fail(
                                execution_id,
                                "No capable AI provider available after T0 miss "
                                "(Hub Simulator is not used as an automatic fallback).",
                                code="unavailable",
                            )
                        else:
                            result = _escalate_to_ai(
                                chosen=chosen_alt,
                                reason="t0_miss_general_knowledge",
                                prior=result,
                                preserve_model=bool(adapter_id or selected_model),
                            )
                    elif (
                        adapter_id
                        and not solved
                        and result.get("t0_unsolved")
                        and not result.get("answer")
                    ):
                        # Selected AI waiting after T0 miss without escalate flag → do not waste.
                        pass
                else:
                    result = self._execute_agent(
                        execution_id,
                        prompt_n,
                        recommendation,
                        context_preview,
                        adapter_id=adapter_id,
                        repository_ids=repository_ids,
                        settings=settings,
                        manual_override=is_manual,
                    )
        except AgentCenterError as exc:
            result = self._fail(execution_id, str(exc), code=exc.code)
        except Exception as exc:  # noqa: BLE001
            result = self._fail(execution_id, str(exc), code="execution_failed")

        result = public_execution_fields(result)
        if is_terminal(str(result.get("status") or "")):
            self._record_history(result)
            with self._lock:
                live = self._active.get(result.get("id") or "")
                if live:
                    result = dict(result)
                    if live.get("usage") is not None:
                        result["usage"] = dict(live.get("usage") or {})
                    if live.get("prior_findings") is not None:
                        result["prior_findings"] = list(live.get("prior_findings") or [])
            log_lifecycle(
                event="step_finished",
                status=str(result.get("status")),
                provider_id=str(result.get("provider_id") or ""),
                tool_ids=list((result.get("context") or {}).get("tool_ids") or []),
                started_at=str(result.get("started_at") or ""),
                finished_at=str(result.get("finished_at") or ""),
                failure_reason=str(result.get("error") or ""),
                execution_id=str(result.get("id") or ""),
            )
        return result

    def _record_history(self, row: dict[str, Any]) -> None:
        if self.history is None or row.get("history_recorded"):
            return
        status = str(row.get("status") or "")
        status = normalize_status(status, error_code=str(row.get("error_code") or "") or None)
        if status == "completed":
            outcome = "success"
        elif status == "cancelled":
            outcome = "cancel"
        elif status == "timed_out":
            outcome = "failure"
        elif status == "paused_for_approval":
            outcome = "paused"
        else:
            outcome = "failure"
        usage = {}
        agent_run = row.get("agent_run") or {}
        if isinstance(agent_run, dict):
            usage = agent_run.get("usage") or {}
        if not usage and isinstance(row.get("usage"), dict):
            usage = row.get("usage") or {}
        # Prefer stamped telemetry (event-sourced) over raw usage.
        tel = row.get("telemetry") if isinstance(row.get("telemetry"), dict) else {}
        if not tel:
            from hub.agent_center.routing.telemetry import attach_execution_telemetry

            stamped = attach_execution_telemetry(dict(row))
            tel = stamped.get("telemetry") or {}
            usage = stamped.get("usage") or usage
            row = stamped
        parsed = _usage_breakdown(usage)
        if tel:
            # Telemetry is authoritative for AI token accounting.
            parsed = {
                "input_tokens": tel.get("input_tokens"),
                "output_tokens": tel.get("output_tokens"),
                "cached_tokens": tel.get("cached_tokens"),
                "total_tokens": tel.get("total_ai_tokens"),
                "usage_source": tel.get("usage_source") or parsed.get("usage_source"),
            }
        tokens = parsed.get("total_tokens")
        # Pure T0: force zeros even if a leaked usage dict exists.
        if tel.get("t0_pure") or (
            str(row.get("mode") or "") in {"deterministic", "grounding_gate"}
            and not row.get("agent_run_id")
            and not tel.get("llm_invoked")
        ):
            tokens = 0
            parsed = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "usage_source": "actual",
            }
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
        prior = row.get("prior_findings") or []
        if not isinstance(prior, list):
            prior = []
        try:
            from hub.agent_center.routing.budget import band_to_tokens
            from hub.agent_center.routing.cost import estimate_cost_usd
            from hub.agent_center.routing.settings import default_settings

            settings = row.get("_routing_settings") or default_settings()
            est_tokens = band_to_tokens(str(row.get("estimated_usage") or ""))
            est_cost = estimate_cost_usd(
                est_tokens, provider_id=str(row.get("provider_id") or ""), settings=settings
            )
            act_cost = estimate_cost_usd(
                tokens, provider_id=str(row.get("provider_id") or ""), settings=settings
            )
        except Exception:  # noqa: BLE001
            est_tokens = None
            est_cost = None
            act_cost = None
        try:
            self.history.record_event(
                {
                    "id": row.get("id"),
                    "workspace": row.get("workspace") or "work",
                    "actor": row.get("actor") or "owner",
                    "provider_id": (
                        "deterministic"
                        if tel.get("t0_pure") or not tel.get("llm_invoked")
                        else (tel.get("provider") or row.get("provider_id") or "unknown")
                    )
                    if tel
                    else (row.get("provider_id") or "unknown"),
                    "adapter_id": "" if tel.get("t0_pure") else (row.get("adapter_id") or ""),
                    "tier": tel.get("routing_tier") or row.get("tier") or "",
                    "task_type": row.get("task_type") or "general",
                    "status": status,
                    "outcome": outcome,
                    "retries": int(row.get("attempt") or 0),
                    "runtime_ms": tel.get("runtime_ms")
                    if tel.get("runtime_ms") is not None
                    else _parse_runtime_ms(row.get("started_at"), row.get("finished_at")),
                    "estimated_usage": row.get("estimated_usage") or "",
                    "estimated_tokens": est_tokens,
                    "actual_tokens": tokens,
                    "input_tokens": parsed.get("input_tokens"),
                    "output_tokens": parsed.get("output_tokens"),
                    "cached_tokens": parsed.get("cached_tokens"),
                    "usage_source": parsed.get("usage_source")
                    or ("actual" if tokens is not None else "estimate"),
                    "estimated_cost_usd": est_cost,
                    "actual_cost_usd": act_cost,
                    "findings_reused": prior,
                    "rbac_role": row.get("rbac_role") or "",
                    "t0_llm_avoided": bool(tel.get("t0_pure"))
                    or (row.get("mode") == "deterministic" and outcome == "success"),
                    "fallback_from": row.get("fallback_from") or "",
                    "escalated_to": row.get("escalated_to") or "",
                    "prompt_fingerprint": row.get("prompt_fingerprint") or "",
                    "error_code": row.get("error_code") or "",
                    "partial_summary": partial,
                    "answer": row.get("answer") or "",
                    "prompt_for_keywords": row.get("prompt") or "",
                    "execution_type": tel.get("execution_type") or "",
                    "llm_invoked": bool(tel.get("llm_invoked")),
                    "model": "" if tel.get("t0_pure") else (tel.get("model") or ""),
                    "child_ai_run_id": ""
                    if tel.get("t0_pure")
                    else (tel.get("child_ai_run_id") or ""),
                    "tools_used": list(tel.get("tools_used") or []),
                    "telemetry": tel,
                }
            )
            with self._lock:
                if row.get("id") in self._active:
                    self._active[row["id"]]["history_recorded"] = True
                    self._active[row["id"]]["partial_summary"] = partial
                    self._active[row["id"]]["telemetry"] = tel
                    self._active[row["id"]]["usage"] = {
                        "input_tokens": parsed.get("input_tokens"),
                        "output_tokens": parsed.get("output_tokens"),
                        "cached_tokens": parsed.get("cached_tokens"),
                        "total_tokens": tokens,
                        "usage_source": parsed.get("usage_source"),
                        "estimated_cost_usd": est_cost,
                        "actual_cost_usd": act_cost,
                        "estimated_tokens": est_tokens,
                        "execution_type": tel.get("execution_type"),
                        "llm_invoked": tel.get("llm_invoked"),
                        "routing_tier": tel.get("routing_tier"),
                        "child_ai_run_id": tel.get("child_ai_run_id"),
                        "tools_used": list(tel.get("tools_used") or []),
                        "provider": tel.get("provider"),
                        "model": tel.get("model"),
                        "runtime_ms": tel.get("runtime_ms"),
                    }
        except Exception:  # noqa: BLE001
            pass

    def _fail(self, execution_id: str, error: str, *, code: str = "execution_failed") -> dict[str, Any]:
        with self._lock:
            row = self._active.get(execution_id)
            if not row:
                raise AgentCenterError(error, code=code)
            status = normalize_status(
                "timed_out" if code in {"timeout", "timed_out"} else "failed",
                error_code=code,
            )
            if code == "unavailable":
                status = "failed"
            row["status"] = status
            row["error"] = error
            row["error_code"] = code
            row["finished_at"] = _utcnow()
            fp = row.get("fingerprint")
            if fp and self._fingerprints.get(fp) == execution_id:
                self._fingerprints.pop(fp, None)
            return public_execution_fields(dict(row))

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

    def _tools_context(
        self,
        tool_ids: list[str],
        repository_ids: list[str],
        dhis2_environment: str = "",
    ) -> AgentToolsContext:
        svc = self.agent_center
        profile = get_profile(INTERNAL_WORK_PROFILE)
        tools = normalize_tools(profile, tool_ids)
        return AgentToolsContext(
            registry=svc.registry,
            repository_ids=list(repository_ids or []),
            notebook=svc.notebook,
            sql_store=svc.sql_store,
            sql_executor=getattr(svc, "sql_executor", None),
            sql_connections=getattr(svc, "sql_connections", None),
            uid_index=svc.uid_index,
            email=svc.email,
            calendar=svc.calendar,
            job_store=svc.job_store,
            audit_store=svc.audit_store,
            dhis2_reports=svc.dhis2_reports,
            notepad_factory=svc.notepad_factory,
            profile_id=profile.id,
            workspace=profile.workspace,
            dhis2_environment=(
                str(dhis2_environment or "").strip().lower()
                if str(dhis2_environment or "").strip().lower() in {"stage", "live"}
                else ""
            ),
            allowed_tools=set(tools),
        )

    def _execute_t0(
        self,
        execution_id: str,
        prompt: str,
        recommendation: RouteRecommendation,
        context_preview: dict[str, Any],
    ) -> dict[str, Any]:
        from hub.agent_center.grounding import (
            answer_from_evidence,
            apply_grounding_to_answer,
            collect_evidence_packet,
            evaluate_answer_grounding,
            format_cannot_verify,
            resolve_prompt_scope,
        )
        from hub.agent_center.completion import (
            derive_completion_contract,
            validate_completion,
        )
        from hub.agent_center.scope import SCOPE_GK, SCOPE_NATIONAL, SCOPE_WEB

        self._update(execution_id, status="running", started_at=_utcnow(), mode="deterministic")
        tools = list(context_preview.get("tool_ids") or select_minimal_tools(recommendation.classification))
        repos = list(context_preview.get("repository_ids") or [])
        repository_knowledge = (
            context_preview.get("repository_intelligence")
            if isinstance(context_preview.get("repository_intelligence"), dict)
            else {}
        )
        try:
            ctx = self._tools_context(
                tools, repos, str(context_preview.get("dhis2_environment") or "")
            )
            scope = resolve_prompt_scope(prompt, repository_ids=repos)
            requires = scope.requires_project_evidence
            # Broader scope: do not force selected-repo evidence collection as authoritative.
            # Keep explicitly selected repos for Inspect/project packing unless broader override.
            if not scope.use_selected_repo and not repos:
                repos = []
            elif not scope.use_selected_repo and scope.allow_general_knowledge:
                # National/GK/web: drop repo tools, but leave RI diagnostics alone.
                repos = []
            packet = context_preview.get("evidence_packet")
            if not isinstance(packet, dict) or (requires or scope.try_deterministic_tools or repos):
                packet = collect_evidence_packet(prompt, ctx, repository_ids=repos)
            # Attach bounded Repository Intelligence as project evidence for Inspect/code asks.
            if repository_knowledge.get("items") or repository_knowledge.get("profiles"):
                packet = self.agent_center._grounding_with_repository_intelligence(
                    {"evidence_packet": packet, "required": requires},
                    repository_knowledge,
                    prompt=prompt,
                    repository_ids=repos,
                ).get("evidence_packet") or packet
                ri_tool = {
                    "tool": "repository_intelligence",
                    "ok": bool(repository_knowledge.get("profiles")),
                    "args": {
                        "repository_ids": list(
                            (repository_knowledge.get("diagnostics") or {}).get("repository_ids")
                            or repos
                        ),
                        "entries": int(
                            (repository_knowledge.get("diagnostics") or {}).get(
                                "knowledge_entries_used"
                            )
                            or len(repository_knowledge.get("items") or [])
                        ),
                    },
                    "result": {
                        "used": bool((repository_knowledge.get("diagnostics") or {}).get("used")),
                        "freshness": (repository_knowledge.get("diagnostics") or {}).get("freshness"),
                        "item_count": repository_knowledge.get("item_count") or 0,
                    },
                }
                tool_results = list(packet.get("tool_results") or [])
                tool_results.append(ri_tool)
                sources = list(packet.get("sources") or [])
                sources.append("tool:repository_intelligence")
                for item in (repository_knowledge.get("items") or [])[:6]:
                    rid = str(item.get("repository_id") or "")
                    path = str(item.get("path") or "")
                    if path:
                        sources.append(f"repository_intelligence:{rid}:{path}")
                packet = {
                    **packet,
                    "tool_results": tool_results,
                    "sources": list(dict.fromkeys(sources)),
                    "usable": bool(packet.get("usable") or repository_knowledge.get("items")),
                }
            results = list(packet.get("tool_results") or [])
            # Keep RI diagnostics on the execution row for Inspect T0 completions.
            self._update(
                execution_id,
                repository_intelligence_diagnostics=dict(
                    repository_knowledge.get("diagnostics") or {}
                ),
                context_items=list(dict.fromkeys(
                    list((self.get_status(execution_id) or {}).get("context_items") or [])
                    + [str(src) for src in (packet.get("sources") or []) if str(src).strip()]
                )),
            )

            contract = derive_completion_contract(
                prompt,
                classification_signals=list(recommendation.classification.signals or []),
            )
            # Preserve detected filters on the execution row for downstream/AI escalation.
            self._update(
                execution_id,
                completion_contract=contract.public(),
                detected_filters=dict(contract.filters),
            )

            deterministic_answer = answer_from_evidence(prompt, packet)
            t0_fallthrough = False
            t0_capability_escalate = False
            fallthrough_kinds = {SCOPE_NATIONAL, SCOPE_GK, SCOPE_WEB}
            signals = set(recommendation.classification.signals or [])
            authoritative_data = (
                "authoritative_data_query" in signals
                or "data_query" in signals
                or "structured_data_lookup" in signals
                or scope.kind == "dhis2_data"
            )

            def _completion_for(ans: str):
                return validate_completion(
                    contract, prompt=prompt, answer=ans, evidence=packet
                )

            if deterministic_answer:
                completion = _completion_for(deterministic_answer)
                if completion.task_solved and completion.answer_grounded:
                    answer = deterministic_answer
                else:
                    # Evidence ≠ completion — do not present discovery as the final result.
                    deterministic_answer = None

            sql_attempt = None
            failure = None
            # Capability / connected-DB path for structured authoritative asks only.
            # National/GK/web fallthrough must not be blocked by SQL probing.
            run_capability_path = bool(authoritative_data) or (
                requires
                and contract.intent
                in {"count", "list", "lookup", "status", "metadata", "comparison"}
            )
            if not deterministic_answer and run_capability_path:
                from hub.agent_center.capability import (
                    attempt_deterministic_sql,
                    classify_t0_failure,
                    find_saved_sql_matches,
                    merge_sql_attempt_into_packet,
                    should_escalate_to_ai,
                    snapshot_capabilities,
                )

                svc = self.agent_center
                matches = find_saved_sql_matches(
                    getattr(svc, "sql_store", None) or getattr(ctx, "sql_store", None),
                    filters=dict(contract.filters or {}),
                    prompt=prompt,
                )
                # Also pull saved-query ids discovered in the evidence packet.
                for hit in packet.get("hits") or []:
                    if not isinstance(hit, dict):
                        continue
                    if str(hit.get("source") or "") == "sql:saved_query" and hit.get("query_id"):
                        qid = str(hit.get("query_id"))
                        if not any(str(m.get("id")) == qid for m in matches):
                            store = getattr(svc, "sql_store", None)
                            if store is not None and callable(getattr(store, "get_query", None)):
                                try:
                                    loaded = store.get_query(qid)
                                    if isinstance(loaded, dict):
                                        matches.append(loaded)
                                except Exception:  # noqa: BLE001
                                    pass
                sql_attempt = attempt_deterministic_sql(
                    prompt=prompt,
                    contract=contract,
                    sql_store=getattr(svc, "sql_store", None) or getattr(ctx, "sql_store", None),
                    sql_executor=getattr(svc, "sql_executor", None)
                    or getattr(ctx, "sql_executor", None),
                    sql_connections=getattr(svc, "sql_connections", None)
                    or getattr(ctx, "sql_connections", None),
                    matches=matches,
                )
                if sql_attempt.attempted or sql_attempt.tool_result:
                    packet = merge_sql_attempt_into_packet(packet, sql_attempt)
                    results = list(packet.get("tool_results") or results)
                if sql_attempt.ok and sql_attempt.answer:
                    completion = validate_completion(
                        contract,
                        prompt=prompt,
                        answer=sql_attempt.answer,
                        evidence=packet,
                    )
                    if completion.task_solved and completion.answer_grounded:
                        deterministic_answer = sql_attempt.answer
                caps = snapshot_capabilities(
                    sql_store=getattr(svc, "sql_store", None),
                    sql_executor=getattr(svc, "sql_executor", None),
                    sql_connections=getattr(svc, "sql_connections", None),
                    dhis2_reports=getattr(svc, "dhis2_reports", None),
                    saved_matches=matches,
                )
                if not deterministic_answer:
                    failure = classify_t0_failure(
                        contract=contract,
                        packet=packet,
                        caps=caps,
                        sql_attempt=sql_attempt,
                        authoritative_data=authoritative_data,
                    )
                    self._update(
                        execution_id,
                        t0_failure_reason=failure.reason,
                        next_capability=failure.next_capability,
                        db_query_attempted=bool(failure.db_query_attempted),
                        capability_snapshot=caps.public(),
                        failure_classification=failure.public(),
                    )

            if deterministic_answer:
                answer = deterministic_answer
            elif failure is not None and should_escalate_to_ai(failure):
                t0_capability_escalate = True
                answer = ""
            elif (
                failure is not None
                and failure.next_capability == "cannot_verify"
            ):
                answer = format_cannot_verify(
                    repository_ids=repos or list(context_preview.get("repository_ids") or []),
                    reason=failure.detail or failure.reason,
                    errors=list(packet.get("errors") or []),
                )
            elif requires and not packet.get("usable"):
                answer = format_cannot_verify(
                    repository_ids=repos or list(context_preview.get("repository_ids") or []),
                    reason=str(packet.get("summary") or "No usable project evidence."),
                    errors=list(packet.get("errors") or []),
                )
            elif authoritative_data and not packet.get("usable"):
                answer = format_cannot_verify(
                    repository_ids=repos or list(context_preview.get("repository_ids") or []),
                    reason=str(
                        packet.get("summary")
                        or "Authoritative data sources did not return a verifiable value."
                    ),
                    errors=list(packet.get("errors") or []),
                )
            elif packet.get("usable") and not deterministic_answer:
                # Supporting evidence found but completion contract unsatisfied.
                completion = _completion_for("")
                if contract.intent == "explanation" and not authoritative_data:
                    t0_capability_escalate = True
                    answer = ""
                    self._update(
                        execution_id,
                        t0_failure_reason="t0_explanation_synthesis",
                        next_capability="ai_escalate",
                    )
                elif (
                    scope.allow_general_knowledge
                    and scope.kind in fallthrough_kinds
                    and not authoritative_data
                ):
                    t0_fallthrough = True
                    answer = ""
                else:
                    answer = format_cannot_verify(
                        repository_ids=repos or list(context_preview.get("repository_ids") or []),
                        reason=(
                            f"Evidence found but task unsolved for intent={contract.intent}; "
                            f"need {contract.required_output}. "
                            f"{completion.reason}"
                        ),
                        errors=list(packet.get("errors") or []),
                    )
            elif (
                not packet.get("usable")
                and scope.allow_general_knowledge
                and scope.kind in fallthrough_kinds
                and not authoritative_data
            ):
                t0_fallthrough = True
                answer = ""
            else:
                # Never present raw tool dumps as the requested final result.
                completion = _completion_for("")
                if (
                    scope.allow_general_knowledge
                    and scope.kind in fallthrough_kinds
                    and not authoritative_data
                ):
                    t0_fallthrough = True
                    answer = ""
                else:
                    answer = format_cannot_verify(
                        repository_ids=repos or list(context_preview.get("repository_ids") or []),
                        reason=(
                            f"T0 could not satisfy completion contract "
                            f"(intent={contract.intent}, need {contract.required_output})."
                        ),
                        errors=list(packet.get("errors") or []),
                    )

            if t0_fallthrough or t0_capability_escalate:
                from hub.agent_center.completion import merge_completion_into_grounding

                partial_completion = _completion_for(answer or "")
                fallthrough_grounding = merge_completion_into_grounding(
                    {
                        "grounded": False,
                        "grounded_label": "No",
                        "source": ", ".join(packet.get("sources") or []) or "none",
                        "reason": (
                            (failure.detail if failure else None)
                            or "T0 unsolved; continuing to next capable route."
                        ),
                        "policy_violation": False,
                        "cannot_verify": False,
                        "required": bool(requires),
                        "scope": scope.kind,
                    },
                    partial_completion,
                )
                return public_execution_fields(
                    self._update(
                        execution_id,
                        status="completed",
                        answer="",
                        tool_results=results,
                        grounding=fallthrough_grounding,
                        evidence_packet={
                            "summary": packet.get("summary"),
                            "usable": packet.get("usable"),
                            "sources": packet.get("sources") or [],
                            "hit_count": len(packet.get("hits") or []),
                            "errors": packet.get("errors") or [],
                            "hits": packet.get("hits") or [],
                        },
                        finished_at=_utcnow(),
                        mode="deterministic",
                        t0_fallthrough=bool(t0_fallthrough),
                        t0_capability_escalate=bool(t0_capability_escalate),
                        t0_unsolved=True,
                        ai_escalation_occurred=False,
                        db_query_attempted=bool(
                            (failure and failure.db_query_attempted)
                            or (sql_attempt and sql_attempt.attempted)
                        ),
                        t0_failure_reason=(
                            "t0_explanation_synthesis"
                            if contract.intent == "explanation" and packet.get("usable")
                            else (failure.reason if failure else "")
                        ) or "",
                        next_capability=(
                            (failure.next_capability if failure else None)
                            or ("ai_escalate" if t0_capability_escalate else "")
                        ),
                    )
                )

            priors = context_preview.get("prior_findings") or []
            if priors:
                answer += "\n\nPrior findings used: " + "; ".join(
                    str(p.get("summary") or "")[:120] for p in priors[:3]
                )

            status = evaluate_answer_grounding(
                prompt, answer, repository_ids=repos, evidence=packet
            )
            # Finish only when Task Solved + Grounded for successful data answers.
            if deterministic_answer and not (
                status.get("task_solved") and status.get("answer_grounded")
            ):
                status = {
                    **status,
                    "cannot_verify": True,
                    "grounded": False,
                    "grounded_label": "No",
                    "task_solved": False,
                    "task_solved_label": "No",
                    "answer_grounded": False,
                }
                answer = format_cannot_verify(
                    repository_ids=repos or list(context_preview.get("repository_ids") or []),
                    reason="Completion contract not satisfied after deterministic tools.",
                    errors=list(packet.get("errors") or []),
                )
                answer = apply_grounding_to_answer(answer, status)
            else:
                answer = apply_grounding_to_answer(answer, status)
            final_status = "failed" if status.get("policy_violation") else "completed"
            return public_execution_fields(
                self._update(
                    execution_id,
                    status=final_status,
                    answer=answer,
                    tool_results=results,
                    grounding=status,
                    evidence_packet={
                        "summary": packet.get("summary"),
                        "usable": packet.get("usable"),
                        "sources": packet.get("sources") or [],
                        "hit_count": len(packet.get("hits") or []),
                        "errors": packet.get("errors") or [],
                        "hits": packet.get("hits") or [],
                    },
                    finished_at=_utcnow(),
                    mode="deterministic",
                    error=status.get("reason") if status.get("policy_violation") else "",
                    error_code="ungrounded_answer" if status.get("policy_violation") else "",
                    t0_fallthrough=False,
                    t0_capability_escalate=False,
                    ai_escalation_occurred=False,
                    db_query_attempted=bool(sql_attempt and sql_attempt.attempted),
                    t0_failure_reason="",
                    next_capability="",
                )
            )
        except AgentCenterError as exc:
            return self._fail(execution_id, str(exc), code=exc.code)
        except Exception as exc:  # noqa: BLE001
            return self._fail(execution_id, str(exc), code="execution_failed")

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
        manual_override: bool = False,
    ) -> dict[str, Any]:
        available, detail = self._provider_available(adapter_id)
        chosen = adapter_id
        fallback_from = None
        fallback_reason = ""
        if not available:
            # Never silently substitute another provider (esp. Hub Simulator).
            # Surface the real availability / auth / connection error instead.
            msg = detail or f"Provider {adapter_id} unavailable"
            if manual_override:
                msg = (
                    f"Selected provider {adapter_id} is unavailable or not authenticated. "
                    f"{detail or 'Connect or re-authenticate it, then retry.'} "
                    "No automatic fallback was used."
                )
            else:
                msg = (
                    f"Recommended provider {adapter_id} is unavailable. "
                    f"{detail or ''} "
                    "Choose another agent explicitly — Hub Simulator is not used as a fallback."
                ).strip()
            self._update(
                execution_id,
                fallback_reason="provider_unavailable_no_auto_fallback",
                resolved_provider=adapter_id,
            )
            logger.info(
                "airix_provider_resolution selected=%s recommended=%s resolved=%s "
                "selected_model=%s recommended_model=%s resolved_model=%s "
                "fallback_reason=%s manual_override=%s",
                adapter_id if manual_override else "-",
                recommendation.recommended_agent or "-",
                adapter_id,
                str(context_preview.get("model") or "-"),
                str(getattr(recommendation, "recommended_model", None) or "-"),
                "-",
                "provider_unavailable_no_auto_fallback",
                str(bool(manual_override)).lower(),
            )
            return self._fail(execution_id, msg.strip(), code="unavailable")

        # Hub Simulator only when this adapter was explicitly selected / accepted.
        if chosen == "hub-simulator" and not manual_override:
            recommended = (recommendation.recommended_agent or "").strip()
            if recommended not in {"low-cost", "hub-simulator"}:
                return self._fail(
                    execution_id,
                    "Hub Simulator cannot be used as an automatic fallback for real tasks. "
                    "Select Hub Simulator explicitly if you want the demo agent.",
                    code="simulator_not_allowed",
                )

        # Preserve T0 → AI escalation markers already stamped by _escalate_to_ai.
        with self._lock:
            prior_row = dict(self._active.get(execution_id) or {})
        prior_fallback = str(prior_row.get("fallback_from") or "").strip() or None
        prior_fallback_reason = str(prior_row.get("fallback_reason") or "").strip()
        prior_ri_diag = (
            prior_row.get("repository_intelligence_diagnostics")
            if isinstance(prior_row.get("repository_intelligence_diagnostics"), dict)
            else {}
        )
        if not prior_ri_diag:
            prior_ri_diag = (
                context_preview.get("repository_intelligence_diagnostics")
                if isinstance(context_preview.get("repository_intelligence_diagnostics"), dict)
                else {}
            )
        synthesis_escalation = bool(
            context_preview.get("synthesis_escalation")
            or context_preview.get("bounded_evidence_only")
            or str(prior_row.get("t0_failure_reason") or "") == "t0_explanation_synthesis"
            or str(context_preview.get("t0_failure_reason") or "") == "t0_explanation_synthesis"
        )
        if prior_fallback:
            fallback_from = prior_fallback
            fallback_reason = prior_fallback_reason or fallback_reason

        self._update(
            execution_id,
            status="running",
            started_at=prior_row.get("started_at") or _utcnow(),
            adapter_id=chosen,
            resolved_provider=chosen,
            provider_id=chosen if synthesis_escalation or prior_fallback else prior_row.get("provider_id") or chosen,
            fallback_from=fallback_from,
            fallback_reason=fallback_reason,
            manual_override=bool(manual_override),
            t0_unsolved=False if synthesis_escalation else prior_row.get("t0_unsolved"),
            ai_escalation_occurred=bool(
                prior_row.get("ai_escalation_occurred") or synthesis_escalation or prior_fallback
            ),
            repository_intelligence_diagnostics=dict(
                prior_ri_diag
                or (
                    (context_preview.get("repository_intelligence") or {}).get("diagnostics")
                    if isinstance(context_preview.get("repository_intelligence"), dict)
                    else {}
                )
            ),
        )

        classification = recommendation.classification
        tools = list(context_preview.get("tool_ids") or select_minimal_tools(classification))
        from hub.agent_center.grounding import (
            apply_grounding_to_answer,
            collect_evidence_packet,
            evaluate_answer_grounding,
            format_cannot_verify,
            format_evidence_for_prompt,
            grounding_rules_text,
            resolve_prompt_scope,
        )
        from hub.agent_center.repository_context import (
            agent_requires_repository,
            resolve_repository_context,
        )

        requested_repos = list(repository_ids or []) or list(
            context_preview.get("repository_ids") or []
        )
        scope = resolve_prompt_scope(prompt, repository_ids=requested_repos)
        # Task-scoped packing: strip repos for non-coding classifications.
        repos = select_repository_ids(classification, requested_repos)
        if not scope.use_selected_repo and not agent_requires_repository(chosen):
            repos = []
        if agent_requires_repository(chosen):
            try:
                selectable = self.agent_center.repositories(profile_id=INTERNAL_WORK_PROFILE)
            except Exception:  # noqa: BLE001
                selectable = []
            resolved = resolve_repository_context(
                agent_id=chosen,
                repository_ids=requested_repos,
                active_repository_id=str(context_preview.get("active_repository_id") or "").strip()
                or None,
                selected_repository_id=str(
                    context_preview.get("selected_repository_id") or ""
                ).strip()
                or None,
                repositories=selectable,
            )
            if not resolved["ok"]:
                return self._fail(
                    execution_id,
                    str(resolved.get("error") or "Repository required"),
                    code=str(resolved.get("code") or "repository_required"),
                )
            repos = list(resolved.get("repository_ids") or [])
        elif scope.requires_project_evidence and requested_repos:
            # Keep explicit selection for project-grounded questions even on API agents.
            repos = requested_repos[:2]

        hints = list(context_preview.get("hints") or [])[:6]
        for finding in (context_preview.get("prior_findings") or [])[:3]:
            summary = str(finding.get("summary") or "").strip()
            if summary:
                hints.append(f"prior_finding: {summary[:160]}")
        if context_preview.get("partial_results"):
            hints.append(f"partial_results: {str(context_preview.get('partial_results'))[:160]}")

        requires = scope.requires_project_evidence
        allow_gk = bool(context_preview.get("allow_general_knowledge")) or scope.allow_general_knowledge
        direct_mode = str(context_preview.get("routing_mode") or "").strip().lower() == "direct"
        interaction_mode = normalize_interaction_mode(
            str(context_preview.get("interaction_mode") or context_preview.get("routing_mode") or "smart")
        )
        packet = context_preview.get("evidence_packet")
        if not isinstance(packet, dict):
            packet = {}
        # Cheap evidence for context packing — never answers/terminates the task in Direct mode.
        if (
            not bool(context_preview.get("bounded_evidence_only"))
            and (requires or (scope.try_deterministic_tools and not packet.get("usable")) or direct_mode)
        ):
            try:
                ctx = self._tools_context(
                    tools, repos, str(context_preview.get("dhis2_environment") or "")
                )
                # Cap tools for Direct so packing stays lightweight.
                pack_tools = tools[:4] if direct_mode else tools
                packet = collect_evidence_packet(prompt, ctx, repository_ids=repos)
                if direct_mode:
                    # Keep packet as context only; strip any temptation to treat as final answer.
                    packet = {
                        **packet,
                        "context_only": True,
                        "tool_ids": pack_tools,
                    }
            except Exception as exc:  # noqa: BLE001
                packet = {
                    "repository_ids": repos,
                    "usable": False,
                    "hits": [],
                    "sources": [],
                    "errors": [str(exc)],
                    "summary": f"Evidence collection failed: {exc}",
                    "tool_results": [],
                    "context_only": bool(direct_mode),
                }

        # Do not send selected-repo project tasks to agents without usable evidence.
        # General/national/web scope falls through to model knowledge instead.
        # Direct mode never terminates here — context prep only; grounding still applies after.
        if (
            not direct_mode
            and requires
            and repos
            and not packet.get("usable")
            and not allow_gk
        ):
            answer = format_cannot_verify(
                repository_ids=repos,
                reason=str(packet.get("summary") or "No usable project evidence."),
                errors=list(packet.get("errors") or []),
            )
            status = evaluate_answer_grounding(
                prompt, answer, repository_ids=repos, evidence=packet
            )
            answer = apply_grounding_to_answer(answer, status)
            return public_execution_fields(
                self._update(
                    execution_id,
                    status="completed",
                    answer=answer,
                    grounding=status,
                    evidence_packet={
                        "summary": packet.get("summary"),
                        "usable": False,
                        "sources": packet.get("sources") or [],
                        "hit_count": 0,
                        "errors": packet.get("errors") or [],
                    },
                    finished_at=_utcnow(),
                    mode="grounding_gate",
                    adapter_id=chosen,
                )
            )

        # When grounding is required but no repo is selected and evidence is empty,
        # still attach cannot-verify rules for project-lookup prompts; block coding CLIs only.
        if (
            not direct_mode
            and requires
            and not repos
            and not packet.get("usable")
            and agent_requires_repository(chosen)
            and not allow_gk
        ):
            answer = format_cannot_verify(
                repository_ids=repos,
                reason="Project lookup requires a selected connected repository and Hub evidence.",
                errors=list(packet.get("errors") or []),
            )
            status = evaluate_answer_grounding(
                prompt, answer, repository_ids=repos, evidence=packet
            )
            answer = apply_grounding_to_answer(answer, status)
            return public_execution_fields(
                self._update(
                    execution_id,
                    status="completed",
                    answer=answer,
                    grounding=status,
                    finished_at=_utcnow(),
                    mode="grounding_gate",
                    adapter_id=chosen,
                )
            )

        selected_model = str(context_preview.get("model") or "").strip()
        provider_changed = bool(fallback_from)
        conv_id = str(context_preview.get("conversation_id") or "").strip()
        session_reused = bool(conv_id)
        prior_fp = str(context_preview.get("context_fingerprint") or "").strip()
        # Explicit escalate/preserve model must not be wiped by provider_changed.
        model_for_run = selected_model
        payload = {
            "profile_id": INTERNAL_WORK_PROFILE,
            "mode": "plan" if interaction_mode == "plan" else ("find" if interaction_mode == "inspect" else "ask"),
            "prompt": prompt,
            "agent_id": chosen,
            "model": model_for_run,
            "tool_ids": tools,
            "repository_ids": repos,
            "hints": hints[:10],
            "files": {},
            "provider_changed": bool(provider_changed and not model_for_run),
            "previous_provider": fallback_from or "",
            "evidence_packet": packet,
            "grounding_rules": grounding_rules_text(
                repository_ids=repos, requires=requires, scope=scope
            ),
            # Direct bypasses routing only — never auto-enable general knowledge.
            "allow_general_knowledge": allow_gk,
            "conversation_id": conv_id or None,
            "routing_mode": "direct" if direct_mode else "smart",
            "interaction_mode": interaction_mode,
            "context_sources": list(context_preview.get("context_sources") or []),
            "dhis2_environment": context_preview.get("dhis2_environment") or "",
            "reuse_context": bool(prior_fp),
            "context_fingerprint": prior_fp,
            "repository_intelligence": context_preview.get("repository_intelligence"),
            "bounded_evidence_only": bool(context_preview.get("bounded_evidence_only")),
        }
        # Also inject evidence text into hints so adapters without pack hooks still see it.
        evidence_text = format_evidence_for_prompt(packet)
        if evidence_text:
            hints.append(evidence_text[:500])
            payload["hints"] = hints[:12]

        try:
            run = self.agent_center.start_run(payload)
        except AgentCenterError as exc:
            return self._fail(execution_id, str(exc), code=exc.code)
        except Exception as exc:  # noqa: BLE001
            return self._fail(execution_id, str(exc), code="execution_failed")

        run_id = str(run.get("id") or "")
        status = str(run.get("status") or "")
        run_ctx = run.get("context") if isinstance(run.get("context"), dict) else {}
        run_knowledge = (
            run_ctx.get("repository_intelligence")
            if isinstance(run_ctx.get("repository_intelligence"), dict)
            else {}
        )
        if not run_knowledge:
            run_knowledge = (
                context_preview.get("repository_intelligence")
                if isinstance(context_preview.get("repository_intelligence"), dict)
                else {}
            )
        context_items: list[str] = []
        for src in (packet.get("sources") or [])[:8]:
            if str(src).strip():
                context_items.append(str(src).strip())
        for f in (run_ctx.get("files") or [])[:8]:
            if isinstance(f, dict):
                path = str(f.get("path") or "").strip()
                rid = str(f.get("repo_id") or "").strip()
                if path:
                    context_items.append(f"{rid}:{path}" if rid else path)
        for repo in repos[:4]:
            context_items.append(f"repository:{repo}")
        for item in (run_knowledge.get("items") or [])[:6]:
            rid = str(item.get("repository_id") or "").strip()
            path = str(item.get("path") or "").strip()
            if path:
                context_items.append(f"repository_intelligence:{rid}:{path}")
        context_items = list(dict.fromkeys(context_items))
        packed_chars = run_ctx.get("packed_prompt_chars")
        if packed_chars is None:
            packed_chars = len(str(run.get("packed_prompt") or ""))
        # Prefer T0 RI diagnostics when synthesis packing emptied profiles.
        run_diag = (
            run_knowledge.get("diagnostics")
            if isinstance(run_knowledge.get("diagnostics"), dict)
            else {}
        )
        ri_diag = dict(run_diag or {})
        if prior_ri_diag and (
            not ri_diag.get("used")
            or int(ri_diag.get("knowledge_entries_used") or 0)
            < int(prior_ri_diag.get("knowledge_entries_used") or 0)
        ):
            ri_diag = {**ri_diag, **prior_ri_diag, "used": True if prior_ri_diag.get("used") else ri_diag.get("used")}
        self._update(
            execution_id,
            agent_run_id=run_id,
            model=str(run.get("model") or ""),
            selected_model=selected_model,
            resolved_model=str(run.get("model") or ""),
            grounding=(run.get("context") or {}).get("grounding"),
            evidence_packet=(run.get("context") or {}).get("evidence_packet") or {
                "summary": packet.get("summary"),
                "usable": packet.get("usable"),
                "sources": packet.get("sources") or [],
                "hit_count": len(packet.get("hits") or []),
                "errors": packet.get("errors") or [],
                "hits": packet.get("hits") or [],
            },
            conversation_id=str(run.get("conversation_id") or conv_id or ""),
            session_reused=session_reused,
            context_items=context_items,
            context_chars=int(packed_chars or 0),
            repository_intelligence_diagnostics=ri_diag,
            routing_mode="direct" if direct_mode else "smart",
            interaction_mode=interaction_mode,
            fallback_from=fallback_from,
            route_path=(
                f"T0 → {chosen}/{str(run.get('model') or selected_model or '').strip()}".rstrip("/")
                if (synthesis_escalation or prior_fallback)
                else ""
            ),
        )

        if status == "unavailable":
            return self._fail(
                execution_id,
                str(run.get("error") or detail or "Agent unavailable"),
                code="unavailable",
            )

        if status in {"succeeded", "completed"} or (
            extract_provider_answer(run) and status not in {"queued", "running"}
        ):
            return self._finalize_synthesis_or_agent_answer(
                execution_id,
                prompt=prompt,
                run=run,
                repository_ids=repos,
                evidence_packet=packet,
                synthesis_escalation=synthesis_escalation,
                chosen=chosen,
            )

        # Provider started async — wait until terminal (or timeout/cancel).
        timeout = float(getattr(self, "step_wait_seconds", None) or DEFAULT_STEP_WAIT_SECONDS)
        interval = float(getattr(self, "poll_interval_seconds", None) or DEFAULT_POLL_INTERVAL_SECONDS)
        return self._wait_for_agent_run(
            execution_id,
            run_id=run_id,
            timeout_seconds=timeout,
            poll_interval=interval,
            prompt=prompt,
            repository_ids=repos,
            evidence_packet=packet,
            synthesis_escalation=synthesis_escalation,
            chosen=chosen,
        )

    def _finalize_synthesis_or_agent_answer(
        self,
        execution_id: str,
        *,
        prompt: str,
        run: dict[str, Any],
        repository_ids: list[str] | None,
        evidence_packet: dict[str, Any] | None,
        synthesis_escalation: bool = False,
        chosen: str = "",
        reevaluate_grounding: bool | None = None,
    ) -> dict[str, Any]:
        """Propagate a terminal child answer onto the parent execution row."""
        from hub.agent_center.grounding import apply_grounding_to_answer, evaluate_answer_grounding

        packet = evidence_packet if isinstance(evidence_packet, dict) else {}
        answer = extract_provider_answer(run)
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        model = str(run.get("model") or "").strip()
        provider = str(run.get("agent_id") or chosen or "").strip()

        if synthesis_escalation and not answer:
            reason = str(
                run.get("error")
                or "Child provider returned empty content after explanation synthesis."
            )
            return public_execution_fields(
                self._update(
                    execution_id,
                    status="failed",
                    answer="",
                    finished_at=str(run.get("finished_at") or _utcnow()),
                    agent_run=run,
                    usage=usage,
                    error=f"synthesis_failed: {reason}",
                    error_code="synthesis_failed",
                    t0_unsolved=False,
                    ai_escalation_occurred=True,
                    next_capability="synthesis_failed",
                    route_path=f"T0 → {provider}/{model}".rstrip("/") if provider else "T0 → AI",
                    evidence_packet={
                        "summary": packet.get("summary"),
                        "usable": packet.get("usable"),
                        "sources": packet.get("sources") or [],
                        "hit_count": len(packet.get("hits") or []),
                        "errors": list(packet.get("errors") or []) + [reason],
                        "hits": packet.get("hits") or [],
                    },
                )
            )

        context_grounding = (run.get("context") or {}).get("grounding")
        if reevaluate_grounding is None:
            # Sync completions: keep adapter-provided grounding when present (Direct/Ask).
            # Synthesis and async waits force a fresh score against the evidence packet.
            reevaluate_grounding = synthesis_escalation or not isinstance(context_grounding, dict)
        if not reevaluate_grounding and isinstance(context_grounding, dict):
            g_status = context_grounding
        else:
            g_status = evaluate_answer_grounding(
                prompt,
                answer,
                repository_ids=repository_ids or [],
                evidence=packet,
            )
            if g_status.get("policy_violation"):
                from hub.agent_center.grounding import format_cannot_verify

                answer = format_cannot_verify(
                    repository_ids=repository_ids or [],
                    reason=str(
                        g_status.get("reason")
                        or "Ungrounded general-knowledge substitution blocked."
                    ),
                )
            answer = apply_grounding_to_answer(answer, g_status)
        final = "failed" if g_status.get("policy_violation") else "completed"
        route = ""
        if synthesis_escalation or str(
            (self.get_status(execution_id) or {}).get("fallback_from") or ""
        ).lower() in {"deterministic", "t0"}:
            route = f"T0 → {provider}/{model}".rstrip("/") if provider else "T0 → AI"
        return public_execution_fields(
            self._update(
                execution_id,
                status=final,
                answer=answer,
                finished_at=str(run.get("finished_at") or _utcnow()),
                agent_run=run,
                usage=usage,
                grounding=g_status,
                evidence_packet={
                    "summary": packet.get("summary"),
                    "usable": packet.get("usable"),
                    "sources": packet.get("sources") or [],
                    "hit_count": len(packet.get("hits") or []),
                    "errors": packet.get("errors") or [],
                    "hits": packet.get("hits") or [],
                },
                error=g_status.get("reason") if g_status.get("policy_violation") else "",
                error_code="ungrounded_answer" if g_status.get("policy_violation") else "",
                t0_unsolved=False,
                ai_escalation_occurred=bool(
                    synthesis_escalation
                    or (self.get_status(execution_id) or {}).get("ai_escalation_occurred")
                ),
                route_path=route,
                resolved_provider=provider or None,
                resolved_model=model or None,
            )
        )

    def _wait_for_agent_run(
        self,
        execution_id: str,
        *,
        run_id: str,
        timeout_seconds: float = DEFAULT_STEP_WAIT_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        prompt: str = "",
        repository_ids: list[str] | None = None,
        evidence_packet: dict[str, Any] | None = None,
        synthesis_escalation: bool = False,
        chosen: str = "",
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_run: dict[str, Any] = {}
        while time.monotonic() < deadline:
            live = self.get_status(execution_id) or {}
            if live.get("cancel_requested") or live.get("status") == "cancelled":
                return public_execution_fields(
                    self._update(
                        execution_id,
                        status="cancelled",
                        finished_at=_utcnow(),
                        error=live.get("error") or "Cancelled by user",
                        error_code="cancelled",
                        agent_run=last_run or live.get("agent_run"),
                    )
                )
            try:
                run = self.agent_center.get_run(
                    str(run_id), profile_id=INTERNAL_WORK_PROFILE
                )
            except AgentCenterError as exc:
                return self._fail(execution_id, str(exc), code=exc.code or "execution_failed")
            except Exception as exc:  # noqa: BLE001
                return self._fail(execution_id, str(exc), code="execution_failed")
            last_run = run if isinstance(run, dict) else {}
            status = str(last_run.get("status") or "")
            if status in {"succeeded", "completed"} or (
                extract_provider_answer(last_run) and status not in {"queued", "running"}
            ):
                return self._finalize_synthesis_or_agent_answer(
                    execution_id,
                    prompt=prompt or str(last_run.get("prompt") or ""),
                    run=last_run,
                    repository_ids=repository_ids or list(last_run.get("repository_ids") or []),
                    evidence_packet=evidence_packet,
                    synthesis_escalation=synthesis_escalation,
                    chosen=chosen or str(last_run.get("agent_id") or ""),
                    reevaluate_grounding=True,
                )
            if status in {"failed", "cancelled", "timed_out", "unavailable", "timeout"}:
                mapped = normalize_status(status)
                if synthesis_escalation:
                    return public_execution_fields(
                        self._update(
                            execution_id,
                            status="failed",
                            answer=extract_provider_answer(last_run),
                            error=(
                                "synthesis_failed: "
                                + str(last_run.get("error") or status)
                            ),
                            error_code="synthesis_failed",
                            finished_at=str(last_run.get("finished_at") or _utcnow()),
                            agent_run=last_run,
                            usage=last_run.get("usage")
                            if isinstance(last_run.get("usage"), dict)
                            else {},
                        )
                    )
                return public_execution_fields(
                    self._update(
                        execution_id,
                        status=mapped,
                        answer=extract_provider_answer(last_run),
                        error=str(last_run.get("error") or status),
                        error_code=str(last_run.get("error_code") or status),
                        finished_at=str(last_run.get("finished_at") or _utcnow()),
                        agent_run=last_run,
                    )
                )
            time.sleep(max(0.05, float(poll_interval)))

        # Timed out — best-effort cancel provider run.
        try:
            self.agent_center.cancel_run(str(run_id), profile_id=INTERNAL_WORK_PROFILE)
        except Exception:  # noqa: BLE001
            pass
        if synthesis_escalation:
            return public_execution_fields(
                self._update(
                    execution_id,
                    status="failed",
                    error=f"synthesis_failed: Timed out after {int(timeout_seconds)}s waiting for provider",
                    error_code="synthesis_failed",
                    finished_at=_utcnow(),
                    agent_run=last_run,
                )
            )
        return self._fail(
            execution_id,
            f"Timed out after {int(timeout_seconds)}s waiting for provider",
            code="timed_out",
        )

    def refresh(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._active.get(execution_id)
            if row is None:
                return None
            agent_run_id = row.get("agent_run_id")
            if is_terminal(str(row.get("status") or "")):
                return public_execution_fields(dict(row))
            if is_stale(row, stale_seconds=DEFAULT_STALE_SECONDS):
                stale = self._update(
                    execution_id,
                    status="timed_out",
                    error="Stale running execution recovered",
                    error_code="timed_out",
                    finished_at=_utcnow(),
                )
                stale = public_execution_fields(stale)
                self._record_history(stale)
                log_lifecycle(
                    event="step_stale_timeout",
                    status="timed_out",
                    provider_id=str(stale.get("provider_id") or ""),
                    started_at=str(stale.get("started_at") or ""),
                    finished_at=str(stale.get("finished_at") or ""),
                    execution_id=execution_id,
                    failure_reason="Stale running execution recovered",
                )
                return stale
            if not agent_run_id or str(row.get("status") or "") not in {"queued", "running"}:
                return public_execution_fields(dict(row))
        try:
            run = self.agent_center.get_run(
                str(agent_run_id), profile_id=INTERNAL_WORK_PROFILE
            )
        except AgentCenterError:
            cur = self.get_status(execution_id)
            return public_execution_fields(cur) if cur else None
        status = str(run.get("status") or "")
        if status in {"succeeded", "completed"}:
            synthesis = (
                str(row.get("t0_failure_reason") or "") == "t0_explanation_synthesis"
                or bool(row.get("synthesis_escalation"))
                or bool((row.get("context") or {}).get("synthesis_escalation"))
                or bool((row.get("context") or {}).get("bounded_evidence_only"))
            )
            packet = row.get("evidence_packet") if isinstance(row.get("evidence_packet"), dict) else {}
            result = self._finalize_synthesis_or_agent_answer(
                execution_id,
                prompt=str(row.get("prompt") or ""),
                run=run,
                repository_ids=list(row.get("repository_ids") or [])
                or list((row.get("context") or {}).get("repository_ids") or []),
                evidence_packet=packet,
                synthesis_escalation=synthesis,
                chosen=str(row.get("adapter_id") or row.get("resolved_provider") or ""),
            )
            self._record_history(result)
            return result
        if status in {"failed", "cancelled", "unavailable", "timed_out", "timeout"}:
            mapped = normalize_status(status)
            result = public_execution_fields(
                self._update(
                    execution_id,
                    status=mapped,
                    error=str(run.get("error") or ""),
                    error_code=str(run.get("error_code") or status),
                    finished_at=str(run.get("finished_at") or _utcnow()),
                    agent_run=run,
                    answer=extract_provider_answer(run),
                    usage=run.get("usage") if isinstance(run.get("usage"), dict) else {},
                )
            )
            self._record_history(result)
            return result
        return public_execution_fields(
            self._update(
                execution_id,
                agent_run=run,
                answer=extract_provider_answer(run) or str(row.get("answer") or ""),
                usage=run.get("usage") if isinstance(run.get("usage"), dict) else row.get("usage"),
            )
        )
