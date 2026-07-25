"""Fetch and summarize DHIS2 /api/schemas definitions for validation guidance."""

from __future__ import annotations

from typing import Any

from hub.dhis2.client import Dhis2Client, Dhis2Error


def summarize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a DHIS2 schema payload to required/optional property hints."""
    properties = schema.get("properties") or {}
    required: list[dict[str, str]] = []
    optional: list[dict[str, str]] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        # Skip noisy / system properties for the builder UI.
        if name in {"href", "access", "favorites", "sharing", "translations", "attributeValues"}:
            continue
        entry = {
            "name": name,
            "propertyType": str(prop.get("propertyType") or prop.get("type") or ""),
            "required": bool(prop.get("required")),
        }
        if prop.get("required"):
            required.append(entry)
        else:
            optional.append(entry)
    return {
        "klass": schema.get("klass") or schema.get("name") or schema.get("singular"),
        "singular": schema.get("singular"),
        "plural": schema.get("plural"),
        "required": required,
        "optional_count": len(optional),
        "raw_property_count": len(properties) if isinstance(properties, dict) else 0,
    }


def load_schema_summary(client: Dhis2Client, klass: str) -> dict[str, Any]:
    """Return schema summary or a friendly offline stub."""
    try:
        schema = client.get_schema(klass)
        summary = summarize_schema(schema)
        summary["ok"] = True
        summary["source"] = "dhis2"
        return summary
    except Dhis2Error as exc:
        return {
            "ok": False,
            "klass": klass,
            "required": [],
            "optional_count": 0,
            "raw_property_count": 0,
            "source": "unavailable",
            "detail": exc.message,
        }
