"""Extract UIDs referenced by program indicator expression/filter text."""

from __future__ import annotations

import re
from typing import Any

_UID = r"[A-Za-z][A-Za-z0-9]{10}"
_STAGE_DE = re.compile(rf"#\{{({_UID})\.({_UID})\}}")
_A_ATTR = re.compile(rf"A\{{({_UID})\}}")
_C_CONST = re.compile(rf"C\{{({_UID})\}}")
_HASH_DE = re.compile(rf"#\{{({_UID})\}}")
_ANY_UID = re.compile(rf"\b({_UID})\b")


def extract_pi_references(expression: str, filter_text: str) -> dict[str, Any]:
    text = f"{expression or ''}\n{filter_text or ''}"
    stages: list[str] = []
    data_elements: list[str] = []
    attributes: list[str] = []
    constants: list[str] = []

    for stage_uid, de_uid in _STAGE_DE.findall(text):
        if stage_uid not in stages:
            stages.append(stage_uid)
        if de_uid not in data_elements:
            data_elements.append(de_uid)

    for uid in _A_ATTR.findall(text):
        if uid not in attributes:
            attributes.append(uid)
    for uid in _C_CONST.findall(text):
        if uid not in constants:
            constants.append(uid)
    for uid in _HASH_DE.findall(text):
        # Bare #{deUid} (no stage) — treat as DE
        if uid not in data_elements and uid not in stages:
            data_elements.append(uid)

    known = set(stages) | set(data_elements) | set(attributes) | set(constants)
    unresolved = [u for u in _ANY_UID.findall(text) if u not in known]
    # de-dupe unresolved preserving order
    seen: set[str] = set()
    unresolved_unique: list[str] = []
    for uid in unresolved:
        if uid in seen:
            continue
        seen.add(uid)
        unresolved_unique.append(uid)

    return {
        "program_stages": stages,
        "data_elements": data_elements,
        "attributes": attributes,
        "constants": constants,
        "unresolved": unresolved_unique,
    }
