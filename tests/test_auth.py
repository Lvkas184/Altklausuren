import os
import unittest

from auth import AuthConfig, _role_from_claims, _split_header_values


class AuthConfigTest(unittest.TestCase):
    def test_auth_disabled_by_default(self):
        old_value = os.environ.pop("AUTH_ENABLED", None)
        try:
            self.assertFalse(AuthConfig.from_env().enabled)
        finally:
            if old_value is not None:
                os.environ["AUTH_ENABLED"] = old_value

    def test_auth_enabled_from_env(self):
        old_value = os.environ.get("AUTH_ENABLED")
        try:
            os.environ["AUTH_ENABLED"] = "true"
            self.assertTrue(AuthConfig.from_env().enabled)
        finally:
            if old_value is None:
                os.environ.pop("AUTH_ENABLED", None)
            else:
                os.environ["AUTH_ENABLED"] = old_value

    def test_auth_validate_requires_secret_key(self):
        old_values = {key: os.environ.get(key) for key in [
            "AUTH_ENABLED",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
            "DRIVE_ROOT_FOLDER_ID",
            "SECRET_KEY",
        ]}
        try:
            os.environ["AUTH_ENABLED"] = "true"
            os.environ["GOOGLE_CLIENT_ID"] = "client-id"
            os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
            os.environ["GOOGLE_REDIRECT_URI"] = "https://altklausuren.forum-wi.de/auth/callback"
            os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
            os.environ.pop("SECRET_KEY", None)

            with self.assertRaises(Exception):
                AuthConfig.from_env().validate()
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_auth_validate_rejects_placeholders(self):
        old_values = {key: os.environ.get(key) for key in [
            "AUTH_ENABLED",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
            "DRIVE_ROOT_FOLDER_ID",
            "SECRET_KEY",
        ]}
        try:
            os.environ["AUTH_ENABLED"] = "true"
            os.environ["GOOGLE_CLIENT_ID"] = "replace-with-oauth-client-id"
            os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
            os.environ["GOOGLE_REDIRECT_URI"] = "https://altklausuren.forum-wi.de/auth/callback"
            os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
            os.environ["SECRET_KEY"] = "secret-key"

            with self.assertRaises(Exception):
                AuthConfig.from_env().validate()
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_forward_auth_role_mapping_uses_groups_and_entitlements(self):
        old_values = {key: os.environ.get(key) for key in [
            "ADMIN_EMAILS",
            "AUTH_ROLE_ADMIN_GROUPS",
            "AUTH_ROLE_EDITOR_GROUPS",
            "AUTH_ROLE_VIEWER_GROUPS",
            "AUTH_ROLE_ADMIN_ENTITLEMENTS",
            "AUTH_ROLE_EDITOR_ENTITLEMENTS",
            "AUTH_ROLE_VIEWER_ENTITLEMENTS",
        ]}
        try:
            os.environ["ADMIN_EMAILS"] = "admin@forum-wi.de"
            os.environ["AUTH_ROLE_ADMIN_GROUPS"] = "Vorstand"
            os.environ["AUTH_ROLE_EDITOR_GROUPS"] = "Referat Altklausuren"
            os.environ["AUTH_ROLE_VIEWER_GROUPS"] = "Aktive"
            os.environ["AUTH_ROLE_EDITOR_ENTITLEMENTS"] = "altklausuren:editor"

            self.assertEqual(
                _role_from_claims(
                    email="person@forum-wi.de",
                    groups={"Referat Altklausuren"},
                    entitlements=set(),
                ),
                "editor",
            )
            self.assertEqual(
                _role_from_claims(
                    email="person@forum-wi.de",
                    groups=set(),
                    entitlements={"altklausuren:editor"},
                ),
                "editor",
            )
            self.assertEqual(
                _role_from_claims(
                    email="admin@forum-wi.de",
                    groups={"Aktive"},
                    entitlements=set(),
                ),
                "admin",
            )
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_split_header_values_accepts_json_and_csv(self):
        self.assertEqual(_split_header_values('["Aktive", "Referat Altklausuren"]'), {"Aktive", "Referat Altklausuren"})
        self.assertEqual(_split_header_values("Aktive,Referat Altklausuren"), {"Aktive", "Referat Altklausuren"})


if __name__ == "__main__":
    unittest.main()
