"""Orchestration for Prompting & Agent Center."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from hub.agent_center.adapters import build_adapters
from hub.agent_center.adapters.base import AgentAdapter, public_availability
from hub.agent_center.context_builder import build_context_preview, selectable_repositories
from hub.agent_center.models import (
    DEFAULT_TIMEOUT_SECONDS,
    DISABLED_MODES,
    MAX_PROMPT_CHARS,
    MODES,
    mode_label,
    normalize_mode,
)
from hub.agent_center.openai_runner import OpenAIRunner
from hub.agent_center.openai_settings import OpenAISettings, load_openai_settings
from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.runner import AgentRunner
from hub.agent_center.store import AgentCenterStore
from hub.registry.models import Registry
from hub.settings import ROOT_DIR

AuditFn = Callable[..., None]


class AgentCenterError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class AgentCenterService:
    def __init__(
        self,
        registry: Registry,
        *,
        store: AgentCenterStore | None = None,
        adapters: list[AgentAdapter] | None = None,
        audit: AuditFn | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        openai_settings: OpenAISettings | None = None,
        notebook: Any | None = None,
        sql_store: Any | None = None,
        uid_index: Any | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or AgentCenterStore()
        self.adapters = adapters if adapters is not None else build_adapters()
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self.openai_settings = openai_settings or load_openai_settings()
        self.notebook = notebook
        self.sql_store = sql_store
        self.uid_index = uid_index
        self.runner = AgentRunner(self.store, audit=audit)
        self.openai_runner = OpenAIRunner(
            self.store,
            settings=self.openai_settings,
            audit=audit,
        )

    def list_modes(self) -> list[dict[str, Any]]:
        rows = [{"id": m, "label": mode_label(m), "enabled": True} for m in MODES]
        for m in DISABLED_MODES:
            rows.append({"id": m, "label": mode_label(m), "enabled": False, "note": "Not yet available"})
        return rows

    def list_agents(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        mode_n = normalize_mode(mode) if mode else None
        out: list[dict[str, Any]] = []
        for adapter in self.adapters:
            av = adapter.availability()
            row = public_availability(av)
            row["is_api"] = bool(getattr(adapter, "is_api_adapter", False))
            if mode_n and mode_n not in row["modes"]:
                row["runnable"] = False
                row["detail"] = (row.get("detail") or "") + f" · mode {mode_n} unsupported"
            out.append(row)
        return out

    def get_agent(self, agent_id: str) -> AgentAdapter | None:
        for adapter in self.adapters:
            if adapter.descriptor.id == agent_id:
                return adapter
        return None

    def list_models(self, agent_id: str, *, mode: str | None = None) -> dict[str, Any]:
        adapter = self.get_agent(agent_id)
        if adapter is None:
            raise AgentCenterError(f"Unknown agent: {agent_id}", code="unknown_agent")
        mode_n = normalize_mode(mode) if mode else "ask"
        av = adapter.availability()
        if getattr(adapter, "is_api_adapter", False) and hasattr(adapter, "list_model_details"):
            details = adapter.list_model_details(mode=mode_n)
            return {
                "agent_id": agent_id,
                "mode": mode_n,
                "models": details.get("models") or [],
                "model_details": details.get("model_details") or [],
                "groups": details.get("groups") or {},
                "recommended_model": details.get("recommended_model"),
                "recommendation_reason": details.get("recommendation_reason"),
                "models_source": details.get("models_source"),
                "default_model": self.openai_settings.default_model,
                "reasoning_efforts": details.get("reasoning_efforts") or [],
                "status": av.status,
                "runnable": av.status in {"available", "degraded"} and bool(details.get("models")),
                "error": details.get("error") or "",
            }

        models, source = adapter.list_models()
        return {
            "agent_id": agent_id,
            "mode": mode_n,
            "models": models,
            "model_details": [{"id": m, "display_name": m, "availability": "available"} for m in models],
            "groups": {},
            "recommended_model": models[0] if models else None,
            "recommendation_reason": "managed",
            "models_source": source,
            "default_model": "",
            "reasoning_efforts": [],
            "status": av.status,
            "runnable": av.status in {"available", "degraded"} and bool(models),
            "error": "",
        }

    def repositories(self) -> list[dict[str, Any]]:
        return selectable_repositories(self.registry)

    def preview_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        preview = build_context_preview(
            self.registry,
            repository_ids=list(payload.get("repository_ids") or []),
            mode=str(payload.get("mode") or "ask"),
            prompt=str(payload.get("prompt") or ""),
            query_hints=list(payload.get("hints") or []),
            explicit_files=dict(payload.get("files") or {}),
        )
        preview["tools"] = {
            "enabled": [
                "repo_search",
                "read_file",
                "uid_lookup",
                "sql_lookup",
                "notebook_lookup",
            ],
            "disabled": [
                "edit",
                "terminal",
                "sql_execute",
                "email_access",
                "auto_apply",
            ],
        }
        return preview

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = normalize_mode(str(payload.get("mode") or "ask"))
        if mode in DISABLED_MODES:
            raise AgentCenterError("Edit/Test modes are not yet available", code="mode_disabled")
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise AgentCenterError("Prompt is required", code="prompt_required")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AgentCenterError(f"Prompt exceeds {MAX_PROMPT_CHARS} characters", code="prompt_too_long")

        agent_id = str(payload.get("agent_id") or "").strip()
        adapter = self.get_agent(agent_id)
        if adapter is None:
            raise AgentCenterError(f"Unknown agent: {agent_id}", code="unknown_agent")
        av = adapter.availability()
        if av.status not in {"available", "degraded"}:
            run = self.store.create_run(
                {
                    "status": "unavailable",
                    "mode": mode,
                    "agent_id": agent_id,
                    "agent_label": av.label,
                    "model": str(payload.get("model") or ""),
                    "repository_ids": list(payload.get("repository_ids") or []),
                    "prompt": prompt,
                    "packed_prompt": "",
                    "context": {"detail": av.detail},
                    "referenced_files": [],
                }
            )
            self.store.update_run(
                run["id"],
                status="unavailable",
                error=av.detail or "Agent unavailable",
                finished_at=run["created_at"],
            )
            if self.audit:
                self.audit(
                    action="AGENT_RUN_UNAVAILABLE",
                    detail={"run_id": run["id"], "agent_id": agent_id, "detail": av.detail},
                )
            return self.store.get_run(run["id"]) or run

        if mode not in av.modes:
            raise AgentCenterError(f"Agent does not support mode {mode}", code="mode_unsupported")

        model = str(payload.get("model") or "").strip()
        reasoning_effort_raw = str(payload.get("reasoning_effort") or "").strip()
        run_opts: dict[str, Any] = {}

        if getattr(adapter, "is_api_adapter", False) and hasattr(adapter, "resolve_run_model"):
            resolved = adapter.resolve_run_model(
                mode=mode,
                requested_model=model or None,
                force_refresh=True,
            )
            if not resolved.get("ok"):
                code = str(resolved.get("code") or "model_unavailable")
                raise AgentCenterError(str(resolved.get("error") or "Model unavailable"), code=code)
            model = str(resolved["model"])
            from hub.agent_center.openai_catalog import normalize_reasoning_effort

            effort = normalize_reasoning_effort(
                reasoning_effort_raw,
                supported=bool(resolved.get("supports_reasoning_effort")),
            )
            if reasoning_effort_raw and resolved.get("supports_reasoning_effort") and effort is None:
                raise AgentCenterError(
                    f"Invalid reasoning_effort {reasoning_effort_raw!r}",
                    code="reasoning_effort_invalid",
                )
            run_opts = {
                "reasoning_effort": effort,
                "background": bool(resolved.get("background")),
                "is_pro": bool(resolved.get("is_pro")),
                "timeout_seconds": float(resolved.get("timeout_seconds") or self.openai_settings.timeout_seconds),
                "selection_reason": resolved.get("reason"),
            }
        else:
            models, source = adapter.list_models()
            if not model and models:
                model = models[0]
            if models and model and model not in models:
                if not (source == "fallback" and model == self.openai_settings.default_model):
                    raise AgentCenterError(f"Model not offered by agent: {model}", code="model_invalid")
            if not model:
                raise AgentCenterError("No model available", code="model_unavailable")

        preview = self.preview_context(payload)
        if not preview.get("ok"):
            errors = preview.get("scope_errors") or []
            missing = preview.get("missing_repository_ids") or []
            msg = "; ".join(errors + [f"missing:{m}" for m in missing]) or "Invalid repository scope"
            raise AgentCenterError(msg, code="scope_invalid")

        roots = preview["roots"]
        cwd = Path(roots[0]["path"])
        packed = preview["packed_prompt"]
        referenced = [
            {"repo_id": f["repo_id"], "path": f["path"]} for f in preview.get("files") or []
        ]
        for item in preview.get("instructions") or []:
            referenced.append({"repo_id": item["repo_id"], "path": item["path"], "kind": "instruction"})

        run = self.store.create_run(
            {
                "status": "queued",
                "mode": mode,
                "agent_id": agent_id,
                "agent_label": av.label,
                "model": model,
                "repository_ids": preview["repository_ids"],
                "prompt": prompt,
                "packed_prompt": packed,
                "context": {
                    "roots": roots,
                    "files": preview.get("files") or [],
                    "excluded_secrets": preview.get("excluded_secrets") or [],
                    "packed_prompt_chars": preview.get("packed_prompt_chars"),
                    "tools": preview.get("tools"),
                    "model_selection": run_opts.get("selection_reason"),
                    "reasoning_effort": run_opts.get("reasoning_effort"),
                    "background": run_opts.get("background"),
                    "is_pro": run_opts.get("is_pro"),
                },
                "referenced_files": referenced,
            }
        )

        if self.audit:
            self.audit(
                action="AGENT_RUN_SUBMIT",
                detail={
                    "run_id": run["id"],
                    "agent_id": agent_id,
                    "mode": mode,
                    "model": model,
                    "reasoning_effort": run_opts.get("reasoning_effort"),
                    "background": run_opts.get("background"),
                    "repository_ids": preview["repository_ids"],
                    "prompt_chars": len(prompt),
                },
            )

        if getattr(adapter, "is_api_adapter", False):
            tools_ctx = AgentToolsContext(
                registry=self.registry,
                repository_ids=list(preview["repository_ids"]),
                notebook=self.notebook,
                sql_store=self.sql_store,
                uid_index=self.uid_index,
                max_result_chars=self.openai_settings.max_tool_result_chars,
            )
            tools_ctx.referenced_files.extend(referenced)
            self.openai_runner.start(
                run_id=run["id"],
                model=model,
                mode=mode,
                user_prompt=prompt,
                packed_prompt=packed,
                tools_ctx=tools_ctx,
                timeout_seconds=float(
                    payload.get("timeout_seconds")
                    or run_opts.get("timeout_seconds")
                    or self.openai_settings.timeout_seconds
                    or self.timeout_seconds
                ),
                reasoning_effort=run_opts.get("reasoning_effort"),
                background=bool(run_opts.get("background")),
            )
            return self.store.get_run(run["id"]) or run

        # CLI adapters
        prompt_dir = ROOT_DIR / "data" / "agent_center" / "runs" / run["id"]
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / "prompt.txt"
        prompt_path.write_text(packed, encoding="utf-8")

        try:
            argv = adapter.build_argv(
                mode=mode,
                prompt=packed,
                model=model,
                cwd=str(cwd),
                prompt_file=str(prompt_path),
            )
        except TypeError:
            argv = adapter.build_argv(mode=mode, prompt=packed, model=model, cwd=str(cwd))
        except ValueError as exc:
            self.store.update_run(
                run["id"],
                status="failed",
                error=str(exc),
                finished_at=run["created_at"],
            )
            raise AgentCenterError(str(exc), code="argv_invalid") from exc

        if not argv or any(not isinstance(x, str) for x in argv):
            self.store.update_run(run["id"], status="failed", error="Invalid agent argv", finished_at=run["created_at"])
            raise AgentCenterError("Invalid agent argv", code="argv_invalid")
        for part in argv:
            if part in {";", "&&", "||", "|", ">", "<", "`"}:
                self.store.update_run(
                    run["id"], status="failed", error="Rejected unsafe argv token", finished_at=run["created_at"]
                )
                raise AgentCenterError("Rejected unsafe argv token", code="argv_unsafe")

        run_cwd = cwd
        if getattr(adapter.descriptor, "provider", "") == "hub_simulator" or agent_id == "hub-simulator":
            run_cwd = ROOT_DIR

        self.runner.start(
            run_id=run["id"],
            argv=argv,
            cwd=run_cwd,
            timeout_seconds=float(payload.get("timeout_seconds") or self.timeout_seconds),
        )
        return self.store.get_run(run["id"]) or run

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise AgentCenterError("Run not found", code="not_found")
        # Cooperative cancel for both CLI and API runners
        self.openai_runner.cancel(run_id)
        updated = self.runner.cancel(run_id) or self.store.get_run(run_id) or run
        if self.audit:
            self.audit(action="AGENT_RUN_CANCEL", detail={"run_id": run_id})
        return updated

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise AgentCenterError("Run not found", code="not_found")
        return run

    def history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_runs(limit=limit)

    def page_bootstrap(self) -> dict[str, Any]:
        return {
            "modes": self.list_modes(),
            "agents": self.list_agents(),
            "repositories": self.repositories(),
            "prompts": self.store.list_prompts(),
            "history": self.history(limit=30),
            "openai": self.openai_settings.public_status(),
            "safety": {
                "read_only": True,
                "edit_test": "Not yet available",
                "secret_exclusion": True,
                "output_untrusted": True,
                "tools_allowlist": [
                    "repo_search",
                    "read_file",
                    "uid_lookup",
                    "sql_lookup",
                    "notebook_lookup",
                ],
            },
        }
