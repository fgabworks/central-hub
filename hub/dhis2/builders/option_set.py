"""Option Set metadata builder (preview-only)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from hub.dhis2.builders.base import BaseMetadataBuilder


class OptionSetBuilder(BaseMetadataBuilder):
    def build_payload(self, form: dict[str, Any], *, operation: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self._clean_str(form.get("name")),
            "valueType": self._clean_str(form.get("valueType")) or "TEXT",
        }
        for key in ("id", "code", "description"):
            value = self._clean_str(form.get(key))
            if value:
                payload[key] = value

        options_raw = form.get("options_json")
        if options_raw and str(options_raw).strip():
            try:
                options = json.loads(str(options_raw))
            except json.JSONDecodeError as exc:
                raise ValueError("Options JSON is invalid.") from exc
            if not isinstance(options, list):
                raise ValueError("Options JSON must be an array.")
            cleaned_options: list[dict[str, Any]] = []
            for index, option in enumerate(options, start=1):
                if not isinstance(option, dict):
                    raise ValueError("Each option must be an object.")
                item: dict[str, Any] = {
                    "name": self._clean_str(option.get("name")) or f"Option {index}",
                    "code": self._clean_str(option.get("code")) or f"OPT{index}",
                    "sortOrder": index,
                }
                if option.get("id"):
                    item["id"] = self._clean_str(option.get("id"))
                cleaned_options.append(item)
            payload["options"] = cleaned_options

        if operation == "update" and not payload.get("id"):
            raise ValueError("Update preview requires a UID (id).")
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def options_csv(payload_object: dict[str, Any]) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["sortOrder", "code", "name"])
        for option in payload_object.get("options") or []:
            writer.writerow([option.get("sortOrder"), option.get("code"), option.get("name")])
        return stream.getvalue()
