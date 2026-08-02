"""Focused regression tests for the Data Explorer data-first workspace."""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import unittest

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))


class DataExplorerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = create_app()
        app.config.update(TESTING=True)
        cls.client = app.test_client()
        cls.js = (ROOT / "static/js/data_explorer.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/data_explorer.css").read_text(encoding="utf-8")

    def test_route_renders_redesigned_workspace_without_duplicate_controls(self) -> None:
        response = self.client.get("/data-explorer")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        parser = _IdCollector()
        parser.feed(html)
        duplicates = [name for name, count in Counter(parser.ids).items() if count > 1]
        self.assertEqual(duplicates, [])
        for required_id in (
            "dex-env",
            "dex-search",
            "dex-refresh",
            "dex-export-shortcut",
            "dex-table-filter",
            "dex-tree",
            "dex-grid",
            "dex-filter-form",
            "dex-filter-column",
            "dex-filter-operator",
            "dex-filter-value",
            "dex-filter-chips",
            "dex-filter-clear",
            "dex-details",
            "dex-selected-row-list",
        ):
            self.assertIn(required_id, parser.ids)

    def test_primary_tabs_and_read_only_status_are_preserved(self) -> None:
        html = self.client.get("/data-explorer").get_data(as_text=True)
        for label in (
            "Browse Data",
            "Schema",
            "Relationships",
            "Lineage",
            "Export",
            "Export Jobs",
            "History",
        ):
            self.assertIn(f">{label}<", html)
        self.assertIn("Read-only", html)

    def test_selection_filter_refresh_pagination_export_and_details_are_wired(self) -> None:
        for behavior in (
            'addEventListener("input", filterTree)',
            'addEventListener("click", function () { loadGrid(1); })',
            'addEventListener("click", runExport)',
            'classList.toggle("is-selected"',
            'renderSelectedRow(data.columns || [], row)',
            'setGridState("Loading rows…")',
            '"No rows match the current filter."',
            '"Page " + data.page + " of " + pages',
            'state.filters.push({ column: column.name, op: operator, value: value })',
            'url.searchParams.set("filters", JSON.stringify(state.filters))',
            'url.searchParams.set("page", String(state.page))',
            'state.sortColumn = null',
            'data-filter-index',
        ):
            self.assertIn(behavior, self.js)

    def test_data_grid_and_responsive_drawer_styles_are_present(self) -> None:
        for style in (
            "grid-template-columns: 280px minmax(0, 1fr) 320px",
            "position: sticky",
            "overflow: auto",
            ".dex-table tbody tr.is-selected",
            "@media (max-width: 1280px)",
            "@media (max-width: 820px)",
            ".dex-layout.details-collapsed",
            ".dex-filter-chip",
            '.dex-table th[data-sort="asc"]',
            '.dex-table th[data-sort="desc"]',
        ):
            self.assertIn(style, self.css)
        self.assertNotIn("background: #fff", self.css)


if __name__ == "__main__":
    unittest.main()
