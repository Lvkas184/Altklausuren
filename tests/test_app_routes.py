from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

import app as app_module
from storage import Catalog


class AppRoutesTest(unittest.TestCase):
    def test_index_renders_catalog_overview(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                app_module.catalog.create_subject("Mathematik 1", "M1")

                response = app_module.app.test_client().get("/")

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Klausurenstand", response.data)
                self.assertIn(b"Mathematik 1", response.data)
                self.assertIn(b"catalog-search", response.data)
                self.assertIn(b"Google Drive", response.data)
            finally:
                app_module.catalog = original_catalog

    def test_auth_gate_redirects_when_enabled(self):
        with app_module.app.test_client() as client:
            old_env = dict()
            keys = [
                "AUTH_ENABLED",
                "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REDIRECT_URI",
                "DRIVE_ROOT_FOLDER_ID",
            ]
            import os

            for key in keys:
                old_env[key] = os.environ.get(key)
            try:
                os.environ["AUTH_ENABLED"] = "true"
                os.environ["GOOGLE_CLIENT_ID"] = "client-id"
                os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"

                response = client.get("/")

                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.headers["Location"])
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
