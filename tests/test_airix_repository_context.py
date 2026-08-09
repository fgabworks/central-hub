"""AiriX repository context resolution for coding agents."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from hub.agent_center.dock import load_dock_prefs, save_dock_prefs
from hub.agent_center.repository_context import (
    agent_requires_repository,
    resolve_repository_context,
)
from hub.agent_center.routing.context import (
    build_minimal_context_preview,
    select_repository_ids,
)
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation
from hub.agent_center.service import AgentCenterError, AgentCenterService


def _repos(*ids: str, root: Path | None = None) -> list[dict]:
    out = []
    for rid in ids:
        row: dict = {"id": rid, "name": rid, "selectable": True}
        if root is not None:
            path = root / rid
            path.mkdir(parents=True, exist_ok=True)
            row["path"] = str(path)
        out.append(row)
    return out


def _classification(**overrides) -> PromptClassification:
    base = dict(
        task_type="coding",
        complexity=2,
        risk="medium",
        estimated_scope_files=3,
        context_size="medium",
        needs_coding=True,
        needs_testing=False,
        needs_architecture=False,
        deterministic_capable=False,
        signals=["code"],
    )
    base.update(overrides)
    return PromptClassification(**base)


class ResolveRepositoryContextTests(unittest.TestCase):
    def test_zero_repos_requires_selection_for_codex(self) -> None:
        resolved = resolve_repository_context(agent_id="codex", repository_ids=[], repositories=[])
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["code"], "repository_unavailable")
        self.assertTrue(resolved["needs_selection"])

    def test_sole_connected_auto_selected(self) -> None:
        resolved = resolve_repository_context(
            agent_id="claude-code",
            repository_ids=[],
            repositories=_repos("only-one"),
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["repository_ids"], ["only-one"])
        self.assertEqual(resolved["source"], "sole_connected")

    def test_multiple_without_selection_requires_user(self) -> None:
        resolved = resolve_repository_context(
            agent_id="cursor-agent",
            repository_ids=[],
            repositories=_repos("a", "b"),
        )
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["code"], "repository_required")
        self.assertEqual(resolved["repository_ids"], [])

    def test_does_not_blind_pick_first_of_many(self) -> None:
        resolved = resolve_repository_context(
            agent_id="codex",
            repository_ids=[],
            repositories=_repos("first", "second"),
        )
        self.assertFalse(resolved["ok"])
        self.assertNotIn("first", resolved["repository_ids"])

    def test_explicit_selection_wins(self) -> None:
        resolved = resolve_repository_context(
            agent_id="codex",
            repository_ids=["second"],
            active_repository_id="first",
            selected_repository_id="first",
            repositories=_repos("first", "second"),
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["repository_ids"], ["second"])
        self.assertEqual(resolved["source"], "explicit")

    def test_persisted_selection_before_active_workspace(self) -> None:
        resolved = resolve_repository_context(
            agent_id="codex",
            repository_ids=[],
            selected_repository_id="second",
            active_repository_id="first",
            repositories=_repos("first", "second"),
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["repository_ids"], ["second"])
        self.assertEqual(resolved["source"], "persisted_selection")

    def test_active_workspace_when_no_persisted(self) -> None:
        resolved = resolve_repository_context(
            agent_id="codex",
            repository_ids=[],
            active_repository_id="second",
            repositories=_repos("first", "second"),
        )
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["repository_ids"], ["second"])
        self.assertEqual(resolved["source"], "active_workspace")

    def test_non_repo_agents_do_not_require_repo(self) -> None:
        for agent in ("grok", "openai-api", "hub-simulator"):
            resolved = resolve_repository_context(
                agent_id=agent,
                repository_ids=[],
                repositories=_repos("a", "b"),
            )
            self.assertTrue(resolved["ok"], agent)
            self.assertEqual(resolved["repository_ids"], [])
            self.assertFalse(resolved["required"])

    def test_path_validation_fails_when_missing(self) -> None:
        resolved = resolve_repository_context(
            agent_id="codex",
            repository_ids=["gone"],
            repositories=[{"id": "gone", "selectable": True, "path": "/no/such/path/for-airix-test"}],
        )
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["code"], "repository_path_missing")

    def test_agent_requires_repository_matrix(self) -> None:
        self.assertTrue(agent_requires_repository("codex"))
        self.assertTrue(agent_requires_repository("claude-code"))
        self.assertTrue(agent_requires_repository("cursor-agent"))
        self.assertFalse(agent_requires_repository("grok"))
        self.assertFalse(agent_requires_repository("deterministic"))


class ServiceResolveRepositoryIdsTests(unittest.TestCase):
    def _svc(self, repos: list[dict]) -> AgentCenterService:
        svc = AgentCenterService.__new__(AgentCenterService)
        svc.repositories = lambda profile_id: list(repos)  # type: ignore[method-assign]
        return svc

    def test_service_raises_when_multiple_unselected(self) -> None:
        svc = self._svc(_repos("a", "b"))
        with self.assertRaises(AgentCenterError) as ctx:
            svc.resolve_repository_ids("okarun", repository_ids=[], agent_id="codex")
        self.assertEqual(ctx.exception.code, "repository_required")

    def test_service_sole_repo(self) -> None:
        svc = self._svc(_repos("only"))
        self.assertEqual(
            svc.resolve_repository_ids("okarun", repository_ids=[], agent_id="codex"),
            ["only"],
        )

    def test_manual_override_keeps_explicit_repo(self) -> None:
        svc = self._svc(_repos("a", "b"))
        self.assertEqual(
            svc.resolve_repository_ids(
                "okarun", repository_ids=["b"], agent_id="cursor-agent"
            ),
            ["b"],
        )

    def test_default_wrapper_returns_empty_instead_of_raise(self) -> None:
        svc = self._svc(_repos("a", "b"))
        self.assertEqual(
            svc.default_repository_ids("okarun", repository_ids=[], agent_id="codex"),
            [],
        )


class DockPrefsPersistenceTests(unittest.TestCase):
    def test_selected_repository_persists_per_workspace(self) -> None:
        db = MagicMock()
        store: dict[str, str] = {}

        def get_pref(_db, key, default=""):
            return store.get(key, default)

        def set_pref(_db, key, value):
            store[key] = value

        from hub.agent_center import dock as dock_mod

        original_get = dock_mod.get_pref
        original_set = dock_mod.set_pref
        dock_mod.get_pref = get_pref  # type: ignore[assignment]
        dock_mod.set_pref = set_pref  # type: ignore[assignment]
        try:
            save_dock_prefs(db, "work", {"selected_repository_id": "repo-b"})
            loaded = load_dock_prefs(db, "work")
            self.assertEqual(loaded["selected_repository_id"], "repo-b")
            other = load_dock_prefs(db, "personal")
            self.assertEqual(other["selected_repository_id"], "")
        finally:
            dock_mod.get_pref = original_get  # type: ignore[assignment]
            dock_mod.set_pref = original_set  # type: ignore[assignment]


class SmartRoutingRepoContextTests(unittest.TestCase):
    def test_non_coding_task_strips_repos_from_preview(self) -> None:
        classification = _classification(
            task_type="lookup",
            complexity=1,
            risk="low",
            estimated_scope_files=1,
            context_size="small",
            needs_coding=False,
            deterministic_capable=True,
            signals=["dhis2"],
        )
        rec = RouteRecommendation(
            task_type="lookup",
            complexity=1,
            risk="low",
            recommended_agent="deterministic",
            recommended_label="T0",
            recommended_tier="T0",
            alternative_agent=None,
            alternative_label=None,
            confidence=0.9,
            reason="lookup",
            estimated_usage="Very Low",
            approval_required=False,
            classification=classification,
        )
        preview = build_minimal_context_preview(
            prompt="list recent dhis2 jobs",
            classification=classification,
            recommendation=rec,
            repository_ids=["central-hub"],
        )
        self.assertEqual(preview["repository_ids"], [])

    def test_coding_task_keeps_requested_repos(self) -> None:
        ids = select_repository_ids(
            _classification(task_type="coding"),
            ["repo-a", "repo-b"],
        )
        self.assertEqual(ids, ["repo-a", "repo-b"])


if __name__ == "__main__":
    unittest.main()
