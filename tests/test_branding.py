"""CLIMATE Settings → Branding: separate app logo and AiriX avatar assets."""

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
        self.assertFalse(state["custom_logo"])
        self.assertFalse(state["custom_avatar"])
        self.assertEqual(state["display"], "wordmark")
        self.assertEqual(state["avatar_display"], "icon")
        self.assertEqual(state["fit"], "contain")
        self.assertEqual(state["default_icon"], "img/climate-mark.png")
        self.assertEqual(state["default_full"], "img/climate-logo.png")

    def test_save_png_to_disk_not_base64_settings(self) -> None:
        png = _png_bytes()
        state = self.svc.save(display="full", fit="contain", payload=png, filename="wide.png")
        self.assertTrue(state["custom"])
        self.assertTrue(state["custom_logo"])
        self.assertFalse(state["custom_avatar"])
        logo = Path(self.tmp.name) / "logo.png"
        self.assertTrue(logo.is_file())
        self.assertEqual(logo.read_bytes(), png)
        settings = json.loads((Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        dumped = json.dumps(settings)
        self.assertNotIn("base64", dumped.lower())
        self.assertNotIn("data:image", dumped)
        self.assertEqual(settings["filename"], "logo.png")
        self.assertEqual(settings["display"], "full")
        self.assertEqual(settings.get("avatar_filename") or "", "")

    def test_avatar_is_independent_of_logo(self) -> None:
        logo = _png_bytes((16, 8), (20, 80, 200, 255))
        avatar = _png_bytes((8, 8), (200, 40, 40, 255))
        self.svc.save(display="full", payload=logo, filename="wide.png")
        state = self.svc.save(avatar_payload=avatar, avatar_filename="airix.png")
        self.assertTrue(state["custom_logo"])
        self.assertTrue(state["custom_avatar"])
        self.assertEqual((Path(self.tmp.name) / "logo.png").read_bytes(), logo)
        self.assertEqual((Path(self.tmp.name) / "avatar.png").read_bytes(), avatar)
        again = BrandingService(Path(self.tmp.name))
        restored = again.state()
        self.assertTrue(restored["custom_logo"])
        self.assertTrue(restored["custom_avatar"])
        path, ctype = again.avatar_file()
        self.assertEqual(ctype, "image/png")
        self.assertEqual(path.read_bytes(), avatar)

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
        with self.assertRaises(BrandingError) as avatar_jpeg:
            self.svc.save(avatar_payload=b"\xff\xd8\xff", avatar_filename="x.jpg")
        self.assertEqual(avatar_jpeg.exception.code, "type_unsupported")

    def test_reset_removes_custom_files(self) -> None:
        self.svc.save(payload=_png_bytes(), filename="mark.png", avatar_payload=_png_bytes((8, 8)), avatar_filename="a.png")
        self.assertTrue((Path(self.tmp.name) / "logo.png").is_file())
        self.assertTrue((Path(self.tmp.name) / "avatar.png").is_file())
        state = self.svc.reset()
        self.assertFalse(state["custom"])
        self.assertFalse(state["custom_avatar"])
        self.assertFalse((Path(self.tmp.name) / "logo.png").is_file())
        self.assertFalse((Path(self.tmp.name) / "avatar.png").is_file())
        self.assertEqual(state["display"], "wordmark")
        self.assertEqual(state["avatar_display"], "icon")
        self.assertEqual(state["fit"], "contain")

    def test_png_dimensions_and_remove(self) -> None:
        png = _png_bytes((16, 8))
        state = self.svc.save(payload=png, filename="wide.png")
        self.assertEqual(state["logo_width"], 16)
        self.assertEqual(state["logo_height"], 8)
        avatar = _png_bytes((8, 8), (200, 40, 40, 255))
        state = self.svc.save(avatar_payload=avatar, avatar_filename="airix.png")
        self.assertEqual(state["avatar_width"], 8)
        self.assertEqual(state["avatar_height"], 8)
        state = self.svc.save(remove_logo=True)
        self.assertFalse(state["custom_logo"])
        self.assertTrue(state["custom_avatar"])
        self.assertFalse((Path(self.tmp.name) / "logo.png").is_file())
        self.assertTrue((Path(self.tmp.name) / "avatar.png").is_file())
        state = self.svc.save(remove_avatar=True)
        self.assertFalse(state["custom_avatar"])
        self.assertFalse((Path(self.tmp.name) / "avatar.png").is_file())

    def test_cover_is_coerced_to_contain(self) -> None:
        state = self.svc.save(display="full", fit="cover", payload=_png_bytes(), filename="a.png")
        self.assertEqual(state["fit"], "contain")

    def test_persists_across_new_service_instance(self) -> None:
        self.svc.save(display="icon", fit="cover", payload=_svg_bytes(), filename="logo.svg")
        again = BrandingService(Path(self.tmp.name))
        state = again.state()
        self.assertTrue(state["custom"])
        self.assertEqual(state["display"], "icon")
        self.assertEqual(state["fit"], "contain")
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
        self.assertIn("Header", html)
        self.assertIn("AiriX sizes", html)
        self.assertIn("CLIMATE Chat", html)
        self.assertIn("Code Assistant", html)
        self.assertIn("App Branding", html)
        self.assertIn("AiriX Chat Avatar", html)
        self.assertIn("Wordmark", html)
        self.assertIn("Full logo", html)
        self.assertNotIn("Icon only", html)
        self.assertNotIn(">Cover<", html)
        self.assertIn("AiriX avatars always use the dedicated icon asset.", html)
        self.assertIn("branding-avatar-file", html)
        self.assertIn("Replace", html)
        self.assertIn("Remove", html)
        self.assertIn("Reset to defaults", html)
        self.assertIn("Save changes", html)
        self.assertIn("PNG, SVG, or WEBP", html)
        self.assertIn("settings_branding.js", html)
        self.assertIn("branding-preview-header", html)
        self.assertIn("branding-preview-avatar", html)
        self.assertIn("is-size-32", html)
        self.assertIn("is-size-36", html)
        self.assertIn("is-size-40", html)
        self.assertIn(">AiriX<", html)
        overview = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("/settings/branding", overview)
        self.assertIn("Branding", overview)

    def test_upload_logo_does_not_become_avatar(self) -> None:
        png = _png_bytes((16, 8))
        saved = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(png), "climate.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(saved.status_code, 200)
        body = saved.get_json()
        branding = body["branding"]
        self.assertTrue(body["ok"])
        self.assertTrue(branding["custom"])
        self.assertTrue(branding["custom_logo"])
        self.assertFalse(branding["custom_avatar"])
        self.assertEqual(branding["avatar_display"], "icon")
        self.assertIn("/branding/logo", branding["logo_url"])
        self.assertIn("/branding/logo", branding["full_url"])
        self.assertIn("climate-mark.png", branding["avatar_url"])
        self.assertNotIn("/branding/logo", branding["avatar_url"])
        self.assertIn("climate-mark.png", branding["icon_url"])
        served = self.client.get(branding["logo_url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, png)
        self.assertEqual(self.client.get("/branding/avatar").status_code, 404)
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
        next_branding = options.get_json()["branding"]
        self.assertTrue(next_branding["custom"])
        self.assertEqual(next_branding["display"], "wordmark")
        self.assertEqual(next_branding["fit"], "contain")
        self.assertTrue((Path(self.tmp.name) / "logo.png").is_file())
        self.assertEqual(next_branding["logo_width"], 16)
        self.assertEqual(next_branding["logo_height"], 8)
        removed = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "remove_logo": "1"},
            content_type="multipart/form-data",
        )
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(removed.get_json()["branding"]["custom_logo"])
        self.assertFalse((Path(self.tmp.name) / "logo.png").is_file())

    def test_upload_avatar_only_and_both(self) -> None:
        avatar_png = _png_bytes((8, 8), (180, 40, 40, 255))
        avatar = self.client.post(
            "/api/settings/branding",
            data={"display": "wordmark", "fit": "contain", "avatar": (io.BytesIO(avatar_png), "airix.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(avatar.status_code, 200)
        branding = avatar.get_json()["branding"]
        self.assertFalse(branding["custom_logo"])
        self.assertTrue(branding["custom_avatar"])
        self.assertIn("/branding/avatar", branding["avatar_url"])
        self.assertIn("climate-mark.png", branding["icon_url"])
        self.assertNotIn("climate-logo.png", branding["avatar_url"])
        served = self.client.get("/branding/avatar")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.data, avatar_png)

        logo_png = _png_bytes((20, 8), (20, 80, 200, 255))
        both = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(logo_png), "wide.png")},
            content_type="multipart/form-data",
        )
        branding = both.get_json()["branding"]
        self.assertTrue(branding["custom_logo"])
        self.assertTrue(branding["custom_avatar"])
        self.assertIn("/branding/logo", branding["logo_url"])
        self.assertIn("/branding/avatar", branding["avatar_url"])
        self.assertNotEqual(branding["logo_url"].split("?")[0], branding["avatar_url"].split("?")[0])
        self.assertEqual(self.client.get(branding["logo_url"]).data, logo_png)
        self.assertEqual(self.client.get(branding["avatar_url"]).data, avatar_png)

    def test_webp_and_svg_upload(self) -> None:
        webp = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(_webp_bytes()), "mark.webp")},
            content_type="multipart/form-data",
        )
        self.assertEqual(webp.status_code, 200)
        self.assertTrue(webp.get_json()["branding"]["custom_logo"])
        svg = self.client.post(
            "/api/settings/branding",
            data={"display": "wordmark", "fit": "contain", "avatar": (io.BytesIO(_svg_bytes()), "mark.svg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(svg.status_code, 200)
        asset = self.client.get("/branding/avatar")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("svg", asset.headers.get("Content-Type", ""))

    def test_reset_and_reject_jpeg(self) -> None:
        self.client.post(
            "/api/settings/branding",
            data={
                "display": "full",
                "fit": "contain",
                "logo": (io.BytesIO(_png_bytes()), "a.png"),
                "avatar": (io.BytesIO(_png_bytes((8, 8))), "b.png"),
            },
            content_type="multipart/form-data",
        )
        reset = self.client.post("/api/settings/branding/reset")
        self.assertEqual(reset.status_code, 200)
        branding = reset.get_json()["branding"]
        self.assertFalse(branding["custom"])
        self.assertFalse(branding["custom_avatar"])
        self.assertEqual(self.client.get("/branding/logo").status_code, 404)
        self.assertEqual(self.client.get("/branding/avatar").status_code, 404)
        bad = self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(b"\xff\xd8\xff"), "x.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(bad.status_code, 400)

    def test_code_workspace_and_chat_use_avatar_not_logo(self) -> None:
        logo = _png_bytes((16, 8), (20, 80, 200, 255))
        avatar = _png_bytes((8, 8), (200, 40, 40, 255))
        self.client.post(
            "/api/settings/branding",
            data={"display": "full", "fit": "contain", "logo": (io.BytesIO(logo), "wide.png")},
            content_type="multipart/form-data",
        )
        html = self.client.get("/work/climate").get_data(as_text=True)
        self.assertIn("climate-brand-mark", html)
        self.assertIn("/branding/logo", html)
        icon_at = html.find('data-brand-icon="')
        self.assertGreater(icon_at, 0)
        icon_attr = html[icon_at:icon_at + 180]
        self.assertIn("climate-mark.png", icon_attr)
        self.assertNotIn("/branding/logo", icon_attr)
        self.assertIn("AiriX · Code Assistant", html)
        assistant = html[html.find("climate-assistant-header"):html.find("climate-assistant-session")]
        self.assertIn("climate-mark.png", assistant)
        self.assertNotIn("/branding/logo", assistant)
        self.assertNotIn("data-brand-display", assistant)
        self.assertNotIn("climate-brand-lockup", assistant)
        script = (ROOT / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        self.assertIn('getAttribute("data-brand-icon")', script)
        self.assertNotIn('querySelector(".climate-brand-mark")', script)
        chat = self.client.get("/work/chat").get_data(as_text=True)
        self.assertIn("ax-chat-mark", chat)
        mark_at = chat.find("ax-chat-mark-well")
        self.assertGreater(mark_at, 0)
        mark = chat[mark_at:mark_at + 500]
        self.assertIn("climate-mark.png", mark)
        self.assertNotIn("/branding/logo", mark)
        self.assertNotIn("climate-logo.png", mark)

        self.client.post(
            "/api/settings/branding",
            data={"avatar": (io.BytesIO(avatar), "airix.png")},
            content_type="multipart/form-data",
        )
        climate = self.client.get("/work/climate").get_data(as_text=True)
        self.assertIn("/branding/avatar", climate)
        assistant = climate[climate.find("climate-assistant-header"):climate.find("climate-assistant-session")]
        self.assertIn("/branding/avatar", assistant)
        self.assertNotIn("/branding/logo", assistant)
        chat = self.client.get("/work/chat").get_data(as_text=True)
        mark = chat[chat.find("ax-chat-mark-well"):chat.find("ax-chat-mark-well") + 500]
        self.assertIn("/branding/avatar", mark)
        self.assertNotIn("climate-logo.png", mark)

    def test_replace_and_restart_preserve_independent_files(self) -> None:
        first_logo = _png_bytes((16, 8), (20, 80, 200, 255))
        first_avatar = _png_bytes((8, 8), (200, 40, 40, 255))
        self.client.post(
            "/api/settings/branding",
            data={
                "display": "full",
                "logo": (io.BytesIO(first_logo), "wide.png"),
                "avatar": (io.BytesIO(first_avatar), "airix.png"),
            },
            content_type="multipart/form-data",
        )
        next_logo = _png_bytes((20, 10), (10, 160, 80, 255))
        next_avatar = _png_bytes((10, 10), (40, 40, 200, 255))
        replaced = self.client.post(
            "/api/settings/branding",
            data={
                "display": "full",
                "logo": (io.BytesIO(next_logo), "wide-2.png"),
                "avatar": (io.BytesIO(next_avatar), "airix-2.png"),
            },
            content_type="multipart/form-data",
        )
        branding = replaced.get_json()["branding"]
        self.assertEqual(self.client.get(branding["logo_url"]).data, next_logo)
        self.assertEqual(self.client.get(branding["avatar_url"]).data, next_avatar)
        self.assertEqual(branding["original_name"], "wide-2.png")
        self.assertEqual(branding["avatar_original_name"], "airix-2.png")

        restarted = create_app()
        restarted.config["TESTING"] = True
        restarted.config["BRANDING"] = BrandingService(Path(self.tmp.name))
        other = restarted.test_client()
        restored = other.get("/api/settings/branding").get_json()["branding"]
        self.assertTrue(restored["custom_logo"])
        self.assertTrue(restored["custom_avatar"])
        self.assertEqual(other.get(restored["logo_url"]).data, next_logo)
        self.assertEqual(other.get(restored["avatar_url"]).data, next_avatar)
        climate = other.get("/work/climate").get_data(as_text=True)
        self.assertIn("/branding/logo", climate)
        self.assertIn("/branding/avatar", climate)
        chat = other.get("/work/chat").get_data(as_text=True)
        self.assertIn("/branding/avatar", chat)
        self.assertNotIn("/branding/logo", chat[chat.find("ax-chat-mark-well"):chat.find("ax-chat-mark-well") + 500])

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
        self.assertNotIn('data-brand-fit="cover"', shell)
        self.assertIn("object-fit: contain", style)
        self.assertIn(".brand-logo-mark", style)
        self.assertNotIn('data-brand-fit="cover"', style)
        self.assertIn("object-fit: contain", climate)
        self.assertIn(".climate-brand-mark", climate)
        self.assertIn("padding: 3px", climate)
        self.assertIn("object-fit: contain", chat)
        self.assertIn(".ax-msg-avatar", chat)
        self.assertIn("padding: 4px", chat)
        self.assertIn(".brand-avatar", style)
        self.assertIn(".brand-avatar", shell)
        self.assertIn(".is-size-32", style)
        self.assertIn(".is-size-36", style)
        self.assertIn(".is-size-40", style)
        self.assertIn("min-height: 48px", style)
        self.assertIn(".branding-preview-chat-example", style)
        self.assertIn(".branding-preview-assistant", style)
        self.assertIn(".branding-asset-meta", style)
        self.assertIn("@media (max-width: 900px)", style)
        js = (ROOT / "static" / "js" / "settings_branding.js").read_text(encoding="utf-8")
        self.assertIn("Replace", js)
        self.assertIn("remove_logo", js)
        self.assertIn("createObjectURL", js)
        self.assertIn("FormData", js)
        self.assertIn("avatarAssetUrl", js)
        self.assertIn("headerAssetUrl", js)
        self.assertIn("branding-avatar-file", js)
        self.assertNotIn("readAsDataURL", js)
        self.assertNotIn("avatar_display", js)
        template = (ROOT / "templates" / "settings_branding.html").read_text(encoding="utf-8")
        self.assertNotIn("Icon only", template)
        self.assertNotIn('name="fit"', template)
        self.assertIn("AiriX avatars always use the dedicated icon asset.", template)
        self.assertIn("Save changes", template)
        self.assertIn("Reset to defaults", template)
        self.assertIn("branding-error", template)
        self.assertIn("branding-ok", template)
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("import brand_icon, brand_logo with context", base)
        macros = (ROOT / "templates" / "macros.html").read_text(encoding="utf-8")
        self.assertIn("brand_avatar", macros)
        self.assertIn("branding.full_url", macros)
        self.assertNotIn("branding.logo_url if branding is defined and branding.logo_url else icon_src", macros)


if __name__ == "__main__":
    unittest.main()
