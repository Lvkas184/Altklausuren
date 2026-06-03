from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
import sqlite3
import tarfile
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backup import _backup_sqlite, _create_backup_archive, _rotate_local


class BackupTest(unittest.TestCase):
    def test_backup_sqlite_creates_consistent_database_copy(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            db_path = data_dir / "source.sqlite3"
            backup_path = data_dir / "backup.sqlite3"

            with closing(sqlite3.connect(db_path)) as db:
                db.execute("create table subjects (id text primary key, title text not null)")
                db.execute("insert into subjects (id, title) values (?, ?)", ("m1", "Mathematik 1"))
                db.commit()

            _backup_sqlite(db_path, backup_path)

            with closing(sqlite3.connect(backup_path)) as backup:
                row = backup.execute("select title from subjects where id = ?", ("m1",)).fetchone()

            self.assertEqual(row[0], "Mathematik 1")

    def test_backup_archive_contains_database_and_subject_files_but_not_cache_or_credentials(self):
        with TemporaryDirectory() as temp:
            data_dir = Path(temp)
            db_path = data_dir / "altklausuren.sqlite3"
            backup_path = data_dir / "db-backups" / "altklausuren-20260525-120000.tar.gz"
            backup_path.parent.mkdir()
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("create table subjects (id text primary key, title text not null)")
                db.execute("insert into subjects (id, title) values (?, ?)", ("m1", "Mathematik 1"))
                db.commit()
            (data_dir / "subjects" / "m1").mkdir(parents=True)
            (data_dir / "subjects" / "m1" / "current.pdf").write_bytes(b"%PDF")
            (data_dir / "drive_cache").mkdir()
            (data_dir / "drive_cache" / "file.pdf").write_bytes(b"cache")
            (data_dir / "credentials").mkdir()
            (data_dir / "credentials" / "service_account.json").write_text("{}", encoding="utf-8")
            (data_dir / "drive_config.json").write_text('{"root_url": "folder"}', encoding="utf-8")

            _create_backup_archive(data_dir, db_path, backup_path)

            with tarfile.open(backup_path, "r:gz") as archive:
                names = set(archive.getnames())

            self.assertIn("altklausuren.sqlite3", names)
            self.assertIn("subjects/m1/current.pdf", names)
            self.assertIn("drive_config.json", names)
            self.assertFalse(any(name.startswith("drive_cache/") for name in names))
            self.assertFalse(any(name.startswith("credentials/") for name in names))

    def test_rotate_local_removes_old_archive_files(self):
        with TemporaryDirectory() as temp:
            backup_dir = Path(temp)
            for index in range(9):
                (backup_dir / f"altklausuren-20260525-12000{index}.tar.gz").write_text("backup", encoding="utf-8")

            _rotate_local(backup_dir)

            self.assertEqual(len(list(backup_dir.glob("altklausuren-*.tar.gz"))), 7)


if __name__ == "__main__":
    unittest.main()
