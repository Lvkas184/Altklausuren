from pathlib import Path
from io import BytesIO
import os
from tempfile import TemporaryDirectory
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

import app as app_module
from pypdf import PdfReader, PdfWriter
from storage import Catalog


def _pdf_bytes(pages: int) -> BytesIO:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output


def _make_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        writer.write(output)


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

    def test_create_subject_post_creates_module(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))

                response = app_module.app.test_client().post(
                    "/subjects",
                    data={"title": "Rechnungswesen", "code": "RW"},
                    follow_redirects=True,
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Rechnungswesen", response.data)
                self.assertIsNotNone(app_module.catalog.get_subject("rw"))
            finally:
                app_module.catalog = original_catalog

    def test_subject_detail_and_print_render(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                _make_pdf(app_module.catalog.subject_dir(subject["id"]) / "current.pdf", 1)
                app_module.catalog.set_current_pages(subject["id"], 1)

                client = app_module.app.test_client()
                detail = client.get(f"/subjects/{subject['id']}")
                print_view = client.get(f"/subjects/{subject['id']}/print")

                self.assertEqual(detail.status_code, 200)
                self.assertIn(b"Klausuren-Tabelle", detail.data)
                self.assertEqual(print_view.status_code, 200)
                self.assertIn(b"Druckansicht", print_view.data)
            finally:
                app_module.catalog = original_catalog

    def test_import_collection_preserves_uploaded_pdf(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")

                response = app_module.app.test_client().post(
                    f"/subjects/{subject['id']}/import-collection",
                    data={"pdf": (_pdf_bytes(2), "DRUCK_Mathe_I.pdf")},
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )

                current = app_module.catalog.subject_dir(subject["id"]) / "current.pdf"
                updated = app_module.catalog.get_subject(subject["id"])

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(PdfReader(str(current)).pages), 2)
                self.assertEqual(updated["current_pages"], 2)
                self.assertTrue(updated["submissions"][0]["collection_import"])
            finally:
                app_module.catalog = original_catalog

    def test_edit_submission_regenerates_current_pdf(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                upload = subject_dir / "incoming" / "upload.pdf"
                _make_pdf(upload, 2)
                _make_pdf(subject_dir / "current.pdf", 2)
                submission = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "original_filename": "upload.pdf",
                        "stored_upload": "incoming/upload.pdf",
                        "added_pages": 2,
                        "current_pages": 2,
                    },
                )

                response = app_module.app.test_client().post(
                    f"/subjects/{subject['id']}/submissions/{submission['id']}",
                    data={
                        "kind": "Gedaechtnisprotokoll",
                        "term": "SoSe 2025",
                        "exam_date": "2025-07-01",
                        "instructor": "Weiss",
                        "solution": "Ja",
                        "notes": "aktualisiert",
                        "sort_order": "1",
                    },
                    follow_redirects=True,
                )

                current = subject_dir / "current.pdf"
                updated = app_module.catalog.get_submission(subject["id"], submission["id"])

                self.assertEqual(response.status_code, 200)
                self.assertEqual(updated["kind"], "Gedaechtnisprotokoll")
                self.assertEqual(app_module.catalog.get_subject(subject["id"])["current_pages"], 3)
                self.assertEqual(len(PdfReader(str(current)).pages), 3)
            finally:
                app_module.catalog = original_catalog

    def test_viewer_cannot_create_subject(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            old_env = {
                "AUTH_ENABLED": os.environ.get("AUTH_ENABLED"),
                "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID"),
                "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "GOOGLE_REDIRECT_URI": os.environ.get("GOOGLE_REDIRECT_URI"),
                "DRIVE_ROOT_FOLDER_ID": os.environ.get("DRIVE_ROOT_FOLDER_ID"),
            }
            try:
                app_module.catalog = Catalog(Path(temp))
                os.environ["AUTH_ENABLED"] = "true"
                os.environ["GOOGLE_CLIENT_ID"] = "client-id"
                os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                client = app_module.app.test_client()
                with client.session_transaction() as session:
                    session["user"] = {"email": "viewer@forum-wi.de", "name": "Viewer", "role": "viewer"}

                response = client.post(
                    "/subjects",
                    data={"title": "Rechnungswesen", "code": "RW"},
                    follow_redirects=False,
                )

                self.assertEqual(response.status_code, 302)
                self.assertIsNone(app_module.catalog.get_subject("rw"))
            finally:
                app_module.catalog = original_catalog
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

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
