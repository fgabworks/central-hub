"""AiriX Tool Runtime — provider failure handling + action-based approval."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from hub.agent_center.routing.models import (
    PromptClassification,
    RouteRecommendation,
    RoutingSettings,
)
from hub.agent_center.routing.orchestrate import build_orchestration_plan
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.roles import detect_role
from hub.agent_center.tool_runtime.policy import policy_gate
from hub.agent_center.tool_runtime.provider_failures import (
    GLOBAL_PROVIDER_HEALTH,
    ProviderHealthCache,
    classify_provider_failure,
    pick_fallback_tool_runtime_provider,
)


def _classification(**overrides: Any) -> PromptClassification:
    base = dict(
        task_type="sql_investigation",
        complexity=40,
        risk="low",
        estimated_scope_files=2,
        context_size="small",
        needs_coding=False,
        needs_testing=False,
        needs_architecture=False,
        deterministic_capable=True,
        signals=["sql"],
    )
    base.update(overrides)
    return PromptClassification(**base)


def _recommendation(*, agent: str = "openai-api", approval: bool = False) -> RouteRecommendation:
    c = _classification()
    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent=agent,
        recommended_label=agent,
        recommended_tier="T1",
        alternative_agent="grok",
        alternative_label="Grok",
        confidence=0.8,
        reason="test",
        estimated_usage="Low",
        approval_required=approval,
        classification=c,
        providers_considered=[agent, "grok"],
        recommended_model="gpt-test",
        recommended_model_reason="test",
    )


class ProviderFailureClassifyTests(unittest.TestCase):
    def test_quota_hard_no_retry(self) -> None:
        info = classify_provider_failure(
            error="credit_balance_exhausted / insufficient_quota",
            error_code="stream_error",
        )
        self.assertEqual(info.category, "quota")
        self.assertTrue(info.hard)
        self.assertFalse(info.retryable)

    def test_auth_hard(self) -> None:
        info = classify_provider_failure(error="bad key", error_code="auth", http_status=401)
        self.assertEqual(info.category, "auth")
        self.assertTrue(info.hard)

    def test_rate_limit_transient(self) -> None:
        info = classify_provider_failure(error="too many", error_code="rate_limit", http_status=429)
        self.assertEqual(info.category, "rate_limit")
        self.assertTrue(info.retryable)
        self.assertFalse(info.hard)

    def test_timeout_transient(self) -> None:
        info = classify_provider_failure(error="timed out", error_code="timeout")
        self.assertEqual(info.category, "timeout")
        self.assertTrue(info.retryable)

    def test_health_cache_skips_hard_failed(self) -> None:
        cache = ProviderHealthCache(ttl_seconds=60)
        cache.mark_failure("openai-api", category="quota", message="quota")
        self.assertFalse(cache.is_healthy("openai-api"))
        alt = pick_fallback_tool_runtime_provider(
            failed_provider="openai-api",
            configured=["openai-api", "grok"],
            health=cache,
            availability=lambda _p: (True, ""),
        )
        self.assertEqual(alt, "grok")


class ProviderFailureExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        GLOBAL_PROVIDER_HEALTH.clear()
        from hub.agent_center.routing.execution import RouteExecutor

        self.svc = MagicMock()
        self.svc.api_runners = {"openai-api": object(), "grok": object()}
        self.executor = RouteExecutor(self.svc)

    def _seed_row(
        self,
        execution_id: str,
        *,
        manual: bool = False,
        max_retries: int = 2,
        provider: str = "openai-api",
    ) -> None:
        settings = RoutingSettings(max_retries=max_retries, require_approval_before_codex=True)
        rec = _recommendation(agent=provider)
        with self.executor._lock:
            self.executor._active[execution_id] = {
                "id": execution_id,
                "status": "running",
                "manual_override": manual,
                "_routing_settings": settings,
                "_recommendation": rec,
                "context": {
                    "tool_runtime": True,
                    "tool_runtime_lean_context": True,
                    "completion_contract": {"required": ["count"]},
                    "detected_filters": {"facility": "x"},
                    "evidence_packet": {"usable": True, "hits": [{"id": 1}]},
                    "repository_ids": ["repo-a"],
                },
                "selected_provider": provider,
                "selected_model": "gpt-test",
                "resolved_provider": provider,
                "resolved_model": "gpt-test",
                "provider_tried": [provider],
                "provider_retry_count": 0,
                "provider_switch_count": 0,
                "evidence_packet": {"usable": True, "hits": [{"id": 1}]},
            }

    def test_auto_quota_no_retry_loop_falls_back_once(self) -> None:
        eid = "exec-quota-1"
        self._seed_row(eid, manual=False)
        run = {
            "status": "failed",
            "error": "credit_balance_exhausted",
            "error_code": "quota",
            "agent_id": "openai-api",
            "model": "gpt-test",
            "usage": {},
            "answer": "",
        }
        calls: list[str] = []

        def fake_execute(execution_id, prompt, recommendation, context_preview, **kwargs):
            calls.append(str(kwargs.get("adapter_id") or ""))
            self.assertTrue(context_preview.get("evidence_packet"))
            self.assertTrue(context_preview.get("completion_contract"))
            self.assertTrue(context_preview.get("tool_runtime_lean_context"))
            return {"status": "completed", "answer": "ok via fallback", "id": execution_id}

        with patch.object(self.executor, "_provider_available", return_value=(True, "")):
            with patch.object(self.executor, "_execute_agent", side_effect=fake_execute):
                recovered = self.executor._maybe_recover_provider_failure(
                    eid,
                    prompt="how many?",
                    run=run,
                    repository_ids=["repo-a"],
                    evidence_packet={"usable": True, "hits": [{"id": 1}]},
                    synthesis_escalation=False,
                    chosen="openai-api",
                )
        self.assertIsNotNone(recovered)
        self.assertEqual(calls, ["grok"])
        with self.executor._lock:
            row = self.executor._active[eid]
        self.assertEqual(int(row.get("provider_retry_count") or 0), 0)
        telem = row.get("provider_failure_telemetry") or {}
        self.assertEqual(telem.get("failure_category"), "quota")
        self.assertTrue(telem.get("fallback_attempted"))
        self.assertEqual(telem.get("fallback_provider"), "grok")
        self.assertTrue(telem.get("context_preserved"))
        self.assertFalse(telem.get("retry_attempted"))

    def test_auto_hard_failure_no_alternative_exact_error(self) -> None:
        eid = "exec-quota-2"
        self._seed_row(eid, manual=False)
        GLOBAL_PROVIDER_HEALTH.mark_failure("grok", category="auth", message="auth")
        run = {
            "status": "failed",
            "error": "insufficient_quota",
            "error_code": "quota",
            "agent_id": "openai-api",
            "model": "gpt-test",
            "usage": {},
            "answer": "",
        }
        with patch.object(self.executor, "_provider_available", return_value=(True, "")):
            with patch.object(self.executor, "_execute_agent") as mock_exec:
                recovered = self.executor._maybe_recover_provider_failure(
                    eid,
                    prompt="how many?",
                    run=run,
                    repository_ids=["repo-a"],
                    evidence_packet={},
                    synthesis_escalation=False,
                    chosen="openai-api",
                )
                mock_exec.assert_not_called()
        self.assertIsNone(recovered)
        with self.executor._lock:
            row = self.executor._active[eid]
        self.assertIn("no_compatible_provider", str(row.get("fallback_reason") or ""))

    def test_manual_provider_no_silent_fallback(self) -> None:
        eid = "exec-manual-1"
        self._seed_row(eid, manual=True)
        run = {
            "status": "failed",
            "error": "insufficient_quota",
            "error_code": "quota",
            "agent_id": "openai-api",
            "model": "gpt-test",
            "usage": {},
            "answer": "",
        }
        with patch.object(self.executor, "_execute_agent") as mock_exec:
            recovered = self.executor._maybe_recover_provider_failure(
                eid,
                prompt="how many?",
                run=run,
                repository_ids=["repo-a"],
                evidence_packet={},
                synthesis_escalation=False,
                chosen="openai-api",
            )
            mock_exec.assert_not_called()
        self.assertIsNone(recovered)
        with self.executor._lock:
            row = self.executor._active[eid]
        self.assertEqual(row.get("fallback_reason"), "manual_no_silent_fallback")
        self.assertIn("no automatic fallback", str(row.get("next_action") or "").lower())

    def test_transient_uses_bounded_retry(self) -> None:
        eid = "exec-retry-1"
        self._seed_row(eid, manual=False, max_retries=2)
        run = {
            "status": "failed",
            "error": "OpenAI rate limit",
            "error_code": "rate_limit",
            "agent_id": "openai-api",
            "model": "gpt-test",
            "usage": {},
            "answer": "",
        }
        calls: list[str] = []

        def fake_execute(execution_id, prompt, recommendation, context_preview, **kwargs):
            calls.append(str(kwargs.get("adapter_id") or ""))
            return {"status": "completed", "answer": "ok", "id": execution_id}

        with patch.object(self.executor, "_execute_agent", side_effect=fake_execute):
            recovered = self.executor._maybe_recover_provider_failure(
                eid,
                prompt="retry me",
                run=run,
                repository_ids=["repo-a"],
                evidence_packet={},
                synthesis_escalation=False,
                chosen="openai-api",
            )
        self.assertIsNotNone(recovered)
        self.assertEqual(calls, ["openai-api"])
        with self.executor._lock:
            row = self.executor._active[eid]
        self.assertEqual(int(row.get("provider_retry_count") or 0), 1)
        telem = row.get("provider_failure_telemetry") or {}
        self.assertTrue(telem.get("retry_attempted"))
        self.assertFalse(telem.get("fallback_attempted"))

    def test_parent_finalize_surfaces_exact_provider_error(self) -> None:
        eid = "exec-ui-1"
        self._seed_row(eid, manual=True)
        run = {
            "status": "failed",
            "error": "credit_balance_exhausted",
            "error_code": "quota",
            "agent_id": "openai-api",
            "model": "gpt-test",
            "usage": {},
            "answer": "",
            "finished_at": "2026-08-10T00:00:00+00:00",
        }
        out = self.executor._finalize_synthesis_or_agent_answer(
            eid,
            prompt="how many?",
            run=run,
            repository_ids=["repo-a"],
            evidence_packet={"usable": False, "hits": [], "errors": [], "sources": []},
            synthesis_escalation=False,
            chosen="openai-api",
        )
        self.assertEqual(out.get("status"), "failed")
        self.assertIn("credit_balance_exhausted", str(out.get("error") or ""))
        self.assertIn("credit_balance_exhausted", str(out.get("answer") or ""))
        self.assertIn("no automatic fallback", str(out.get("error") or "").lower())


class ActionApprovalPolicyTests(unittest.TestCase):
    def test_codex_provider_no_longer_requires_approval(self) -> None:
        registry = ProviderRegistry()
        spec = registry.get("codex")
        assert spec is not None
        self.assertFalse(spec.requires_approval)

    def test_orchestration_codex_step_no_approval_gate(self) -> None:
        rec = RouteRecommendation(
            **{
                **_recommendation(agent="codex").__dict__,
                "recommended_tier": "T3",
                "escalation_reason": "architecture",
                "classification": _classification(complexity=80, needs_architecture=True),
            }
        )
        role = detect_role("redesign architecture across modules", rec.classification)
        steps = build_orchestration_plan(
            recommendation=rec,
            role=role,
            settings=RoutingSettings(require_approval_before_codex=True, allow_escalation=True),
        )
        codex_steps = [s for s in steps if s.provider_id == "codex"]
        self.assertTrue(codex_steps)
        for step in codex_steps:
            self.assertFalse(step.approval_required)
            self.assertNotIn("approval required", step.label.lower())

    def test_ro_tools_no_approval(self) -> None:
        for tool in ("sql_lookup", "sql_query_execute", "repo_search", "read_file", "uid_lookup"):
            gate = policy_gate(tool, interaction_mode="inspect", active_names={tool})
            self.assertTrue(gate.get("allowed"), tool)
            self.assertFalse(gate.get("requires_approval"), tool)

    def test_write_tools_still_require_approval_or_block(self) -> None:
        # Phase-1 registry is RO-only; unknown/non-RO tools stay blocked by policy.
        gate = policy_gate("run_command", interaction_mode="agent", allow_writes=False)
        self.assertFalse(gate.get("allowed"))
        # When a future write tool is registered with requires_approval, gate must demand it.
        from hub.agent_center.tool_runtime.specs import ACCESS_WRITE, ToolSpec
        from unittest.mock import patch

        write_spec = ToolSpec(
            name="file_write",
            description="write",
            capability="files",
            domain="workspace",
            access=ACCESS_WRITE,
            allowed_modes=("agent",),
            requires_approval=True,
        )
        with patch(
            "hub.agent_center.tool_runtime.policy.get_tool_spec",
            return_value=write_spec,
        ):
            gated = policy_gate("file_write", interaction_mode="agent", allow_writes=False)
        self.assertFalse(gated.get("allowed"))
        self.assertTrue(gated.get("requires_approval"))
        self.assertIn(gated.get("reason"), {"write_tools_blocked_phase1", "approval_required"})

    def test_manual_codex_inspect_execute_skips_provider_approval(self) -> None:
        from hub.agent_center.routing.context import build_direct_agent_recommendation
        from hub.agent_center.routing.execution import RouteExecutor

        direct = build_direct_agent_recommendation(
            "inspect schema",
            provider_id="codex",
            model="codex-1",
        )
        self.assertFalse(direct.approval_required)

        svc = MagicMock()
        executor = RouteExecutor(svc)
        rec = _recommendation(agent="codex", approval=True)
        settings = RoutingSettings(require_approval_before_codex=True, max_retries=1)

        with patch.object(executor, "_provider_available", return_value=(True, "ok")):
            with patch.object(
                executor,
                "_execute_agent",
                return_value={
                    "status": "completed",
                    "answer": "from codex",
                    "model": "codex-1",
                    "resolved_model": "codex-1",
                },
            ):
                out = executor.execute(
                    prompt="list tables",
                    recommendation=rec,
                    settings=settings,
                    agent_override="codex",
                    repository_ids=["repo-a"],
                    approve_codex=False,
                    force=True,
                    workspace="test",
                    attempt=0,
                    manual_override=True,
                    routing_mode="direct",
                    interaction_mode="inspect",
                    model="codex-1",
                )
        self.assertNotEqual(out.get("error_code"), "approval_required")
        self.assertNotEqual(out.get("status"), "paused_for_approval")
        # Exact selected Codex model preserved on the execution row.
        self.assertEqual(out.get("selected_model") or out.get("model"), "codex-1")


class DirectRecommendationApprovalTests(unittest.TestCase):
    def test_ask_plan_agent_codex_no_provider_approval(self) -> None:
        from hub.agent_center.routing.context import build_direct_agent_recommendation

        for mode_provider in ("codex", "claude-code", "cursor-agent"):
            rec = build_direct_agent_recommendation(
                "explain",
                provider_id=mode_provider,
                model="m1",
            )
            self.assertFalse(rec.approval_required, mode_provider)


if __name__ == "__main__":
    unittest.main()
