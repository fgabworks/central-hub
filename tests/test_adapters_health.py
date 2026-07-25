"""Health probe caching and parallel AdapterManager behavior."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from hub.adapters.manager import AdapterManager
from hub.registry.models import HealthCheckConfig, Registry, RegistryDefaults, Repository


def _repo(repo_id: str, *, delay: float = 0.0) -> Repository:
    return Repository(
        id=repo_id,
        name=repo_id,
        type="command",
        enabled=True,
        local_path="samples/sample-cli",
        health_check=HealthCheckConfig(type="path", local_path="samples/sample-cli"),
        # delay stored only for test hooks via id
        tags=[f"delay:{delay}"] if delay else [],
    )


class AdapterHealthCacheTests(unittest.TestCase):
    def test_check_all_uses_cache_within_ttl(self) -> None:
        registry = Registry(
            repositories=[_repo("a"), _repo("b")],
            defaults=RegistryDefaults(),
        )
        manager = AdapterManager(registry, cache_ttl_seconds=30.0)
        calls = {"n": 0}

        def fake_check(repo: Repository) -> dict:
            calls["n"] += 1
            return {
                "repository_id": repo.id,
                "name": repo.name,
                "type": repo.type,
                "enabled": True,
                "ok": True,
                "status": "healthy",
                "detail": "ok",
                "latency_ms": 1,
                "checked_at": "t",
            }

        with patch.object(manager, "check_repository", side_effect=fake_check):
            first = manager.check_all()
            second = manager.check_all()
            self.assertEqual(len(first), 2)
            self.assertEqual(calls["n"], 2)  # one parallel pass; cache hit adds none
            forced = manager.check_all(force=True)
            self.assertEqual(len(forced), 2)
            self.assertEqual(calls["n"], 4)
            self.assertEqual(second[0]["repository_id"], first[0]["repository_id"])

        # Mutations to returned lists must not poison the cache.
        first[0]["ok"] = False
        self.assertTrue(second[0]["ok"])

    def test_parallel_check_all_faster_than_sequential(self) -> None:
        registry = Registry(
            repositories=[_repo("a"), _repo("b"), _repo("c")],
            defaults=RegistryDefaults(),
        )
        manager = AdapterManager(registry, cache_ttl_seconds=0, max_workers=3)

        def slow_check(repo: Repository) -> dict:
            time.sleep(0.15)
            return {
                "repository_id": repo.id,
                "name": repo.name,
                "type": repo.type,
                "enabled": True,
                "ok": True,
                "status": "healthy",
                "detail": "ok",
                "latency_ms": 150,
                "checked_at": "t",
            }

        with patch.object(manager, "check_repository", side_effect=slow_check):
            started = time.perf_counter()
            results = manager.check_all(force=True)
            elapsed = time.perf_counter() - started
        self.assertEqual(len(results), 3)
        # Sequential would be ~0.45s; parallel should finish well under that.
        self.assertLess(elapsed, 0.35)


if __name__ == "__main__":
    unittest.main()
