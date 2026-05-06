import os
import unittest

from auth import AuthConfig


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


if __name__ == "__main__":
    unittest.main()
