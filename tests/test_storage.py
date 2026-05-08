from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage import Catalog


class StorageTest(unittest.TestCase):
    def test_json_catalog_is_migrated_to_sqlite(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "catalog.json").write_text(
                json.dumps(
                    {
                        "subjects": {
                            "mathematik-i": {
                                "id": "mathematik-i",
                                "slug": "mathematik-i",
                                "title": "Mathematik I",
                                "code": "M1",
                                "current_pages": 4,
                                "submissions": [
                                    {
                                        "id": "sub-1",
                                        "kind": "Altklausur",
                                        "term": "WiSe 2024/25",
                                        "stored_upload": "incoming/a.pdf",
                                        "added_pages": 3,
                                    }
                                ],
                                "drive_sync": {
                                    "drive_file_id": "file-1",
                                    "drive_filename": "DRUCK_Mathe_I.pdf",
                                    "last_drive_fingerprint": "abc",
                                    "sync_status": "synced",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            catalog = Catalog(data_dir)
            subject = catalog.get_subject("mathematik-i")

            self.assertTrue((data_dir / "altklausuren.sqlite3").exists())
            self.assertEqual(subject["title"], "Mathematik I")
            self.assertEqual(subject["current_pages"], 4)
            self.assertEqual(subject["submissions"][0]["id"], "sub-1")
            self.assertEqual(subject["drive_sync"]["drive_file_id"], "file-1")


if __name__ == "__main__":
    unittest.main()
