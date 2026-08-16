"""Manual CLIMATE vs Direct Codex token-efficiency benchmark."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hub.climate.service import ClimateService
from hub.climate.token_efficiency import (
    COMMIT_CHANGED,
    PACKET_MARKERS,
    STATUS_CANCELLED,
    STATUS_MEASURED,
    STATUS_MEASURING,
    STATUS_NOT_COMPARABLE,
    STATUS_NOT_MEASURED,
    STATUS_UNAVAILABLE,
    TokenEfficiencyService,
    comparison_from_totals,
    extract_user_prompt,
    usage_from_provider,
)
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate import FakeCodingAdapter


ANC_PROMPT = (
    "Give me the logic of the ANC.\n"
    "Cite the exact implementation files/functions.\n"
    "Do not edit anything."
)


class StaticCodexAdapter:
    def __init__(self, exe: str = "codex") -> None:
        self.exe = exe
        self.argv_calls: list[dict] = []

    def resolve_executable(self) -> str:
        return self.exe

    def _detect_version(self, exe: str) -> str:
        return "test-codex"

    def build_argv(
        self,
        *,
        mode: str,
        prompt: str,
        model: str,
        cwd: str,
        prompt_file: str = "",
        provider_session_id: str = "",
        persist_session: bool = False,
    ) -> list[str]:
        self.argv_calls.append(
            {
                "mode": mode,
                "prompt": prompt,
                "model": model,
                "cwd": cwd,
                "prompt_file": prompt_file,
                "provider_session_id": provider_session_id,
                "persist_session": persist_session,
            }
        )
        argv = [self.exe, "-C", cwd, "--sandbox", "read-only", "exec", "--json"]
        if provider_session_id:
            argv.extend(["resume", "--json"])
        if not persist_session:
            argv.append("--ephemeral")
        if model:
            argv.extend(["--model", model])
        argv.append("-" if prompt_file else prompt)
        return argv


class FakeProc:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        usage = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 500000,
                "output_tokens": 154900,
                "total_tokens": 654900,
                "cached_input_tokens": 12000,
            },
        }
        self.stdout = io.StringIO(json.dumps(usage) + "\n")
        self.returncode = 0

    def poll(self):
        return 0 if self.stdout.tell() >= len(self.stdout.getvalue()) else None

    def terminate(self):
        self.returncode = 1


class MissingCachedProc(FakeProc):
    def __init__(self, argv, **kwargs):
        self.argv = argv
        usage = {
            "type": "turn.completed",
            "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
        }
        self.stdout = io.StringIO(json.dumps(usage) + "\n")
        self.returncode = 0


class NegativeSavingsProc(FakeProc):
    def __init__(self, argv, **kwargs):
        self.argv = argv
        usage = {
            "type": "turn.completed",
            "usage": {"input_tokens": 70, "output_tokens": 10, "total_tokens": 80},
        }
        self.stdout = io.StringIO(json.dumps(usage) + "\n")
        self.returncode = 0


class BlockingProc:
    def __init__(self, argv, **kwargs):
        self.argv = argv
        self._stop = threading.Event()
        self.stdout = self
        self.returncode = None

    def readline(self):
        self._stop.wait(30)
        return ""

    def poll(self):
        return None if not self._stop.is_set() else 1

    def terminate(self):
        self.returncode = 1
        self._stop.set()


def _git_init(root: Path) -> str:
    for args in (
        ("init",),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("add", "."),
        ("commit", "-m", "init"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, shell=False, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    return sha.stdout.strip()


class TokenEfficiencyUnitTests(unittest.TestCase):
    def test_missing_usage_fields_stay_unavailable(self):
        usage = usage_from_provider({"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 2)
        self.assertEqual(usage["total_tokens"], 12)
        self.assertIsNone(usage["cached_input_tokens"])
        self.assertIsNone(usage_from_provider({})["total_tokens"])
        self.assertEqual(usage_from_provider({})["source"], "unavailable")

    def test_increase_headline(self):
        row = comparison_from_totals(100, 80)
        self.assertEqual(row["difference"], 20)
        self.assertEqual(row["percent"], 25.0)
        self.assertEqual(row["relative"], 1.25)
        self.assertEqual(row["tone"], "increase")
        self.assertEqual(row["primary"], "Used 20 more tokens (+25.0%)")
        self.assertEqual(row["secondary"], "CLIMATE used 1.25× Direct provider usage")
        self.assertNotIn("Saved", row["primary"])
        self.assertNotIn("savings", row["primary"].lower())

    def test_savings_headline(self):
        row = comparison_from_totals(493229, 654900)
        self.assertEqual(row["difference"], -161671)
        self.assertEqual(row["tone"], "savings")
        self.assertEqual(row["primary"], "Saved 161,671 tokens (-24.7%)")
        self.assertIn("fewer provider tokens", row["secondary"])

    def test_measured_pnc_ratio(self):
        row = comparison_from_totals(708792, 280908)
        self.assertEqual(row["difference"], 427884)
        self.assertEqual(row["percent"], 152.3)
        self.assertEqual(row["relative"], 2.52)
        self.assertEqual(row["primary"], "Used 427,884 more tokens (+152.3%)")
        self.assertEqual(row["secondary"], "CLIMATE used 2.52× Direct provider usage")

    def test_extract_user_prompt_from_packet(self):
        packed = (
            "CLIMATE context packet (ASK).\n"
            f"Task:\n{ANC_PROMPT}\n"
            "Confidence: high\n"
            "Repository access: the provider starts at the approved repository root\n"
        )
        self.assertEqual(extract_user_prompt(packed), ANC_PROMPT)

    def test_public_rebuilds_legacy_increase_wording(self):
        svc = TokenEfficiencyService(persist_root=Path(tempfile.mkdtemp()))
        record = {
            "status": STATUS_MEASURED,
            "snapshot": {
                "provider": "codex",
                "user_prompt": ANC_PROMPT,
                "session_reused": True,
                "session_fresh": False,
                "climate_usage": {"total_tokens": 708792, "input_tokens": 702221, "cached_input_tokens": 557056, "output_tokens": 6571, "source": "provider"},
                "source_candidates": 17,
                "files_inspected": 2,
            },
            "direct": {"usage": {"total_tokens": 280908, "source": "provider"}, "success": True, "files_inspected": 4},
            "comparison": {"difference": -427884, "percent": -152.3, "headline": "Measured savings: old"},
        }
        public = svc.public(record)
        self.assertEqual(public["comparison"]["tone"], "increase")
        self.assertEqual(public["comparison"]["primary"], "Used 427,884 more tokens (+152.3%)")
        self.assertIn("resumed", public["comparability_note"].lower())
        self.assertEqual(public["climate"]["source_candidates"], 17)
        self.assertEqual(public["climate"]["files_inspected"], 2)


class TokenEfficiencyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.sha = _git_init(self.repo)
        self.persist = self.root / "runs"
        self.popen_calls: list[list[str]] = []
        self.adapter = StaticCodexAdapter()

    def tearDown(self):
        self.temp.cleanup()

    def _service(self, popen=FakeProc) -> TokenEfficiencyService:
        def wrapped(argv, **kwargs):
            self.popen_calls.append(list(argv))
            return popen(argv, **kwargs)

        return TokenEfficiencyService(persist_root=self.persist, popen=wrapped)

    def _snapshot(self, svc: TokenEfficiencyService, run_id: str = "run-anc", **extra):
        kwargs = {
            "run_id": run_id,
            "user_prompt": ANC_PROMPT,
            "repository_id": "work-repo",
            "repository_path": str(self.repo),
            "provider": "codex",
            "model": "gpt-5.4",
            "read_only": True,
            "codex_executable": "codex",
            "codex_version": "test-codex",
            "reasoning_config": {"sandbox": "read-only", "json": True, "model": "gpt-5.4"},
            "context_packet_chars": 1800,
            "context_tokens_est": 527,
            "source_candidates": 4,
            "persist": True,
        }
        kwargs.update(extra)
        record = svc.capture_snapshot(**kwargs)
        return svc.update_climate_metrics(
            run_id,
            usage={"input_tokens": 400000, "output_tokens": 93229, "total_tokens": 493229, "session_reused": True},
            started_at="2026-08-14T00:00:00+00:00",
            finished_at="2026-08-14T00:02:00+00:00",
            tool_activity=[{"type": "command_execution", "name": "rg", "detail": "app.py"}],
            persist=True,
        ) or record

    def _wait(self, svc: TokenEfficiencyService, run_id: str, timeout: float = 5.0):
        thread = svc._threads.get(run_id)
        if thread:
            thread.join(timeout)
            self.assertFalse(thread.is_alive())

    def test_direct_is_manual_only(self):
        svc = self._service()
        record = self._snapshot(svc)
        self.assertEqual(record["status"], STATUS_NOT_MEASURED)
        self.assertEqual(self.popen_calls, [])
        public = svc.public(record)
        self.assertEqual(public["status"], "Not measured")
        self.assertIsNone(public["direct"])
        self.assertEqual(public["compare_label"], "Compare with Direct")
        self.assertEqual(public["execution_mode"], "climate_assisted")

    def test_climate_run_is_not_rerun(self):
        svc = self._service()
        self._snapshot(svc)
        record = svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        finished = svc.load("run-anc")
        self.assertEqual(finished["status"], STATUS_MEASURED)
        self.assertEqual(finished["snapshot"]["user_prompt"], ANC_PROMPT)
        self.assertEqual(finished["snapshot"]["climate_usage"]["total_tokens"], 493229)
        self.assertEqual(len(self.adapter.argv_calls), 1)

    def test_direct_argv_is_fresh_ephemeral(self):
        svc = self._service()
        self._snapshot(svc)
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        argv = self.popen_calls[0]
        self.assertEqual(argv[0], "codex")
        self.assertIn("-C", argv)
        self.assertEqual(argv[argv.index("-C") + 1], str(self.repo))
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn("--json", argv)
        self.assertIn("--ephemeral", argv)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.4")
        self.assertIn("-", argv)
        self.assertNotIn("resume", argv)
        call = self.adapter.argv_calls[0]
        self.assertEqual(call["provider_session_id"], "")
        self.assertFalse(call["persist_session"])
        prompt = (self.persist / "run-anc" / "direct_prompt.txt").read_text(encoding="utf-8")
        self.assertEqual(prompt, ANC_PROMPT)
        for marker in PACKET_MARKERS:
            self.assertNotIn(marker, prompt)

    def test_packet_in_direct_prompt_is_rejected(self):
        svc = self._service()
        self._snapshot(svc, user_prompt="CLIMATE context packet (ASK).\nTask:\n" + ANC_PROMPT)
        record = svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(record["status"], "Failed")
        self.assertIn("CLIMATE packet", record["reason"])

    def test_assisted_comparison_allows_resolver_packet(self):
        svc = self._service()
        self._snapshot(svc, execution_mode="direct")
        packet = "CLIMATE context packet (ASK).\nTask:\n" + ANC_PROMPT + "\n"
        record = svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
            comparison_prompt=packet,
            comparison_side="assisted",
        )
        self._wait(svc, "run-anc")
        finished = svc.load("run-anc")
        self.assertIn(finished["status"], {STATUS_MEASURING, STATUS_MEASURED})
        self.assertTrue(finished.get("assisted"))
        prompt = (self.persist / "run-anc" / "assisted_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("CLIMATE context packet", prompt)
        public = svc.public(finished)
        self.assertEqual(public["execution_mode"], "direct")
        self.assertEqual(public["compare_label"], "Compare with CLIMATE")
        self.assertEqual(public["direct"]["usage"]["total_tokens"], 493229)

    def test_changed_commit_blocks_comparison(self):
        svc = self._service()
        self._snapshot(svc)
        with mock.patch("hub.climate.token_efficiency.git_head", return_value="b" * 40):
            record = svc.start_direct(
                "run-anc",
                adapter=self.adapter,
                repository_id="work-repo",
                repository_path=str(self.repo),
            )
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(record["status"], STATUS_NOT_COMPARABLE)
        self.assertEqual(record["reason"], COMMIT_CHANGED)
        self.assertEqual(svc.public(record)["status"], "Not comparable")

    def test_missing_commit_is_unavailable(self):
        svc = self._service()
        record = svc.reconstruct_from_agent_run(
            {
                "id": "a4d4e4bdd4384385bd0151615fba53d9",
                "agent_id": "codex",
                "model": "gpt-5.4",
                "packed_prompt": (
                    "CLIMATE context packet (ASK).\n"
                    f"Task:\n{ANC_PROMPT}\n"
                    "Confidence: high\n"
                    "Repository access: bounded packet only.\n"
                ),
                "repository_ids": ["work-repo"],
                "usage": {
                    "input_tokens": 400000,
                    "output_tokens": 93229,
                    "total_tokens": 493229,
                    "session_reused": True,
                },
                "status": "completed",
            },
            repository_path=str(self.repo),
        )
        self.assertEqual(record["status"], STATUS_UNAVAILABLE)
        self.assertIn("commit SHA", record["reason"])
        self.assertEqual(record["snapshot"]["commit_sha"], "")
        self.assertEqual(record["snapshot"]["user_prompt"], ANC_PROMPT)
        blocked = svc.start_direct(
            "a4d4e4bdd4384385bd0151615fba53d9",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(blocked["status"], STATUS_UNAVAILABLE)

    def test_cancel_stops_direct(self):
        svc = self._service(popen=BlockingProc)
        self._snapshot(svc)
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        for _ in range(50):
            if "run-anc" in svc._procs:
                break
            time.sleep(0.05)
        record = svc.cancel("run-anc")
        self._wait(svc, "run-anc")
        self.assertEqual(record["status"], STATUS_CANCELLED)
        self.assertEqual(svc.load("run-anc")["status"], STATUS_CANCELLED)

    def test_git_unchanged_before_and_after(self):
        svc = self._service()
        self._snapshot(svc)
        before = (self.repo / "app.py").read_text(encoding="utf-8")
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        finished = svc.load("run-anc")
        self.assertTrue(finished["direct"]["git_unchanged"])
        self.assertEqual((self.repo / "app.py").read_text(encoding="utf-8"), before)
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo,
            check=True,
            shell=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(porcelain.stdout.strip(), "")

    def test_negative_savings_persists(self):
        svc = self._service(popen=NegativeSavingsProc)
        self._snapshot(
            svc,
        )
        svc.update_climate_metrics(
            "run-anc",
            usage={"input_tokens": 90, "output_tokens": 10, "total_tokens": 100},
            persist=True,
        )
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        finished = svc.load("run-anc")
        self.assertEqual(finished["status"], STATUS_MEASURED)
        self.assertEqual(finished["comparison"]["tone"], "increase")
        self.assertEqual(finished["comparison"]["primary"], "Used 20 more tokens (+25.0%)")
        public = svc.public(finished)
        self.assertEqual(public["comparison"]["tone"], "increase")
        self.assertNotIn("Saved", public["comparison"]["primary"])

    def test_measured_result_does_not_rerun_on_reload(self):
        svc = self._service()
        self._snapshot(svc)
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        self.assertEqual(len(self.popen_calls), 1)
        reloaded = TokenEfficiencyService(persist_root=self.persist, popen=FakeProc)
        loaded = reloaded.load("run-anc")
        self.assertEqual(loaded["status"], STATUS_MEASURED)
        again = reloaded.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self.assertEqual(again["status"], STATUS_MEASURED)
        self.assertEqual(len(self.popen_calls), 1)
        public = reloaded.public(loaded)
        blob = json.dumps(public)
        self.assertNotIn("direct_benchmark.jsonl", blob)
        self.assertNotIn("turn.completed", blob)

    def test_missing_cached_tokens_are_unavailable_not_zero(self):
        svc = self._service(popen=MissingCachedProc)
        self._snapshot(svc)
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        usage = svc.load("run-anc")["direct"]["usage"]
        self.assertEqual(usage["total_tokens"], 100)
        self.assertIsNone(usage["cached_input_tokens"])
        self.assertIsNone(svc.public(svc.load("run-anc"))["direct"]["usage"]["cached_input_tokens"])

    def test_jsonl_stays_on_disk_only(self):
        svc = self._service()
        self._snapshot(svc)
        svc.start_direct(
            "run-anc",
            adapter=self.adapter,
            repository_id="work-repo",
            repository_path=str(self.repo),
        )
        self._wait(svc, "run-anc")
        jsonl = self.persist / "run-anc" / "direct_benchmark.jsonl"
        self.assertTrue(jsonl.is_file())
        self.assertIn("turn.completed", jsonl.read_text(encoding="utf-8"))
        public = svc.public(svc.load("run-anc"))
        self.assertNotIn("raw_lines", public)
        self.assertNotIn("jsonl", public)


class ClimateServiceTokenEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.work = root / "work"
        self.work.mkdir()
        (self.work / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.work / "AGENTS.md").write_text("# Agents\nUse repository files.\n", encoding="utf-8")
        (self.work / "SKILLS.md").write_text("# Skills\n\n## ANC Binary\nExplain ANC.\n", encoding="utf-8")
        _git_init(self.work)
        self.registry = Registry(
            [Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.work))]
        )
        self.coding = FakeCodingAdapter()
        self.service = ClimateService(
            self.registry,
            RepositoryWorkspaceService(WorkspaceSettings()),
            self.coding,
        )
        self.service.token_efficiency = TokenEfficiencyService(persist_root=root / "runs")

    def tearDown(self):
        self.temp.cleanup()

    def test_execute_does_not_start_direct(self):
        started = []
        original = self.service.token_efficiency.start_direct

        def wrapped(*args, **kwargs):
            started.append(1)
            return original(*args, **kwargs)

        self.service.token_efficiency.start_direct = wrapped  # type: ignore[method-assign]
        run = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="gpt-5.4",
            prompt=ANC_PROMPT,
            current_file="app.py",
            selected_files=[],
        )
        self.assertEqual(started, [])
        self.assertEqual(len(self.coding.calls), 1)
        te = run.get("token_efficiency") or {}
        self.assertEqual(te.get("status"), STATUS_NOT_MEASURED)
        record = self.service.token_efficiency.load(run["id"])
        self.assertEqual(record["snapshot"]["user_prompt"], ANC_PROMPT)
        self.assertNotIn("CLIMATE context packet", record["snapshot"]["user_prompt"])
        self.assertIn("CLIMATE context packet", self.coding.calls[0]["prompt"])

        self.coding._answers[run["id"]] = {
            "id": run["id"],
            "status": "completed",
            "provider": "codex",
            "agent_id": "codex",
            "model": "gpt-5.4",
            "answer": "ANC logic lives in app.py.",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            "logs": "[turn.completed]",
        }
        result = self.service.result("work", run["id"])
        self.assertEqual(started, [])
        self.assertEqual(result["token_efficiency"]["status"], STATUS_NOT_MEASURED)
        self.assertEqual(result["token_efficiency"]["climate"]["total"], 18)
        self.assertEqual(len(self.coding.calls), 1)

    def test_evaluate_does_not_rerun_climate(self):
        run = self.service.execute(
            "work",
            "work-repo",
            provider="codex",
            model="gpt-5.4",
            prompt=ANC_PROMPT,
            current_file="app.py",
            selected_files=[],
        )
        self.coding._answers[run["id"]] = {
            "id": run["id"],
            "status": "completed",
            "provider": "codex",
            "agent_id": "codex",
            "model": "gpt-5.4",
            "answer": "ANC logic lives in app.py.",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        }
        self.service.result("work", run["id"])
        adapter = StaticCodexAdapter()
        self.coding.agent_center = type(
            "AC",
            (),
            {"connections": type("C", (), {"adapters": {"codex": adapter}})(), "store": None},
        )()
        pops: list[list[str]] = []

        def popen(argv, **kwargs):
            pops.append(list(argv))
            return FakeProc(argv, **kwargs)

        self.service.token_efficiency._popen = popen
        payload = self.service.evaluate_token_efficiency("work", run["id"])
        thread = self.service.token_efficiency._threads.get(run["id"])
        if thread:
            thread.join(5)
        self.assertEqual(len(self.coding.calls), 1)
        self.assertEqual(len(pops), 1)
        self.assertIn("--ephemeral", pops[0])
        self.assertNotIn("resume", pops[0])
        self.assertIn(payload["status"], {STATUS_MEASURING, STATUS_MEASURED})
        finished = self.service.token_efficiency_status("work", run["id"])
        self.assertEqual(finished["status"], STATUS_MEASURED)

    def test_non_codex_execute_has_no_benchmark(self):
        run = self.service.execute(
            "work",
            "work-repo",
            provider="cursor-agent",
            model="cursor-exact",
            prompt="review",
            current_file="app.py",
            selected_files=[],
        )
        self.assertNotIn("token_efficiency", run)


class TokenEfficiencyUiContractTests(unittest.TestCase):
    def test_ui_keeps_jsonl_out_of_chat(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "climate.css").read_text(encoding="utf-8")
        self.assertIn("Compare with Direct", script)
        self.assertIn("Compare with CLIMATE", script)
        self.assertNotIn("Evaluate Token Savings", script)
        self.assertIn("data-te-action", script)
        self.assertIn("Unavailable", script)
        self.assertNotIn("direct_benchmark.jsonl", script)
        self.assertIn("climate-token-efficiency", css)
        evaluate = script.split("function evaluateTokenSavings", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("/evaluate", evaluate)
        hydrate = script.split("function hydrateTokenEfficiency", 1)[1].split("\n  function ", 1)[0]
        self.assertNotIn("/evaluate", hydrate)

    def test_result_colors_and_labels(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "climate.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "climate.css").read_text(encoding="utf-8")
        self.assertIn("CLIMATE run total", script)
        self.assertIn("Benchmark details", script)
        self.assertIn("↳ Cached portion", script)
        self.assertIn("Candidate Sources", script)
        self.assertIn("Files actually inspected", script)
        self.assertIn("Token comparison unavailable", script)
        self.assertIn("● Measuring", script)
        self.assertIn("Fresh read-only benchmark running", script)
        self.assertIn("is-save", css)
        self.assertIn("is-cost", css)
        self.assertIn("is-wait", css)
        self.assertIn("is-muted", css)
        self.assertNotRegex(css, r"\.climate-token-efficiency\s*\{[^}]*background:\s*#(7ddea0|f08b87)")
        measured = script.split("function renderTokenEfficiencyMeasured", 1)[1].split("\n  function ", 1)[0]
        self.assertEqual(measured.count("cmp.primary"), 1)
        self.assertNotIn("✓ Measured comparison", measured)
        self.assertIn("is-subset", script)
        details = script.split("function renderTokenEfficiencyDetails", 1)[1].split("\n  function ", 1)[0]
        self.assertIn("subset of Input", details)


if __name__ == "__main__":
    unittest.main()
