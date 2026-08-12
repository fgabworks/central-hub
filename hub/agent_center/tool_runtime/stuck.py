"""Duplicate / stuck-call guard with Phase 2 recovery nudges."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _fingerprint(tool: str, arguments: dict[str, Any] | None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        payload = str(args)
    return f"{tool}:{payload}"


_RECOVERY_ALTERNATES: dict[str, tuple[str, ...]] = {
    "sql_lookup": ("sql_query_execute", "data_explorer_lookup", "org_unit_lookup"),
    "sql_query_execute": ("sql_lookup", "org_unit_lookup", "uid_lookup"),
    "repo_search": ("read_file", "repository_intelligence", "skill_recall"),
    "read_file": ("repo_search", "repository_intelligence", "skill_recall"),
    "repository_intelligence": ("skill_recall", "repo_search", "read_file"),
    "skill_recall": ("repository_intelligence", "notebook_lookup"),
    "uid_lookup": ("org_unit_lookup", "dhis2_reports_lookup", "sql_lookup"),
    "org_unit_lookup": ("uid_lookup", "sql_lookup", "dhis2_reports_lookup"),
    "dhis2_reports_lookup": ("uid_lookup", "org_unit_lookup"),
    "jobs_lookup": ("audit_lookup", "notebook_lookup"),
}


@dataclass
class StuckGuard:
    duplicate_limit: int = 2
    max_recoveries: int = 2
    _counts: dict[str, int] = field(default_factory=dict)
    _last: str = ""
    _recoveries: int = 0
    _used_alternates: set[str] = field(default_factory=set)

    def note(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Record a call.

        Returns {blocked, recover, reason, count, fingerprint, suggest_tools, retry}.
        Phase 2: first duplicates can recover with alternate-tool nudges before hard stop.
        """
        tool_name = str(tool or "").strip()
        fp = _fingerprint(tool_name, arguments)
        count = self._counts.get(fp, 0) + 1
        self._counts[fp] = count
        limit = max(1, int(self.duplicate_limit))
        over = count > limit
        self._last = fp
        if not over:
            return {
                "blocked": False,
                "recover": False,
                "reason": "",
                "count": count,
                "fingerprint": fp,
                "limit": limit,
                "suggest_tools": [],
                "retry": count,
            }

        # Soft recovery: suggest alternate tools once or twice before hard stuck.
        if self._recoveries < max(0, int(self.max_recoveries)):
            alts = [
                a
                for a in _RECOVERY_ALTERNATES.get(tool_name, ("repository_intelligence", "skill_recall"))
                if a not in self._used_alternates and a != tool_name
            ]
            if alts:
                self._recoveries += 1
                for a in alts[:2]:
                    self._used_alternates.add(a)
                return {
                    "blocked": False,
                    "recover": True,
                    "reason": "duplicate_recover",
                    "count": count,
                    "fingerprint": fp,
                    "limit": limit,
                    "suggest_tools": alts[:3],
                    "retry": self._recoveries,
                }

        return {
            "blocked": True,
            "recover": False,
            "reason": "duplicate_tool_call",
            "count": count,
            "fingerprint": fp,
            "limit": limit,
            "suggest_tools": [],
            "retry": self._recoveries,
        }

    def reset(self) -> None:
        self._counts.clear()
        self._last = ""
        self._recoveries = 0
        self._used_alternates.clear()
