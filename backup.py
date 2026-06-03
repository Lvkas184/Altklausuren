#!/usr/bin/env python3
"""
Tägliches Datenbackup für Altklausuren.

Was es tut:
  1. Erstellt ein tar.gz-Archiv mit SQLite-Backup und lokalen PDF-Daten
  2. Löscht lokale Backups die älter als KEEP_LOCAL (7) Tage sind
  3. Lädt das Archiv nach Google Drive hoch (wenn BACKUP_DRIVE_FOLDER_ID gesetzt)
  4. Löscht ältere Backups auf Drive (behält die neuesten KEEP_DRIVE Kopien)

Verwendung:
  python backup.py

Benötigte Umgebungsvariablen:
  ALTKLAUSUREN_DATA_DIR   Pfad zum data/-Verzeichnis (Standard: ./data)
  BACKUP_DRIVE_FOLDER_ID  Google-Drive-Ordner-ID für Offsite-Backup (optional)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tarfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

KEEP_LOCAL = 7
KEEP_DRIVE = 7


def main() -> None:
    data_dir = Path(os.environ.get("ALTKLAUSUREN_DATA_DIR", Path(__file__).parent / "data"))
    db_path = data_dir / "altklausuren.sqlite3"

    if not db_path.exists():
        print(f"FEHLER: Datenbank nicht gefunden: {db_path}", file=sys.stderr)
        sys.exit(1)

    backup_dir = data_dir / "db-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"altklausuren-{stamp}.tar.gz"
    _create_backup_archive(data_dir, db_path, backup_path)
    print(f"Backup erstellt: {backup_path.name} ({backup_path.stat().st_size // 1024} KB)")

    _rotate_local(backup_dir)

    folder_id = os.environ.get("BACKUP_DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        print("BACKUP_DRIVE_FOLDER_ID nicht gesetzt — kein Drive-Upload.")
        return

    _upload_to_drive(backup_path, folder_id, data_dir)


def _rotate_local(backup_dir: Path) -> None:
    backups = sorted(backup_dir.glob("altklausuren-*.tar.gz"))
    for old in backups[:-KEEP_LOCAL]:
        old.unlink()
        print(f"Lokal gelöscht: {old.name}")


def _backup_sqlite(db_path: Path, backup_path: Path) -> None:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)


def _create_backup_archive(data_dir: Path, db_path: Path, backup_path: Path) -> None:
    with TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        sqlite_copy = temp_dir / "altklausuren.sqlite3"
        _backup_sqlite(db_path, sqlite_copy)

        with tarfile.open(backup_path, "w:gz") as archive:
            archive.add(sqlite_copy, arcname="altklausuren.sqlite3")

            subjects_dir = data_dir / "subjects"
            if subjects_dir.exists():
                archive.add(subjects_dir, arcname="subjects", filter=_backup_tar_filter)

            for json_path in sorted(data_dir.glob("*.json")):
                archive.add(json_path, arcname=json_path.name, filter=_backup_tar_filter)


def _backup_tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    excluded = {"drive_cache", "credentials", "db-backups"}
    if any(part in excluded for part in Path(info.name).parts):
        return None
    return info


def _upload_to_drive(backup_path: Path, folder_id: str, data_dir: Path) -> None:
    try:
        from drive_client import DriveClient
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        print(f"Drive-Abhängigkeit fehlt: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        client = DriveClient(data_dir / "credentials")
        service = client.service()

        media = MediaFileUpload(str(backup_path), mimetype="application/gzip", resumable=False)
        result = service.files().create(
            body={"name": backup_path.name, "parents": [folder_id]},
            media_body=media,
            fields="id, name",
            supportsAllDrives=True,
        ).execute()
        print(f"Drive-Upload: {result['name']} (id={result['id']})")

        _rotate_drive(service, folder_id)

    except Exception as exc:
        print(f"Drive-Upload fehlgeschlagen: {exc}", file=sys.stderr)
        sys.exit(1)


def _rotate_drive(service, folder_id: str) -> None:
    response = service.files().list(
        q=f"'{folder_id}' in parents and trashed = false and name contains 'altklausuren-'",
        fields="files(id, name)",
        orderBy="createdTime asc",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=100,
    ).execute()
    old_files = response.get("files", [])[:-KEEP_DRIVE]
    for old in old_files:
        service.files().delete(fileId=old["id"], supportsAllDrives=True).execute()
        print(f"Drive gelöscht: {old['name']}")


if __name__ == "__main__":
    main()
