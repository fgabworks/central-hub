"""ARCTIC service facade (Personal only)."""

from __future__ import annotations

from typing import Any

from hub.arctic.store import ArcticError, ArcticStore


class ArcticService:
    """Thin service boundary — always Personal / ARCTIC, never VANTA/Work."""

    workspace = "personal"
    climate_section = "ARCTIC"

    def __init__(self, store: ArcticStore | None = None) -> None:
        self.store = store or ArcticStore()

    def dashboard(self) -> dict[str, Any]:
        return self.store.dashboard()

    def get_profile(self) -> dict[str, Any]:
        return self.store.get_profile()

    def update_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.update_profile(payload)

    def list_documents(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.list_documents(**kwargs)

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        return self.store.get_document(doc_id)

    def register_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.register_document(payload)

    def update_document(self, doc_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.update_document(doc_id, payload)

    def set_primary_role(self, doc_id: str, role: str) -> dict[str, Any]:
        return self.store.set_primary_role(doc_id, role)

    def delete_document(self, doc_id: str) -> bool:
        return self.store.delete_document(doc_id)

    def touch_accessed(self, doc_id: str) -> dict[str, Any] | None:
        return self.store.touch_accessed(doc_id)

    def latest_cv(self) -> dict[str, Any] | None:
        return self.store.latest_cv()

    def career_pack(self) -> dict[str, Any]:
        return self.store.career_pack()

    def list_sources(self) -> list[dict[str, Any]]:
        return self.store.list_sources()

    def refresh_sources(self) -> list[dict[str, Any]]:
        return self.store.refresh_sources()


__all__ = ["ArcticService", "ArcticError"]
