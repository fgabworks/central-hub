"""Generic schema-driven metadata payload builder (preview-only)."""

from __future__ import annotations

import json
from typing import Any

from hub.dhis2.builders.base import BaseMetadataBuilder


class GenericSchemaBuilder(BaseMetadataBuilder):
    def build_payload(self, form: dict[str, Any], *, operation: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field in self.type_spec.fields:
            value = form.get(field.id)
            if field.input == "checkbox":
                if isinstance(value, bool):
                    payload[field.id] = value
                elif str(value).lower() in {"1", "true", "on", "yes"}:
                    payload[field.id] = True
                elif str(value).lower() in {"0", "false", "off", "no"}:
                    payload[field.id] = False
            elif field.input == "dependency":
                ref = self._ref(self._clean_str(value))
                if ref:
                    payload[field.id] = ref
            elif field.input == "number" and self._has_value(value):
                text = str(value).strip()
                try:
                    payload[field.id] = int(text) if text.lstrip("-").isdigit() else float(text)
                except ValueError as exc:
                    raise ValueError(f"Field '{field.id}' must be numeric.") from exc
            elif field.input == "json" and self._has_value(value):
                try:
                    payload[field.id] = json.loads(str(value))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Field '{field.id}' contains invalid JSON.") from exc
            else:
                cleaned = self._clean_str(value)
                if cleaned is not None:
                    payload[field.id] = cleaned

        if operation == "update" and not payload.get("id"):
            raise ValueError("Update preview requires a UID (id).")
        return payload
