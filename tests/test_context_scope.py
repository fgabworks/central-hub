"""CLIMATE Chat context scope resolution."""

from __future__ import annotations

import unittest

from hub.climate.context_scope import ALL, GENERAL, REPOSITORY, normalize_context_scope, resolve_chat_scope


class ContextScopeTests(unittest.TestCase):
    def test_general_is_default(self):
        self.assertEqual(normalize_context_scope(""), GENERAL)
        self.assertEqual(normalize_context_scope("general"), GENERAL)
        self.assertEqual(normalize_context_scope("no-repository"), GENERAL)
        self.assertEqual(resolve_chat_scope({}), (GENERAL, ""))
        self.assertEqual(resolve_chat_scope({"repository_id": "none"}), (GENERAL, ""))
        self.assertEqual(
            resolve_chat_scope({"context_scope": "general", "repository_id": "work-repo"}),
            (GENERAL, ""),
        )

    def test_all_repositories(self):
        self.assertEqual(normalize_context_scope("all"), ALL)
        self.assertEqual(normalize_context_scope("all-repositories"), ALL)
        self.assertEqual(resolve_chat_scope({"context_scope": "all", "repository_id": "work-repo"}), (ALL, ""))

    def test_specific_repository(self):
        self.assertEqual(resolve_chat_scope({"repository_id": "work-repo"}), (REPOSITORY, "work-repo"))
        self.assertEqual(
            resolve_chat_scope({"context_scope": "repository", "repository_id": "sql-queries"}),
            (REPOSITORY, "sql-queries"),
        )
        self.assertEqual(resolve_chat_scope({"context_scope": "live-processing-local"}), (REPOSITORY, "live-processing-local"))
        self.assertEqual(normalize_context_scope("repository"), REPOSITORY)
        self.assertEqual(resolve_chat_scope({"context_scope": "repository"}), (GENERAL, ""))

    def test_does_not_treat_workspace_ids_as_repos(self):
        self.assertEqual(resolve_chat_scope({"repository_id": "vanta"}), (GENERAL, ""))
        self.assertEqual(resolve_chat_scope({"context_scope": "work"}), (GENERAL, ""))


if __name__ == "__main__":
    unittest.main()
