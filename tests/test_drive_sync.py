from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader, PdfWriter

from drive_sync import CONFLICT, DRIVE_NEW, SYNCED, poll_drive_changes, push_subject_to_drive, sync_drive_folder
from drive_sync import _select_print_collections
from drive_sync import _subject_title
from storage import Catalog


def _make_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        writer.write(output)


class FakeDriveClient:
    def __init__(self):
        self.metadata = {
            "file-1": {
                "id": "file-1",
                "name": "DRUCK_Mathe_I.pdf",
                "mimeType": "application/pdf",
                "parents": ["folder-1"],
                "modifiedTime": "2026-01-01T00:00:00Z",
                "md5Checksum": "old",
                "webViewLink": "https://drive.example/file-1",
            }
        }
        self.download_pages = 1
        self.uploaded = []
        self.archived = []

    def list_pdfs_recursive(self, root_url):
        file = dict(self.metadata["file-1"])
        file["folder_path"] = "Altklausuren/Mathematik I"
        raw_file = dict(file)
        raw_file["id"] = "file-raw"
        raw_file["name"] = "Mathe_I_WS24.pdf"
        return [file, raw_file]

    def download_file(self, file_id, target_path):
        _make_pdf(Path(target_path), self.download_pages)

    def get_file_metadata(self, file_id):
        return dict(self.metadata[file_id])

    def find_or_create_archive_folder(self, parent_folder_id, name="_Archiv"):
        return "archive-folder"

    def copy_to_archive_folder(self, file_id, archive_folder_id, name=None):
        self.archived.append((file_id, archive_folder_id, name))
        return {"id": "archive-copy"}

    def upload_new_version(self, file_id, source_path):
        self.uploaded.append((file_id, str(source_path)))
        self.metadata[file_id] = self.metadata[file_id] | {
            "modifiedTime": "2026-01-02T00:00:00Z",
            "md5Checksum": "new",
        }
        return dict(self.metadata[file_id])


class DriveSyncTest(unittest.TestCase):
    def test_subject_title_uses_module_folder_for_klausuren_paths(self):
        self.assertEqual(
            _subject_title(
                {"folder_path": "Drive/Mathe/Mathe 1/Klausuren", "name": "DRUCK_Mathe_I.pdf"},
                "root",
            ),
            "Mathe 1",
        )
        self.assertEqual(
            _subject_title(
                {"folder_path": "Drive/Mathe/Mathe 2/Klausuren/Archiv", "name": "DRUCK_Mathe_2.pdf"},
                "root",
            ),
            "Mathe 2",
        )
        self.assertEqual(
            _subject_title(
                {"folder_path": "Drive/Recht/Vertragsgestaltung im IT-Bereich/PDF", "name": "merged.pdf"},
                "root",
            ),
            "Vertragsgestaltung im IT-Bereich",
        )
        self.assertEqual(
            _subject_title(
                {"folder_path": "Drive/Recht/Internetrecht/Internetrecht/Internetrecht_pdf", "name": "Internetrecht_merged.pdf"},
                "root",
            ),
            "Internetrecht",
        )
        self.assertEqual(
            _subject_title(
                {
                    "folder_path": "Drive/Mathe/Mathe 1/Mathe 1 - Mündliche Protokolle/Mündliche Protokolle/Protokolle Original",
                    "name": "ALT_merged.pdf",
                },
                "root",
            ),
            "Mathe 1 - Mündliche Protokolle",
        )

    def test_select_print_collections_prefers_non_reverse_per_subject(self):
        files = [
            {"folder_path": "Drive/Mathe/Mathe 1/Klausuren", "name": "DRUCK_Mathe_I_reverse.pdf"},
            {"folder_path": "Drive/Mathe/Mathe 1/Klausuren", "name": "DRUCK_Mathe_I.pdf"},
            {"folder_path": "Drive/Recht/Privatrechtliche Übung (PÜ)", "name": "PÜ_DRUCK.pdf"},
        ]

        selected = _select_print_collections(files, "root")

        self.assertEqual(len(selected), 2)
        self.assertIn("DRUCK_Mathe_I.pdf", {file["name"] for file in selected})
        self.assertIn("PÜ_DRUCK.pdf", {file["name"] for file in selected})

    def test_select_print_collections_accepts_merged_pdfs(self):
        files = [
            {"folder_path": "Drive/Recht/Urheberrecht (Master)/PDF", "name": "merged.pdf"},
            {"folder_path": "Drive/Recht/Vertragsgestaltung im IT-Bereich/PDF", "name": "merged.pdf"},
        ]

        selected = _select_print_collections(files, "root")

        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {"Urheberrecht (Master)", "Vertragsgestaltung im IT-Bereich"},
            {_subject_title(file, "root") for file in selected},
        )

    def test_select_print_collections_prefers_druck_over_merged_for_same_subject(self):
        files = [
            {"folder_path": "Drive/Recht/Privatrechtliche Übung (PÜ)", "name": "merged.pdf"},
            {"folder_path": "Drive/Recht/Privatrechtliche Übung (PÜ)", "name": "PÜ_DRUCK.pdf"},
        ]

        selected = _select_print_collections(files, "root")

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["name"], "PÜ_DRUCK.pdf")

    def test_initial_import_maps_drive_file_to_subject(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            client = FakeDriveClient()

            result = sync_drive_folder(data_dir=data_dir, root_url="folder-1", client=client)
            subject = Catalog(data_dir).get_subject("mathematik-i")

            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["source_found"], 2)
            self.assertEqual(subject["drive_sync"]["drive_file_id"], "file-1")
            self.assertEqual(subject["drive_sync"]["sync_status"], SYNCED)
            self.assertTrue((data_dir / "subjects" / "mathematik-i" / "current.pdf").exists())

    def test_initial_import_accepts_druck_inside_filename(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            client = FakeDriveClient()
            client.metadata["file-1"]["name"] = "PÜ_DRUCK.pdf"

            result = sync_drive_folder(data_dir=data_dir, root_url="folder-1", client=client)

            self.assertEqual(result["imported"], 1)

    def test_push_replaces_drive_file_when_remote_is_unchanged(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            catalog = Catalog(data_dir)
            subject = catalog.create_subject("Mathematik I")
            _make_pdf(catalog.subject_dir(subject["id"]) / "current.pdf", 1)
            catalog.update_drive_sync(
                subject["id"],
                {
                    "drive_file_id": "file-1",
                    "drive_folder_id": "folder-1",
                    "drive_filename": "DRUCK_Mathe_I.pdf",
                    "last_drive_fingerprint": "old",
                    "sync_status": SYNCED,
                },
            )
            client = FakeDriveClient()

            result = push_subject_to_drive(data_dir=data_dir, subject_id=subject["id"], client=client)
            updated = Catalog(data_dir).get_subject(subject["id"])

            self.assertTrue(result["pushed"])
            self.assertEqual(updated["drive_sync"]["sync_status"], SYNCED)
            self.assertEqual(updated["drive_sync"]["last_drive_fingerprint"], "new")
            self.assertEqual(len(client.archived), 1)
            self.assertEqual(len(client.uploaded), 1)

    def test_push_does_not_overwrite_when_remote_changed(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            catalog = Catalog(data_dir)
            subject = catalog.create_subject("Mathematik I")
            _make_pdf(catalog.subject_dir(subject["id"]) / "current.pdf", 1)
            catalog.update_drive_sync(
                subject["id"],
                {
                    "drive_file_id": "file-1",
                    "drive_folder_id": "folder-1",
                    "drive_filename": "DRUCK_Mathe_I.pdf",
                    "last_drive_fingerprint": "old",
                    "sync_status": SYNCED,
                },
            )
            client = FakeDriveClient()
            client.metadata["file-1"]["md5Checksum"] = "remote-new"

            result = push_subject_to_drive(data_dir=data_dir, subject_id=subject["id"], client=client)
            updated = Catalog(data_dir).get_subject(subject["id"])

            self.assertFalse(result["pushed"])
            self.assertEqual(result["status"], CONFLICT)
            self.assertEqual(updated["drive_sync"]["sync_status"], CONFLICT)
            self.assertEqual(client.uploaded, [])

    def test_poll_imports_remote_change(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            catalog = Catalog(data_dir)
            subject = catalog.create_subject("Mathematik I")
            _make_pdf(catalog.subject_dir(subject["id"]) / "current.pdf", 1)
            catalog.update_drive_sync(
                subject["id"],
                {
                    "drive_file_id": "file-1",
                    "drive_folder_id": "folder-1",
                    "drive_filename": "DRUCK_Mathe_I.pdf",
                    "last_drive_fingerprint": "old",
                    "sync_status": SYNCED,
                },
            )
            client = FakeDriveClient()
            client.metadata["file-1"]["md5Checksum"] = "remote-new"
            client.download_pages = 2

            result = poll_drive_changes(data_dir=data_dir, client=client)
            updated = Catalog(data_dir).get_subject(subject["id"])
            current = catalog.subject_dir(subject["id"]) / "current.pdf"

            self.assertEqual(result["imported"], 1)
            self.assertEqual(updated["drive_sync"]["sync_status"], DRIVE_NEW)
            self.assertEqual(len(PdfReader(str(current)).pages), 2)


if __name__ == "__main__":
    unittest.main()
