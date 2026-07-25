"""Data Element metadata builder (preview-only)."""

from __future__ import annotations

from typing import Any

from hub.dhis2.builders.base import BaseMetadataBuilder


class DataElementBuilder(BaseMetadataBuilder):
    def build_payload(self, form: dict[str, Any], *, operation: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self._clean_str(form.get("name")),
            "shortName": self._clean_str(form.get("shortName")),
            "domainType": self._clean_str(form.get("domainType")) or "AGGREGATE",
            "valueType": self._clean_str(form.get("valueType")) or "TEXT",
            "aggregationType": self._clean_str(form.get("aggregationType")) or "SUM",
        }
        for key in ("id", "code", "description", "formName"):
            value = self._clean_str(form.get(key))
            if value:
                payload[key] = value

        category_combo = self._ref(self._clean_str(form.get("categoryCombo")))
        if category_combo:
            payload["categoryCombo"] = category_combo

        option_set = self._ref(self._clean_str(form.get("optionSet")))
        if option_set:
            payload["optionSet"] = option_set

        zero = form.get("zeroIsSignificant")
        if isinstance(zero, bool):
            payload["zeroIsSignificant"] = zero
        elif str(zero).lower() in {"1", "true", "on", "yes"}:
            payload["zeroIsSignificant"] = True
        elif str(zero).lower() in {"0", "false", "off", "no"}:
            payload["zeroIsSignificant"] = False

        if operation == "update" and not payload.get("id"):
            raise ValueError("Update preview requires a UID (id).")
        return {key: value for key, value in payload.items() if value is not None}
