from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from drive_client import DriveClient, DriveSetupError, extract_drive_id
from storage import Catalog


CONFIG_PATH = Path("data") / "drive_config.json"


def load_drive_config(data_dir: Path) -> dict:
    path = data_dir / "drive_config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_drive_config(data_dir: Path, config: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "drive_config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def sync_drive_folder(*, data_dir: Path, root_url: str) -> dict:
    catalog = Catalog(data_dir)
    client = DriveClient(data_dir / "credentials")
    files = client.list_pdfs_recursive(root_url)

    imported = 0
    skipped = 0
    cache_dir = data_dir / "drive_cache"
    sync_started_at = datetime.now().isoformat(timespec="seconds")

    for file in files:
        subject_title = _subject_title(file, root_url)
        subject = catalog.find_or_create_subject(title=subject_title, code="")
        fingerprint = file.get("md5Checksum") or file.get("modifiedTime") or file["id"]

        if subject.get("drive", {}).get("fingerprint") == fingerprint:
            skipped += 1
            continue

        cache_path = cache_dir / f"{file['id']}.pdf"
        client.download_file(file["id"], cache_path)

        subject_dir = catalog.subject_dir(subject["id"])
        current_path = subject_dir / "current.pdf"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, current_path)

        catalog.update_drive_subject(
            subject["id"],
            {
                "file_id": file["id"],
                "name": file["name"],
                "folder_path": file["folder_path"],
                "web_view_link": file.get("webViewLink", ""),
                "modified_time": file.get("modifiedTime", ""),
                "size": file.get("size", ""),
                "md5_checksum": file.get("md5Checksum", ""),
                "fingerprint": fingerprint,
                "synced_at": sync_started_at,
                "cache_path": str(cache_path.relative_to(data_dir)),
            },
            current_pages=_page_count(current_path),
        )
        imported += 1

    save_drive_config(
        data_dir,
        {
            "root_url": root_url,
            "root_id": extract_drive_id(root_url),
            "last_sync_at": sync_started_at,
            "last_file_count": len(files),
        },
    )

    return {"found": len(files), "imported": imported, "skipped": skipped, "synced_at": sync_started_at}


def sync_local_folder(*, data_dir: Path, root_path: str) -> dict:
    root = Path(root_path).expanduser()
    if not root.exists() or not root.is_dir():
        raise DriveSetupError("Der lokale Drive-Ordner wurde nicht gefunden.")

    catalog = Catalog(data_dir)
    files = sorted(root.rglob("*.pdf"), key=lambda path: str(path).lower())
    imported = 0
    skipped = 0
    cache_dir = data_dir / "drive_cache"
    sync_started_at = datetime.now().isoformat(timespec="seconds")

    for file_path in files:
        relative = file_path.relative_to(root)
        subject_title = _local_subject_title(relative)
        subject = catalog.find_or_create_subject(title=subject_title, code="")
        stat = file_path.stat()
        fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"

        if subject.get("drive", {}).get("fingerprint") == fingerprint:
            skipped += 1
            continue

        cache_path = cache_dir / "local" / relative
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, cache_path)

        subject_dir = catalog.subject_dir(subject["id"])
        current_path = subject_dir / "current.pdf"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, current_path)

        catalog.update_drive_subject(
            subject["id"],
            {
                "source": "local",
                "name": file_path.name,
                "folder_path": str(relative.parent),
                "local_source_path": str(file_path),
                "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "size": str(stat.st_size),
                "fingerprint": fingerprint,
                "synced_at": sync_started_at,
                "cache_path": str(cache_path.relative_to(data_dir)),
            },
            current_pages=_page_count(current_path),
        )
        imported += 1

    config = load_drive_config(data_dir)
    config.update(
        {
            "local_root_path": str(root),
            "last_local_sync_at": sync_started_at,
            "last_local_file_count": len(files),
        }
    )
    save_drive_config(data_dir, config)

    return {"found": len(files), "imported": imported, "skipped": skipped, "synced_at": sync_started_at}


def _subject_title(file: dict, root_url: str) -> str:
    folder_parts = [part for part in file["folder_path"].split("/") if part]
    if len(folder_parts) >= 2:
        return folder_parts[-1]
    return Path(file["name"]).stem


def _local_subject_title(relative_path: Path) -> str:
    if len(relative_path.parts) >= 2:
        return relative_path.parts[-2]
    return relative_path.stem


def _page_count(path: Path) -> int:
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0
