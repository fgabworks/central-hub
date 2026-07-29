"""Run tab status reconciliation — process vs health facets."""

from __future__ import annotations

import unittest

from hub.repository_workspace.process_manager import ManagedRun
from hub.repository_workspace.run_status import (
    DISPLAY_FAILED,
    DISPLAY_RUNNING,
    DISPLAY_RUNNING_HEALTHY,
    DISPLAY_RUNNING_UNHEALTHY,
    DISPLAY_STARTING,
    DISPLAY_STOPPED,
    DISPLAY_STOPPING,
    TONE_AMBER,
    TONE_GRAY,
    TONE_GREEN,
    TONE_RED,
    actions_for_display,
    build_run_dashboard,
    detect_port_orphan,
    display_status_for,
    health_state_from_run,
    process_kind_label,
    process_state_from_status,
    reconcile_run,
)


def _run(**kwargs) -> ManagedRun:
    base = dict(
        run_id="r1",
        repo_id="demo-repo",
        profile_id="demo-profile",
        environment="development",
        port=9001,
        status="stopped",
        pid=111,
        local_url="http://127.0.0.1:9001/",
        health_url="http://127.0.0.1:9001/health",
        started_at="2026-07-27T06:00:00+00:00",
        last_health_ok=None,
        last_health_detail="",
    )
    base.update(kwargs)
    return ManagedRun(**base)


class DisplayToneAndActionsTests(unittest.TestCase):
    def test_status_colors(self) -> None:
        cases = [
            ("starting", None, "", DISPLAY_STARTING, TONE_AMBER),
            ("healthy", True, "HTTP 200", DISPLAY_RUNNING_HEALTHY, TONE_GREEN),
            ("unhealthy", False, "timeout", DISPLAY_RUNNING_UNHEALTHY, TONE_AMBER),
            ("running", None, "", DISPLAY_RUNNING, TONE_GREEN),  # no health URL below
            ("failed", None, "", DISPLAY_FAILED, TONE_RED),
            ("stopping", None, "", DISPLAY_STOPPING, TONE_AMBER),
            ("stopped", True, "HTTP 200", DISPLAY_STOPPED, TONE_GRAY),
        ]
        for status, ok, detail, display, tone in cases:
            run = _run(
                status=status,
                last_health_ok=ok,
                last_health_detail=detail,
                health_url=None if status == "running" else "http://127.0.0.1:9001/health",
            )
            view = reconcile_run(run, check_port=False)
            self.assertEqual(view.display_status, display, status)
            self.assertEqual(view.display_tone, tone, status)

    def test_action_visibility(self) -> None:
        self.assertEqual(actions_for_display(DISPLAY_STOPPED, has_local_url=True, has_run_id=False), ["start"])
        self.assertEqual(
            actions_for_display(DISPLAY_STARTING, has_local_url=True, has_run_id=True),
            ["stop", "view_logs"],
        )
        self.assertEqual(
            actions_for_display(DISPLAY_RUNNING_HEALTHY, has_local_url=True, has_run_id=True),
            ["stop", "restart", "open_app", "view_logs"],
        )
        self.assertEqual(
            actions_for_display(DISPLAY_RUNNING_UNHEALTHY, has_local_url=False, has_run_id=True),
            ["stop", "restart", "view_logs"],
        )
        self.assertEqual(
            actions_for_display(DISPLAY_FAILED, has_local_url=True, has_run_id=True),
            ["retry", "view_logs"],
        )
        self.assertEqual(
            actions_for_display(DISPLAY_STOPPING, has_local_url=True, has_run_id=True),
            ["view_logs"],
        )

    def test_auto_refresh_flag(self) -> None:
        for status in ("starting", "healthy", "unhealthy", "stopping"):
            view = reconcile_run(_run(status=status), check_port=False)
            self.assertTrue(view.auto_refresh, status)
        stopped = reconcile_run(_run(status="stopped"), check_port=False)
        self.assertFalse(stopped.auto_refresh)


class ProcessHealthSeparationTests(unittest.TestCase):
    def test_stopped_never_shows_http_200_as_health(self) -> None:
        run = _run(
            status="stopped",
            last_health_ok=True,
            last_health_detail="HTTP 200",
        )
        view = reconcile_run(run, check_port=False)
        self.assertEqual(view.process_state, "stopped")
        self.assertEqual(view.health_state, "stale")
        self.assertEqual(view.display_status, DISPLAY_STOPPED)
        self.assertNotIn("HTTP 200", view.health_detail)
        self.assertEqual(view.to_public()["last_health_detail"], "")
        # Combined invalid state must not appear
        blob = f"{view.display_status} · {view.health_detail}"
        self.assertNotIn("Stopped · HTTP 200", blob)

    def test_process_and_health_facets(self) -> None:
        self.assertEqual(process_state_from_status("healthy"), "running")
        self.assertEqual(process_state_from_status("unhealthy"), "running")
        self.assertEqual(
            health_state_from_run(
                _run(status="stopped", last_health_ok=True, last_health_detail="HTTP 200"),
                process_alive=False,
            ),
            "stale",
        )
        self.assertEqual(
            display_status_for(process_state="running", health_state="healthy"),
            DISPLAY_RUNNING_HEALTHY,
        )


class PortOrphanTests(unittest.TestCase):
    def test_stopped_but_port_active_detection(self) -> None:
        orphan = detect_port_orphan(
            port=5050,
            tracked_pid=99,
            process_alive=False,
            port_is_available=lambda _p: False,
            listeners_for=lambda _p: [4242],
        )
        self.assertIsNotNone(orphan)
        self.assertEqual(orphan["code"], "port_orphan")
        self.assertIn("another process is using port 5050", orphan["message"])
        self.assertEqual(orphan["listener_pids"], [4242])

        # Port free → no orphan
        self.assertIsNone(
            detect_port_orphan(
                port=5050,
                tracked_pid=99,
                process_alive=False,
                port_is_available=lambda _p: True,
                listeners_for=lambda _p: [],
            )
        )

        # Alive process → no orphan
        self.assertIsNone(
            detect_port_orphan(
                port=5050,
                tracked_pid=99,
                process_alive=True,
                port_is_available=lambda _p: False,
                listeners_for=lambda _p: [99],
            )
        )

    def test_reconcile_includes_orphan_warning(self) -> None:
        view = reconcile_run(
            _run(status="stopped", port=5050, pid=11, last_health_ok=True, last_health_detail="HTTP 200"),
            check_port=True,
            port_is_available=lambda _p: False,
            listeners_for=lambda _p: [222],
        )
        self.assertEqual(view.warning_code, "port_orphan")
        self.assertIn("port 5050", view.warning or "")


class PidReconciliationAndDashboardTests(unittest.TestCase):
    def test_pid_reconciliation_via_process_alive(self) -> None:
        # Hub still thinks healthy, but fingerprint says dead
        view = reconcile_run(
            _run(status="healthy", last_health_ok=True, last_health_detail="HTTP 200"),
            process_alive=False,
            check_port=False,
        )
        self.assertEqual(view.process_state, "stopped")
        self.assertEqual(view.display_status, DISPLAY_STOPPED)
        self.assertEqual(view.health_state, "stale")

    def test_dashboard_active_first_and_history(self) -> None:
        runs = [
            _run(run_id="old", status="stopped", started_at="2026-07-27T05:00:00+00:00"),
            _run(
                run_id="live",
                status="healthy",
                started_at="2026-07-27T06:00:00+00:00",
                last_health_ok=True,
                last_health_detail="HTTP 200",
            ),
            _run(run_id="older", status="failed", started_at="2026-07-27T04:00:00+00:00"),
        ]
        dash = build_run_dashboard(
            runs,
            repo_id="demo-repo",
            check_ports=False,
        )
        self.assertEqual(dash["active"]["run_id"], "live")
        self.assertEqual(dash["active"]["display_status"], DISPLAY_RUNNING_HEALTHY)
        self.assertTrue(dash["auto_refresh"])
        hist_ids = [h["run_id"] for h in dash["history"]]
        self.assertEqual(hist_ids, ["old", "older"])

    def test_history_limited_to_five(self) -> None:
        runs = [
            _run(
                run_id=f"r{i}",
                status="stopped",
                started_at=f"2026-07-27T{10 + i:02d}:00:00+00:00",
            )
            for i in range(8)
        ]
        dash = build_run_dashboard(runs, repo_id="demo-repo", check_ports=False)
        self.assertEqual(len(dash["history"]), 5)
        self.assertEqual(dash["history_total"], 8)
        self.assertEqual(dash["history"][0]["run_id"], "r7")
        self.assertEqual(dash["history"][-1]["run_id"], "r3")

    def test_generic_repository_support(self) -> None:
        # Any repo/profile ids work — no PMNP hardcoding
        run = _run(
            repo_id="acme-api-local",
            profile_id="acme-web",
            status="starting",
            port=3333,
        )
        view = reconcile_run(run, check_port=False)
        self.assertEqual(view.repo_id, "acme-api-local")
        self.assertEqual(view.profile_id, "acme-web")
        self.assertEqual(view.display_status, DISPLAY_STARTING)
        dash = build_run_dashboard(
            [run],
            repo_id="acme-api-local",
            preferred_profile_id="acme-web",
            preferred_port=3333,
            check_ports=False,
        )
        self.assertEqual(dash["active"]["repo_id"], "acme-api-local")

    def test_process_kind_labels(self) -> None:
        self.assertEqual(process_kind_label({"managed_by_hub": True}), "Managed")
        self.assertEqual(
            process_kind_label({"managed_by_hub": False, "confidence": "High"}),
            "External",
        )
        self.assertEqual(
            process_kind_label(
                {
                    "managed_by_hub": False,
                    "confidence": "Low",
                    "detection_reasons": ["possible_stale"],
                }
            ),
            "Possible stale process",
        )


if __name__ == "__main__":
    unittest.main()
