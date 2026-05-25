from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
import sqlite3
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backup import _backup_sqlite


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


if __name__ == "__main__":
    unittest.main()
