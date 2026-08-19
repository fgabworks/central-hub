"""CLIMATE weather-galaxy sky contracts."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ClimateSkyContractTests(unittest.TestCase):
    def test_shared_shell_includes_css_only_sky(self) -> None:
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "climate-theme.css").read_text(encoding="utf-8")
        js = (ROOT / "static" / "js" / "climate_sky.js").read_text(encoding="utf-8")
        self.assertIn('class="climate-sky"', base)
        self.assertIn("climate-sky-quiet", base)
        self.assertIn("climate_sky.js", base)
        self.assertIn("climate-sky-galaxy", base)
        self.assertIn("climate-sky-horizon", base)
        self.assertIn("climate-sky-weather", base)
        self.assertIn("pointer-events: none", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("is-sky-paused", css)
        self.assertIn("climate-sky-quiet", css)
        self.assertIn("visibilitychange", js)
        self.assertIn("prefers-reduced-motion", js)
        self.assertNotIn('getContext("webgl")', js)
        self.assertNotIn("WebGLRenderingContext", js)
        self.assertNotIn("requestAnimationFrame", js)
        self.assertNotIn("THREE", js)

    def test_sky_is_enabled_on_shared_surfaces_and_quiet_in_code_workspace(self) -> None:
        from app import create_app

        app = create_app()
        app.config.update(TESTING=True)
        client = app.test_client()
        dash = client.get("/work").get_data(as_text=True)
        chat = client.get("/work/chat").get_data(as_text=True)
        connections = client.get("/system/ai-connections").get_data(as_text=True)
        workspace = client.get("/work/climate").get_data(as_text=True)
        for html in (dash, chat, connections, workspace):
            self.assertIn('class="climate-sky"', html)
            self.assertIn("climate_sky.js", html)
        self.assertNotIn("climate-sky-quiet", dash.split("<body", 1)[1][:400])
        self.assertNotIn("climate-sky-quiet", chat.split("<body", 1)[1][:400])
        self.assertNotIn("climate-sky-quiet", connections.split("<body", 1)[1][:400])
        self.assertIn("climate-sky-quiet", workspace.split("<body", 1)[1][:400])
        chat_css = (ROOT / "static" / "css" / "climate_chat.css").read_text(encoding="utf-8")
        self.assertIn("background: transparent", chat_css)
        self.assertIn("color-mix(in srgb, var(--climate-bg, #0d0f12) 88%, transparent)", chat_css)
