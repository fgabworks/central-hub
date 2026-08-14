"""Presentation-only formatting for CLIMATE implementation-logic answers."""

from __future__ import annotations

import unittest

from hub.climate.coding import ClimateCodingAdapter
from hub.climate.logic_format import (
    format_logic_explanation,
    is_logic_explanation_prompt,
    logic_explanation_instructions,
    source_traces,
)


PNC_PROMPT = (
    "Give me the logic of the PNC.\n"
    "Cite the exact implementation files/functions.\n"
    "Do not edit anything."
)

PNC_JUMBLED = """
## PNC Logic

Exact implementation files/functions:
`lookup/convergence/derive_pnc_four.py` — `derive`
`lookup/convergence/quarter_policy.py` — `interview_scoring_policy`
Helper: `lookup/convergence/member_anc_pnc_compliance.py` — `derive_member_pnc_compliance`

Eligibility + edge cases:
- no eligible member → N/A
- no check due yet → N/A
- missing required dates → evaluate all four checks
- member Pass/Fail values stay member-level until household roll-up

Example:
Interview 10 days after delivery: 24-hour and 3-day checks must already be Yes.

Household/member roll-up:
Pass if at least one evaluable postpartum member passes (`passing > 0`).

Decision table / thresholds:
| Days since delivery | Required checks |
| --- | --- |
| <1 | none → N/A |
| 1–2 | 24-hour |
| 3–13 | 24-hour + 3-day |
| 14–41 | first 3 |
| ≥42 | all 4 |

Core rule:
- 2025 Q3–Q4 → pass with at least 2 Yes out of 4 checks
- 2025 Q3–Q4 → pass with at least 2 Yes out of 4 checks
- Other periods → all PNC checks already due by interview date must be Yes

### In one line
PNC passes when the period's required Yes-count is met for at least one eligible postpartum member.
""".strip()


class LogicPromptDetectionTests(unittest.TestCase):
    def test_matches_implementation_logic_questions(self):
        for prompt in (
            PNC_PROMPT,
            "Give me the logic of the ANC.",
            "Explain this indicator logic",
            "Explain how ANC Binary is derived",
            "What are the eligibility rules for PNC?",
        ):
            self.assertTrue(is_logic_explanation_prompt(prompt), prompt)

    def test_skips_unrelated_and_edit_prompts(self):
        for prompt in (
            "what is the name of the repo",
            "Fix ANC Binary",
            "Open a PR for the scoring module",
        ):
            self.assertFalse(is_logic_explanation_prompt(prompt), prompt)

    def test_uses_packet_task_not_source_snippets(self):
        packet = (
            "CLIMATE context packet (ASK).\n"
            "Task:\nwhat is the name of the repo\n"
            "Confidence: low\n"
            "Repository access: bounded packet only.\n"
            "Likely source mentions scoring thresholds and eligibility rules in derive.py."
        )
        self.assertFalse(is_logic_explanation_prompt(packet))
        logic_packet = (
            "CLIMATE context packet (ASK).\n"
            f"Task:\n{PNC_PROMPT}\n"
            "Confidence: high\n"
            "Repository access: the provider starts at the approved repository root."
        )
        self.assertTrue(is_logic_explanation_prompt(logic_packet))


class LogicExplanationFormatTests(unittest.TestCase):
    def test_core_rule_appears_before_implementation(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertLess(formatted.index("Core rule:"), formatted.index("Exact implementation"))
        self.assertLess(formatted.index("Core rule:"), formatted.index("derive_pnc_four.py"))
        self.assertTrue(formatted.startswith("## PNC Logic"))

    def test_repeated_rule_text_is_minimized(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertEqual(
            formatted.count("2025 Q3–Q4 → pass with at least 2 Yes out of 4 checks"),
            1,
        )

    def test_decision_table_renders(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertIn("| Days since delivery | Required checks |", formatted)
        self.assertIn("| ≥42 | all 4 |", formatted)
        self.assertIn("| <1 | none → N/A |", formatted)

    def test_exact_source_files_and_functions_are_preserved(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertEqual(
            source_traces(formatted),
            [
                ("lookup/convergence/derive_pnc_four.py", "derive"),
                ("lookup/convergence/quarter_policy.py", "interview_scoring_policy"),
                (
                    "lookup/convergence/member_anc_pnc_compliance.py",
                    "derive_member_pnc_compliance",
                ),
            ],
        )

    def test_edge_cases_remain_present(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertIn("no eligible member → N/A", formatted)
        self.assertIn("no check due yet → N/A", formatted)
        self.assertIn("missing required dates → evaluate all four checks", formatted)

    def test_one_line_summary_is_preserved_not_invented(self):
        formatted = format_logic_explanation(PNC_JUMBLED)
        self.assertIn("### In one line", formatted)
        self.assertIn("at least one eligible postpartum member", formatted)
        without_summary = PNC_JUMBLED.replace(
            "### In one line\nPNC passes when the period's required Yes-count is met for at least one eligible postpartum member.",
            "",
        ).strip()
        reformatted = format_logic_explanation(without_summary)
        self.assertNotIn("### In one line", reformatted)

    def test_production_vs_helper_distinction_is_preserved(self):
        instructions = logic_explanation_instructions()
        self.assertIn("production scoring functions first", instructions)
        self.assertIn("helper or recommended-timing functions", instructions)
        formatted = format_logic_explanation(PNC_JUMBLED)
        helper_at = formatted.index("Helper: `lookup/convergence/member_anc_pnc_compliance.py`")
        derive_at = formatted.index("`lookup/convergence/derive_pnc_four.py` — `derive`")
        self.assertLess(derive_at, helper_at)

    def test_insufficient_evidence_is_not_fabricated(self):
        raw = (
            "Not enough repository evidence to confirm PNC pass/fail thresholds.\n"
            "Cannot verify household roll-up.\n"
        )
        self.assertEqual(format_logic_explanation(raw), raw.strip())
        self.assertNotIn("| Days since delivery |", format_logic_explanation(raw))
        self.assertNotIn("### In one line", format_logic_explanation(raw))

    def test_unrelated_or_unsectioned_answers_are_unchanged(self):
        chat = "The selected repository is named work-repo."
        self.assertEqual(format_logic_explanation(chat), chat)
        chronological = (
            "Period policy in quarter_policy.py interview_scoring_policy: "
            "2025 Q3/Q4 at least 2 Yes; other periods all due checks Yes. "
            "Household Pass if passing > 0."
        )
        self.assertEqual(format_logic_explanation(chronological), chronological)


class LogicExplanationWiringTests(unittest.TestCase):
    def test_humanize_formats_ask_logic_answers_only(self):
        formatted, diag = ClimateCodingAdapter.humanize_answer(
            PNC_JUMBLED,
            task_mode="ask",
            prompt=PNC_PROMPT,
        )
        self.assertLess(formatted.index("Core rule:"), formatted.index("Exact implementation"))
        self.assertFalse(diag)
        unchanged, _ = ClimateCodingAdapter.humanize_answer(
            PNC_JUMBLED,
            task_mode="ask",
            prompt="what is the name of the repo",
        )
        self.assertEqual(unchanged, PNC_JUMBLED)
        edit, _ = ClimateCodingAdapter.humanize_answer(
            PNC_JUMBLED,
            task_mode="edit",
            prompt=PNC_PROMPT,
        )
        self.assertEqual(edit, PNC_JUMBLED)

    def test_execute_adds_outline_only_for_ask_logic_prompts(self):
        class StubCenter:
            def __init__(self):
                self.payload = None

            def start_run(self, payload):
                self.payload = payload
                return {
                    "id": "r1",
                    "status": "running",
                    "agent_id": "codex",
                    "model": "m",
                    "answer": "",
                    "logs": "",
                    "usage": {},
                }

        center = StubCenter()
        adapter = ClimateCodingAdapter(center)

        def fake_availability(provider=None, *, refresh=False):
            row = {
                "id": "codex",
                "state": "connected",
                "status": "Connected",
                "detail": "",
                "account_label": "",
                "capabilities": {"native_repository_investigation": True},
            }
            return row if provider else [row]

        adapter.availability = fake_availability  # type: ignore[method-assign]
        adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt=(
                "CLIMATE context packet (ASK).\n"
                f"Task:\n{PNC_PROMPT}\n"
                "Confidence: high\n"
                "Repository access: bounded packet only."
            ),
            task_mode="ask",
            repository_investigation=True,
        )
        packed = center.payload["prompt"]
        self.assertIn("Core rule:", packed)
        self.assertIn("Decision table / thresholds:", packed)
        self.assertIn("### In one line", packed)
        self.assertIn("production scoring functions first", packed)

        adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt=(
                "CLIMATE context packet (ASK).\n"
                "Task:\nwhat is the name of the repo\n"
                "Confidence: low\n"
                "Repository access: bounded packet only.\n"
                "Likely source mentions scoring thresholds."
            ),
            task_mode="ask",
        )
        self.assertNotIn("Decision table / thresholds:", center.payload["prompt"])

        adapter.execute(
            workspace="work",
            repository_id="work-repo",
            provider="codex",
            model="m",
            prompt="Fix ANC Binary",
            task_mode="edit",
        )
        self.assertIn("EDIT mode", center.payload["prompt"])
        self.assertNotIn("Decision table / thresholds:", center.payload["prompt"])
