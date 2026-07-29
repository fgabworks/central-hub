"""Tests for Repository Processes / Active Application polling policy."""

from __future__ import annotations

import unittest

from hub.repository_workspace.process_polling import (
    ACTIVE_STATUS_INTERVAL_MS,
    PROCESS_SCAN_INTERVAL_MS,
    PROCESS_SCAN_TIMEOUT_MS,
    ScanRequestGate,
    decide_polls,
    is_active_lifecycle,
    polling_config_for_ui,
    should_poll_active_status,
    should_poll_process_scan,
)


class PollingIntervalTests(unittest.TestCase):
    def test_polling_intervals(self) -> None:
        self.assertGreaterEqual(ACTIVE_STATUS_INTERVAL_MS, 3000)
        self.assertLessEqual(ACTIVE_STATUS_INTERVAL_MS, 5000)
        self.assertEqual(PROCESS_SCAN_INTERVAL_MS, 15000)
        self.assertEqual(PROCESS_SCAN_TIMEOUT_MS, 10000)
        cfg = polling_config_for_ui()
        self.assertEqual(cfg["process_scan_interval_ms"], 15000)
        self.assertEqual(cfg["process_scan_timeout_ms"], 10000)


class VisibilityPauseTests(unittest.TestCase):
    def test_visibility_pause(self) -> None:
        # Tab hidden → no polls
        d = decide_polls(
            auto_refresh=True,
            display_status="Running + Healthy",
            panel_visible=True,
            tab_visible=False,
        )
        self.assertFalse(d.poll_active_status)
        self.assertFalse(d.poll_process_scan)
        self.assertEqual(d.reason, "tab_hidden")

        # Panel hidden → status may continue, process scan pauses
        d2 = decide_polls(
            auto_refresh=True,
            display_status="Starting",
            panel_visible=False,
            tab_visible=True,
        )
        self.assertTrue(d2.poll_active_status)
        self.assertFalse(d2.poll_process_scan)
        self.assertEqual(d2.reason, "panel_hidden_status_only")

        # Idle / stopped → no automatic process scans
        for status in ("Stopped", "Failed"):
            self.assertFalse(
                should_poll_process_scan(
                    display_status=status,
                    panel_visible=True,
                    tab_visible=True,
                )
            )
            self.assertFalse(
                should_poll_active_status(display_status=status, tab_visible=True)
            )

        # Active + visible → both
        d3 = decide_polls(
            display_status="Running",
            panel_visible=True,
            tab_visible=True,
        )
        self.assertTrue(d3.poll_active_status)
        self.assertTrue(d3.poll_process_scan)


class OverlapAndTimeoutTests(unittest.TestCase):
    def test_overlap_prevention(self) -> None:
        gate = ScanRequestGate(timeout_ms=10000)
        self.assertTrue(gate.begin(0))
        self.assertTrue(gate.in_flight)
        self.assertTrue(gate.to_public()["scan_button_disabled"])
        # Second begin blocked while in flight
        self.assertFalse(gate.begin(100))
        gate.finish(200)
        self.assertFalse(gate.in_flight)
        self.assertTrue(gate.begin(300))

    def test_timeout(self) -> None:
        gate = ScanRequestGate(timeout_ms=10000)
        gate.begin(0)
        self.assertFalse(gate.timed_out(9999))
        self.assertTrue(gate.timed_out(10000))
        self.assertTrue(gate.timed_out(15000))
        gate.finish(15000, error="Process scan timed out after 10s")
        self.assertEqual(gate.last_error, "Process scan timed out after 10s")
        self.assertFalse(gate.in_flight)

    def test_manual_refresh_allowed_when_idle(self) -> None:
        # Manual refresh is always caller-driven; policy only gates *automatic* scans.
        self.assertFalse(
            should_poll_process_scan(
                display_status="Stopped",
                panel_visible=True,
                tab_visible=True,
            )
        )
        # Manual path uses the gate, not should_poll_process_scan
        gate = ScanRequestGate()
        self.assertTrue(gate.begin(0), "manual refresh must be able to start when idle")
        gate.finish(50)
        self.assertIsNone(gate.last_error)

    def test_active_lifecycle_helpers(self) -> None:
        self.assertTrue(is_active_lifecycle("Starting"))
        self.assertTrue(is_active_lifecycle(auto_refresh=True))
        self.assertFalse(is_active_lifecycle("Stopped"))


if __name__ == "__main__":
    unittest.main()
