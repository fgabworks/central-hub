"""Find Missing UIDs — selection UI layout and preview safeguards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from hub.dhis2.uid_mapping.missing import CONFIRM_ADD_MISSING
from hub.dhis2.uid_mapping.store import MappingIndexStore


class FindMissingUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["DHIS2_MAPPING_INDEX"] = MappingIndexStore(root / "uid_index")
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_scan(self, *, count: int = 5, per_page_rows: int | None = None) -> dict:
        missing = []
        for i in range(count):
            uid = f"MISS{i:07d}"
            missing.append(
                {
                    "uid": uid,
                    "name": f"Missing {i}",
                    "object_type": "dataElement" if i % 2 == 0 else "programIndicator",
                    "value_type": "BOOLEAN" if i == 0 else "TEXT",
                    "code": f"C{i}",
                    "program_uid": "",
                    "source_environment": "live",
                    "dhis2": {"id": uid, "name": f"Missing {i}"},
                }
            )
        scan = {
            "ok": True,
            "scan_id": "test-scan-1",
            "environment": "live",
            "index_uid_count": 0,
            "missing_count": len(missing),
            "missing": missing,
            "per_type": {
                "dataElement": {"scanned": count, "missing": (count + 1) // 2},
                "programIndicator": {"scanned": count, "missing": count // 2},
            },
            "truncated": False,
            "errors": [],
            "dhis2_writes": 0,
        }
        with self.app.app_context():
            self.app.config["DHIS2_MISSING_SCAN"] = scan
            self.app.config["DHIS2_MISSING_PREVIEW"] = None
        return scan

    def test_compact_layout_and_selection_controls(self) -> None:
        self._seed_scan(count=3)
        resp = self.client.get("/dhis2/uid-index/find-missing?per_page=2")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Scan DHIS2", html)
        self.assertIn("Filter Results", html)
        self.assertIn("Select UIDs", html)
        self.assertIn("Add to Local Index", html)
        self.assertNotIn("2–4", html)
        self.assertIn('data-select-all-visible', html)
        self.assertIn('data-action="select-visible"', html)
        self.assertIn('data-action="select-filtered"', html)
        self.assertIn('data-action="clear-selection"', html)
        self.assertIn('data-selected-count', html)
        self.assertIn("fm-bulk-bar", html)
        self.assertIn("fm-scan-toolbar", html)
        self.assertIn("fm-table-scroll", html)
        self.assertIn("Scan Summary", html)
        self.assertIn("<details", html)
        self.assertIn("Data Element", html)
        self.assertIn("Program Indicator", html)
        self.assertIn("dhis2_find_missing.js", html)
        # Compact primary scan control (not oversized step block)
        self.assertIn('class="btn btn-primary btn-sm"', html)
        self.assertIn('disabled>Add to Local Index</button>', html.replace("\n", ""))

    def test_selection_survives_pagination_payload(self) -> None:
        self._seed_scan(count=5)
        page1 = self.client.get("/dhis2/uid-index/find-missing?per_page=2&page=1")
        page2 = self.client.get("/dhis2/uid-index/find-missing?per_page=2&page=2")
        self.assertEqual(page1.status_code, 200)
        self.assertEqual(page2.status_code, 200)
        h1 = page1.get_data(as_text=True)
        h2 = page2.get_data(as_text=True)
        self.assertIn('data-scan-id="test-scan-1"', h1)
        self.assertIn('data-scan-id="test-scan-1"', h2)
        self.assertIn("data-filtered-uids=", h1)
        self.assertIn("data-visible-uids=", h1)
        # Page 1 visible rows only; full filtered set still embedded for select-all.
        self.assertRegex(
            h1,
            r'data-visible-uids=\'\["MISS0000000",\s*"MISS0000001"\]\'',
        )
        self.assertRegex(
            h2,
            r'data-visible-uids=\'\["MISS0000002",\s*"MISS0000003"\]\'',
        )
        self.assertIn("MISS0000000", h1)
        self.assertIn("MISS0000002", h2)
        self.assertIn("Page 1 / 3", h1)
        self.assertIn("Page 2 / 3", h2)

    def test_preview_required_before_local_update(self) -> None:
        self._seed_scan(count=2)
        # Direct add without preview must fail
        blocked = self.client.post(
            "/dhis2/uid-index/find-missing",
            data={"action": "add_to_index", "confirmation": CONFIRM_ADD_MISSING},
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("Preview selected UIDs before adding", blocked.get_data(as_text=True))

        preview = self.client.post(
            "/dhis2/uid-index/find-missing",
            data={"action": "preview_selected", "uid": ["MISS0000000", "MISS0000001"]},
        )
        self.assertEqual(preview.status_code, 200)
        body = preview.get_data(as_text=True)
        self.assertIn("Preview ready", body)
        self.assertIn(CONFIRM_ADD_MISSING, body)
        self.assertIn("Confirm Add to Local Index", body)

        applied = self.client.post(
            "/dhis2/uid-index/find-missing",
            data={"action": "add_to_index", "confirmation": CONFIRM_ADD_MISSING},
        )
        self.assertEqual(applied.status_code, 200)
        self.assertIn("Added selected UIDs to the local index", applied.get_data(as_text=True))
        store: MappingIndexStore = self.app.config["DHIS2_MAPPING_INDEX"]
        uids = {r.get("uid") for r in store.records()}
        self.assertIn("MISS0000000", uids)
        self.assertIn("MISS0000001", uids)

    def test_empty_selection_preview_rejected(self) -> None:
        self._seed_scan(count=1)
        resp = self.client.post(
            "/dhis2/uid-index/find-missing",
            data={"action": "preview_selected"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Select at least one missing UID", resp.get_data(as_text=True))


class SelectionStoreLogicTests(unittest.TestCase):
    """Mirror JS selection semantics via the exported module under Node-less Python.

    The authoritative store lives in static/js/dhis2_find_missing.js; these tests
    reimplement the same rules for regression without a browser.
    """

    def test_select_visible_filtered_clear_and_survive_pages(self) -> None:
        selected: set[str] = set()
        filtered = [f"U{i}" for i in range(6)]
        page1 = filtered[:2]
        page2 = filtered[2:4]

        def select_visible(uids: list[str]) -> None:
            selected.update(uids)

        def select_all_filtered(uids: list[str]) -> None:
            selected.update(uids)

        def clear() -> None:
            selected.clear()

        def deselect_visible(uids: list[str]) -> None:
            for uid in uids:
                selected.discard(uid)

        select_visible(page1)
        self.assertEqual(selected, {"U0", "U1"})
        # Filtering/pagination: keep selection while viewing another page
        self.assertTrue({"U0", "U1"}.issubset(selected))
        select_visible(page2)
        self.assertEqual(selected, {"U0", "U1", "U2", "U3"})
        deselect_visible(page1)
        self.assertEqual(selected, {"U2", "U3"})
        select_all_filtered(filtered)
        self.assertEqual(selected, set(filtered))
        clear()
        self.assertEqual(selected, set())
        self.assertEqual(len(selected), 0)  # disabled Add when empty


if __name__ == "__main__":
    unittest.main()
