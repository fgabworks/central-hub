"""Audit mapping profile helpers for UID explorer."""

from __future__ import annotations

import unittest

from hub.dhis2.uid_mapping.audit_profile import (
    answer_kind,
    build_audit_profile,
    enrich_record_mapping_fields,
    extract_stage_data_element_refs,
    parse_program_label,
    summarize_option_set,
)
from hub.dhis2.uid_mapping.models import NormalizedUidRecord


class AuditProfileTests(unittest.TestCase):
    def test_parse_program_label(self) -> None:
        uid, name = parse_program_label("oSNoNtcmLXL - Household")
        self.assertEqual(uid, "oSNoNtcmLXL")
        self.assertEqual(name, "Household")

    def test_boolean_answer_kind(self) -> None:
        kind = answer_kind("BOOLEAN")
        self.assertEqual(kind["label"], "Yes / No")

    def test_stage_refs_from_filter(self) -> None:
        refs = extract_stage_data_element_refs(
            "(#{gKsusTMmABW.dxag8YT8w46} == 1) && #{pzQalCsjr9F.JzxYzLgo0P9} == 'Approved'"
        )
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["program_stage_uid"], "gKsusTMmABW")

    def test_build_profile_from_index_extras(self) -> None:
        record = {
            "uid": "juN5ad4xl5E",
            "name": "Attendance",
            "object_type": "programIndicator",
            "value_type": "",
            "program_uid": "oSNoNtcmLXL - Household",
            "extras": {
                "expression": "V{tei_count}",
                "filter": "#{gKsusTMmABW.dxag8YT8w46} == 1",
                "formName": "",
            },
        }
        profile = build_audit_profile(record)
        self.assertEqual(profile["program_uid"], "oSNoNtcmLXL")
        self.assertEqual(profile["program_name"], "Household")
        self.assertIn("gKsusTMmABW", profile["program_stage_uids"])
        self.assertTrue(profile["connections"])

    def test_option_set_yes_no_summary(self) -> None:
        summary = summarize_option_set(
            {
                "id": "optYesNoUid1",
                "name": "Yes No",
                "options": [
                    {"id": "aaaaaaaaaaa", "name": "Yes", "code": "1"},
                    {"id": "bbbbbbbbbbb", "name": "No", "code": "0"},
                ],
            }
        )
        assert summary is not None
        self.assertTrue(summary["yes_no_like"])
        profile = build_audit_profile(
            {"uid": "CdEfGhIjKl1", "name": "Choice", "value_type": "TEXT", "option_set_uid": "optYesNoUid1"},
            option_set={
                "id": "optYesNoUid1",
                "name": "Yes No",
                "options": summary["options"],
            },
        )
        self.assertEqual(profile["answer"]["label"], "Option set choice")

    def test_enrich_record_splits_program(self) -> None:
        rec = NormalizedUidRecord(
            uid="AbCdEfGhIj1",
            name="PI",
            object_type="programIndicator",
            program_uid="oSNoNtcmLXL - Household",
            extras={"filter": "#{gKsusTMmABW.AbCdEfGhIj1} == 1"},
        )
        enrich_record_mapping_fields(rec)
        self.assertEqual(rec.program_uid, "oSNoNtcmLXL")
        self.assertEqual(rec.program_stage_uid, "gKsusTMmABW")
        self.assertEqual(rec.extras.get("program_name"), "Household")


if __name__ == "__main__":
    unittest.main()
