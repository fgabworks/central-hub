"""Domain-term extraction for CLIMATE repository targeting.

Ranks exact concepts, acronyms, and aliases above generic leftover words.
Does not encode connected-repository business rules; it only builds search hints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hub.climate.retrieval_policy import (
    extract_reference_phrases,
    is_simple_reference_query,
    ranking_adjustment,
)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]{2,}")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
_SNAKE_ACRONYM = re.compile(r"\b(?:CH_)?[A-Z]{2,8}(?:_STATUS|_SCORE|_RULE)?\b")

GENERIC_TERMS = {
    "about", "after", "anything", "cite", "code", "complete", "completely",
    "data", "date", "does", "edit", "eastern", "exact", "explain", "file", "files",
    "from", "fully", "function", "functions", "give", "how", "implementation",
    "into", "list", "logic", "member", "members", "name", "names", "northern",
    "nothing", "please", "province", "provinces", "region", "regions",
    "score", "source", "sources", "southern", "status", "tell", "test", "that",
    "the", "this", "type", "value", "visit", "western", "what", "whats", "when",
    "where", "which", "who", "why", "with", "year", "child", "children",
    "household", "households",
}

QUESTION_STOP = {
    "what", "whats", "who", "how", "why", "when", "where", "which", "is",
    "are", "the", "a", "an", "of", "for", "me", "please", "define", "explain",
    "tell", "about", "give",
}


@dataclass(frozen=True)
class DomainQuery:
    phrases: tuple[str, ...] = ()
    acronyms: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()

    def search_terms(self) -> list[str]:
        """Progressive search needles: phrase → acronym/alias → distinctive tokens."""
        terms: list[str] = []
        terms.extend(self.phrases)
        terms.extend(self.acronyms)
        terms.extend(self.aliases)
        terms.extend(self.strong)
        return list(dict.fromkeys(t for t in terms if t and t.lower() not in GENERIC_TERMS))

    def match_needles(self) -> set[str]:
        return {
            t.lower()
            for t in (*self.phrases, *self.acronyms, *self.aliases, *self.strong)
            if t
        }


def extract_domain_query(prompt: str) -> DomainQuery:
    text = str(prompt or "").strip()
    if not text:
        return DomainQuery()
    tokens = [t for t in _TOKEN.findall(text) if t]
    lower_tokens = [t.lower() for t in tokens]
    significant = [t for t in lower_tokens if t not in QUESTION_STOP]
    weak = tuple(dict.fromkeys(t for t in significant if t in GENERIC_TERMS))
    strong_core = [t for t in significant if t not in GENERIC_TERMS]
    strong: list[str] = []
    for token in strong_core:
        strong.append(token)
        strong.extend(_related_stems(token))

    phrases: list[str] = []
    phrases.extend(extract_reference_phrases(text))
    distinctive = [t for t in significant if t not in GENERIC_TERMS]
    if len(distinctive) >= 2:
        phrases.append(" ".join(distinctive[:6]))
    generic_heavy = sum(1 for t in significant[:6] if t in GENERIC_TERMS) >= 2
    if len(significant) >= 2 and not generic_heavy:
        phrases.append(" ".join(significant[:6]))

    acronyms = [m.group(0) for m in _ACRONYM.finditer(text)]
    acronyms.extend(m.group(0) for m in _SNAKE_ACRONYM.finditer(text))
    if significant and not is_simple_reference_query(text):
        letters = "".join(word[0] for word in significant if word)
        if 2 <= len(letters) <= 6:
            acronyms.append(letters.upper())
        distinctive_letters = "".join(word[0] for word in significant if word not in GENERIC_TERMS)
        if 2 <= len(distinctive_letters) <= 6:
            acronyms.append(distinctive_letters.upper())
        # Keep generic-word initials when they complete a known multi-word concept
        # ("Fully Immunized Child" → FIC, not only FI).
        if len(significant) >= 3:
            acronyms.append("".join(word[0] for word in significant[:4]).upper())

    unique_acronyms = tuple(dict.fromkeys(a for a in acronyms if 2 <= len(a) <= 12))
    aliases: list[str] = []
    for acr in unique_acronyms:
        aliases.extend(_aliases_for_acronym(acr))
    return DomainQuery(
        phrases=tuple(dict.fromkeys(p for p in phrases if p)),
        acronyms=unique_acronyms,
        aliases=tuple(dict.fromkeys(aliases)),
        strong=tuple(dict.fromkeys(strong)),
        weak=weak,
    )


def score_source(path: str, content: str, query: DomainQuery, *, prompt: str = "") -> int:
    """Score a candidate file for a domain question. Generic leftovers stay cheap."""
    if not path:
        return 0
    rel = str(path).replace("\\", "/").lower()
    name = Path(rel).name
    stem = Path(rel).stem.lower()
    blob = f"{rel}\n{content[:4000]}".lower()
    score = 0
    for phrase in query.phrases:
        needle = phrase.lower()
        if needle in blob:
            score += 48
        compact = needle.replace(" ", "_")
        squeezed = needle.replace(" ", "")
        if compact in rel or squeezed in rel or needle in rel:
            score += 28
    for acr in query.acronyms:
        low = acr.lower()
        if low == stem or low in Path(rel).parts or f"/{low}." in f"/{rel}" or f"_{low}_" in f"_{stem}_":
            score += 40
        elif re.search(rf"\b{re.escape(low)}\b", name) or low in stem:
            score += 34
        elif re.search(rf"\b{re.escape(low)}\b", blob):
            score += 22
    for alias in query.aliases:
        low = alias.lower()
        if low in rel:
            score += 30
        elif re.search(rf"\b{re.escape(low)}\b", blob):
            score += 16
    for token in query.strong:
        if token in name or token in stem:
            score += 14
        elif token in blob:
            score += 5
    for token in query.weak:
        if token in name:
            score += 1
    return score + ranking_adjustment(path, prompt=prompt)


def identifier_matches_query(value: str, query: DomainQuery) -> bool:
    parts = {part for part in re.split(r"[^a-z0-9]+", str(value or "").lower()) if part}
    needles = {t.lower() for t in (*query.acronyms, *query.aliases, *query.strong) if t}
    return bool(parts & needles)


def _aliases_for_acronym(acr: str) -> list[str]:
    raw = str(acr or "").strip()
    if not raw:
        return []
    upper = raw.upper()
    lower = raw.lower()
    aliases = [
        upper,
        f"CH_{upper}",
        f"{upper}_STATUS",
        f"{upper}_SCORE",
        f"derive_{lower}",
        f"{lower}_status",
        f"{lower}_compliance",
        f"{lower}_classification",
    ]
    if upper == "FIC":
        aliases.extend(("fully immunized child", "immunization"))
    if upper == "CIC":
        aliases.extend(("completely immunized child", "immunization"))
    return aliases


def _related_stems(token: str) -> list[str]:
    word = str(token or "").lower()
    out: list[str] = []
    if word.endswith("ized") and len(word) > 6:
        base = word[:-3]  # immuniz
        out.extend((base + "ation", base + "e", word[:-1]))  # immunization, immunize, immunize
    elif word.endswith("ization") and len(word) > 8:
        out.append(word[:-5] + "ed")  # immunization → immunized
    return [item for item in out if item and item != word]
