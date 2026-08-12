"""ARCTIC ↔ AiriX / VANTA isolation helpers.

Rules:
- Personal files/metadata never automatically enter RI, logs, or prompts.
- Only explicitly selected ARCTIC document ids / profile fields enter AI context.
- Work/VANTA data must never be mixed into ARCTIC context packing.
- Does not modify AiriX Tool Runtime.
"""

from __future__ import annotations

from typing import Any

from hub.arctic.models import ARCTIC_WORKSPACE, CLIMATE_PERSONAL, CLIMATE_WORK
from hub.arctic.store import ArcticStore

# Fields safe to include when the user explicitly opts profile into AI context.
_SAFE_PROFILE_AI_FIELDS = (
    "display_name",
    "headline",
    "email",
    "location",
    "summary",
    "skills",
    "links",
)


def assert_personal_workspace(workspace: str | None) -> None:
    ws = str(workspace or "").strip().lower()
    if ws and ws != ARCTIC_WORKSPACE:
        raise PermissionError(
            f"ARCTIC is Personal-only ({CLIMATE_PERSONAL}); refused workspace={workspace}"
        )


def build_arctic_ai_context(
    store: ArcticStore,
    *,
    document_ids: list[str] | None = None,
    include_profile: bool = False,
    include_latest_cv: bool = False,
    workspace: str | None = "personal",
) -> dict[str, Any]:
    """Pack ARCTIC metadata for AI only when explicitly requested.

    Never embeds file bytes. Never pulls VANTA/Work repositories or RI.
    """
    assert_personal_workspace(workspace or "personal")
    selected_ids = [
        str(i).strip() for i in (document_ids or []) if str(i).strip()
    ]
    documents: list[dict[str, Any]] = []
    for doc_id in selected_ids:
        doc = store.get_document(doc_id)
        if not doc:
            continue
        # Metadata only — no path contents, no binary.
        documents.append(
            {
                "id": doc["id"],
                "title": doc.get("title") or "",
                "primary_role": doc.get("primary_role") or "",
                "primary_role_label": doc.get("primary_role_label") or "",
                "source_type": doc.get("source_type") or "",
                "tags": list(doc.get("tags") or []),
                "notes": (doc.get("notes") or "")[:400],
                # Deliberately omit source_ref from default AI pack unless needed for open.
                "content_embedded": False,
            }
        )
        store.touch_accessed(doc_id)

    profile_block: dict[str, Any] | None = None
    if include_profile:
        profile = store.get_profile()
        profile_block = {k: profile.get(k) for k in _SAFE_PROFILE_AI_FIELDS}

    latest_cv = None
    if include_latest_cv:
        cv = store.latest_cv()
        if cv:
            latest_cv = {
                "id": cv["id"],
                "title": cv.get("title") or "",
                "primary_role": "cv",
                "note": "Resolved via Primary CV (latest CV).",
                "content_embedded": False,
            }
            if cv["id"] not in {d["id"] for d in documents}:
                documents.append(
                    {
                        "id": cv["id"],
                        "title": cv.get("title") or "",
                        "primary_role": "cv",
                        "primary_role_label": cv.get("primary_role_label") or "CV / Resume",
                        "source_type": cv.get("source_type") or "",
                        "tags": list(cv.get("tags") or []),
                        "notes": (cv.get("notes") or "")[:400],
                        "content_embedded": False,
                    }
                )

    return {
        "climate_section": CLIMATE_PERSONAL,
        "workspace": ARCTIC_WORKSPACE,
        "isolated_from": CLIMATE_WORK,
        "auto_ri": False,
        "auto_prompt_injection": False,
        "content_embedded": False,
        "profile": profile_block,
        "latest_cv": latest_cv,
        "documents": documents,
        "document_ids": [d["id"] for d in documents],
        "note": (
            "ARCTIC context is explicit-selection only. "
            "VANTA/Work data is not included. File bytes are not embedded."
        ),
    }


def is_arctic_data_in_payload(payload: dict[str, Any] | None) -> bool:
    """Heuristic: detect accidental ARCTIC leakage markers in a pack."""
    if not isinstance(payload, dict):
        return False
    blob = str(payload).lower()
    markers = ("arctic_documents", "climate_section': 'arctic", '"climate_section": "arctic"')
    return any(m in blob for m in markers)


def work_context_must_exclude_arctic(work_payload: dict[str, Any] | None) -> bool:
    """Return True when a Work/VANTA payload is clean of ARCTIC auto-injection."""
    if not isinstance(work_payload, dict):
        return True
    section = str(work_payload.get("climate_section") or "").upper()
    if section == CLIMATE_PERSONAL:
        return False
    if work_payload.get("arctic_auto_include"):
        return False
    docs = work_payload.get("arctic_documents")
    if docs:
        return False
    return True
