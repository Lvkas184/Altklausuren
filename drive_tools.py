from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from config import load_dotenv
from drive_client import DriveClient, DriveSetupError
from drive_sync import (
    _is_print_collection,
    _select_print_collections,
    accept_drive_version,
    load_drive_config,
    poll_drive_changes,
    push_subject_to_drive,
    sync_drive_folder,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.getenv("ALTKLAUSUREN_DATA_DIR", BASE_DIR / "data")).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description="Google-Drive-Werkzeuge fuer die Altklausuren-App")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("authorize", help="Google OAuth Login starten und Token lokal speichern")

    check_parser = subparsers.add_parser("check", help="Drive-Zugriff mit den aktuellen Credentials prüfen")
    check_parser.add_argument("root_url", nargs="?", help="Google-Drive-Ordner-URL oder Ordner-ID")

    list_parser = subparsers.add_parser("list", help="Drive-PDFs anzeigen, ohne sie zu importieren")
    list_parser.add_argument("root_url", nargs="?", help="Google-Drive-Ordner-URL oder Ordner-ID")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximale Anzahl angezeigter PDFs")
    list_parser.add_argument("--all", action="store_true", help="Alle PDFs anzeigen, nicht nur DRUCK-Sammlungen")

    sync_parser = subparsers.add_parser("sync", help="Drive-Ordner rekursiv importieren")
    sync_parser.add_argument("root_url", help="Google-Drive-Ordner-URL oder Ordner-ID")
    sync_parser.add_argument("--all", action="store_true", help="Alle PDFs importieren, nicht nur DRUCK-Sammlungen")

    local_sync_parser = subparsers.add_parser("local-sync", help="Lokalen Drive-Desktop-Ordner importieren")
    local_sync_parser.add_argument("root_path", help="Lokaler Ordnerpfad")

    poll_parser = subparsers.add_parser("poll", help="Drive-Dateien auf externe Änderungen prüfen")

    push_parser = subparsers.add_parser("push", help="Lokale Fach-PDF nach Drive hochladen")
    push_parser.add_argument("subject_id", help="Fach-ID")
    push_parser.add_argument("--force", action="store_true", help="Drive-Konflikt bewusst ueberschreiben")

    accept_parser = subparsers.add_parser("accept-drive", help="Drive-Version fuer ein Fach lokal übernehmen")
    accept_parser.add_argument("subject_id", help="Fach-ID")

    args = parser.parse_args()

    try:
        if args.command == "authorize":
            DriveClient(DATA_DIR / "credentials").authorize()
            print("Google Drive wurde autorisiert.")
        elif args.command == "check":
            root_url = args.root_url or load_drive_config(DATA_DIR).get("root_url") or os.getenv("DRIVE_ROOT_FOLDER_ID", "")
            result = DriveClient(DATA_DIR / "credentials").check_access(root_url)
            print(f"Credential-Modus: {result['credential_mode']}")
            print(f"Ordner: {result['folder_name']} ({result['folder_id']})")
            print(f"MIME-Type: {result['mime_type']}")
        elif args.command == "list":
            root_url = args.root_url or load_drive_config(DATA_DIR).get("root_url") or os.getenv("DRIVE_ROOT_FOLDER_ID", "")
            files = DriveClient(DATA_DIR / "credentials").list_pdfs_recursive(root_url)
            source_count = len(files)
            if not args.all:
                files = _select_print_collections(files, root_url)
            print(f"{len(files)} PDFs angezeigt ({source_count} PDFs insgesamt gefunden).")
            for file in files[: args.limit]:
                print(f"- {file.get('folder_path', '')} / {file.get('name', '')}")
            if len(files) > args.limit:
                print(f"... {len(files) - args.limit} weitere PDFs nicht angezeigt.")
        elif args.command == "sync":
            result = sync_drive_folder(data_dir=DATA_DIR, root_url=args.root_url, include_all=args.all)
            print(
                f"{result['found']} PDFs gefunden, {result['imported']} importiert, "
                f"{result['skipped']} unveraendert."
            )
        elif args.command == "local-sync":
            from drive_sync import sync_local_folder

            result = sync_local_folder(data_dir=DATA_DIR, root_path=args.root_path)
            print(
                f"{result['found']} PDFs gefunden, {result['imported']} importiert, "
                f"{result['skipped']} unveraendert."
            )
        elif args.command == "poll":
            result = poll_drive_changes(data_dir=DATA_DIR)
            print(
                f"{result['checked']} Fächern geprueft, {result['imported']} Drive-Änderungen importiert, "
                f"{result['conflicts']} Konflikte, {result['errors']} Fehler."
            )
        elif args.command == "push":
            result = push_subject_to_drive(data_dir=DATA_DIR, subject_id=args.subject_id, force=args.force)
            print(f"Status: {result['status']}")
        elif args.command == "accept-drive":
            result = accept_drive_version(data_dir=DATA_DIR, subject_id=args.subject_id)
            print(f"Status: {result['status']}")
    except DriveSetupError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
