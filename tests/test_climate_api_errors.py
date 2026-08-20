"""CLIMATE API JSON error hardening — never return Flask HTML for /api/climate/*."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from hub.agent_center.redact import classify_provider_error
from hub.agent_center.service import AgentCenterError
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError


SPAWN = (
    "failed to spawn code-mode host .codex/.sandbox-bin/codex-code-mode-host.exe: "
    "The system cannot find the file specified. (os error 2)"
)
MODEL = "gpt-5.6-luna"
RUN_BODY = {
    "provider": "codex",
    "model": MODEL,
    "prompt": "Reply with ok",
    "display_prompt": "Reply with ok",
    "task_mode": "ask",
    "context_scope": "repository",
    "repository_id": "live-processing-local",
    "attached_files": [],
    "current_file": "",
    "selection": "",
    "selected_files": [],
    "include_repo_context": False,
    "surface": "workspace",
}


class ClimateApiJsonErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _install_execute(self, fn):
        self.app.config["CLIMATE"].execute = fn
        self.app.config["CLIMATE"].execute_chat = fn

    def _assert_json_error(self, resp, *, status: int, code: str | None = None):
        self.assertEqual(resp.status_code, status, resp.get_data(as_text=True)[:400])
        self.assertIn("application/json", resp.content_type)
        body = resp.get_data(as_text=True)
        self.assertNotIn("<!doctype", body.lower())
        self.assertNotIn("<html", body.lower())
        data = resp.get_json(silent=True) or {}
        self.assertIsInstance(data, dict)
        self.assertFalse(data.get("ok"))
        self.assertTrue(str(data.get("error") or "").strip())
        self.assertTrue(str(data.get("code") or "").strip())
        if code:
            self.assertEqual(data["code"], code)
        self.assertNotIn("sk-", str(data.get("error") or ""))
        return data

    def test_repository_runs_duplicate_repository_id_is_json_not_html(self):
        captured: dict = {}

        def fake(workspace, repository_id, **payload):
            captured["workspace"] = workspace
            captured["repository_id"] = repository_id
            captured["payload"] = payload
            return {"id": "run-1", "status": "running", "provider": "codex", "model": MODEL}

        self._install_execute(fake)
        resp = self.client.post(
            "/api/climate/work/repositories/live-processing-local/runs",
            json={**RUN_BODY, "execution_mode": "climate_assisted"},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        self.assertIn("application/json", resp.content_type)
        self.assertEqual(captured["repository_id"], "live-processing-local")
        self.assertNotIn("repository_id", captured["payload"])
        self.assertEqual((resp.get_json() or {}).get("run", {}).get("id"), "run-1")

    def test_workspace_runs_pops_repository_id_for_airix_and_direct(self):
        for mode in ("climate_assisted", "direct"):
            captured: dict = {}

            def fake(workspace, repository_id, **payload):
                captured["workspace"] = workspace
                captured["repository_id"] = repository_id
                captured["payload"] = payload
                captured["mode"] = payload.get("execution_mode")
                return {
                    "id": f"run-{mode}",
                    "status": "running",
                    "provider": "codex",
                    "model": payload.get("model"),
                    "execution_mode": payload.get("execution_mode"),
                }

            self._install_execute(fake)
            resp = self.client.post(
                "/api/climate/work/workspace/runs",
                json={**RUN_BODY, "execution_mode": mode, "model": MODEL},
            )
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
            self.assertIn("application/json", resp.content_type)
            self.assertNotIn("<!doctype", resp.get_data(as_text=True).lower())
            self.assertEqual(captured["repository_id"], "live-processing-local")
            self.assertNotIn("repository_id", captured["payload"])
            self.assertEqual(captured["payload"].get("model"), MODEL)
            self.assertEqual(captured["payload"].get("execution_mode"), mode)

    def test_unexpected_exception_returns_structured_json(self):
        def boom(workspace, repository_id, **payload):
            raise RuntimeError("unexpected climate failure")

        self._install_execute(boom)
        resp = self.client.post(
            "/api/climate/work/repositories/live-processing-local/runs",
            json={**RUN_BODY, "execution_mode": "direct"},
        )
        data = self._assert_json_error(resp, status=500, code="server_error")
        self.assertIn("unexpected climate failure", data["error"])

    def test_codex_host_spawn_exception_is_incomplete_cli_json(self):
        def boom(workspace, repository_id, **payload):
            raise RuntimeError(SPAWN)

        self._install_execute(boom)
        resp = self.client.post(
            "/api/climate/work/workspace/runs",
            json={**RUN_BODY, "execution_mode": "climate_assisted"},
        )
        data = self._assert_json_error(resp, status=409, code="incomplete_cli")
        self.assertIn("failed to spawn", data["error"])
        self.assertIn("codex-code-mode-host.exe", data["error"])

    def test_typeerror_duplicate_kwarg_is_invalid_request_json(self):
        def boom(workspace, repository_id, **payload):
            raise TypeError("ClimateService.execute() got multiple values for argument 'repository_id'")

        self._install_execute(boom)
        resp = self.client.post(
            "/api/climate/work/repositories/live-processing-local/runs",
            json=RUN_BODY,
        )
        data = self._assert_json_error(resp, status=400, code="invalid_request")
        self.assertEqual(data["error"], "Invalid coding request.")

    def test_secret_is_redacted_in_json_error(self):
        def boom(workspace, repository_id, **payload):
            raise RuntimeError("OPENAI_API_KEY=sk-secretvaluehere boom")

        self._install_execute(boom)
        resp = self.client.post(
            "/api/climate/work/workspace/runs",
            json=RUN_BODY,
        )
        data = self._assert_json_error(resp, status=500)
        self.assertNotIn("sk-secretvaluehere", data["error"])
        self.assertNotIn("sk-secretvaluehere", resp.get_data(as_text=True))

    def test_climate_coding_error_stays_json(self):
        def boom(workspace, repository_id, **payload):
            raise ClimateCodingError("Local repository unavailable", code="repository_unavailable")

        self._install_execute(boom)
        resp = self.client.post(
            "/api/climate/work/repositories/live-processing-local/runs",
            json=RUN_BODY,
        )
        self._assert_json_error(resp, status=409, code="repository_unavailable")

    def test_api_404_and_405_are_json(self):
        missing = self.client.get("/api/climate/work/does-not-exist")
        self._assert_json_error(missing, status=404, code="not_found")
        method = self.client.get("/api/climate/work/workspace/runs")
        self._assert_json_error(method, status=405, code="method_not_allowed")

    def test_chat_runs_pops_workspace_kwarg(self):
        captured: dict = {}

        def fake(workspace, **payload):
            captured["workspace"] = workspace
            captured["payload"] = payload
            return {"id": "chat-1", "status": "running"}

        self.app.config["CLIMATE"].execute_chat = fake
        resp = self.client.post(
            "/api/climate/work/chat/runs",
            json={"provider": "gemini", "model": "gemini-flash", "prompt": "hi", "workspace": "work"},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        self.assertEqual(captured["workspace"], "work")
        self.assertNotIn("workspace", captured["payload"])


class ClimateFrontendErrorContractTests(unittest.TestCase):
    def test_workspace_json_fetch_inspects_content_type(self):
        script = Path("static/js/climate.js").read_text(encoding="utf-8")
        fetch_fn = script.split("function jsonFetch", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("content-type", fetch_fn.lower())
        self.assertIn("JSON.parse", fetch_fn)
        self.assertNotIn("response.json()", fetch_fn)
        self.assertIn("html_response", fetch_fn)
        self.assertIn("diagnostics", fetch_fn)
        self.assertIn("Codex runtime could not start. Check the local Codex installation/runtime.", script)
        self.assertIn("retryFromMessage", script)
        self.assertIn("climate-assistant-retry", script)
        self.assertIn("redactClientText", script)

    def test_chat_json_fetch_inspects_content_type(self):
        script = Path("static/js/climate_chat.js").read_text(encoding="utf-8")
        fetch_fn = script.split("function jsonFetch", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("content-type", fetch_fn.lower())
        self.assertNotIn("res.json()", fetch_fn)
        self.assertIn("html_response", fetch_fn)
        self.assertIn("Codex runtime could not start. Check the local Codex installation/runtime.", script)


class ClassifySpawnErrorTests(unittest.TestCase):
    def test_spawn_keeps_technical_detail(self):
        classified = classify_provider_error(SPAWN)
        self.assertEqual(classified["code"], "incomplete_cli")
        self.assertIn("failed to spawn", classified["detail"])
        self.assertIn("codex-code-mode-host.exe", classified["detail"])


class CodingAdapterWrapTests(unittest.TestCase):
    def _adapter(self, start_side_effect):
        agent = mock.Mock()
        agent.start_run.side_effect = start_side_effect
        adapter = ClimateCodingAdapter(agent)
        adapter.availability = mock.Mock(
            return_value={"id": "codex", "state": "connected", "detail": "", "status": "Connected"}
        )
        return adapter

    def test_unexpected_start_run_becomes_climate_coding_error(self):
        adapter = self._adapter(RuntimeError(SPAWN))
        with self.assertRaises(ClimateCodingError) as ctx:
            adapter.execute(
                workspace="work",
                repository_id="work-repo",
                provider="codex",
                model=MODEL,
                prompt="hi",
            )
        self.assertEqual(ctx.exception.code, "incomplete_cli")
        self.assertIn("failed to spawn", str(ctx.exception))

    def test_agent_center_error_still_maps_code(self):
        adapter = self._adapter(AgentCenterError("Prompt is required", code="prompt_required"))
        with self.assertRaises(ClimateCodingError) as ctx:
            adapter.execute(
                workspace="work",
                repository_id="work-repo",
                provider="codex",
                model=MODEL,
                prompt="hi",
            )
        self.assertEqual(ctx.exception.code, "prompt_required")


if __name__ == "__main__":
    unittest.main()
