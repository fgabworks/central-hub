"""Decode NPMO standard-report design bindings (no HTML value scraping)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from hub.hcsc_indicators.cache import DESIGN_CACHE
from hub.settings import ROOT_DIR

NPMO_UID = "qTQD08sNuzZ"
_DEFAULT_DB = ROOT_DIR / "data" / "dhis2_reports.db"

_MAP_RE = re.compile(
    r"summarySingleOuDxToElementId\s*=\s*\{(.*?)\};",
    re.DOTALL,
)
_PAIR_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9]{10})\s*:\s*[\"']([^\"']+)[\"']",
)


def decode_npmo_design(
    *,
    db_path: Path | None = None,
    report_uid: str = NPMO_UID,
    force: bool = False,
) -> dict[str, Any]:
    """Extract dx→element bindings from synced designContent."""
    path = Path(db_path) if db_path else _DEFAULT_DB
    cache_key = f"design:{path.resolve()}:{report_uid}"
    if not force:
        cached = DESIGN_CACHE.get(cache_key)
        if cached is not None:
            return cached

    if not path.is_file():
        payload = {
            "ok": False,
            "report_uid": report_uid,
            "error": f"Reports database not found: {path}",
            "dx_to_element": {},
            "element_to_dx": {},
            "unresolved_elements": [],
        }
        DESIGN_CACHE.set(cache_key, payload)
        return payload

    con = sqlite3.connect(str(path))
    try:
        row = con.execute(
            "SELECT name, environment, design_content FROM synced_standard_reports WHERE uid = ? "
            "ORDER BY CASE environment WHEN 'stage' THEN 0 WHEN 'live' THEN 1 ELSE 2 END LIMIT 1",
            (report_uid,),
        ).fetchone()
    finally:
        con.close()

    if not row or not row[2]:
        payload = {
            "ok": False,
            "report_uid": report_uid,
            "error": "Synced NPMO designContent not available — sync Stage reports first.",
            "dx_to_element": {},
            "element_to_dx": {},
            "unresolved_elements": ["Number_Convergent_Bgy", "Pct_Convergence_Mun"],
        }
        DESIGN_CACHE.set(cache_key, payload)
        return payload

    name, environment, html = row[0], row[1], row[2]
    dx_to_element: dict[str, str] = {}
    match = _MAP_RE.search(html)
    if match:
        for uid, element in _PAIR_RE.findall(match.group(1)):
            dx_to_element[uid] = element
    element_to_dx = {v: k for k, v in dx_to_element.items()}

    # Spans that are cleared/set in JS but have no dx map entry.
    unresolved_elements: list[str] = []
    for element_id in ("Number_Convergent_Bgy", "Pct_Convergence_Mun"):
        if element_id not in element_to_dx and element_id in html:
            unresolved_elements.append(element_id)

    payload = {
        "ok": True,
        "report_uid": report_uid,
        "report_name": name,
        "environment": environment,
        "dx_to_element": dx_to_element,
        "element_to_dx": element_to_dx,
        "unresolved_elements": unresolved_elements,
        "notes": (
            "Decoded from synced designContent only. "
            "Number_Convergent_Bgy / Pct_Convergence_Mun are client-computed in report JS."
        ),
    }
    DESIGN_CACHE.set(cache_key, payload)
    return payload
