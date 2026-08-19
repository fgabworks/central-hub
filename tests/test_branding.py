"""CLIMATE Settings → Branding: local logo asset, display/fit, reset."""

from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from app import create_app
from hub.branding.service import BrandingError, BrandingService

ROOT = Path(__file__).resolve().parents[1]


def _png_bytes(size=(12, 6), color=(20, 80, 200, 255)) -> bytes:
    width, height = size
    red, green, blue, alpha = color

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes((red, green, blue, alpha)) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _webp_bytes() -> bytes:
    inner = b"VP8L\x0d\x00\x00\x00\x2f\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(inner)) + b"WEBP" + inner


def _svg_bytes(extra: str = "") -> bytes:
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='12' viewBox='0 0 24 12'>"
        "<rect width='24' height='12' fill='#1d4ed8'/>"
        f"{extra}</svg>"
    ).encode("utf-8")


class BrandingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = BrandingService(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_state_uses_contain_wordmark(self) -> None:
        state = self.svc.state()
        self.assertFalse(state["custom"])
        self.assertEqual(state["display"], "wordmark")
        self.assertEqual(state["avatar_display"], "icon")
        self.assertEqual(state["fit"], "contain")
        self.assertEqual(state["default_icon"], "img/climate-mark.png")
        self.assertEqual(state["default_full"], "img/climate-logo.png")

    def test_save_png_to_disk_not_base64_settings(self) -> None:
        png = _png_bytes()
        state = self.svc.save(display="full", fit="contain", payload=png, filename="wide.png")
        self.assertTrue(state["custom"])
        logo = Path(self.tmp.name) / "logo.png"
        self.assertTrue(logo.is_file())
        self.assertEqual(logo.read_bytes(), png)
        settings = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("base64", json.dumps(settings).lower())
        self.assertNotIn("data:image", json.dumps(settings))
        self.assertEqual(settings["filename"], "logo.png")
        self.assertEqual(settings["display"], "full")

    def test_rejects_jpeg_and_scripted_svg(self) -> None:
        with self.assertRaises(BrandingError) as jpeg:
            self.svc.save(payload=b"\xff\xd8\xff", filename="x.jpg")
        self.assertEqual(jpeg.exception.code, "type_unsupported")
        with self.assertRaises(BrandingError) as disguised:
            self.svc.save(payload=b"\xff\xd8\xff\xe0", filename="logo.png")
        self.assertEqual(disguised.exception.code, "type_unsupported")
        with self.assertRaises(BrandingError) as svg:
            self.svc.save(payload=_svg_bytes("<script>alert(1)</script>"), filename="x.svg")
        self.assertEqual(svg.exception.code, "type_unsupported")

    def test_reset_removes_custom_file(self) -> None:
        self.svc.save(payload=_png_bytes(), filename="mark.png")
        self.assertTrue((Path(self.tmp.name) / "logo.png").is_file())
        state = self.svc.reset()
        self.assertFalse(state["custom"])
        self.assertFalse((Path(self.tmp.name) / "logo.png").is_file())
        self.assertEqual(state["display"], "wordmark")
        self.assertEqual(state["avatar_display"], "icon")
        self.assertEqual(state["fit"], "contain")

    def test_persists_across_new_service_instance(self) -> None:
        self.svc.save(display="icon", fit="cover", payload=_svg_bytes(), filename="logo.svg")
        again = BrandingService(Path(self.tmp.name))
        state = again.state()
        self.assertTrue(state["custom"])
        self.assertEqual(state["display"], "icon")
        self.assertEqual(state["fit"], "cover")
        self.assertEqual(state["filename"], "logo.svg")
        path, ctype = again.logo_file()
        self.assertEqual(ctype, "image/svg+xml")
        self.assertTrue(path.is_file())
        again.save(display="wordmark", fit="contain")
        third = BrandingService(Path(self.tmp.name))
        self.assertTrue(third.state()["custom"])
        self.assertEqual(third.state()["display"], "wordmark")
        self.assertEqual(third.state()["fit"], "contain")
        self.assertTrue((Path(self.tmp.name) / "logo.svg").is_file())


class BrandingPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["BRANDING"] = BrandingService(Path(self.tmp.name))
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_settings_branding_page_and_nav(self) -> None:
        html = self.client.get("/settings/branding").get_data(as_text=True)
        self.assertIn("Branding", html)
        self.assertIn("Header / full logo", html)
        self.assertIn("Chat avatar / icon-only", html)
        self.assertIn("App Branding", html)
        self.assertIn("Chat Avatar", html)
        self.assertIn("Wordmark", html)
        self.assertIn("Full logo", html)
        self.assertIn("Icon only", html)
        self.assertIn("Contain", html)
        self.assertIn("Cover", html)
        self.assertIn("Reset to default", html)
        self.assertIn("PNG, SVG, or WEBP", html)
        self.assertIn("settings_branding.js", html)
        self.assertIn("branding-preview-header", html)
        self.assertIn("branding-preview-avatar", html)
        overview = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("/settings/branding", overview)
        self.assertIn("Branding", overview)

    def test_upload_preview_save_and_logo_route(self) -> None:
        png = _png_bytes((16, 8))
        saved = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(png), "climate.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(saved.status_code, 200)
        body = saved.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["branding"]["custom"])
        self.assertEqual(body["branding"]["avatar_display"], "icon")
        self.assertIn("/branding/logo", body["branding"]["logo_url"])
        self.assertIn("/branding/logo", body["branding"]["avatar_url"])
        self.assertIn("/branding/logo", body["branding"]["icon_url"])
        served = self.client.get(body["branding"]["logo_url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, png)
        self.assertIn("image/png", served.headers.get("Content-Type", ""))
        work = self.client.get("/work").get_data(as_text=True)
        self.assertIn("/branding/logo", work)
        self.assertIn('data-brand-display="full"', work)
        self.assertIn('data-brand-fit="contain"', work)
        page = self.client.get("/settings/branding").get_data(as_text=True)
        self.assertIn("/branding/logo", page)
        self.assertIn('data-brand-display="full"', page)
        prefs = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("base64", json.dumps(prefs).lower())
        self.assertEqual(prefs["filename"], "logo.png")
        options = self.client.post(
            "/api/settings/branding",
            data={"display": "wordmark", "fit": "cover"},
            content_type="multipart/form-data",
        )
        self.assertEqual(options.status_code, 200)
        self.assertTrue(options.get_json()["branding"]["custom"])
        self.assertEqual(options.get_json()["branding"]["display"], "wordmark")
        self.assertEqual(options.get_json()["branding"]["fit"], "cover")
        self.assertTrue((Path(self.tmp.name) / "logo.png").is_file())

    def test_webp_and_svg_upload(self) -> None:
        webp = self.client.post(
            "/api/settings/branding",
            data={"display": "icon", "fit": "cover", "logo": (io.BytesIO(_webp_bytes()), "mark.webp")},
            content_type="multipart/form-data",
        )
        self.assertEqual(webp.status_code, 200)
        self.assertTrue(webp.get_json()["branding"]["custom"])
        svg = self.client.post(
            "/api/settings/branding",
            data={"display": "wordmark", "fit": "contain", "logo": (io.BytesIO(_svg_bytes()), "mark.svg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(svg.status_code, 200)
        asset = self.client.get("/branding/logo")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("svg", asset.headers.get("Content-Type", ""))

    def test_reset_and_reject_jpeg(self) -> None:
        self.client.post(
            "/api/settings/branding",
            data={"display": "icon", "fit": "contain", "logo": (io.BytesIO(_png_bytes()), "a.png")},
            content_type="multipart/form-data",
        )
        reset = self.client.post("/api/settings/branding/reset")
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.get_json()["branding"]["custom"])
        self.assertEqual(self.client.get("/branding/logo").status_code, 404)
        bad = self.client.post(
            "/api/settings/branding",
            data={"display": "icon", "fit": "contain", "logo": (io.BytesIO(b"\xff\xd8\xff"), "x.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(bad.status_code, 400)

    def test_code_workspace_stays_compact_icon(self) -> None:
        self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "cover", "logo": (io.BytesIO(_png_bytes()), "wide.png")},
            content_type="multipart/form-data",
        )
        html = self.client.get("/work/climate").get_data(as_text=True)
        self.assertIn("climate-brand-mark", html)
        self.assertIn("/branding/logo", html)
        self.assertIn('data-brand-icon="', html)
        self.assertIn("AiriX · Code Assistant", html)
        self.assertIn("climate-assistant-header", html)
        assistant = html[html.find("climate-assistant-header"):html.find("climate-assistant-session")]
        self.assertNotIn("data-brand-display", assistant)
        self.assertNotIn("climate-brand-lockup", assistant)
        script = (ROOT / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        self.assertIn('getAttribute("data-brand-icon")', script)
        self.assertNotIn('querySelector(".climate-brand-mark")', script)
        chat = self.client.get("/work/chat").get_data(as_text=True)
        self.assertIn("ax-chat-mark", chat)
        self.assertIn("data-brand-icon", chat)
        self.assertIn("ax-chat-mark-well", chat)
        self.assertIn('data-brand-display="icon"', chat)
        mark_at = chat.find("ax-chat-mark-well")
        self.assertGreater(mark_at, 0)
        mark = chat[mark_at:mark_at + 500]
        self.assertIn("/branding/logo", mark)
        self.assertNotIn("climate-logo.png", mark)

    def test_full_app_branding_keeps_default_chat_icon(self) -> None:
        saved = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain"},
            content_type="multipart/form-data",
        )
        self.assertEqual(saved.status_code, 200)
        branding = saved.get_json()["branding"]
        self.assertFalse(branding["custom"])
        self.assertEqual(branding["avatar_display"], "icon")
        self.assertIn("climate-logo.png", branding["logo_url"])
        self.assertIn("climate-mark.png", branding["avatar_url"])
        self.assertNotIn("climate-logo.png", branding["avatar_url"])
        work = self.client.get("/work").get_data(as_text=True)
        self.assertIn("climate-logo.png", work)
        self.assertIn('data-brand-display="full"', work)
        chat = self.client.get("/work/chat").get_data(as_text=True)
        self.assertIn("climate-mark.png", chat)
        mark_at = chat.find("ax-chat-mark-well")
        self.assertGreater(mark_at, 0)
        mark = chat[mark_at:mark_at + 500]
        self.assertIn("climate-mark.png", mark)
        self.assertNotIn("climate-logo.png", mark)
        climate_js = (ROOT / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        chat_js = (ROOT / "static" / "js" / "climate_chat.js").read_text(encoding="utf-8")
        for script in (climate_js, chat_js):
            self.assertIn('img/providers/gemini.svg', script.replace("/static/", ""))
            self.assertIn("img/providers/codex.svg", script.replace("/static/", ""))
            self.assertIn("img/providers/claude-code.svg", script)
            self.assertIn("img/providers/cursor-agent.svg", script)


class BrandingCssContractTests(unittest.TestCase):
    def test_shared_logos_use_contain_by_default(self) -> None:
        shell = (ROOT / "static" / "css" / "climate-shell.css").read_text(encoding="utf-8")
        style = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        climate = (ROOT / "static" / "css" / "climate.css").read_text(encoding="utf-8")
        chat = (ROOT / "static" / "css" / "climate_chat.css").read_text(encoding="utf-8")
        self.assertIn("object-fit: contain", shell)
        self.assertIn('data-brand-fit="cover"', shell)
        self.assertIn("object-fit: contain", style)
        self.assertIn(".brand-logo-mark", style)
        self.assertIn('data-brand-fit="cover"', style)
        self.assertIn("object-fit: contain", climate)
        self.assertIn(".climate-brand-mark", climate)
        self.assertIn("padding: 3px", climate)
        self.assertIn("object-fit: contain", chat)
        self.assertIn(".ax-msg-avatar", chat)
        self.assertIn("padding: 4px", chat)
        self.assertIn(".brand-avatar", style)
        self.assertIn(".brand-avatar", shell)
        js = (ROOT / "static" / "js" / "settings_branding.js").read_text(encoding="utf-8")
        self.assertIn("createObjectURL", js)
        self.assertIn("FormData", js)
        self.assertIn("avatarAssetUrl", js)
        self.assertIn("headerAssetUrl", js)
        self.assertNotIn("readAsDataURL", js)
        self.assertIn("avatar_display", js)
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("import brand_icon, brand_logo with context", base)
        macros = (ROOT / "templates" / "macros.html").read_text(encoding="utf-8")
        self.assertIn("brand_avatar", macros)


if __name__ == "__main__":
    unittest.main()
