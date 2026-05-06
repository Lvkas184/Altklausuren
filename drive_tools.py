from __future__ import annotations

import argparse
import sys
from pathlib import Path

from drive_client import DriveClient, DriveSetupError
from drive_sync import sync_drive_folder


DATA_DIR = Path(__file__).resolve().parent / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Google-Drive-Werkzeuge fuer die Altklausuren-App")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("authorize", help="Google OAuth Login starten und Token lokal speichern")

    sync_parser = subparsers.add_parser("sync", help="Drive-Ordner rekursiv importieren")
    sync_parser.add_argument("root_url", help="Google-Drive-Ordner-URL oder Ordner-ID")

    local_sync_parser = subparsers.add_parser("local-sync", help="Lokalen Drive-Desktop-Ordner importieren")
    local_sync_parser.add_argument("root_path", help="Lokaler Ordnerpfad")

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
    except DriveSetupError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
