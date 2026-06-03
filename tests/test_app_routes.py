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
os.environ["ALTKLAUSUREN_SKIP_DOTENV"] = "true"

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


def _csrf(client) -> str:
    with client.session_transaction() as session:
        token = session.setdefault("csrf_token", "test-csrf-token")
    return token


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

                client = app_module.app.test_client()
                response = client.post(
                    "/subjects",
                    data={"title": "Rechnungswesen", "code": "RW", "csrf_token": _csrf(client)},
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

                client = app_module.app.test_client()
                response = client.post(
                    f"/subjects/{subject['id']}/import-collection",
                    data={"pdf": (_pdf_bytes(2), "DRUCK_Mathe_I.pdf"), "csrf_token": _csrf(client)},
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )

                current = app_module.catalog.subject_dir(subject["id"]) / "current.pdf"
                updated = app_module.catalog.get_subject(subject["id"])

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(PdfReader(str(current)).pages), 2)
                self.assertEqual(updated["current_pages"], 2)
                self.assertTrue(updated["submissions"][0]["collection_import"])
                self.assertTrue((app_module.catalog.subject_dir(subject["id"]) / "single.pdf").exists())
            finally:
                app_module.catalog = original_catalog

    def test_invalid_pdf_upload_is_removed_from_incoming(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                client = app_module.app.test_client()

                response = client.post(
                    f"/subjects/{subject['id']}/submissions",
                    data={
                        "pdf": (BytesIO(b"not a pdf"), "broken.pdf"),
                        "csrf_token": _csrf(client),
                    },
                    content_type="multipart/form-data",
                    follow_redirects=False,
                )

                incoming = app_module.catalog.subject_dir(subject["id"]) / "incoming"
                self.assertEqual(response.status_code, 302)
                self.assertEqual(list(incoming.glob("*.pdf")), [])
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

                client = app_module.app.test_client()
                response = client.post(
                    f"/subjects/{subject['id']}/submissions/{submission['id']}",
                    data={
                        "kind": "Gedaechtnisprotokoll",
                        "term": "SoSe 2025",
                        "exam_date": "2025-07-01",
                        "instructor": "Weiss",
                        "solution": "Ja",
                        "notes": "aktualisiert",
                        "sort_order": "1",
                        "csrf_token": _csrf(client),
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

    def test_edit_submission_rejects_invalid_sort_order(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                upload = subject_dir / "incoming" / "upload.pdf"
                _make_pdf(upload, 1)
                _make_pdf(subject_dir / "current.pdf", 1)
                submission = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "original_filename": "upload.pdf",
                        "stored_upload": "incoming/upload.pdf",
                        "added_pages": 1,
                        "current_pages": 1,
                    },
                )
                client = app_module.app.test_client()

                response = client.post(
                    f"/subjects/{subject['id']}/submissions/{submission['id']}",
                    data={
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "sort_order": "not-a-number",
                        "csrf_token": _csrf(client),
                    },
                    follow_redirects=False,
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(app_module.catalog.get_submission(subject["id"], submission["id"])["sort_order"], 1)
            finally:
                app_module.catalog = original_catalog

    def test_edit_submission_rolls_back_when_regeneration_fails(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                _make_pdf(subject_dir / "current.pdf", 1)
                submission = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "original_filename": "missing.pdf",
                        "stored_upload": "incoming/missing.pdf",
                        "added_pages": 1,
                        "current_pages": 1,
                    },
                )
                client = app_module.app.test_client()

                response = client.post(
                    f"/subjects/{subject['id']}/submissions/{submission['id']}",
                    data={
                        "kind": "Gedächtnisprotokoll",
                        "term": "SoSe 2026",
                        "sort_order": "1",
                        "csrf_token": _csrf(client),
                    },
                    follow_redirects=False,
                )
                updated = app_module.catalog.get_submission(subject["id"], submission["id"])

                self.assertEqual(response.status_code, 302)
                self.assertEqual(updated["kind"], "Altklausur")
                self.assertEqual(updated["term"], "WiSe 2024/25")
            finally:
                app_module.catalog = original_catalog

    def test_delete_submission_rolls_back_when_regeneration_fails(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                upload = subject_dir / "incoming" / "upload.pdf"
                _make_pdf(upload, 1)
                _make_pdf(subject_dir / "current.pdf", 1)
                good = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "stored_upload": "incoming/upload.pdf",
                        "added_pages": 1,
                        "current_pages": 1,
                    },
                )
                app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Gedächtnisprotokoll",
                        "term": "SoSe 2026",
                        "stored_upload": "incoming/missing.pdf",
                        "added_pages": 1,
                        "current_pages": 2,
                    },
                )
                client = app_module.app.test_client()

                response = client.post(
                    f"/subjects/{subject['id']}/submissions/{good['id']}/delete",
                    data={"csrf_token": _csrf(client)},
                    follow_redirects=False,
                )

                self.assertEqual(response.status_code, 302)
                self.assertIsNotNone(app_module.catalog.get_submission(subject["id"], good["id"]))
            finally:
                app_module.catalog = original_catalog

    def test_regenerate_skips_drive_push_when_pdf_is_unchanged_and_synced(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            original_push = app_module.push_subject_to_drive
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                upload = subject_dir / "incoming" / "upload.pdf"
                _make_pdf(upload, 1)
                submission = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "stored_upload": "incoming/upload.pdf",
                        "added_pages": 1,
                    },
                )
                app_module.regenerate_current_pdf(
                    subject=app_module.catalog.get_subject(subject["id"]),
                    subject_dir=subject_dir,
                )
                app_module.catalog.update_drive_sync(
                    subject["id"],
                    {
                        "drive_file_id": "file-1",
                        "last_drive_fingerprint": "old",
                        "sync_status": app_module.SYNCED,
                    },
                    current_pages=2,
                )

                def fail_push(**_kwargs):
                    raise AssertionError("unchanged regenerate should not push to Drive")

                app_module.push_subject_to_drive = fail_push
                client = app_module.app.test_client()
                response = client.post(
                    f"/subjects/{subject['id']}/regenerate",
                    data={"csrf_token": _csrf(client)},
                    follow_redirects=True,
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn("Drive-Upload wurde", response.get_data(as_text=True))
                self.assertIsNotNone(app_module.catalog.get_submission(subject["id"], submission["id"]))
            finally:
                app_module.push_subject_to_drive = original_push
                app_module.catalog = original_catalog

    def test_release_proto_session_rolls_back_when_regeneration_fails(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            original_regenerate = app_module.regenerate_current_pdf
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                existing = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Altklausur",
                        "term": "WiSe 2024/25",
                        "stored_upload": "incoming/existing.pdf",
                        "sort_order": 7,
                    },
                )
                proto_session = app_module.catalog.create_proto_session(subject["id"], "SoSe 2026")
                app_module.catalog.save_proto_session_editor(proto_session["id"], "Aufgabe 1\n\nAufgabe 2")
                app_module.catalog.close_proto_session(proto_session["id"])

                def fail_regenerate(**_kwargs):
                    raise app_module.PdfProcessingError("Regeneration fehlgeschlagen")

                app_module.regenerate_current_pdf = fail_regenerate
                client = app_module.app.test_client()

                response = client.post(
                    f"/subjects/{subject['id']}/sessions/{proto_session['id']}/release",
                    data={"csrf_token": _csrf(client)},
                    follow_redirects=False,
                )
                updated_subject = app_module.catalog.get_subject(subject["id"])
                updated_session = app_module.catalog.get_proto_session_by_id(proto_session["id"])

                self.assertEqual(response.status_code, 302)
                self.assertEqual(updated_session["status"], "closed")
                self.assertEqual(len(updated_subject["submissions"]), 1)
                self.assertEqual(updated_subject["submissions"][0]["id"], existing["id"])
                self.assertEqual(updated_subject["submissions"][0]["sort_order"], 7)
            finally:
                app_module.regenerate_current_pdf = original_regenerate
                app_module.catalog = original_catalog

    def test_split_collection_rolls_back_when_regeneration_fails(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            original_regenerate = app_module.regenerate_current_pdf
            try:
                app_module.catalog = Catalog(Path(temp))
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                subject_dir = app_module.catalog.subject_dir(subject["id"])
                upload = subject_dir / "incoming" / "druck.pdf"
                _make_pdf(upload, 2)
                _make_pdf(subject_dir / "current.pdf", 2)
                submission = app_module.catalog.add_submission(
                    subject["id"],
                    {
                        "kind": "Sammlungsimport",
                        "stored_upload": "incoming/druck.pdf",
                        "collection_import": True,
                        "added_pages": 2,
                        "current_pages": 2,
                    },
                )

                def fail_regenerate(**_kwargs):
                    raise app_module.PdfProcessingError("Regeneration fehlgeschlagen")

                app_module.regenerate_current_pdf = fail_regenerate
                client = app_module.app.test_client()
                response = client.post(
                    f"/subjects/{subject['id']}/submissions/{submission['id']}/split",
                    data={
                        "group_start_0": "1",
                        "group_end_0": "1",
                        "group_kind_0": "Gedächtnisprotokoll",
                        "group_start_1": "2",
                        "group_end_1": "2",
                        "group_kind_1": "Altklausur",
                        "csrf_token": _csrf(client),
                    },
                    follow_redirects=False,
                )
                updated = app_module.catalog.get_subject(subject["id"])

                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(updated["submissions"]), 1)
                self.assertEqual(updated["submissions"][0]["id"], submission["id"])
                self.assertTrue(updated["submissions"][0]["collection_import"])
                self.assertEqual(list((subject_dir / "incoming").glob("*split*.pdf")), [])
            finally:
                app_module.regenerate_current_pdf = original_regenerate
                app_module.catalog = original_catalog

    def test_proto_session_public_endpoints_work_when_auth_enabled(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            old_env = {
                "AUTH_ENABLED": os.environ.get("AUTH_ENABLED"),
                "SECRET_KEY": os.environ.get("SECRET_KEY"),
                "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID"),
                "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "GOOGLE_REDIRECT_URI": os.environ.get("GOOGLE_REDIRECT_URI"),
                "DRIVE_ROOT_FOLDER_ID": os.environ.get("DRIVE_ROOT_FOLDER_ID"),
                "PUBLIC_BASE_URL": os.environ.get("PUBLIC_BASE_URL"),
            }
            try:
                app_module.catalog = Catalog(Path(temp))
                os.environ["AUTH_ENABLED"] = "true"
                os.environ["SECRET_KEY"] = "test-secret"
                os.environ["GOOGLE_CLIENT_ID"] = "client-id"
                os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                os.environ["PUBLIC_BASE_URL"] = "http://example.test"
                subject = app_module.catalog.create_subject("Mathematik 1", "M1")
                proto_session = app_module.catalog.create_proto_session(subject["id"], "SoSe 2026")
                client = app_module.app.test_client()

                page = client.get(f"/session/{proto_session['token']}")
                qr = client.get(f"/session/{proto_session['token']}/qr.png")
                contribution = client.post(
                    f"/session/{proto_session['token']}/contribute",
                    json={"text": "Aufgabe 1"},
                )
                moderation = client.get(f"/subjects/{subject['id']}/sessions/{proto_session['id']}")

                self.assertEqual(page.status_code, 200)
                self.assertEqual(qr.status_code, 200)
                self.assertEqual(contribution.status_code, 200)
                self.assertEqual(app_module.catalog.get_proto_contributions(proto_session["id"])[0]["text"], "Aufgabe 1")
                self.assertEqual(moderation.status_code, 302)
                self.assertIn("/login", moderation.headers["Location"])
            finally:
                app_module.catalog = original_catalog
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

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
                    data={"title": "Rechnungswesen", "code": "RW", "csrf_token": _csrf(client)},
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

    def test_admin_email_is_displayed_as_admin_role(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            old_env = {
                "AUTH_ENABLED": os.environ.get("AUTH_ENABLED"),
                "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID"),
                "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "GOOGLE_REDIRECT_URI": os.environ.get("GOOGLE_REDIRECT_URI"),
                "DRIVE_ROOT_FOLDER_ID": os.environ.get("DRIVE_ROOT_FOLDER_ID"),
                "ADMIN_EMAILS": os.environ.get("ADMIN_EMAILS"),
            }
            try:
                app_module.catalog = Catalog(Path(temp))
                os.environ["AUTH_ENABLED"] = "true"
                os.environ["GOOGLE_CLIENT_ID"] = "client-id"
                os.environ["GOOGLE_CLIENT_SECRET"] = "client-secret"
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                os.environ["ADMIN_EMAILS"] = "lukas.heinz@forum-wi.de"
                client = app_module.app.test_client()
                with client.session_transaction() as session:
                    session["user"] = {"email": "lukas.heinz@forum-wi.de", "name": "Lukas Heinz", "role": "editor"}

                response = client.get("/")

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"admin", response.data)
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

    def test_login_page_renders_google_button(self):
        with app_module.app.test_client() as client:
            old_env = {key: os.environ.get(key) for key in [
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
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                os.environ["SECRET_KEY"] = "test-secret"

                response = client.get("/login?next=/subjects/mathe")

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Anmelden im Altklausuren-System", response.data)
                self.assertIn("Über Google anmelden".encode(), response.data)
                self.assertIn(b"/login/google", response.data)
                with client.session_transaction() as session:
                    self.assertEqual(session["login_next"], "/subjects/mathe")
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_login_ignores_favicon_next_target(self):
        with app_module.app.test_client() as client:
            old_env = {key: os.environ.get(key) for key in [
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
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                os.environ["SECRET_KEY"] = "test-secret"

                response = client.get("/login?next=/favicon.ico")

                self.assertEqual(response.status_code, 200)
                with client.session_transaction() as session:
                    self.assertNotIn("login_next", session)
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_favicon_is_public(self):
        with app_module.app.test_client() as client:
            old_auth = os.environ.get("AUTH_ENABLED")
            try:
                os.environ["AUTH_ENABLED"] = "true"
                response = client.get("/favicon.ico")

                self.assertEqual(response.status_code, 204)
            finally:
                if old_auth is None:
                    os.environ.pop("AUTH_ENABLED", None)
                else:
                    os.environ["AUTH_ENABLED"] = old_auth

    def test_google_login_starts_oauth_redirect(self):
        with app_module.app.test_client() as client:
            old_env = {key: os.environ.get(key) for key in [
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
                os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost/auth/callback"
                os.environ["DRIVE_ROOT_FOLDER_ID"] = "folder-id"
                os.environ["SECRET_KEY"] = "test-secret"

                response = client.get("/login/google")

                self.assertEqual(response.status_code, 302)
                self.assertIn("https://accounts.google.com/o/oauth2/v2/auth", response.headers["Location"])
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_forward_auth_headers_create_editor_session(self):
        with TemporaryDirectory() as temp:
            original_catalog = app_module.catalog
            old_env = {key: os.environ.get(key) for key in [
                "AUTH_ENABLED",
                "AUTH_PROVIDER",
                "SECRET_KEY",
                "ALLOWED_GOOGLE_DOMAIN",
                "AUTH_ROLE_EDITOR_GROUPS",
            ]}
            try:
                app_module.catalog = Catalog(Path(temp))
                app_module.catalog.create_subject("Mathematik 1", "M1")
                os.environ["AUTH_ENABLED"] = "true"
                os.environ["AUTH_PROVIDER"] = "forward_auth"
                os.environ["SECRET_KEY"] = "test-secret"
                os.environ["ALLOWED_GOOGLE_DOMAIN"] = "forum-wi.de"
                os.environ["AUTH_ROLE_EDITOR_GROUPS"] = "Referat Altklausuren"

                response = app_module.app.test_client().get(
                    "/",
                    headers={
                        "X-authentik-email": "editor@forum-wi.de",
                        "X-authentik-name": "Editor User",
                        "X-authentik-groups": "Referat Altklausuren",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"editor", response.data)
            finally:
                app_module.catalog = original_catalog
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_forward_auth_rejects_user_without_mapped_role(self):
        old_env = {key: os.environ.get(key) for key in [
            "AUTH_ENABLED",
            "AUTH_PROVIDER",
            "SECRET_KEY",
            "ALLOWED_GOOGLE_DOMAIN",
            "AUTH_ROLE_VIEWER_GROUPS",
            "AUTH_ROLE_EDITOR_GROUPS",
            "AUTH_ROLE_ADMIN_GROUPS",
            "FORWARD_AUTH_DEFAULT_ROLE",
        ]}
        try:
            os.environ["AUTH_ENABLED"] = "true"
            os.environ["AUTH_PROVIDER"] = "forward_auth"
            os.environ["SECRET_KEY"] = "test-secret"
            os.environ["ALLOWED_GOOGLE_DOMAIN"] = "forum-wi.de"
            os.environ["AUTH_ROLE_VIEWER_GROUPS"] = "Altklausuren Viewer"
            os.environ["AUTH_ROLE_EDITOR_GROUPS"] = "Altklausuren Editor"
            os.environ["AUTH_ROLE_ADMIN_GROUPS"] = "Altklausuren Admin"
            os.environ["FORWARD_AUTH_DEFAULT_ROLE"] = ""

            response = app_module.app.test_client().get(
                "/",
                headers={
                    "X-authentik-email": "person@forum-wi.de",
                    "X-authentik-groups": "Andere Gruppe",
                },
            )

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
