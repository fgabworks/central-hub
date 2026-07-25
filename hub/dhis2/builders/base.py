"""Shared metadata payload validation and preview; never writes to DHIS2."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from hub.dhis2.client import Dhis2Client, Dhis2Error
from hub.dhis2.schemas import load_schema_summary
from hub.dhis2.type_config import MetadataTypeSpec
from hub.dhis2.uid_index import UidIndex
from hub.dhis2.workspace import catalog_schema_summary


class BaseMetadataBuilder(ABC):
    def __init__(self, client: Dhis2Client, type_spec: MetadataTypeSpec, uid_index: UidIndex | None = None) -> None:
        self.client = client
        self.type_spec = type_spec
        self.uid_index = uid_index

    @abstractmethod
    def build_payload(self, form: dict[str, Any], *, operation: str) -> dict[str, Any]:
        """Return the exact object payload for the metadata type."""

    def preview(self, form: dict[str, Any], *, operation: str, check_remote: bool = True) -> dict[str, Any]:
        errors = self._missing_required(form)
        try:
            payload_object = self.build_payload(form, operation=operation)
        except ValueError as exc:
            errors.append(str(exc))
            payload_object = {}
        return self._preview_object(
            payload_object,
            operation=operation,
            errors=errors,
            required_source=form,
            check_remote=check_remote,
        )

    def preview_raw(self, raw_json: str, *, operation: str, check_remote: bool = True) -> dict[str, Any]:
        """Parse and revalidate an object or exact metadata envelope from the raw editor."""
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            return self._invalid_raw(f"Invalid raw JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).", raw_json)
        if not isinstance(parsed, dict):
            return self._invalid_raw("Raw JSON must be an object.", raw_json)

        if self.type_spec.plural in parsed:
            rows = parsed.get(self.type_spec.plural)
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                return self._invalid_raw(
                    f"Envelope '{self.type_spec.plural}' must contain exactly one metadata object.", raw_json
                )
            payload_object = rows[0]
        else:
            payload_object = parsed
        errors: list[str] = []
        if operation == "update" and not payload_object.get("id"):
            errors.append("Update preview requires a UID (id).")
        return self._preview_object(
            payload_object,
            operation=operation,
            errors=errors,
            required_source=payload_object,
            check_remote=check_remote,
        )

    def _preview_object(
        self,
        payload_object: dict[str, Any],
        *,
        operation: str,
        errors: list[str],
        required_source: dict[str, Any],
        check_remote: bool,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        schema = catalog_schema_summary(self.type_spec) or load_schema_summary(self.client, self.type_spec.schema_klass)
        if not schema.get("ok"):
            warnings.append(f"Schema unavailable ({schema.get('detail')}). Using generated field rules only.")

        errors.extend(self._schema_errors(payload_object))
        dependencies = self._dependency_report(payload_object)
        for dep in dependencies:
            if dep.get("required") and not dep.get("value"):
                errors.append(f"Missing dependency: {dep['field']} ({dep['resource']})")
            elif dep.get("value") and not dep.get("resolved"):
                errors.append(
                    f"Unresolved dependency: {dep['field']} '{dep['value']}' was not found unambiguously in the UID mapping index."
                )

        duplicates = self._duplicate_report(payload_object, check_remote=check_remote, warnings=warnings)
        if duplicates.get("has_duplicates"):
            message = duplicates.get("detail") or "Duplicate metadata candidate found."
            (errors if operation == "create" else warnings).append(message)

        metadata_payload = {self.type_spec.plural: [payload_object]} if payload_object else {}
        ok = not errors
        result = {
            "ok": ok,
            "operation": operation,
            "metadata_type": self.type_spec.id,
            "builder_mode": self.type_spec.builder_mode,
            "apply_enabled": False,
            "required_fields": [
                {"id": field.id, "label": field.label, "filled": self._has_value(required_source.get(field.id))}
                for field in self.type_spec.fields if field.required
            ],
            "dependencies": dependencies,
            "schema": schema,
            "duplicates": duplicates,
            "errors": _unique(errors),
            "warnings": _unique(warnings),
            "payload_object": payload_object,
            "payload": metadata_payload,
            "payload_json": json.dumps(metadata_payload, indent=2, ensure_ascii=True),
            "validation_summary": "Valid preview" if ok else "Validation failed",
        }
        options_csv = getattr(self, "options_csv", None)
        if callable(options_csv):
            result["options_csv"] = options_csv(payload_object)
        return result

    def _invalid_raw(self, message: str, raw_json: str) -> dict[str, Any]:
        return {
            "ok": False,
            "operation": None,
            "metadata_type": self.type_spec.id,
            "builder_mode": self.type_spec.builder_mode,
            "apply_enabled": False,
            "required_fields": [],
            "dependencies": [],
            "schema": catalog_schema_summary(self.type_spec),
            "duplicates": {"has_duplicates": False, "local": [], "remote": None, "detail": "Not checked."},
            "errors": [message],
            "warnings": [],
            "payload_object": {},
            "payload": {},
            "payload_json": raw_json,
            "validation_summary": "Validation failed",
        }

    def _schema_errors(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in self.type_spec.fields:
            value = payload.get(field.id)
            if field.required and not self._has_value(value):
                errors.append(f"Missing required field '{field.id}'.")
                continue
            if not self._has_value(value):
                continue
            if field.input == "select" and field.options and value not in field.options:
                errors.append(f"Field '{field.id}' must be one of: {', '.join(field.options)}.")
            elif field.input == "dependency" and (not isinstance(value, dict) or not value.get("id")):
                errors.append(f"Field '{field.id}' must be a reference object with an id.")
            elif field.input == "checkbox" and not isinstance(value, bool):
                errors.append(f"Field '{field.id}' must be boolean.")
            elif field.input == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{field.id}' must be numeric.")
            elif field.input == "json" and not isinstance(value, (list, dict)):
                errors.append(f"Field '{field.id}' must be a JSON collection or object.")
        return errors

    def _missing_required(self, form: dict[str, Any]) -> list[str]:
        return [
            f"Missing required field '{field.id}'."
            for field in self.type_spec.fields
            if field.required and not self._has_value(form.get(field.id))
        ]

    def _dependency_report(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        for field in self.type_spec.fields:
            if field.input != "dependency":
                continue
            raw = payload.get(field.id)
            value = raw.get("id") if isinstance(raw, dict) else raw
            resolved = self.uid_index.get(field.resource or "", str(value)) if value and self.uid_index else None
            report.append({
                "field": field.id,
                "label": field.label,
                "resource": field.resource,
                "required": field.required,
                "value": value or None,
                "resolved": bool(resolved),
                "resolved_name": (resolved or {}).get("name"),
            })
        return report

    def _duplicate_report(self, payload: dict[str, Any], *, check_remote: bool, warnings: list[str]) -> dict[str, Any]:
        local: list[dict[str, Any]] = []
        if self.uid_index:
            local = self.uid_index.find_duplicates(
                self.type_spec.plural,
                name=self._clean_str(payload.get("name")),
                code=self._clean_str(payload.get("code")),
                uid=self._clean_str(payload.get("id")),
            )
        remote = None
        if check_remote and self.client.public_config().get("configured"):
            try:
                remote = self.client.find_duplicates(
                    self.type_spec.plural,
                    name=self._clean_str(payload.get("name")),
                    code=self._clean_str(payload.get("code")),
                    uid=self._clean_str(payload.get("id")),
                )
            except Dhis2Error as exc:
                warnings.append(f"Remote duplicate check skipped: {exc.message}")
        elif check_remote:
            warnings.append("Remote duplicate check skipped: DHIS2 is not configured.")
        remote_matches = (remote or {}).get("matches") or {}
        if isinstance(remote_matches, dict):
            remote_count = sum(len(v) for v in remote_matches.values() if isinstance(v, list))
        else:
            remote_count = 0
        total = len(local) + remote_count
        has_duplicates = total > 0 or bool((remote or {}).get("has_duplicates"))
        return {
            "has_duplicates": has_duplicates,
            "local": local,
            "remote": remote,
            "total": total,
            "detail": (
                f"Found {total} duplicate candidate(s) across the local index and DHIS2."
                if total
                else (remote or {}).get("detail")
                if has_duplicates
                else "No duplicate candidates found."
            ),
        }

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, (bool, int, float)):
            return True
        if isinstance(value, (list, dict)):
            return bool(value)
        return str(value).strip() != ""

    @staticmethod
    def _ref(uid: str | None) -> dict[str, str] | None:
        return {"id": str(uid).strip()} if uid and str(uid).strip() else None

    @staticmethod
    def _clean_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
