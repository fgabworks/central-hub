"""Program Indicator metadata builder (preview-only)."""

from __future__ import annotations

from typing import Any

from hub.dhis2.builders.base import BaseMetadataBuilder


class ProgramIndicatorBuilder(BaseMetadataBuilder):
    def build_payload(self, form: dict[str, Any], *, operation: str) -> dict[str, Any]:
        program = self._ref(self._clean_str(form.get("program")))
        if not program:
            raise ValueError("Program is required for a program indicator.")

        payload: dict[str, Any] = {
            "name": self._clean_str(form.get("name")),
            "shortName": self._clean_str(form.get("shortName")),
            "program": program,
            "expression": self._clean_str(form.get("expression")),
            "analyticsType": self._clean_str(form.get("analyticsType")) or "EVENT",
            "aggregationType": self._clean_str(form.get("aggregationType")) or "SUM",
        }
        for key in ("id", "code", "description", "filter"):
            value = self._clean_str(form.get(key))
            if value:
                payload[key] = value

        decimals = self._clean_str(form.get("decimals"))
        if decimals is not None:
            try:
                payload["decimals"] = int(decimals)
            except ValueError as exc:
                raise ValueError("Decimals must be an integer.") from exc

        if operation == "update" and not payload.get("id"):
            raise ValueError("Update preview requires a UID (id).")
        return {key: value for key, value in payload.items() if value is not None}
