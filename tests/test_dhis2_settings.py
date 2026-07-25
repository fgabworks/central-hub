"""DHIS2 credential alias resolution (stage/live) and public redaction."""

from __future__ import annotations

import json
import unittest

from hub.dhis2.client import Dhis2Client
from hub.dhis2.redact import public_dhis2_config
from hub.settings import Dhis2Settings, resolve_dhis2_credentials


def _env(mapping: dict[str, str]):
    def getter(key: str) -> str | None:
        return mapping.get(key)

    return getter


class ResolveCredentialsTests(unittest.TestCase):
    def test_stage_aliases(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "DHIS2_ENVIRONMENT": "stage",
                    "STAGE_DHIS2_URL": "https://stage.example.org/",
                    "STAGE_DHIS2_USERNAME": "stage-user",
                    "STAGE_DHIS2_PASSWORD": "stage-secret",
                }
            )
        )
        self.assertTrue(resolved.is_configured)
        self.assertEqual(resolved.environment, "stage")
        self.assertEqual(resolved.base_url, "https://stage.example.org")
        self.assertEqual(resolved.username, "stage-user")
        self.assertEqual(resolved.password, "stage-secret")
        self.assertEqual(resolved.credential_fields["STAGE_DHIS2_URL"], "set")
        self.assertEqual(resolved.credential_fields["STAGE_DHIS2_USERNAME"], "set")
        self.assertEqual(resolved.credential_fields["STAGE_DHIS2_PASSWORD"], "set")
        self.assertEqual(resolved.missing_fields, ())

    def test_live_aliases(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "DHIS2_ENVIRONMENT": "live",
                    "LIVE_DHIS2_URL": "https://live.example.org",
                    "LIVE_DHIS2_USERNAME": "live-user",
                    "LIVE_DHIS2_PASSWORD": "live-secret",
                }
            )
        )
        self.assertTrue(resolved.is_configured)
        self.assertEqual(resolved.environment, "live")
        self.assertEqual(resolved.base_url, "https://live.example.org")
        self.assertIn("LIVE_DHIS2_URL", resolved.credential_fields)

    def test_canonical_precedence_over_aliases(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "DHIS2_ENVIRONMENT": "stage",
                    "DHIS2_BASE_URL": "https://canonical.example.org",
                    "DHIS2_USERNAME": "canon-user",
                    "DHIS2_PASSWORD": "canon-secret",
                    "STAGE_DHIS2_URL": "https://stage.example.org",
                    "STAGE_DHIS2_USERNAME": "stage-user",
                    "STAGE_DHIS2_PASSWORD": "stage-secret",
                }
            )
        )
        self.assertEqual(resolved.environment, "canonical")
        self.assertEqual(resolved.base_url, "https://canonical.example.org")
        self.assertEqual(resolved.username, "canon-user")
        self.assertNotEqual(resolved.base_url, "https://stage.example.org")

    def test_partial_canonical_does_not_fall_through_to_aliases(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "DHIS2_ENVIRONMENT": "stage",
                    "DHIS2_BASE_URL": "https://canonical.example.org",
                    # username/password missing
                    "STAGE_DHIS2_URL": "https://stage.example.org",
                    "STAGE_DHIS2_USERNAME": "stage-user",
                    "STAGE_DHIS2_PASSWORD": "stage-secret",
                }
            )
        )
        self.assertFalse(resolved.is_configured)
        self.assertEqual(resolved.environment, "canonical")
        self.assertIn("DHIS2_USERNAME", resolved.missing_fields)
        self.assertIn("DHIS2_PASSWORD", resolved.missing_fields)
        self.assertTrue(
            any("Incomplete canonical" in err for err in resolved.configuration_errors)
        )

    def test_missing_stage_fields(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "DHIS2_ENVIRONMENT": "stage",
                    "STAGE_DHIS2_URL": "https://stage.example.org",
                }
            )
        )
        self.assertFalse(resolved.is_configured)
        self.assertEqual(resolved.environment, "stage")
        self.assertIn("STAGE_DHIS2_USERNAME", resolved.missing_fields)
        self.assertIn("STAGE_DHIS2_PASSWORD", resolved.missing_fields)
        self.assertTrue(
            any("Incomplete stage" in err for err in resolved.configuration_errors)
        )
        # Status map uses field names only — values never appear.
        blob = json.dumps(dict(resolved.credential_fields))
        self.assertNotIn("stage-user", blob)
        self.assertNotIn("https://stage.example.org", blob)

    def test_invalid_environment(self) -> None:
        resolved = resolve_dhis2_credentials(_env({"DHIS2_ENVIRONMENT": "prod"}))
        self.assertFalse(resolved.is_configured)
        self.assertTrue(
            any("Invalid DHIS2_ENVIRONMENT" in err for err in resolved.configuration_errors)
        )

    def test_both_alias_groups_require_environment_selector(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "STAGE_DHIS2_URL": "https://stage.example.org",
                    "STAGE_DHIS2_USERNAME": "stage-user",
                    "STAGE_DHIS2_PASSWORD": "stage-secret",
                    "LIVE_DHIS2_URL": "https://live.example.org",
                    "LIVE_DHIS2_USERNAME": "live-user",
                    "LIVE_DHIS2_PASSWORD": "live-secret",
                }
            )
        )
        self.assertFalse(resolved.is_configured)
        self.assertTrue(
            any("Multiple DHIS2 alias groups" in err for err in resolved.configuration_errors)
        )
        self.assertTrue(
            any("DHIS2_ENVIRONMENT=stage" in err for err in resolved.configuration_errors)
        )

    def test_single_complete_alias_group_auto_selected(self) -> None:
        resolved = resolve_dhis2_credentials(
            _env(
                {
                    "STAGE_DHIS2_URL": "https://stage.example.org",
                    "STAGE_DHIS2_USERNAME": "stage-user",
                    "STAGE_DHIS2_PASSWORD": "stage-secret",
                }
            )
        )
        self.assertTrue(resolved.is_configured)
        self.assertEqual(resolved.environment, "stage")
        self.assertEqual(resolved.base_url, "https://stage.example.org")


class PublicConfigRedactionTests(unittest.TestCase):
    def test_public_config_never_includes_credentials(self) -> None:
        cfg = public_dhis2_config(
            base_url="https://user:super-secret@dhis2.example.org/path",
            username="real-username",
            password="super-secret-password",
            timeout_seconds=10.0,
            allow_writes=False,
            enabled=True,
            configured=True,
            environment="stage",
            credential_fields={
                "STAGE_DHIS2_URL": "set",
                "STAGE_DHIS2_USERNAME": "set",
                "STAGE_DHIS2_PASSWORD": "set",
            },
            configuration_errors=(),
            missing_fields=(),
        )
        dumped = json.dumps(cfg)
        self.assertNotIn("super-secret", dumped)
        self.assertNotIn("real-username", dumped)
        self.assertNotIn("super-secret-password", dumped)
        self.assertNotIn("password", cfg)  # value key absent; password_set only
        self.assertTrue(cfg["username_set"])
        self.assertTrue(cfg["password_set"])
        self.assertEqual(cfg["environment"], "stage")
        self.assertEqual(cfg["credential_fields"]["STAGE_DHIS2_PASSWORD"], "set")
        self.assertNotIn("username", cfg)  # value key removed
        # URL userinfo stripped
        self.assertNotIn("super-secret", cfg["base_url"] or "")

    def test_client_public_config_redaction(self) -> None:
        settings = Dhis2Settings(
            base_url="https://dhis2.example.org",
            username="stage_user",
            password="secret-password",
            timeout_seconds=10.0,
            allow_writes=False,
            enabled=True,
            environment="stage",
            credential_fields={
                "STAGE_DHIS2_URL": "set",
                "STAGE_DHIS2_USERNAME": "set",
                "STAGE_DHIS2_PASSWORD": "set",
            },
            missing_fields=(),
            configuration_errors=(),
        )
        client = Dhis2Client(settings)
        cfg = client.public_config()
        dumped = json.dumps(cfg)
        self.assertNotIn("secret-password", dumped)
        self.assertNotIn("stage_user", dumped)
        self.assertEqual(cfg["credential_fields"]["STAGE_DHIS2_URL"], "set")

    def test_check_status_not_configured_uses_configuration_detail(self) -> None:
        settings = Dhis2Settings(
            base_url=None,
            username=None,
            password=None,
            timeout_seconds=10.0,
            allow_writes=False,
            enabled=True,
            environment="stage",
            missing_fields=("STAGE_DHIS2_PASSWORD",),
            configuration_errors=(
                "Incomplete stage DHIS2 credentials. Missing: STAGE_DHIS2_PASSWORD.",
            ),
            credential_fields={
                "STAGE_DHIS2_URL": "set",
                "STAGE_DHIS2_USERNAME": "set",
                "STAGE_DHIS2_PASSWORD": "missing",
            },
        )
        client = Dhis2Client(settings)
        status = client.check_status()
        self.assertEqual(status["status"], "not_configured")
        self.assertIn("STAGE_DHIS2_PASSWORD", status["detail"])
        self.assertNotIn("secret", status["detail"].lower())


if __name__ == "__main__":
    unittest.main()
