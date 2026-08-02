"""Classify discovered objects into inventory groups (pattern-based; no guessing)."""

from __future__ import annotations

from typing import Any

from hub.data_explorer.config import ExplorerConfig, get_explorer_config
from hub.data_explorer.discovery import ObjectMeta


GROUPS = (
    "Linelist",
    "Tracker",
    "Analytics",
    "Reporting",
    "HCSC/RF",
    "Organisation Units",
    "Application/Internal",
    "Unknown",
)


def classify_object(obj: ObjectMeta, cfg: ExplorerConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_explorer_config()
    key = obj.full_name.lower()
    override = cfg.object_overrides.get(key) or cfg.object_overrides.get(obj.name.lower())
    if override:
        group = str(override.get("group") or "Unknown")
        return {
            "group": group if group in GROUPS else "Unknown",
            "likely_role": str(override.get("likely_role") or ""),
            "confidence": "verified",
            "sensitivity": str(override.get("sensitivity") or _group_sens(group, cfg)),
            "browse_status": str(override.get("browse") or _group_browse(group, cfg)),
            "export_status": str(override.get("export") or _group_export(group, cfg)),
            "classification_source": "object_override",
        }

    for pat in cfg.classification_patterns:
        if pat.pattern.search(obj.name) or pat.pattern.search(obj.full_name):
            group = pat.group if pat.group in GROUPS else "Unknown"
            return {
                "group": group,
                "likely_role": pat.likely_role,
                "confidence": "pattern",
                "sensitivity": _group_sens(group, cfg),
                "browse_status": _group_browse(group, cfg),
                "export_status": _group_export(group, cfg),
                "classification_source": "name_pattern",
            }

    return {
        "group": "Unknown",
        "likely_role": "Unresolved — no verified mapping or matching pattern",
        "confidence": "unresolved",
        "sensitivity": _group_sens("Unknown", cfg),
        "browse_status": _group_browse("Unknown", cfg),
        "export_status": _group_export("Unknown", cfg),
        "classification_source": "none",
    }


def _group_sens(group: str, cfg: ExplorerConfig) -> str:
    gp = cfg.group_policies.get(group)
    return gp.sensitivity if gp else "unresolved"


def _group_browse(group: str, cfg: ExplorerConfig) -> str:
    gp = cfg.group_policies.get(group)
    return gp.browse if gp else "preview_only"


def _group_export(group: str, cfg: ExplorerConfig) -> str:
    gp = cfg.group_policies.get(group)
    return gp.export if gp else "deny"


def build_inventory(
    objects: list[ObjectMeta],
    *,
    lineage_by_object: dict[str, dict[str, Any]] | None = None,
    cfg: ExplorerConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_explorer_config()
    lineage_by_object = lineage_by_object or {}
    grouped: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
    for obj in objects:
        cls = classify_object(obj, cfg)
        lin = lineage_by_object.get(obj.full_name.lower()) or lineage_by_object.get(
            obj.name.lower()
        ) or {"indicators": [], "reports": [], "exports": [], "unresolved": True}
        key_cols = []
        for k in obj.keys:
            if k.kind == "primary":
                key_cols.extend(k.columns)
        if not key_cols and obj.columns:
            key_cols = [c.name for c in obj.columns[:3]]
        entry = {
            "schema": obj.schema,
            "object_name": obj.name,
            "full_name": obj.full_name,
            "object_type": obj.object_type,
            "estimated_rows": obj.estimated_rows,
            "approximate": obj.approximate,
            "key_columns": key_cols,
            "likely_role": cls["likely_role"],
            "known_report_indicator_usage": {
                "indicators": lin.get("indicators") or [],
                "reports": lin.get("reports") or [],
                "exports": lin.get("exports") or [],
            },
            "sensitivity_level": cls["sensitivity"],
            "recommended_browse_status": cls["browse_status"],
            "recommended_export_status": cls["export_status"],
            "confidence": cls["confidence"],
            "classification_unresolved": cls["confidence"] == "unresolved",
        }
        grouped[cls["group"]].append(entry)

    for g in grouped:
        grouped[g].sort(key=lambda x: x["full_name"].lower())

    return {
        "groups": grouped,
        "totals": {g: len(items) for g, items in grouped.items()},
        "object_count": len(objects),
        "note": (
            "Classifications use name patterns and hub registries only. "
            "Physical Live mappings remain unresolved until verified against a configured RO connection."
        ),
    }
