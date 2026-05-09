import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from drive_client import DriveClient, extract_drive_id
from drive_sync import sync_local_folder
from pypdf import PdfReader, PdfWriter


class DriveClientTest(unittest.TestCase):
    def test_extract_drive_id_from_folder_url(self):
        value = extract_drive_id("https://drive.google.com/drive/u/1/folders/0AOnFniEMTZ8bUk9PVA")
        self.assertEqual(value, "0AOnFniEMTZ8bUk9PVA")

    def test_extract_drive_id_accepts_raw_id(self):
        self.assertEqual(extract_drive_id("abc123"), "abc123")

    def test_credentials_mode_detects_missing_and_service_account(self):
        with TemporaryDirectory() as temp:
            credentials_dir = Path(temp) / "credentials"
            client = DriveClient(credentials_dir)
            self.assertEqual(client.credentials_mode(), "missing")

            credentials_dir.mkdir(parents=True)
            (credentials_dir / "service_account.json").write_text("{}", encoding="utf-8")

            self.assertEqual(client.credentials_mode(), "service_account")

    def test_sync_local_folder_imports_pdf_as_subject(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "Drive"
            subject_dir = root / "Mathematik 1"
            subject_dir.mkdir(parents=True)
            source = subject_dir / "Sammlung.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with source.open("wb") as output:
                writer.write(output)

            data_dir = Path(temp) / "data"
            result = sync_local_folder(data_dir=data_dir, root_path=str(root))

            self.assertEqual(result["found"], 1)
            self.assertEqual(result["imported"], 1)
            imported = data_dir / "subjects" / "mathematik-1" / "current.pdf"
            self.assertTrue(imported.exists())
            self.assertEqual(len(PdfReader(str(imported)).pages), 1)


if __name__ == "__main__":
    unittest.main()
