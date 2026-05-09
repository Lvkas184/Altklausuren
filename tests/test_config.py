from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_dotenv


class ConfigTest(unittest.TestCase):
    def test_load_dotenv_sets_missing_values(self):
        with TemporaryDirectory() as temp:
            env_path = Path(temp) / ".env"
            env_path.write_text(
                "AUTH_ENABLED=true\n"
                "GOOGLE_CLIENT_ID=\"client-id\"\n"
                "# ignored\n",
                encoding="utf-8",
            )
            old_auth = os.environ.pop("AUTH_ENABLED", None)
            old_client = os.environ.pop("GOOGLE_CLIENT_ID", None)
            try:
                load_dotenv(env_path)

                self.assertEqual(os.environ["AUTH_ENABLED"], "true")
                self.assertEqual(os.environ["GOOGLE_CLIENT_ID"], "client-id")
            finally:
                for key, value in {"AUTH_ENABLED": old_auth, "GOOGLE_CLIENT_ID": old_client}.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_load_dotenv_does_not_override_environment(self):
        with TemporaryDirectory() as temp:
            env_path = Path(temp) / ".env"
            env_path.write_text("AUTH_ENABLED=true\n", encoding="utf-8")
            old_auth = os.environ.get("AUTH_ENABLED")
            try:
                os.environ["AUTH_ENABLED"] = "false"
                load_dotenv(env_path)

                self.assertEqual(os.environ["AUTH_ENABLED"], "false")
            finally:
                if old_auth is None:
                    os.environ.pop("AUTH_ENABLED", None)
                else:
                    os.environ["AUTH_ENABLED"] = old_auth


if __name__ == "__main__":
    unittest.main()
