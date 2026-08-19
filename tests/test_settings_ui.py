"""Shared CLIMATE Settings layout contract."""

from __future__ import annotations

import unittest

from app import create_app


LIVE_PAGES = (
    ("/settings", "overview", "General"),
    ("/settings/branding", "branding", "Branding"),
    ("/settings/ai-providers", "ai_providers", "AI Providers"),
)

PLANNED_PAGES = (
    ("/settings/appearance", "appearance", "Appearance"),
    ("/settings/integrations", "integrations", "Integrations"),
    ("/settings/security", "security", "Security"),
    ("/settings/notifications", "notifications", "Notifications"),
    ("/settings/advanced", "advanced", "Advanced"),
)


class SettingsUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def _html(self, path: str) -> str:
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def test_all_settings_pages_share_layout(self) -> None:
        for path, _tab, title in LIVE_PAGES + PLANNED_PAGES:
            with self.subTest(path=path):
                html = self._html(path)
                self.assertIn("settings-layout", html)
                self.assertIn("settings-nav-wrap", html)
                self.assertIn("settings-main", html)
                self.assertIn("settings-lede", html)
                self.assertIn("data-section-header", html)
                self.assertIn(f">{title}<", html)
                self.assertIn("aria-current=\"page\"", html)
                self.assertNotIn("page-header", html)
                nav = html[html.find('class="settings-nav"') : html.find("settings-main")]
                self.assertIn("General", nav)
                self.assertIn("Branding", nav)
                self.assertIn("Appearance", nav)
                self.assertIn("AI Providers", nav)
                self.assertIn("Integrations", nav)
                self.assertIn("Security", nav)
                self.assertIn("Notifications", nav)
                self.assertIn("Advanced", nav)
                self.assertIn("/settings/appearance", nav)
                self.assertIn("/settings/ai-providers", nav)

    def test_planned_pages_are_marked_and_do_not_claim_implementation(self) -> None:
        for path, _tab, title in PLANNED_PAGES:
            with self.subTest(path=path):
                html = self._html(path)
                self.assertIn("Planned", html)
                self.assertIn("settings-planned-card", html)
                self.assertIn("No settings are saved from this page yet", html)
                self.assertIn(title, html)
                self.assertNotIn('name="action"', html)

    def test_general_keeps_existing_controls(self) -> None:
        html = self._html("/settings")
        self.assertIn("Runtime", html)
        self.assertIn("Owner role (Phase 6)", html)
        self.assertIn("DHIS2 (read-only)", html)
        self.assertIn("AI Provider Connections", html)
        self.assertIn("AiriX Smart Routing (Phase 5)", html)
        self.assertIn("settings-card", html)
        self.assertIn("settings-form", html)
        self.assertIn("ai-provider-compact", html)

    def test_branding_and_providers_keep_function_markers(self) -> None:
        branding = self._html("/settings/branding")
        self.assertIn("id=\"branding-settings\"", branding)
        self.assertIn("branding-logo-replace", branding)
        self.assertIn("branding-avatar-file", branding)
        self.assertIn("Save changes", branding)
        self.assertIn("settings-layout", branding)
        providers = self._html("/settings/ai-providers")
        self.assertIn("id=\"ai-provider-settings\"", providers)
        self.assertIn("data-endpoint=\"/api/settings/ai-providers\"", providers)
        self.assertIn("ai-provider-key-dialog", providers)
        self.assertIn("settings-layout", providers)

    def test_shared_css_uses_compact_settings_system(self) -> None:
        from pathlib import Path

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".settings-layout", css)
        self.assertIn("max-width: 1080px", css)
        self.assertIn(".settings-card", css)
        self.assertIn(".settings-form", css)
        self.assertIn(".settings-nav-item.is-active", css)
        self.assertIn(".settings-planned-card", css)
        self.assertIn("@media (max-width: 1080px)", css)
        self.assertIn("@media (max-width: 900px)", css)
        self.assertNotIn("rgba(154, 122, 184, 0.18)", css)


if __name__ == "__main__":
    unittest.main()
