"""Process-local provider session cache for Tool Runtime reuse."""

from __future__ import annotations

import threading
import time
from typing import Any


class ProviderSessionCache:
    """Reuse previous_response_id when conversation + provider + model match."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(conversation_id: str, provider: str, model: str) -> str:
        return "|".join(
            [
                str(conversation_id or "").strip(),
                str(provider or "").strip(),
                str(model or "").strip(),
            ]
        )

    def get(
        self,
        *,
        conversation_id: str,
        provider: str,
        model: str,
        context_fingerprint: str = "",
    ) -> dict[str, Any] | None:
        key = self._key(conversation_id, provider, model)
        if not conversation_id or not provider or not model:
            return None
        with self._lock:
            row = self._sessions.get(key)
        if not row:
            return None
        fp = str(context_fingerprint or "").strip()
        stored_fp = str(row.get("context_fingerprint") or "").strip()
        if fp and stored_fp and fp != stored_fp:
            # Context changed — do not reuse provider session blindly.
            return None
        return dict(row)

    def put(
        self,
        *,
        conversation_id: str,
        provider: str,
        model: str,
        previous_response_id: str,
        context_fingerprint: str = "",
    ) -> None:
        key = self._key(conversation_id, provider, model)
        rid = str(previous_response_id or "").strip()
        if not key.strip("|") or not rid:
            return
        with self._lock:
            self._sessions[key] = {
                "conversation_id": conversation_id,
                "provider": provider,
                "model": model,
                "previous_response_id": rid,
                "context_fingerprint": str(context_fingerprint or "").strip(),
                "updated_at": time.time(),
            }
            # Cap cache size.
            if len(self._sessions) > 200:
                oldest = sorted(self._sessions.items(), key=lambda kv: kv[1].get("updated_at") or 0)
                for drop_key, _ in oldest[:40]:
                    self._sessions.pop(drop_key, None)

    def clear(self, conversation_id: str | None = None) -> None:
        with self._lock:
            if not conversation_id:
                self._sessions.clear()
                return
            cid = str(conversation_id).strip()
            for key in [k for k in self._sessions if k.startswith(cid + "|")]:
                self._sessions.pop(key, None)


GLOBAL_PROVIDER_SESSION_CACHE = ProviderSessionCache()
