from __future__ import annotations

import json
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator


class Catalog:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.catalog_path = data_dir / "catalog.json"
        self.db_path = data_dir / "altklausuren.sqlite3"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._migrate_json_catalog()
        self._migrate_subjects_without_entries()
        self._migrate_add_no_cover()

    def list_subjects(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("select * from subjects order by lower(title)").fetchall()
        return [self._subject_from_row(row) for row in rows]

    def get_subject(self, subject_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("select * from subjects where id = ?", (subject_id,)).fetchone()
        return self._subject_from_row(row) if row else None

    def find_or_create_subject(self, title: str, code: str = "") -> dict:
        with self._connect() as db:
            row = db.execute("select * from subjects where lower(title) = lower(?)", (title,)).fetchone()
        if row:
            return self._subject_from_row(row)
        return self.create_subject(title=title, code=code)

    def create_subject(self, title: str, code: str = "") -> dict:
        subject_id = _slugify(code or title)
        with self._connect() as db:
            if db.execute("select 1 from subjects where id = ?", (subject_id,)).fetchone():
                subject_id = f"{subject_id}-{secrets.token_hex(2)}"
            now = _now()
            db.execute(
                """
                insert into subjects (id, slug, title, code, created_at, updated_at, current_pages)
                values (?, ?, ?, ?, ?, ?, 0)
                """,
                (subject_id, subject_id, title, code, now, now),
            )
            db.commit()
        self.subject_dir(subject_id).mkdir(parents=True, exist_ok=True)
        self._audit("subject_created", subject_id, {"title": title, "code": code})
        return self.get_subject(subject_id)

    def add_submission(self, subject_id: str, submission: dict) -> dict:
        submission_id = submission.get("id") or f"sub-{secrets.token_hex(8)}"
        sort_order = submission.get("sort_order")
        with self._connect() as db:
            if sort_order is None:
                max_order = db.execute(
                    "select coalesce(max(sort_order), 0) from submissions where subject_id = ?",
                    (subject_id,),
                ).fetchone()[0]
                sort_order = max_order + 1
            now = _now()
            db.execute(
                """
                insert into submissions (
                    id, subject_id, kind, term, exam_date, instructor, solution, notes,
                    original_filename, stored_upload, added_pages, existing_body_pages,
                    current_pages, export_path, strip_uploaded_cover, collection_import,
                    sort_order, added_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    subject_id,
                    submission.get("kind", ""),
                    submission.get("term", ""),
                    submission.get("exam_date", ""),
                    submission.get("instructor", ""),
                    submission.get("solution", ""),
                    submission.get("notes", ""),
                    submission.get("original_filename", ""),
                    submission.get("stored_upload", ""),
                    int(submission.get("added_pages", 0) or 0),
                    int(submission.get("existing_body_pages", 0) or 0),
                    int(submission.get("current_pages", 0) or 0),
                    submission.get("export_path", ""),
                    1 if submission.get("strip_uploaded_cover") else 0,
                    1 if submission.get("collection_import") else 0,
                    int(sort_order),
                    submission.get("added_at") or now,
                    now,
                ),
            )
            db.execute(
                "update subjects set updated_at = ?, current_pages = ? where id = ?",
                (now, int(submission.get("current_pages", 0) or 0), subject_id),
            )
            db.commit()
        self._audit("submission_created", subject_id, {"submission_id": submission_id})
        return self.get_submission(subject_id, submission_id)

    def get_submission(self, subject_id: str, submission_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "select * from submissions where subject_id = ? and id = ?",
                (subject_id, submission_id),
            ).fetchone()
        return _submission_from_row(row) if row else None

    def update_submission(self, subject_id: str, submission_id: str, updates: dict) -> dict | None:
        allowed = {"kind", "term", "exam_date", "instructor", "solution", "notes", "sort_order"}
        fields = [key for key in updates if key in allowed]
        if not fields:
            return self.get_submission(subject_id, submission_id)
        assignments = ", ".join(f"{field} = ?" for field in fields)
        values = [updates[field] for field in fields]
        values.extend([_now(), subject_id, submission_id])
        with self._connect() as db:
            db.execute(
                f"update submissions set {assignments}, updated_at = ? where subject_id = ? and id = ?",
                values,
            )
            db.execute("update subjects set updated_at = ? where id = ?", (_now(), subject_id))
            db.commit()
        self._audit("submission_updated", subject_id, {"submission_id": submission_id, "fields": fields})
        return self.get_submission(subject_id, submission_id)

    def delete_submission(self, subject_id: str, submission_id: str) -> None:
        with self._connect() as db:
            db.execute("delete from submissions where subject_id = ? and id = ?", (subject_id, submission_id))
            db.execute("update subjects set updated_at = ? where id = ?", (_now(), subject_id))
            db.commit()
        self._audit("submission_deleted", subject_id, {"submission_id": submission_id})

    def reorder_submissions(self, subject_id: str, ordered_ids: list[str]) -> None:
        with self._connect() as db:
            for index, submission_id in enumerate(ordered_ids, start=1):
                db.execute(
                    "update submissions set sort_order = ?, updated_at = ? where subject_id = ? and id = ?",
                    (index, _now(), subject_id, submission_id),
                )
            db.execute("update subjects set updated_at = ? where id = ?", (_now(), subject_id))
            db.commit()
        self._audit("submissions_reordered", subject_id, {"ordered_ids": ordered_ids})

    def set_current_pages(self, subject_id: str, current_pages: int) -> None:
        with self._connect() as db:
            db.execute(
                "update subjects set current_pages = ?, updated_at = ? where id = ?",
                (current_pages, _now(), subject_id),
            )
            db.commit()

    def update_drive_subject(self, subject_id: str, drive_metadata: dict, current_pages: int) -> None:
        sync_metadata = _normalize_drive_sync(drive_metadata)
        with self._connect() as db:
            db.execute(
                "update subjects set current_pages = ?, updated_at = ? where id = ?",
                (current_pages, _now(), subject_id),
            )
            self._upsert_drive_sync(db, subject_id, sync_metadata)
            db.commit()
        self._audit("drive_subject_updated", subject_id, sync_metadata)

    def update_drive_sync(self, subject_id: str, sync_metadata: dict, current_pages: int | None = None) -> None:
        with self._connect() as db:
            existing = db.execute("select * from drive_sync where subject_id = ?", (subject_id,)).fetchone()
            merged = (_drive_sync_from_row(existing) if existing else {}) | sync_metadata
            self._upsert_drive_sync(db, subject_id, merged)
            if current_pages is not None:
                db.execute(
                    "update subjects set current_pages = ?, updated_at = ? where id = ?",
                    (current_pages, _now(), subject_id),
                )
            else:
                db.execute("update subjects set updated_at = ? where id = ?", (_now(), subject_id))
            db.commit()
        self._audit("drive_sync_updated", subject_id, sync_metadata)

    def set_sync_status(self, subject_id: str, status: str, error: str = "") -> None:
        self.update_drive_sync(
            subject_id,
            {
                "sync_status": status,
                "last_sync_error": error,
                "last_sync_attempt_at": _now(),
            },
        )

    def delete_subject(self, subject_id: str) -> None:
        with self._connect() as db:
            db.execute("delete from subjects where id = ?", (subject_id,))
            db.commit()
        self._audit("subject_deleted", subject_id, {})

    def update_subject(self, subject_id: str, title: str, code: str, no_cover: bool = False) -> dict | None:
        with self._connect() as db:
            db.execute(
                "update subjects set title = ?, code = ?, no_cover = ?, updated_at = ? where id = ?",
                (title, code, int(no_cover), _now(), subject_id),
            )
            db.commit()
        self._audit("subject_updated", subject_id, {"title": title, "code": code})
        return self.get_subject(subject_id)

    def subject_dir(self, subject_id: str) -> Path:
        return self.data_dir / "subjects" / subject_id

    def _subject_from_row(self, row: sqlite3.Row) -> dict:
        subject = dict(row)
        with self._connect() as db:
            submissions = db.execute(
                "select * from submissions where subject_id = ? order by sort_order, added_at, id",
                (subject["id"],),
            ).fetchall()
            sync = db.execute("select * from drive_sync where subject_id = ?", (subject["id"],)).fetchone()
        subject["submissions"] = [_submission_from_row(submission) for submission in submissions]
        subject["drive_sync"] = _drive_sync_from_row(sync) if sync else {}
        subject["drive"] = subject["drive_sync"]
        return subject

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                create table if not exists subjects (
                    id text primary key,
                    slug text not null,
                    title text not null,
                    code text not null default '',
                    created_at text not null,
                    updated_at text not null,
                    current_pages integer not null default 0,
                    no_cover integer not null default 0
                );
                create table if not exists submissions (
                    id text primary key,
                    subject_id text not null references subjects(id) on delete cascade,
                    kind text not null default '',
                    term text not null default '',
                    exam_date text not null default '',
                    instructor text not null default '',
                    solution text not null default '',
                    notes text not null default '',
                    original_filename text not null default '',
                    stored_upload text not null default '',
                    added_pages integer not null default 0,
                    existing_body_pages integer not null default 0,
                    current_pages integer not null default 0,
                    export_path text not null default '',
                    strip_uploaded_cover integer not null default 0,
                    collection_import integer not null default 0,
                    sort_order integer not null default 0,
                    added_at text not null,
                    updated_at text not null
                );
                create table if not exists drive_sync (
                    subject_id text primary key references subjects(id) on delete cascade,
                    drive_file_id text not null default '',
                    drive_folder_id text not null default '',
                    drive_folder_path text not null default '',
                    drive_filename text not null default '',
                    last_drive_modified_time text not null default '',
                    last_drive_md5 text not null default '',
                    last_drive_fingerprint text not null default '',
                    last_synced_at text not null default '',
                    sync_status text not null default 'unmapped',
                    last_sync_error text not null default '',
                    last_sync_attempt_at text not null default '',
                    web_view_link text not null default '',
                    archive_folder_id text not null default '',
                    remote_drive_fingerprint text not null default '',
                    remote_drive_modified_time text not null default '',
                    remote_drive_md5 text not null default ''
                );
                create table if not exists files (
                    id text primary key,
                    subject_id text not null references subjects(id) on delete cascade,
                    submission_id text references submissions(id) on delete set null,
                    role text not null,
                    path text not null,
                    original_filename text not null default '',
                    created_at text not null
                );
                create table if not exists audit_log (
                    id integer primary key autoincrement,
                    event text not null,
                    subject_id text,
                    payload text not null default '{}',
                    created_at text not null
                );
                """
            )
            self._ensure_column(db, "drive_sync", "drive_folder_path", "text not null default ''")
            db.commit()

    def _migrate_json_catalog(self) -> None:
        if not self.catalog_path.exists():
            return
        with self._connect() as db:
            existing = db.execute("select count(*) from subjects").fetchone()[0]
            if existing:
                return
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            for subject in data.get("subjects", {}).values():
                db.execute(
                    """
                    insert or ignore into subjects (id, slug, title, code, created_at, updated_at, current_pages)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        subject["id"],
                        subject.get("slug", subject["id"]),
                        subject.get("title", subject["id"]),
                        subject.get("code", ""),
                        subject.get("created_at", _now()),
                        subject.get("updated_at", _now()),
                        int(subject.get("current_pages", 0) or 0),
                    ),
                )
                for index, submission in enumerate(subject.get("submissions", []), start=1):
                    submission_id = submission.get("id") or f"sub-{secrets.token_hex(8)}"
                    db.execute(
                        """
                        insert or ignore into submissions (
                            id, subject_id, kind, term, exam_date, instructor, solution, notes,
                            original_filename, stored_upload, added_pages, existing_body_pages,
                            current_pages, export_path, strip_uploaded_cover, collection_import,
                            sort_order, added_at, updated_at
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            submission_id,
                            subject["id"],
                            submission.get("kind", ""),
                            submission.get("term", ""),
                            submission.get("exam_date", ""),
                            submission.get("instructor", ""),
                            submission.get("solution", ""),
                            submission.get("notes", ""),
                            submission.get("original_filename", ""),
                            submission.get("stored_upload", ""),
                            int(submission.get("added_pages", 0) or 0),
                            int(submission.get("existing_body_pages", 0) or 0),
                            int(submission.get("current_pages", subject.get("current_pages", 0)) or 0),
                            submission.get("export_path", ""),
                            1 if submission.get("strip_uploaded_cover") else _infer_strip_uploaded_cover(submission),
                            1 if submission.get("collection_import") else 0,
                            int(submission.get("sort_order", index) or index),
                            submission.get("added_at", _now()),
                            submission.get("updated_at", submission.get("added_at", _now())),
                        ),
                    )
                drive_metadata = subject.get("drive_sync") or subject.get("drive") or {}
                if drive_metadata:
                    self._upsert_drive_sync(db, subject["id"], _normalize_drive_sync(drive_metadata))
            db.execute(
                "insert into audit_log (event, subject_id, payload, created_at) values (?, ?, ?, ?)",
                ("json_catalog_migrated", None, "{}", _now()),
            )
            db.commit()

    def _upsert_drive_sync(self, db: sqlite3.Connection, subject_id: str, metadata: dict) -> None:
        normalized = _normalize_drive_sync(metadata)
        db.execute(
            """
            insert into drive_sync (
                subject_id, drive_file_id, drive_folder_id, drive_folder_path,
                drive_filename, last_drive_modified_time, last_drive_md5, last_drive_fingerprint,
                last_synced_at, sync_status, last_sync_error, last_sync_attempt_at,
                web_view_link, archive_folder_id, remote_drive_fingerprint,
                remote_drive_modified_time, remote_drive_md5
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(subject_id) do update set
                drive_file_id = excluded.drive_file_id,
                drive_folder_id = excluded.drive_folder_id,
                drive_folder_path = excluded.drive_folder_path,
                drive_filename = excluded.drive_filename,
                last_drive_modified_time = excluded.last_drive_modified_time,
                last_drive_md5 = excluded.last_drive_md5,
                last_drive_fingerprint = excluded.last_drive_fingerprint,
                last_synced_at = excluded.last_synced_at,
                sync_status = excluded.sync_status,
                last_sync_error = excluded.last_sync_error,
                last_sync_attempt_at = excluded.last_sync_attempt_at,
                web_view_link = excluded.web_view_link,
                archive_folder_id = excluded.archive_folder_id,
                remote_drive_fingerprint = excluded.remote_drive_fingerprint,
                remote_drive_modified_time = excluded.remote_drive_modified_time,
                remote_drive_md5 = excluded.remote_drive_md5
            """,
            (
                subject_id,
                normalized.get("drive_file_id", ""),
                normalized.get("drive_folder_id", ""),
                normalized.get("drive_folder_path", ""),
                normalized.get("drive_filename", ""),
                normalized.get("last_drive_modified_time", ""),
                normalized.get("last_drive_md5", ""),
                normalized.get("last_drive_fingerprint", ""),
                normalized.get("last_synced_at", ""),
                normalized.get("sync_status", "unmapped"),
                normalized.get("last_sync_error", ""),
                normalized.get("last_sync_attempt_at", ""),
                normalized.get("web_view_link", ""),
                normalized.get("archive_folder_id", ""),
                normalized.get("remote_drive_fingerprint", ""),
                normalized.get("remote_drive_modified_time", ""),
                normalized.get("remote_drive_md5", ""),
            ),
        )

    def _ensure_column(self, db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"alter table {table} add column {column} {definition}")

    def add_collection_import_if_missing(self, subject_id: str, source_pdf: Path, filename: str) -> None:
        subject = self.get_subject(subject_id)
        if not subject or subject.get("submissions"):
            return
        subject_dir = self.subject_dir(subject_id)
        incoming_dir = subject_dir / "incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        stored_path = incoming_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{filename}"
        shutil.copy2(source_pdf, stored_path)
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(str(source_pdf)).pages)
        except Exception:
            pages = 0
        self.add_submission(subject_id, {
            "kind": "Importierte Sammlung",
            "notes": "Beim Import automatisch erstellt.",
            "original_filename": filename,
            "stored_upload": str(stored_path.relative_to(subject_dir)),
            "added_pages": 0,
            "existing_body_pages": 0,
            "current_pages": pages,
            "export_path": "",
            "strip_uploaded_cover": False,
            "collection_import": True,
        })

    def _migrate_add_no_cover(self) -> None:
        with self._connect() as db:
            cols = [row[1] for row in db.execute("pragma table_info(subjects)").fetchall()]
            if "no_cover" not in cols:
                db.execute("alter table subjects add column no_cover integer not null default 0")
                db.commit()

    def _migrate_subjects_without_entries(self) -> None:
        with self._connect() as db:
            rows = db.execute(
                "select id from subjects where not exists "
                "(select 1 from submissions where subject_id = subjects.id)"
            ).fetchall()
        for row in rows:
            subject_id = row["id"]
            current_path = self.subject_dir(subject_id) / "current.pdf"
            if current_path.exists():
                self.add_collection_import_if_missing(subject_id, current_path, "imported.pdf")

    def _audit(self, event: str, subject_id: str | None, payload: dict) -> None:
        with self._connect() as db:
            db.execute(
                "insert into audit_log (event, subject_id, payload, created_at) values (?, ?, ?, ?)",
                (event, subject_id, json.dumps(payload, ensure_ascii=False), _now()),
            )
            db.commit()


def _submission_from_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["strip_uploaded_cover"] = bool(item.get("strip_uploaded_cover"))
    item["collection_import"] = bool(item.get("collection_import"))
    return item


def _drive_sync_from_row(row: sqlite3.Row | None) -> dict:
    return dict(row) if row else {}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or f"fach-{secrets.token_hex(2)}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _infer_strip_uploaded_cover(submission: dict) -> int:
    return 1 if submission.get("added_pages", 0) and not submission.get("collection_import") else 0


def _normalize_drive_sync(drive_metadata: dict) -> dict:
    file_id = drive_metadata.get("drive_file_id") or drive_metadata.get("file_id", "")
    folder_id = drive_metadata.get("drive_folder_id") or drive_metadata.get("folder_id", "")
    folder_path = drive_metadata.get("drive_folder_path") or drive_metadata.get("folder_path", "")
    filename = drive_metadata.get("drive_filename") or drive_metadata.get("name", "")
    modified_time = drive_metadata.get("last_drive_modified_time") or drive_metadata.get("modified_time", "")
    md5 = drive_metadata.get("last_drive_md5") or drive_metadata.get("md5_checksum", "")
    fingerprint = drive_metadata.get("last_drive_fingerprint") or drive_metadata.get("fingerprint") or md5 or modified_time or file_id
    return {
        "drive_file_id": file_id,
        "drive_folder_id": folder_id,
        "drive_folder_path": folder_path,
        "drive_filename": filename,
        "last_drive_modified_time": modified_time,
        "last_drive_md5": md5,
        "last_drive_fingerprint": fingerprint,
        "last_synced_at": drive_metadata.get("last_synced_at") or drive_metadata.get("synced_at", ""),
        "sync_status": drive_metadata.get("sync_status", "synced" if file_id else "unmapped"),
        "last_sync_error": drive_metadata.get("last_sync_error", ""),
        "last_sync_attempt_at": drive_metadata.get("last_sync_attempt_at", ""),
        "web_view_link": drive_metadata.get("web_view_link", ""),
        "archive_folder_id": drive_metadata.get("archive_folder_id", ""),
        "remote_drive_fingerprint": drive_metadata.get("remote_drive_fingerprint", ""),
        "remote_drive_modified_time": drive_metadata.get("remote_drive_modified_time", ""),
        "remote_drive_md5": drive_metadata.get("remote_drive_md5", ""),
    }
