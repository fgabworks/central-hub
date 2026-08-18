"""Standalone AiriX · CLIMATE Chat — separate from the Workspace Assistant."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from hub.agent_center.db import AgentCenterDb
from hub.agent_center.store import AgentCenterStore
from hub.climate.coding import ClimateCodingAdapter, ClimateCodingError
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository


class ClimateChatPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_standalone_chat_pages_are_not_the_editor(self) -> None:
        work = self.client.get("/work/chat").get_data(as_text=True)
        personal = self.client.get("/personal/chat").get_data(as_text=True)
        for html in (work, personal):
            self.assertIn("AiriX · CLIMATE Chat", html)
            self.assertIn("Ask AiriX", html)
            self.assertIn("New Chat", html)
            self.assertIn('id="ax-chat-history"', html)
            self.assertIn('id="ax-prompt"', html)
            self.assertIn('id="ax-stop"', html)
            self.assertIn('id="ax-provider"', html)
            self.assertIn('id="ax-execution-mode"', html)
            self.assertIn("climate-mode-switch", html)
            self.assertIn("No repository", html)
            self.assertIn("climate_chat.js", html)
            self.assertRegex(html, r'"active_repository_id":\s*""')
            self.assertRegex(html, r'"chat":\s*\{[^}]*"default_provider"')
            self.assertRegex(html, r'"workspace":\s*\{[^}]*"default_provider"')
            self.assertNotIn("climate-monaco", html)
            self.assertNotIn("EXPLORER", html)
            self.assertNotRegex(html, r"AIza[0-9A-Za-z_-]{8,}")
            self.assertNotIn('"api_key"', html)
            self.assertNotIn("apiKey", html)

    def test_workspace_assistant_remains_on_climate_routes(self) -> None:
        html = self.client.get("/work/climate").get_data(as_text=True)
        self.assertIn('id="climate-monaco"', html)
        self.assertIn("EXPLORER", html)
        self.assertIn("AiriX · CLIMATE CHAT", html)
        self.assertNotIn('id="ax-chat"', html)

    def test_nav_lists_chat_and_code_workspace(self) -> None:
        work = self.client.get("/work").get_data(as_text=True)
        side = work[work.find('class="sidebar-nav"') : work.find('class="sidebar-actions"')]
        self.assertIn("CLIMATE Chat", side)
        self.assertIn("Code Workspace", side)
        self.assertIn("/work/chat", side)
        self.assertIn("/work/climate", side)
        self.assertLess(side.index(">Dashboard<"), side.index("CLIMATE Chat"))
        self.assertLess(side.index("CLIMATE Chat"), side.index(">Tasks<"))
        self.assertLess(side.index("Settings"), side.index("Code Workspace"))
        personal = self.client.get("/personal").get_data(as_text=True)
        p_side = personal[
            personal.find('class="sidebar-nav"') : personal.find('class="sidebar-actions"')
        ]
        self.assertIn("CLIMATE Chat", p_side)
        self.assertNotIn("Code Workspace", p_side)
        self.assertNotIn("/work/climate", p_side)
        self.assertNotIn("/personal/climate", p_side)

    def test_chat_models_endpoint_does_not_leak_keys(self) -> None:
        resp = self.client.get("/api/climate/work/providers/gemini/models")
        body = resp.get_data(as_text=True)
        self.assertNotIn("AIza", body)
        self.assertNotRegex(body, r"AIza[0-9A-Za-z_-]{8,}")


class ClimateChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentCenterStore(AgentCenterDb(Path(self.tmp.name) / "agent-center.db"))
        self.center = mock.Mock()
        self.center.store = self.store
        self.center.start_run.return_value = {
            "id": "run-chat",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-test-flash",
            "conversation_id": "conversation-chat",
            "prompt": "Reply with exactly: AiriX Gemini connection successful.",
            "repository_ids": [],
        }
        self.adapter = ClimateCodingAdapter(self.center)
        self.adapter.availability = mock.Mock(
            return_value={"id": "gemini", "state": "connected"}
        )
        repo = Repository(
            id="work-repo",
            name="Work",
            type="command",
            enabled=True,
            tags=["work"],
        )
        self.service = ClimateService(
            registry=Registry(repositories=[repo]),
            repository_workspace=mock.Mock(),
            coding=self.adapter,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_chat_does_not_require_a_repository(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="Reply with exactly: AiriX Gemini connection successful.",
            display_prompt="Reply with exactly: AiriX Gemini connection successful.",
        )
        payload = self.center.start_run.call_args.args[0]
        self.assertEqual(payload["repository_ids"], [])
        self.assertEqual(payload["files"], {})
        self.assertEqual(
            payload["display_prompt"],
            "Reply with exactly: AiriX Gemini connection successful.",
        )
        self.assertNotIn("Repository context:", payload["prompt"])
        self.assertEqual(result["id"], "run-chat")
        self.assertEqual(self.service._run_scope["run-chat"], ("work", ""))
        self.assertEqual(result["execution_mode"], "climate_assisted")
        self.assertIn("AiriX · CLIMATE Chat", payload["prompt"])
        self.assertFalse(payload.get("direct_provider_chat"))
        self.assertFalse(payload.get("inherit_repository_scope"))
        self.assertIsNone(payload.get("active_repository_id"))
        self.assertIsNone(payload.get("selected_repository_id"))

    def test_direct_no_repo_general_question_omits_evidence_gates(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="what is PMNP?",
            display_prompt="what is PMNP?",
            execution_mode="direct",
            repository_id="",
            include_repo_context=False,
        )
        payload = self.center.start_run.call_args.args[0]
        packed = str(payload["prompt"])
        self.assertEqual(result["execution_mode"], "direct")
        self.assertEqual(packed, "what is PMNP?")
        self.assertTrue(payload.get("direct_provider_chat"))
        self.assertTrue(payload.get("allow_general_knowledge"))
        self.assertFalse(payload.get("bounded_evidence_only"))
        self.assertFalse(payload.get("tool_runtime"))
        self.assertEqual(payload["repository_ids"], [])
        lowered = packed.lower()
        for phrase in (
            "cannot verify",
            "evidence packet",
            "no usable project evidence",
            "bounded repository context",
            "from selected context",
        ):
            self.assertNotIn(phrase, lowered)

    def test_direct_explicit_file_includes_selected_context(self) -> None:
        self.service.repository_workspace.preview.return_value = {
            "binary": False,
            "error": "",
            "content": "value = 1\n",
        }
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="summarize the attached file",
            repository_id="work-repo",
            include_repo_context=True,
            selected_files=["app.py"],
            execution_mode="direct",
        )
        payload = self.center.start_run.call_args.args[0]
        packed = str(payload["prompt"])
        self.assertEqual(result["execution_mode"], "direct")
        self.assertTrue(payload.get("direct_provider_chat"))
        self.assertIn("Attached context:", packed)
        self.assertIn("Selected file app.py:", packed)
        self.assertIn("value = 1", packed)
        self.assertIn("summarize the attached file", packed)
        self.assertNotIn("cannot verify", packed.lower())
        self.assertNotIn("AiriX · CLIMATE Chat", packed)
        self.assertEqual(payload["repository_ids"], [])

    def test_placeholder_repository_ids_do_not_become_scope(self) -> None:
        for placeholder in ("", "none", "null", "work", "vanta"):
            self.center.start_run.reset_mock()
            result = self.service.execute_chat(
                "work",
                provider="gemini",
                model="gemini-test-flash",
                prompt="hello",
                repository_id=placeholder,
                include_repo_context=True,
                execution_mode="direct",
            )
            payload = self.center.start_run.call_args.args[0]
            self.assertEqual(payload["repository_ids"], [], placeholder)
            self.assertFalse(payload.get("inherit_repository_scope"), placeholder)
            self.assertEqual(result["execution_mode"], "direct")

    def test_explicit_invalid_repository_is_rejected(self) -> None:
        with self.assertRaises(ClimateCodingError) as caught:
            self.service.execute_chat(
                "work",
                provider="gemini",
                model="gemini-test-flash",
                prompt="hello",
                repository_id="missing-repo",
                include_repo_context=True,
            )
        self.assertEqual(caught.exception.code, "not_found")
        self.center.start_run.assert_not_called()

    def test_surface_chat_hides_repository_conversations(self) -> None:
        general = self.store.create_conversation(profile_id="okarun", title="General")
        repo_chat = self.store.create_conversation(profile_id="okarun", title="Repo")
        self.store.create_run({
            "mode": "ask",
            "agent_id": "gemini",
            "agent_label": "Gemini",
            "model": "gemini-test-flash",
            "repository_ids": [],
            "prompt": "hello",
            "profile_id": "okarun",
            "conversation_id": general["id"],
        })
        self.store.create_run({
            "mode": "ask",
            "agent_id": "codex",
            "agent_label": "Codex",
            "model": "m",
            "repository_ids": ["work-repo"],
            "prompt": "explain file",
            "profile_id": "okarun",
            "conversation_id": repo_chat["id"],
        })
        rows = self.adapter.conversations(workspace="work", surface="chat")
        titles = [row["title"] for row in rows]
        self.assertIn("General", titles)
        self.assertNotIn("Repo", titles)
        scoped = self.adapter.conversations(workspace="work", repository_id="work-repo")
        scoped_titles = [row["title"] for row in scoped]
        self.assertIn("Repo", scoped_titles)
        self.assertNotIn("General", scoped_titles)
        with self.assertRaises(ClimateCodingError) as caught:
            self.adapter.conversation(repo_chat["id"], workspace="work", surface="chat")
        self.assertEqual(caught.exception.code, "not_found")

    def test_explicit_valid_repo_still_invokes_provider(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="hello",
            repository_id="work-repo",
            include_repo_context=True,
            execution_mode="direct",
        )
        payload = self.center.start_run.call_args.args[0]
        self.assertEqual(result["id"], "run-chat")
        self.assertTrue(result["provider_invoked"])
        self.assertEqual(payload["repository_ids"], [])
        self.assertFalse(payload.get("inherit_repository_scope"))
        self.assertIn("Explicit repository context selected", payload["prompt"])


class ClimateChatGeminiScopeTests(unittest.TestCase):
    """execute_chat → Agent Center start_run preview must not reject empty repo scope."""

    def setUp(self) -> None:
        import json
        import time

        from hub.agent_center.adapters.gemini_api import GeminiApiAdapter
        from hub.agent_center.gemini_client import GeminiClient
        from hub.agent_center.service import AgentCenterService
        from hub.repository_workspace.service import RepositoryWorkspaceService
        from hub.repository_workspace.settings import WorkspaceSettings
        from tests.test_gemini_provider import FakeResponse, FakeSession, settings

        self._json = json
        self._time = time
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        work = root / "work"
        work.mkdir()
        (work / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.registry = Registry([
            Repository(
                id="work-repo",
                name="Work",
                type="command",
                enabled=True,
                local_path=str(work),
                tags=["work"],
            )
        ])
        fake = FakeSession(
            model_response=FakeResponse(
                {
                    "models": [
                        {
                            "name": "models/gemini-test-flash",
                            "displayName": "Gemini Test Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                }
            ),
            stream_response=FakeResponse(
                lines=[
                    "data: "
                    + json.dumps(
                        {
                            "candidates": [
                                {"content": {"parts": [{"text": "provider-invoked"}]}}
                            ],
                            "usageMetadata": {
                                "promptTokenCount": 4,
                                "candidatesTokenCount": 2,
                                "totalTokenCount": 6,
                            },
                        }
                    )
                ]
            ),
        )
        gemini_settings = settings()
        gemini = GeminiApiAdapter(settings=gemini_settings)
        gemini.client = GeminiClient(gemini_settings, session=fake)
        self.fake = fake
        store = AgentCenterStore(AgentCenterDb(root / "agent-center.db"))
        self.center = AgentCenterService(
            self.registry, store=store, adapters=[gemini], timeout_seconds=15
        )
        self.service = ClimateService(
            self.registry,
            RepositoryWorkspaceService(WorkspaceSettings()),
            ClimateCodingAdapter(self.center),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _wait(self, run_id: str) -> dict:
        deadline = self._time.time() + 12
        current = {}
        while self._time.time() < deadline:
            current = self.service.result("work", run_id)
            if current.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
                return current
            self._time.sleep(0.05)
        return current

    def _last_gemini_body(self) -> dict:
        self.assertTrue(self.fake.post_calls)
        return self.fake.post_calls[-1][1]["json"]

    def _last_system_instruction(self) -> str:
        parts = (self._last_gemini_body().get("systemInstruction") or {}).get("parts") or []
        return str((parts[0] or {}).get("text") or "") if parts else ""

    def _last_user_text(self) -> str:
        contents = self._last_gemini_body().get("contents") or []
        return str((((contents[-1] or {}).get("parts") or [{}])[0]).get("text") or "")

    def test_direct_and_airix_no_repo_invoke_provider(self) -> None:
        for mode in ("direct", "climate_assisted"):
            result = self.service.execute_chat(
                "work",
                provider="gemini",
                model="gemini-test-flash",
                prompt="Say hi",
                execution_mode=mode,
                repository_id="",
                include_repo_context=False,
            )
            self.assertNotIn("Invalid repository scope", str(result.get("error") or ""))
            self.assertTrue(result.get("provider_invoked"))
            finished = self._wait(result["id"])
            self.assertEqual(finished.get("status"), "completed", finished)
            self.assertIn("provider-invoked", finished.get("answer") or "")
            self.assertEqual(finished.get("repository_id") or "", "")

    def test_direct_no_repo_general_question_omits_evidence_gates(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="what is PMNP?",
            execution_mode="direct",
            repository_id="",
            include_repo_context=False,
        )
        finished = self._wait(result["id"])
        self.assertEqual(finished.get("status"), "completed", finished)
        run = self.center.store.get_run(result["id"]) or {}
        packed = str(run.get("packed_prompt") or "")
        system = self._last_system_instruction()
        user = self._last_user_text()
        self.assertEqual(packed, "what is PMNP?")
        self.assertEqual(user, "what is PMNP?")
        blob = "\n".join([packed, system, user, str(finished.get("answer") or "")]).lower()
        for phrase in (
            "cannot verify",
            "evidence packet",
            "no usable project evidence",
            "bounded repository context",
            "from selected context",
        ):
            self.assertNotIn(phrase, blob)
        self.assertNotIn("You are AiriX", system)
        self.assertIn("Direct mode", system)

    def test_direct_explicit_file_includes_selected_context(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="summarize the attached file",
            execution_mode="direct",
            repository_id="work-repo",
            include_repo_context=True,
            selected_files=["app.py"],
        )
        finished = self._wait(result["id"])
        self.assertEqual(finished.get("status"), "completed", finished)
        run = self.center.store.get_run(result["id"]) or {}
        packed = str(run.get("packed_prompt") or "")
        user = self._last_user_text()
        system = self._last_system_instruction()
        self.assertIn("Attached context:", packed)
        self.assertIn("Selected file app.py:", packed)
        self.assertIn("value = 1", packed)
        self.assertIn("summarize the attached file", packed)
        self.assertIn("value = 1", user)
        self.assertNotIn("cannot verify", packed.lower())
        self.assertNotIn("bounded repository context", system.lower())
        self.assertNotIn("AiriX · CLIMATE Chat", packed)

    def test_airix_still_uses_climate_orchestration_and_evidence_rules(self) -> None:
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-test-flash",
            prompt="what is PMNP?",
            execution_mode="climate_assisted",
            repository_id="",
            include_repo_context=False,
        )
        finished = self._wait(result["id"])
        self.assertEqual(finished.get("status"), "completed", finished)
        run = self.center.store.get_run(result["id"]) or {}
        packed = str(run.get("packed_prompt") or "")
        system = self._last_system_instruction()
        self.assertIn("AiriX · CLIMATE Chat", packed)
        self.assertIn("Use only the user prompt and any supplied bounded context.", packed)
        self.assertIn("what is PMNP?", packed)
        self.assertIn("You are AiriX", system)
        self.assertIn("bounded repository context", system.lower())
        self.assertNotEqual(packed, "what is PMNP?")
        self.assertIn("cannot verify from selected context", packed.lower())

    def test_chat_bootstrap_does_not_inherit_vanta_repo(self) -> None:
        boot = self.service.bootstrap("work", surface="chat")
        self.assertEqual(boot.get("active_repository_id"), "")
        workspace = self.service.bootstrap("work")
        if workspace.get("active_repository_id"):
            self.assertEqual(workspace.get("active_repository_id"), "work-repo")


class ClimateChatUiContractTests(unittest.TestCase):
    def test_chat_js_requires_exact_model_and_stop(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate_chat.js").read_text(encoding="utf-8")
        self.assertIn("/chat/runs", script)
        self.assertIn("surface=chat", script)
        self.assertIn("display_prompt", script)
        self.assertIn("Select exact model", script)
        self.assertIn("chatSurfaceDefaults", script)
        self.assertIn("preferredChatProvider", script)
        self.assertIn("preferredChatModel", script)
        self.assertIn("listedModelOrAuto", script)
        self.assertNotIn("workspaceSurfaceDefaults", script)
        self.assertNotIn('row.id === "gemini" && row.state === "connected"', script)
        self.assertIn('value="" disabled', script)
        self.assertIn("enhanceChatSelects", script)
        self.assertIn("ClimateSelect", script)
        self.assertIn("requestStop", script)
        self.assertIn("execution_mode: currentChatMode()", script)
        self.assertIn("currentChatRepo()", script)
        self.assertIn("no-repository", script)
        self.assertIn("applyChatMode", script)
        self.assertIn("ax-execution-mode", script)
        self.assertIn("ax-repo-context", script)
        self.assertNotIn("include_repo_context: true", script)
        self.assertIn("AiriX is thinking", script)
        self.assertIn(" is thinking", script)
        self.assertIn("currentThinkingLabel", script)
        self.assertIn("Response cancelled", script)
        self.assertIn("You stopped this request.", script)
        self.assertIn("Request failed", script)
        self.assertIn("ax-run-spinner", script)
        self.assertIn("ax-run-typing", script)
        self.assertIn("is-streaming", script)
        self.assertIn("setSessionBusy", script)
        self.assertIn("displayTextFromRun", script)
        self.assertIn("diagnosticsFromRun", script)
        self.assertIn("streamTextFromLogs", script)
        self.assertNotIn("run.answer || run.logs", script)
        self.assertNotIn("success banner", script)
        template = (root / "templates" / "climate_chat.html").read_text(encoding="utf-8")
        self.assertIn("Ask AiriX", template)
        self.assertIn('id="ax-stop"', template)
        self.assertIn(">Stop<", template)
        self.assertNotIn("■ Stop", template)
        self.assertIn("climate_select.js", template)
        self.assertIn("disabled selected>Select exact model", template)
        self.assertNotIn("climate-monaco", template)
        self.assertNotIn("Repository Context", template)
        select_js = (root / "static" / "js" / "climate_select.js").read_text(encoding="utf-8")
        self.assertIn("climate-dd-menu", select_js)
        self.assertIn("is-portal", select_js)
        self.assertIn("is-placeholder", select_js)
        css = (root / "static" / "css" / "climate_chat.css").read_text(encoding="utf-8")
        self.assertIn("color-scheme: dark", css)
        self.assertIn(".ax-pill .climate-dd", css)
        self.assertIn(".ax-run-spinner", css)
        self.assertIn(".ax-run-typing", css)
        self.assertIn(".ax-run-cancelled-icon", css)
        self.assertIn(".ax-chat-controls.is-busy", css)
        self.assertIn("@keyframes ax-spin", css)
        self.assertIn("@keyframes ax-dot", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_chat_processing_states_match_approved_preview(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate_chat.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "climate_chat.css").read_text(encoding="utf-8")
        self.assertIn('return "thinking"', script)
        self.assertIn('return "streaming"', script)
        self.assertIn('return "completed"', script)
        self.assertIn('return "cancelled"', script)
        self.assertIn('return "error"', script)
        self.assertIn("This may take a few seconds.", script)
        self.assertIn("is-busy", script)
        self.assertIn("stopBtn.hidden = idle", script)
        self.assertIn('currentChatMode() !== "direct"', script)
        self.assertIn("streamTextFromLogs", script)
        self.assertIn(".ax-run-status.is-thinking", css)
        self.assertIn(".ax-run-cancelled-icon", css)
        self.assertIn(".ax-run-error-icon", css)


class ClimateChatLiveGeminiTests(unittest.TestCase):
    def _connected_gemini_models(self, client):
        models = client.get("/api/climate/work/providers/gemini/models")
        body = models.get_json(silent=True) or {}
        ids = [str(row) for row in (body.get("models") or []) if str(row).strip()]
        if models.status_code != 200 or not ids:
            self.skipTest("Gemini is not connected")
        chat = [
            row
            for row in ids
            if "computer-use" not in row
            and "embedding" not in row
            and "image" not in row
            and "tts" not in row
        ]
        preferred_order = (
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-3-flash",
        )
        ordered = [row for row in preferred_order if row in chat]
        rest = [
            row
            for row in chat
            if row not in ordered and not row.startswith("gemini-2.5-")
        ]
        return ordered + rest or chat or ids

    def _transient_provider_error(self, current: dict) -> bool:
        error = str(current.get("error") or "")
        return "503" in error or "UNAVAILABLE" in error or "high demand" in error.lower() or "currently experiencing" in error.lower()

    def _post_direct_chat(self, client, *, prompt: str, model: str) -> dict:
        import time

        posted = client.post(
            "/api/climate/work/chat/runs",
            json={
                "provider": "gemini",
                "model": model,
                "prompt": prompt,
                "display_prompt": prompt,
                "execution_mode": "direct",
                "repository_id": "",
                "include_repo_context": False,
            },
        )
        data = posted.get_json(silent=True) or {}
        self.assertLess(posted.status_code, 400, data)
        run_id = str((data.get("run") or {}).get("id") or "")
        self.assertTrue(run_id, data)
        current: dict = {}
        deadline = time.time() + 70
        while time.time() < deadline:
            polled = client.get(f"/api/climate/work/runs/{run_id}")
            current = (polled.get_json(silent=True) or {}).get("run") or {}
            if current.get("status") in {"completed", "failed", "cancelled", "unavailable"}:
                break
            time.sleep(0.4)
        current["_http_status"] = posted.status_code
        current["_post"] = data
        return current

    def _require_live_completion(self, last: dict) -> dict:
        status = str(last.get("status") or "")
        if status != "completed":
            if status == "running" or self._transient_provider_error(last):
                self.skipTest(
                    "Gemini temporarily unavailable: "
                    + str(last.get("error") or status)[:180]
                )
        self.assertEqual(last.get("status"), "completed", last)
        return last

    def test_direct_no_repository_invokes_gemini(self) -> None:
        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        last = {}
        for model in self._connected_gemini_models(client)[:3]:
            last = self._post_direct_chat(
                client, prompt="Reply with exactly: no-repo-ok", model=model
            )
            if last.get("status") == "completed":
                break
            if not self._transient_provider_error(last):
                break
        last = self._require_live_completion(last)
        self.assertTrue((last.get("answer") or "").strip())
        self.assertNotIn("Invalid repository scope", str(last.get("error") or ""))

    def test_direct_what_is_pmnp_answers_without_evidence_warning(self) -> None:
        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        last = {}
        for model in self._connected_gemini_models(client)[:3]:
            last = self._post_direct_chat(client, prompt="what is PMNP?", model=model)
            if last.get("status") == "completed":
                break
            if not self._transient_provider_error(last):
                break
        last = self._require_live_completion(last)
        answer = str(last.get("answer") or "")
        self.assertTrue(answer.strip(), last)
        lowered = answer.lower()
        for phrase in (
            "cannot verify",
            "evidence packet",
            "no usable project evidence",
            "no repository",
            "no project evidence",
        ):
            self.assertNotIn(phrase, lowered, answer)


if __name__ == "__main__":
    unittest.main()
