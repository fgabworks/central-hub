"""Focused UI contract tests for the shared compact section header."""
from __future__ import annotations

import unittest

from app import create_app


REPRESENTATIVE_PAGES = [
    ("/work", "Work Dashboard"),
    ("/repositories", "Repositories"),
    ("/sql", "SQL Workspace"),
    ("/data-explorer", "Data Explorer"),
    ("/jobs", "Jobs"),
    ("/health", "Health"),
    ("/dhis2", "DHIS2 Overview"),
    ("/dhis2/lookup", "DHIS2 Lookup"),
    ("/dhis2/reports", "DHIS2 Reports"),
    ("/work/airix", "AiriX"),
    ("/work/email", "Email Center"),
    ("/system/ai-connections", "AI Connections"),
    ("/audit", "Audit"),
    ("/settings", "Settings"),
]


class SectionHeaderUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_representative_pages_use_shared_section_header(self):
        for path, label in REPRESENTATIVE_PAGES:
            with self.subTest(path=path, label=label):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, path)
                html = resp.get_data(as_text=True)
                self.assertIn("data-section-header", html, path)
                self.assertIn("section-header-shell", html, path)
                self.assertIn("section-header-title", html, path)
                self.assertNotIn("page-header", html, path)
                self.assertNotIn("dex-page-header", html, path)
                self.assertNotIn("pnc-crumb", html, path)
                self.assertNotIn("pnc-header", html, path)

    def test_info_tooltip_present_when_description_exists(self):
        resp = self.client.get("/settings")
        html = resp.get_data(as_text=True)
        self.assertIn("section-header-info", html)
        self.assertIn("About this page", html)
        self.assertIn("Environment and registry configuration overview.", html)

    def test_tabs_render_in_separate_row_below_header(self):
        resp = self.client.get("/dhis2/reports")
        html = resp.get_data(as_text=True)
        header_idx = html.find("data-section-header")
        tabs_idx = html.find("data-section-tabs")
        nav_idx = html.find('aria-label="DHIS2 Report Workspace"')
        filters_idx = html.find('class="nb-filters"')
        self.assertGreater(header_idx, 0)
        self.assertGreater(tabs_idx, header_idx)
        self.assertGreater(nav_idx, tabs_idx)
        self.assertGreater(filters_idx, nav_idx)
        # Tabs must not sit inside the 48px title row.
        title_row_end = html.find("</header>", header_idx)
        self.assertGreater(title_row_end, header_idx)
        self.assertGreater(tabs_idx, title_row_end)

    def test_pages_without_tabs_omit_tab_row(self):
        resp = self.client.get("/sql")
        html = resp.get_data(as_text=True)
        self.assertIn("data-section-header", html)
        self.assertNotIn("data-section-tabs", html)

    def test_data_explorer_workspace_tabs_and_env_badge_preserved(self):
        resp = self.client.get("/data-explorer")
        html = resp.get_data(as_text=True)
        self.assertIn('data-workspace-tab="browse"', html)
        self.assertIn('id="dex-header-env"', html)
        self.assertIn("data-section-tabs", html)
        self.assertNotIn("dex-breadcrumb", html)

    def test_agent_center_actions_preserved(self):
        resp = self.client.get("/work/airix")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="ac-lock-toggle"', html)
        self.assertIn('id="ac-open-dock"', html)
        self.assertIn('id="ac-safety-details"', html)
        self.assertIn("section-header", html)


if __name__ == "__main__":
    unittest.main()
