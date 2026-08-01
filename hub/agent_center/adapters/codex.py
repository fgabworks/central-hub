from __future__ import annotations

from typing import Any

from hub.agent_center.adapters.codex_app_server import call
from hub.agent_center.adapters.cli_common import BaseCliAdapter


class CodexAdapter(BaseCliAdapter):
    authentication_method = "Codex browser or device authentication"

    def _authentication_probe(self, executable: str) -> dict[str, Any]:
        result = self._run_probe([executable, "login", "status"])
        text = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0 and "logged in" in text.lower():
            account = ""
            try:
                payload = call(executable, "account/read", {"refreshToken": False})
                account = _safe_account_label(payload)
            except Exception:
                pass
            return {"state": "connected", "detail": "Codex authenticated", "account_label": account}
        return {"state": "authentication_required", "detail": "Codex authentication required"}

    def _login_argv(self, executable: str) -> list[str]:
        return [executable, "login", "--device-auth"]

    def _logout_argv(self, executable: str) -> list[str]:
        return [executable, "logout"]

    def list_model_details(self, *, mode: str = "ask", force_refresh: bool = False) -> dict[str, Any]:
        exe = self.resolve_executable()
        if not exe:
            return {"models": [], "model_details": [], "models_source": "none", "error": "Codex is unavailable"}
        try:
            payload = call(exe, "model/list", {"includeHidden": False, "limit": 100})
            rows = payload.get("data") or []
            details = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                model_id = str(row.get("id") or row.get("model") or "").strip()
                if model_id:
                    details.append({"id": model_id, "display_name": str(row.get("displayName") or model_id), "availability": "available"})
            ids = [row["id"] for row in details]
            return {"models": ids, "model_details": details, "groups": {}, "recommended_model": ids[0] if ids else None, "models_source": "discovered", "reasoning_efforts": [], "error": ""}
        except Exception as exc:
            return {"models": [], "model_details": [], "groups": {}, "recommended_model": None, "models_source": "error", "reasoning_efforts": [], "error": str(exc)}

    def list_models(self) -> tuple[list[str], str]:
        data = self.list_model_details()
        return data["models"], data["models_source"]

    def _default_template(self, mode: str) -> list[str]:
        return [
            "{executable}", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--disable", "shell_tool", "--disable", "apps", "--disable", "browser_use",
            "--disable", "computer_use", "--disable", "multi_agent", "--disable", "hooks",
            "--disable", "image_generation",
            "--model", "{model}", "{prompt}",
        ]


def _safe_account_label(payload: dict[str, Any]) -> str:
    account = payload.get("account") or {}
    if not isinstance(account, dict):
        return ""
    return str(account.get("email") or account.get("planType") or account.get("type") or "")[:160]
