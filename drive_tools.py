from __future__ import annotations

import argparse
import sys
from pathlib import Path

from drive_client import DriveClient, DriveSetupError
from drive_sync import accept_drive_version, poll_drive_changes, push_subject_to_drive, sync_drive_folder


DATA_DIR = Path(__file__).resolve().parent / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Google-Drive-Werkzeuge fuer die Altklausuren-App")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("authorize", help="Google OAuth Login starten und Token lokal speichern")

    sync_parser = subparsers.add_parser("sync", help="Drive-Ordner rekursiv importieren")
    sync_parser.add_argument("root_url", help="Google-Drive-Ordner-URL oder Ordner-ID")

    local_sync_parser = subparsers.add_parser("local-sync", help="Lokalen Drive-Desktop-Ordner importieren")
    local_sync_parser.add_argument("root_path", help="Lokaler Ordnerpfad")

    poll_parser = subparsers.add_parser("poll", help="Drive-Dateien auf externe Aenderungen pruefen")

    push_parser = subparsers.add_parser("push", help="Lokale Fach-PDF nach Drive hochladen")
    push_parser.add_argument("subject_id", help="Fach-ID")
    push_parser.add_argument("--force", action="store_true", help="Drive-Konflikt bewusst ueberschreiben")

    accept_parser = subparsers.add_parser("accept-drive", help="Drive-Version fuer ein Fach lokal uebernehmen")
    accept_parser.add_argument("subject_id", help="Fach-ID")

    args = parser.parse_args()

    try:
        if args.command == "authorize":
            DriveClient(DATA_DIR / "credentials").authorize()
            print("Google Drive wurde autorisiert.")
        elif args.command == "sync":
            result = sync_drive_folder(data_dir=DATA_DIR, root_url=args.root_url)
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
                f"{result['checked']} Faecher geprueft, {result['imported']} Drive-Aenderungen importiert, "
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
