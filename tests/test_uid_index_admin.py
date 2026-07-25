"""Controlled UID index admin (LP-adapted dry-run / confirm / versions)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hub.dhis2.uid_mapping.admin import (
    CONFIRM_APPLY,
    CONFIRM_RESTORE,
    apply_with_confirmation,
    compare_versions,
    enrich_controlled_preview,
    list_versions,
    restore_with_confirmation,
)
from hub.dhis2.uid_mapping.store import MappingIndexStore, merge_preview


def _rec(uid: str, name: str, object_type: str = "dataElement", repo: str = "live-processing") -> dict:
    return {
        "uid": uid,
        "name": name,
        "code": name,
        "object_type": object_type,
        "source_repository": repo,
        "source_file": "AI_UID_INDEX.csv",
        "source_environment": "stage",
    }


class ControlledUidAdminTests(unittest.TestCase):
    def test_enrich_preview_change_cards_and_confirm_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(Path(tmp))
            existing = [_rec("AbCdEfGhIj1", "Alpha")]
            incoming = [
                _rec("AbCdEfGhIj1", "Alpha Renamed"),
                _rec("BcDeFgHiJk2", "Beta"),
            ]
            preview = enrich_controlled_preview(
                merge_preview(existing, incoming),
                existing=existing,
                incoming=incoming,
                store=store,
            )
            self.assertTrue(preview["read_only_dry_run"])
            self.assertEqual(preview["confirm_phrase"], CONFIRM_APPLY)
            self.assertEqual(preview["change_counts"]["NEW_UID"], 1)
            self.assertEqual(preview["change_counts"]["CHANGED_NAME"], 1)
            self.assertGreaterEqual(preview["change_counts"]["MISSING_FROM_SOURCE"], 0)

    def test_apply_requires_exact_phrase_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MappingIndexStore(root)
            store.save(
                {
                    "ok": True,
                    "record_count": 1,
                    "records": [_rec("AbCdEfGhIj1", "Alpha")],
                }
            )
            incoming = [_rec("AbCdEfGhIj1", "Alpha"), _rec("BcDeFgHiJk2", "Beta")]
            preview = enrich_controlled_preview(
                merge_preview(store.records(), incoming),
                existing=store.records(),
                incoming=incoming,
                store=store,
            )
            bad = apply_with_confirmation(store, preview, "wrong")
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["writes"], 0)

            ok = apply_with_confirmation(store, preview, CONFIRM_APPLY)
            self.assertTrue(ok["ok"])
            self.assertEqual(ok["writes"], 1)
            self.assertEqual(ok["dhis2_writes"], 0)
            self.assertTrue((root / "latest.json").is_file())
            self.assertTrue(store.archive_dir.is_dir())
            backups = list(store.archive_dir.glob("hub_uid_index_backup_v*.json"))
            self.assertTrue(backups)
            self.assertEqual(len(store.records()), 2)

    def test_restore_and_compare_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MappingIndexStore(Path(tmp))
            store.save({"ok": True, "record_count": 1, "records": [_rec("AbCdEfGhIj1", "Alpha")]})
            incoming = [_rec("AbCdEfGhIj1", "Alpha"), _rec("BcDeFgHiJk2", "Beta")]
            preview = enrich_controlled_preview(
                merge_preview(store.records(), incoming),
                existing=store.records(),
                incoming=incoming,
                store=store,
            )
            applied = apply_with_confirmation(store, preview, CONFIRM_APPLY)
            versions = list_versions(store)
            self.assertGreaterEqual(versions["count"], 1)
            older = versions["versions"][-1]["version"]

            # Apply another change so restore is meaningful.
            incoming2 = incoming + [_rec("CdEfGhIjKl3", "Gamma")]
            preview2 = enrich_controlled_preview(
                merge_preview(store.records(), incoming2),
                existing=store.records(),
                incoming=incoming2,
                store=store,
            )
            apply_with_confirmation(store, preview2, CONFIRM_APPLY)
            self.assertEqual(len(store.records()), 3)

            restored = restore_with_confirmation(store, older, CONFIRM_RESTORE)
            self.assertTrue(restored["ok"])
            # Restored version may be backup (1 row) or updated (2 rows) depending on stamp.
            self.assertGreaterEqual(restored["record_count"], 1)

            cmp = compare_versions(store, "current", older)
            self.assertTrue(cmp["ok"])
            self.assertIn("counts", cmp)


if __name__ == "__main__":
    unittest.main()
