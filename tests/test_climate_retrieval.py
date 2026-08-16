"""CLIMATE retrieval ranking, simple-query routing, and large-file policy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.climate.context_resolver import _qualify_source_rows, resolve_climate_context
from hub.climate.domain_query import extract_domain_query, score_source
from hub.climate.retrieval_policy import (
    bounded_matching_excerpt,
    is_large_reference_dump,
    is_noisy_artifact,
    is_simple_reference_query,
    redact_search_snippet,
)
from hub.registry.models import Registry, Repository
from hub.repository_workspace.service import RepositoryWorkspaceService
from hub.repository_workspace.settings import WorkspaceSettings

from tests.test_climate_investigation import ANC_PROMPT

REGION_PROMPT = "give me the provinces of Region VIII - Eastern Visayas"
ANC_CITE_PROMPT = (
    "Give me the logic of the ANC.\n"
    "Cite the exact implementation files/functions.\n"
    "Do not edit anything."
)


def _write_retrieval_fixture(root: Path) -> None:
    (root / "lookup" / "logs" / "bulk_apply_jobs").mkdir(parents=True)
    (root / "lookup" / "org").mkdir(parents=True)
    (root / "lookup" / "convergence").mkdir(parents=True)
    (root / "AI_REFERENCE" / "reference-json").mkdir(parents=True)
    (root / "lookup" / "logs" / "bulk_apply_jobs" / "job1.json").write_text(
        (
            '{"region":"VIII","email":"worker@example.com","username":"bulk.bot",'
            '"phone":"+639171234567","name":"Unrelated Person",'
            '"child":"region region region"}\n'
        ),
        encoding="utf-8",
    )
    (root / "AI_REFERENCE" / "reference-json" / "metadata.json").write_text(
        '{"region":"' + ("Eastern Visayas Region VIII " * 400) + '"}\n',
        encoding="utf-8",
    )
    (root / "lookup" / "org" / "eastern_visayas.json").write_text(
        (
            "{\n"
            '  "name": "Region VIII - Eastern Visayas",\n'
            '  "provinces": ["Biliran", "Eastern Samar", "Leyte", "Northern Samar", '
            '"Samar", "Southern Leyte"]\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    (root / "lookup" / "convergence" / "derive_anc.py").write_text(
        "def derive_anc_score(ctx):\n    return ctx\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Agents\nCite repository files.\n", encoding="utf-8")


class RetrievalPolicyTests(unittest.TestCase):
    def test_region_viii_is_simple_reference_not_implementation(self):
        self.assertTrue(is_simple_reference_query(REGION_PROMPT))
        self.assertFalse(is_simple_reference_query(ANC_PROMPT))
        self.assertFalse(is_simple_reference_query(ANC_CITE_PROMPT))
        self.assertTrue(is_simple_reference_query("what file defines symbol derive_anc_score"))
        self.assertTrue(is_simple_reference_query("name of UID Abcdefghij1"))

    def test_noisy_and_large_dump_detection(self):
        self.assertTrue(is_noisy_artifact("lookup/logs/bulk_apply_jobs/job1.json"))
        self.assertFalse(is_noisy_artifact("lookup/org/eastern_visayas.json"))
        self.assertTrue(is_large_reference_dump("AI_REFERENCE/reference-json/metadata.json"))
        self.assertFalse(is_large_reference_dump("lookup/org/eastern_visayas.json"))

    def test_generic_region_does_not_outrank_visayas_file(self):
        query = extract_domain_query(REGION_PROMPT)
        self.assertIn("region", query.weak)
        self.assertNotIn("region", query.strong)
        self.assertTrue(any("eastern visayas" in p.lower() for p in query.phrases))
        self.assertTrue(any("region viii" in p.lower() for p in query.phrases))
        self.assertNotIn("region", [t.lower() for t in query.search_terms() if t.lower() == "region"])
        visayas = score_source(
            "lookup/org/eastern_visayas.json",
            '{"name":"Region VIII - Eastern Visayas","provinces":["Leyte"]}',
            query,
            prompt=REGION_PROMPT,
        )
        logs = score_source(
            "lookup/logs/bulk_apply_jobs/job1.json",
            '{"region":"VIII","email":"worker@example.com"}',
            query,
            prompt=REGION_PROMPT,
        )
        dump = score_source(
            "AI_REFERENCE/reference-json/metadata.json",
            '{"region":"Eastern Visayas Region VIII"}',
            query,
            prompt=REGION_PROMPT,
        )
        self.assertGreater(visayas, logs)
        self.assertGreater(visayas, dump)

    def test_redact_pii_in_noisy_snippets_keeps_province_names(self):
        noisy = redact_search_snippet(
            '{"email":"worker@example.com","username":"bulk.bot","name":"Unrelated Person"}',
            path="lookup/logs/bulk_apply_jobs/job1.json",
        )
        self.assertNotIn("worker@example.com", noisy)
        self.assertNotIn("bulk.bot", noisy)
        self.assertIn("[redacted]", noisy)
        geo = redact_search_snippet(
            '{"name":"Leyte","region":"Eastern Visayas"}',
            path="lookup/org/eastern_visayas.json",
        )
        self.assertIn("Leyte", geo)
        excerpt = bounded_matching_excerpt(
            '{"email":"a@b.c"}\n{"name":"Region VIII - Eastern Visayas"}\n{"noise":1}\n',
            ["Eastern Visayas"],
        )
        self.assertIn("Eastern Visayas", excerpt)
        self.assertNotIn('"noise"', excerpt)


class ResolverRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "work"
        self.root.mkdir()
        _write_retrieval_fixture(self.root)
        self.registry = Registry([
            Repository(id="work-repo", name="Work", type="command", enabled=True, local_path=str(self.root)),
        ])
        self.repo_service = RepositoryWorkspaceService(WorkspaceSettings())
        self.repo = self.registry.get("work-repo")

    def tearDown(self):
        self.temp.cleanup()

    def _paths(self, resolved) -> list[str]:
        qual = [row["path"] for row in resolved.diagnostics.get("qualification") or []]
        return list(dict.fromkeys(list(resolved.source_files) + qual))

    def test_region_viii_skips_bulk_logs_and_metadata_dump(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt=REGION_PROMPT,
            provider="codex",
            model="m",
            repository_agent=True,
        )
        self.assertTrue(resolved.ok)
        self.assertTrue(resolved.diagnostics.get("simple_reference"))
        self.assertFalse(resolved.diagnostics.get("expanded_search"))
        paths = self._paths(resolved)
        self.assertFalse(any("bulk_apply" in path or "lookup/logs" in path for path in paths), paths)
        self.assertFalse(any("metadata.json" in path for path in resolved.source_files), resolved.source_files)
        self.assertTrue(
            any("eastern_visayas" in path for path in resolved.source_files + paths),
            resolved.source_files,
        )
        packet = resolved.packet.lower()
        self.assertNotIn("worker@example.com", packet)
        self.assertNotIn("unrelated person", packet)
        self.assertIn("lookup/logs", packet)
        queries = [str(q).lower() for q in resolved.diagnostics.get("resolver_queries") or []]
        self.assertTrue(
            any("eastern visayas" in q or "region viii" in q or "visayas" in q for q in queries),
            queries,
        )
        self.assertFalse(any(q == "region" for q in queries), queries)
        self.assertFalse(any(q == "prvev" for q in queries), queries)
        self.assertLessEqual(len(resolved.source_files), 3)

    def test_anc_logic_still_qualifies_implementation(self):
        resolved = resolve_climate_context(
            workspace="work",
            repo=self.repo,
            repository_workspace=self.repo_service,
            prompt=ANC_CITE_PROMPT,
            provider="codex",
            model="m",
            repository_agent=True,
        )
        self.assertTrue(resolved.ok)
        self.assertFalse(resolved.diagnostics.get("simple_reference"))
        authoritative = list(resolved.diagnostics.get("authoritative_sources") or [])
        qualification = resolved.diagnostics.get("qualification") or []
        self.assertTrue(
            any("derive_anc.py" in path for path in authoritative + resolved.source_files),
            {"authoritative": authoritative, "sources": resolved.source_files},
        )
        anc_row = next((row for row in qualification if "derive_anc.py" in row["path"]), None)
        self.assertIsNotNone(anc_row)
        self.assertTrue(anc_row["accepted"])
        self.assertIn("derive_anc_score", anc_row.get("functions") or [])
        self.assertFalse(any("bulk_apply" in path for path in resolved.source_files))

    def test_qualify_rejects_logs_and_dumps(self):
        rows = [
            {
                "path": "lookup/logs/bulk_apply_jobs/job1.json",
                "content": '{"region":"VIII","email":"a@b.c"}',
                "score": 90,
                "reason": "search:content",
            },
            {
                "path": "AI_REFERENCE/reference-json/metadata.json",
                "content": '{"region":"Eastern Visayas"}',
                "score": 80,
                "reason": "search:content",
            },
            {
                "path": "lookup/org/eastern_visayas.json",
                "content": '{"name":"Region VIII - Eastern Visayas","provinces":["Leyte"]}',
                "score": 20,
                "reason": "search:filename",
            },
        ]
        authoritative, diagnostics = _qualify_source_rows(rows, prompt=REGION_PROMPT)
        by_path = {row["path"]: row for row in diagnostics}
        self.assertEqual(by_path["lookup/logs/bulk_apply_jobs/job1.json"]["reason"], "noisy_artifact")
        self.assertFalse(by_path["lookup/logs/bulk_apply_jobs/job1.json"]["accepted"])
        self.assertEqual(by_path["AI_REFERENCE/reference-json/metadata.json"]["reason"], "expensive_dump")
        self.assertFalse(by_path["AI_REFERENCE/reference-json/metadata.json"]["accepted"])
        self.assertTrue(any("eastern_visayas" in row["path"] for row in authoritative + diagnostics))
