from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from drive_client import DriveClient, DriveFileNotFoundError, DriveSetupError, extract_drive_id
from storage import Catalog


CONFIG_PATH = Path("data") / "drive_config.json"
SYNCED = "synced"
UPLOADING = "uploading"
DRIVE_NEW = "drive_new"
CONFLICT = "conflict"
ERROR = "error"
UNMAPPED = "unmapped"


def load_drive_config(data_dir: Path) -> dict:
    path = data_dir / "drive_config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_drive_config(data_dir: Path, config: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "drive_config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def sync_drive_folder(*, data_dir: Path, root_url: str, client: DriveClient | None = None, include_all: bool = False) -> dict:
    catalog = Catalog(data_dir)
    client = client or DriveClient(data_dir / "credentials")
    source_files = client.list_pdfs_recursive(root_url)
    files = source_files if include_all else _select_print_collections(source_files, root_url)

    imported = 0
    skipped = 0
    cache_dir = data_dir / "drive_cache"
    sync_started_at = datetime.now().isoformat(timespec="seconds")

    for file in files:
        subject_title = _subject_title(file, root_url)
        subject = catalog.find_or_create_subject(title=subject_title, code="")
        fingerprint = _drive_fingerprint(file)

        existing_sync = subject.get("drive_sync") or subject.get("drive", {})
        if existing_sync.get("last_drive_fingerprint") == fingerprint or existing_sync.get("fingerprint") == fingerprint:
            catalog.update_drive_sync(
                subject["id"],
                {
                    "file_id": file["id"],
                    "name": file["name"],
                    "folder_path": file["folder_path"],
                    "folder_id": _first_parent(file),
                    "web_view_link": file.get("webViewLink", ""),
                    "modified_time": file.get("modifiedTime", ""),
                    "md5_checksum": file.get("md5Checksum", ""),
                    "fingerprint": fingerprint,
                    "synced_at": existing_sync.get("last_synced_at") or sync_started_at,
                    "sync_status": existing_sync.get("sync_status") or SYNCED,
                },
            )
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
                "folder_id": _first_parent(file),
                "web_view_link": file.get("webViewLink", ""),
                "modified_time": file.get("modifiedTime", ""),
                "size": file.get("size", ""),
                "md5_checksum": file.get("md5Checksum", ""),
                "fingerprint": fingerprint,
                "synced_at": sync_started_at,
                "sync_status": SYNCED,
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
            "last_source_file_count": len(source_files),
        },
    )

    return {
        "found": len(files),
        "source_found": len(source_files),
        "imported": imported,
        "skipped": skipped,
        "synced_at": sync_started_at,
    }


def push_subject_to_drive(
    *,
    data_dir: Path,
    subject_id: str,
    client: DriveClient | None = None,
    force: bool = False,
) -> dict:
    catalog = Catalog(data_dir)
    subject = catalog.get_subject(subject_id)
    if not subject:
        raise DriveSetupError("Das Fach wurde nicht gefunden.")

    sync = subject.get("drive_sync") or {}
    file_id = sync.get("drive_file_id")
    if not file_id:
        catalog.set_sync_status(subject_id, UNMAPPED, "Dieses Fach ist noch keiner Drive-Datei zugeordnet.")
        return {"status": UNMAPPED, "pushed": False}

    subject_dir = catalog.subject_dir(subject_id)
    current_path = subject_dir / "current.pdf"
    if not current_path.exists():
        catalog.set_sync_status(subject_id, ERROR, "Es gibt keine lokale current.pdf fuer dieses Fach.")
        return {"status": ERROR, "pushed": False}

    single_path = subject_dir / "single.pdf"
    push_path = single_path if single_path.exists() else current_path

    client = client or DriveClient(data_dir / "credentials")
    catalog.set_sync_status(subject_id, UPLOADING)

    try:
        remote_metadata = client.get_file_metadata(file_id)
        remote_fingerprint = _drive_fingerprint(remote_metadata)
        expected_fingerprint = sync.get("last_drive_fingerprint")
        if expected_fingerprint and remote_fingerprint != expected_fingerprint and not force:
            catalog.update_drive_sync(
                subject_id,
                {
                    "sync_status": CONFLICT,
                    "last_sync_error": "Drive-Datei wurde seit dem letzten Sync veraendert.",
                    "remote_drive_fingerprint": remote_fingerprint,
                    "remote_drive_modified_time": remote_metadata.get("modifiedTime", ""),
                    "remote_drive_md5": remote_metadata.get("md5Checksum", ""),
                    "last_sync_attempt_at": _now(),
                },
            )
            return {"status": CONFLICT, "pushed": False}

        archive_folder_id = sync.get("archive_folder_id")
        drive_folder_id = sync.get("drive_folder_id") or _first_parent(remote_metadata)
        if not archive_folder_id and drive_folder_id:
            archive_folder_id = client.find_or_create_archive_folder(drive_folder_id)
        if archive_folder_id:
            archive_name = _archive_name(sync.get("drive_filename") or remote_metadata.get("name") or "current.pdf")
            client.copy_to_archive_folder(file_id, archive_folder_id, archive_name)

        updated_metadata = client.upload_new_version(file_id, push_path)
        catalog.update_drive_sync(subject_id, _sync_metadata_from_drive(updated_metadata, archive_folder_id), current_pages=_page_count(current_path))
        return {"status": SYNCED, "pushed": True, "metadata": updated_metadata}
    except DriveFileNotFoundError as exc:
        catalog.update_drive_sync(
            subject_id,
            {
                "sync_status": UNMAPPED,
                "drive_file_id": "",
                "last_sync_error": "Drive-Datei wurde gelöscht oder ist nicht mehr erreichbar.",
                "last_sync_attempt_at": _now(),
            },
        )
        return {"status": UNMAPPED, "pushed": False}
    except DriveSetupError:
        raise
    except Exception as exc:
        catalog.set_sync_status(subject_id, ERROR, str(exc))
        raise DriveSetupError(f"Drive-Upload ist fehlgeschlagen: {exc}") from exc


def accept_drive_version(*, data_dir: Path, subject_id: str, client: DriveClient | None = None) -> dict:
    catalog = Catalog(data_dir)
    subject = catalog.get_subject(subject_id)
    if not subject:
        raise DriveSetupError("Das Fach wurde nicht gefunden.")

    sync = subject.get("drive_sync") or {}
    file_id = sync.get("drive_file_id")
    if not file_id:
        catalog.set_sync_status(subject_id, UNMAPPED, "Dieses Fach ist noch keiner Drive-Datei zugeordnet.")
        return {"status": UNMAPPED, "imported": False}

    client = client or DriveClient(data_dir / "credentials")
    remote_metadata = client.get_file_metadata(file_id)
    cache_path = data_dir / "drive_cache" / f"{file_id}.pdf"
    client.download_file(file_id, cache_path)
    current_path = catalog.subject_dir(subject_id) / "current.pdf"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    _archive_local_current(catalog.subject_dir(subject_id), current_path)
    shutil.copy2(cache_path, current_path)
    catalog.update_drive_sync(subject_id, _sync_metadata_from_drive(remote_metadata, sync.get("archive_folder_id", "")), current_pages=_page_count(current_path))
    return {"status": SYNCED, "imported": True}


def poll_drive_changes(*, data_dir: Path, client: DriveClient | None = None) -> dict:
    catalog = Catalog(data_dir)
    client = client or DriveClient(data_dir / "credentials")
    checked = imported = conflicts = errors = 0

    for subject in catalog.list_subjects():
        sync = subject.get("drive_sync") or {}
        file_id = sync.get("drive_file_id")
        if not file_id:
            continue
        checked += 1
        try:
            remote_metadata = client.get_file_metadata(file_id)
            remote_fingerprint = _drive_fingerprint(remote_metadata)
            if remote_fingerprint == sync.get("last_drive_fingerprint"):
                continue
            if sync.get("sync_status") in {UPLOADING, CONFLICT}:
                catalog.update_drive_sync(
                    subject["id"],
                    {
                        "sync_status": CONFLICT,
                        "last_sync_error": "Drive-Datei wurde geaendert, waehrend lokal ein nicht abgeschlossener Stand existiert.",
                        "remote_drive_fingerprint": remote_fingerprint,
                        "last_sync_attempt_at": _now(),
                    },
                )
                conflicts += 1
                continue

            cache_path = data_dir / "drive_cache" / f"{file_id}.pdf"
            client.download_file(file_id, cache_path)
            current_path = catalog.subject_dir(subject["id"]) / "current.pdf"
            current_path.parent.mkdir(parents=True, exist_ok=True)
            _archive_local_current(catalog.subject_dir(subject["id"]), current_path)
            shutil.copy2(cache_path, current_path)
            metadata = _sync_metadata_from_drive(remote_metadata, sync.get("archive_folder_id", ""))
            metadata["sync_status"] = DRIVE_NEW
            catalog.update_drive_sync(subject["id"], metadata, current_pages=_page_count(current_path))
            imported += 1
        except DriveFileNotFoundError:
            catalog.update_drive_sync(
                subject["id"],
                {
                    "sync_status": UNMAPPED,
                    "drive_file_id": "",
                    "last_sync_error": "Drive-Datei wurde gelöscht oder ist nicht mehr erreichbar.",
                    "last_sync_attempt_at": _now(),
                },
            )
            errors += 1
        except Exception as exc:
            errors += 1
            catalog.set_sync_status(subject["id"], ERROR, str(exc))

    return {"checked": checked, "imported": imported, "conflicts": conflicts, "errors": errors}


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
    if folder_parts:
        title_parts = folder_parts[:]
        while title_parts:
            parent_name = title_parts[-2] if len(title_parts) >= 2 else ""
            if not _is_technical_leaf_folder(title_parts[-1], parent_name):
                break
            title_parts.pop()
        if len(title_parts) >= 2:
            return title_parts[-1]
    return Path(file["name"]).stem


def _is_technical_leaf_folder(folder_name: str, parent_name: str = "") -> bool:
    normalized = folder_name.strip().lower()
    parent_normalized = parent_name.strip().lower()
    return (
        normalized in {"klausuren", "archiv", "pdf", "pdfs", "protokolle original"}
        or normalized.endswith("_pdf")
        or normalized.endswith(" pdf")
        or (normalized == "mündliche protokolle" and "mündliche protokolle" in parent_normalized)
    )


def _is_print_collection(file: dict) -> bool:
    name = file.get("name", "").lower()
    folder_path = file.get("folder_path", "").lower()
    folder_parts = {part for part in folder_path.split("/") if part}
    if folder_parts & {"archiv", "veraltet"}:
        return False
    return name.endswith(".pdf") and ("druck" in name or "merged" in name)


def _select_print_collections(files: list[dict], root_url: str) -> list[dict]:
    selected: dict[str, dict] = {}
    for file in files:
        if not _is_print_collection(file):
            continue
        subject_title = _subject_title(file, root_url)
        current = selected.get(subject_title)
        if current is None or _print_collection_score(file) > _print_collection_score(current):
            selected[subject_title] = file
    return list(selected.values())


def _print_collection_score(file: dict) -> tuple[int, int, str]:
    name = file.get("name", "").lower()
    score = 0
    if "reverse" not in name:
        score += 10
    if "druck" in name:
        score += 5
    if name.startswith("druck"):
        score += 2
    if "merged" in name:
        score += 1
    return (score, len(name), name)


def _local_subject_title(relative_path: Path) -> str:
    if len(relative_path.parts) >= 2:
        return relative_path.parts[-2]
    return relative_path.stem


def _page_count(path: Path) -> int:
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


def _sync_metadata_from_drive(metadata: dict, archive_folder_id: str = "") -> dict:
    fingerprint = _drive_fingerprint(metadata)
    return {
        "drive_file_id": metadata.get("id", ""),
        "drive_folder_id": _first_parent(metadata),
        "drive_filename": metadata.get("name", ""),
        "last_drive_modified_time": metadata.get("modifiedTime", ""),
        "last_drive_md5": metadata.get("md5Checksum", ""),
        "last_drive_fingerprint": fingerprint,
        "last_synced_at": _now(),
        "sync_status": SYNCED,
        "last_sync_error": "",
        "web_view_link": metadata.get("webViewLink", ""),
        "archive_folder_id": archive_folder_id,
    }


def _drive_fingerprint(metadata: dict) -> str:
    return metadata.get("md5Checksum") or metadata.get("modifiedTime") or metadata.get("id", "")


def _first_parent(metadata: dict) -> str:
    parents = metadata.get("parents") or []
    return parents[0] if parents else ""


def _archive_name(filename: str) -> str:
    path = Path(filename)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{path.stem}-{timestamp}{path.suffix or '.pdf'}"


def _archive_local_current(subject_dir: Path, current_path: Path) -> None:
    if not current_path.exists():
        return
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current_path, archive_dir / _archive_name(current_path.name))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
