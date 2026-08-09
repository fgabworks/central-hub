"""Dynamic data-query intent detection for AiriX.

Detects structured/project data questions from meaning and structure
(entities + filters + value intent), not a fixed phrase catalog of locations
or beneficiary types.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Administrative / OU entity cues — abbreviations + full forms (generic, not place names).
_ADMIN_ENTITY = re.compile(
    r"""
    \b(
        org(?:anisation|anization)?\s*units?
        | \bou\b
        | brgy\.?
        | bgy\.?
        | barangay(?:s)?
        | mun(?:icipality|\.)?
        | municipalit(?:y|ies)
        | city
        | cities
        | prov(?:ince|\.)?
        | provinces?
        | reg(?:ion|\.)?
        | regions?
        | national
        | nationwide
        | country[- ]?wide
    )\b
    """,
    re.I | re.VERBOSE,
)

# DHIS2-style UID (11 chars, starts with letter, includes a digit) — structural, not a name list.
_UID_TOKEN = re.compile(r"\b([A-Za-z](?=[A-Za-z0-9]*\d)[A-Za-z0-9]{10})\b")

# Structured value / analytics intent (semantic categories, not one fixed sentence).
_DATA_VALUE_INTENT = re.compile(
    r"""
    \b(
        count(?:s|ing)?
        | how\s+many
        | total(?:s)?
        | number\s+of
        | numerator
        | denominator
        | percent(?:age)?s?
        | pct
        | rate(?:s)?
        | coverage
        | eligib(?:le|ility)
        | beneficiar(?:y|ies)
        | population(?:s)?
        | household(?:s)?
        | member(?:s)?
        | indicator(?:s)?
        | program\s+indicator(?:s)?
        | data\s*element(?:s)?
        | \bde\b
        | \bpi\b
        | analytics?
        | linelist
        | records?
        | results?
        | status(?:es)?
        | approved
        | rejected
        | pending
        | filtered
        | breakdown
        | by\s+(region|province|municipality|barangay|brgy|ou|org)
    )\b
    """,
    re.I | re.VERBOSE,
)

# Count/total framed as "X in/for/under/at <place-or-period>".
_STRUCTURAL_DATA = re.compile(
    r"""
    (
        (?:count|how\s+many|total|number\s+of|sum)\b.{0,80}\b(?:in|for|under|at|within|across)\b
        | (?:numerator|denominator|coverage|rate|percent(?:age)?)\b.{0,60}\b(?:for|of|in)\b
        | \bshow\b.{0,40}\b(?:approved|rejected|pending|eligible|records?|results?)\b
        | \blist\b.{0,40}\b(?:approved|rejected|pending|eligible|records?)\b
    )
    """,
    re.I | re.VERBOSE,
)

_PERIOD = re.compile(
    r"""
    \b(
        20\d{2}\s*[-/]?\s*Q[1-4]
        | Q[1-4]\s*[-/]?\s*20\d{2}
        | 20\d{2}Q[1-4]
        | FY\s*20\d{2}
        | (?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|
           jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|
           dec(?:ember)?)
          \s+20\d{2}
        | 20\d{2}
        | last\s+(?:quarter|month|year)
        | this\s+(?:quarter|month|year)
    )\b
    """,
    re.I | re.VERBOSE,
)

_ENVIRONMENT = re.compile(
    r"\b(stage|staging|live|production|prod)\b",
    re.I,
)

_STATUS = re.compile(
    r"\b(approved|rejected|pending|draft|final(?:ized)?|active|inactive|eligible)\b",
    re.I,
)

_INDICATOR_REF = re.compile(
    r"""
    \b(
        indicator(?:s)?
        | program\s+indicator(?:s)?
        | data\s*element(?:s)?
        | \bpi\b
        | \bde\b
        | uid
    )\b
    """,
    re.I | re.VERBOSE,
)

# Population/group is structural ("pregnant women", "eligible children") — capture as filter text,
# without hard-coding a beneficiary catalog for routing decisions.
_POPULATION_FRAME = re.compile(
    r"""
    (?:
        (?:count|how\s+many|total|number\s+of)\s+
        (?P<group>.{2,60}?)
        \s+(?:in|for|under|at|within|across)\b
    )
    |
    (?:
        \beligible\s+(?P<eligible_group>\w+(?:\s+\w+){0,3})
    )
    """,
    re.I | re.VERBOSE,
)

_LOCATION_FRAME = re.compile(
    r"""
    \b(?:in|for|under|at|within|across)\s+
    (?:
        (?:brgy\.?|bgy\.?|barangay|mun(?:icipality|\.)?|city|prov(?:ince|\.)?|
           province|reg(?:ion|\.)?|region|ou)\s+
    )?
    (?P<loc>[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,4}|[A-Za-z][A-Za-z0-9]{10})
    """,
    re.I | re.VERBOSE,
)

# Expand common admin abbreviations for search needles (not a place-name list).
_ABBREV_EXPAND = {
    "brgy": "barangay",
    "brgy.": "barangay",
    "bgy": "barangay",
    "bgy.": "barangay",
    "mun": "municipality",
    "mun.": "municipality",
    "prov": "province",
    "prov.": "province",
    "reg": "region",
    "reg.": "region",
}


@dataclass(frozen=True)
class DataQueryIntent:
    """Detected structured-data question intent + extracted filters."""

    is_data_query: bool
    confidence: float
    entity_types: tuple[str, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    search_terms: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _entity_types(text: str) -> list[str]:
    found: list[str] = []
    lower = text.lower()
    checks = [
        ("barangay", r"\b(brgy\.?|bgy\.?|barangay(?:s)?)\b"),
        ("municipality", r"\b(mun(?:icipality|\.)?|municipalit(?:y|ies)|city|cities)\b"),
        ("province", r"\b(prov(?:ince|\.)?|provinces?)\b"),
        ("region", r"\b(reg(?:ion|\.)?|regions?)\b"),
        ("national", r"\b(national|nationwide|country[- ]?wide)\b"),
        ("org_unit", r"\b(org(?:anisation|anization)?\s*units?|\bou\b)\b"),
    ]
    for label, pat in checks:
        if re.search(pat, lower, re.I):
            found.append(label)
    if _UID_TOKEN.search(text):
        found.append("uid")
    return list(dict.fromkeys(found))


def extract_data_filters(prompt: str) -> dict[str, Any]:
    """Pull OU/location, period, population, status, indicator, environment when present."""
    text = (prompt or "").strip()
    filters: dict[str, Any] = {}
    if not text:
        return filters

    periods = [m.group(0).strip() for m in _PERIOD.finditer(text)]
    if periods:
        filters["period"] = list(dict.fromkeys(periods))[:4]

    env = _ENVIRONMENT.search(text)
    if env:
        filters["environment"] = env.group(1).lower()

    statuses = [m.group(1).lower() for m in _STATUS.finditer(text)]
    if statuses:
        filters["status"] = list(dict.fromkeys(statuses))[:6]

    if _INDICATOR_REF.search(text):
        filters["indicator_ref"] = True

    uids = [m.group(1) for m in _UID_TOKEN.finditer(text)]
    # Prefer tokens that look like DHIS2 UIDs mentioned near uid/indicator words.
    if uids:
        filters["uids"] = list(dict.fromkeys(uids))[:6]
        filters["location"] = uids[0]

    pop = _POPULATION_FRAME.search(text)
    if pop:
        group = (pop.groupdict().get("group") or pop.groupdict().get("eligible_group") or "").strip()
        group = re.sub(r"\s+", " ", group).strip(" ,.;:")
        if group and len(group) >= 2:
            filters["population_group"] = group[:80]

    loc = _LOCATION_FRAME.search(text)
    if loc:
        location = (loc.group("loc") or "").strip()
        loc_l = location.lower()
        blocked = {"the", "a", "an", "this", "that", "our", "my", "your"}
        if (
            location
            and loc_l not in blocked
            and not loc_l.startswith("this ")
            and not loc_l.startswith("that ")
            and not loc_l.endswith(" module")
            and not loc_l.endswith(" file")
            and not loc_l.endswith(" function")
        ):
            filters["location"] = location[:80]

    # Admin label + following token (e.g. "Brgy. Baloy").
    admin_named = re.search(
        r"\b(?:brgy\.?|bgy\.?|barangay|mun(?:icipality|\.)?|city|prov(?:ince|\.)?|province|"
        r"reg(?:ion|\.)?|region)\s+([A-Za-z][\w.'\-]{1,40})",
        text,
        re.I,
    )
    if admin_named:
        filters["location"] = admin_named.group(1).strip()[:80]
        filters["admin_level_hint"] = admin_named.group(0).split()[0].lower().rstrip(".")

    return filters


def _search_terms(prompt: str, filters: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    loc = str(filters.get("location") or "").strip()
    if loc:
        terms.append(loc)
        # Expand abbreviated admin cue for tool queries.
        admin = str(filters.get("admin_level_hint") or "").lower()
        expanded = _ABBREV_EXPAND.get(admin)
        if expanded:
            terms.append(f"{expanded} {loc}")
    for uid in filters.get("uids") or []:
        terms.append(str(uid))
    for period in filters.get("period") or []:
        terms.append(str(period))
    group = str(filters.get("population_group") or "").strip()
    if group:
        terms.append(group)
    # Dedupe
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.lower()
        if key in seen or len(t) < 2:
            continue
        seen.add(key)
        out.append(t)
    return out[:8]


def detect_data_query_intent(prompt: str) -> DataQueryIntent:
    """
    True when the prompt asks for structured/project values with filters/entities.

    Uses overlapping structural signals rather than one hard-coded sentence template.
    """
    text = (prompt or "").strip()
    if not text:
        return DataQueryIntent(False, 0.0, reason="Empty prompt.")

    entities = _entity_types(text)
    filters = extract_data_filters(text)
    value_hit = bool(_DATA_VALUE_INTENT.search(text))
    structural = bool(_STRUCTURAL_DATA.search(text))
    has_period = bool(filters.get("period"))
    has_location = bool(filters.get("location") or filters.get("uids"))
    has_admin = bool(entities) or bool(_ADMIN_ENTITY.search(text))
    has_status = bool(filters.get("status"))
    has_indicator = bool(filters.get("indicator_ref"))

    score = 0.0
    signals: list[str] = []
    if value_hit:
        score += 0.35
        signals.append("data_value_intent")
    if structural:
        score += 0.35
        signals.append("structural_data_frame")
    if has_admin:
        score += 0.2
        signals.append("admin_or_ou_entity")
    if has_location:
        score += 0.15
        signals.append("location_or_uid_filter")
    if has_period:
        score += 0.15
        signals.append("period_filter")
    if has_status:
        score += 0.1
        signals.append("status_filter")
    if has_indicator:
        score += 0.15
        signals.append("indicator_ref")
    if filters.get("population_group"):
        score += 0.1
        signals.append("population_group_filter")

        # Core rule: value intent + (place/OU/period/status/indicator) ⇒ data query.
    # Ignore weak "location" captures that are really code/module references.
    weak_location = False
    loc_val = str(filters.get("location") or "").lower()
    if loc_val and (
        loc_val in {"this", "that"}
        or loc_val.startswith("this ")
        or loc_val.endswith(" module")
        or loc_val.endswith(" file")
        or loc_val.endswith(" function")
    ):
        weak_location = True
        has_location = bool(filters.get("uids"))

    is_data = False
    if value_hit and (has_admin or has_location or has_period or has_status or has_indicator):
        is_data = True
    elif structural and (has_admin or has_location or has_period or has_status):
        is_data = True
    elif has_indicator and (value_hit or has_period or has_location):
        is_data = True
    elif has_admin and value_hit:
        is_data = True
    # Bare "analytics" wording without OU/period/status is not enough.
    if is_data and not (
        has_admin or has_period or has_status or has_indicator or (has_location and not weak_location)
    ):
        is_data = False

    confidence = min(1.0, round(score, 3))
    if not is_data:
        return DataQueryIntent(
            False,
            confidence,
            entity_types=tuple(entities),
            filters=filters,
            search_terms=tuple(_search_terms(text, filters)),
            signals=tuple(signals),
            reason="No structured data-query pattern detected.",
        )

    signals.append("data_query")
    return DataQueryIntent(
        True,
        max(confidence, 0.55),
        entity_types=tuple(entities),
        filters=filters,
        search_terms=tuple(_search_terms(text, filters)),
        signals=tuple(dict.fromkeys(signals)),
        reason="Structured data/DHIS2 lookup intent with entity and/or filter cues.",
    )
