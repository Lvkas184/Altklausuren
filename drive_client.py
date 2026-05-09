from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

PDF_MIME_TYPE = "application/pdf"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveSetupError(Exception):
    pass


class DriveFileNotFoundError(DriveSetupError):
    pass


class DriveClient:
    def __init__(self, credentials_dir: Path):
        self.credentials_dir = credentials_dir
        self.client_secret_path = credentials_dir / "client_secret.json"
        self.token_path = credentials_dir / "token.json"
        configured_service_account = os.getenv("SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.service_account_path = Path(configured_service_account) if configured_service_account else credentials_dir / "service_account.json"

    def authorize(self) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.client_secret_path.exists():
            raise DriveSetupError(
                "credentials/client_secret.json fehlt. Lege dort die OAuth-Client-Datei aus der Google Cloud Console ab."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_path), SCOPES)
        credentials = flow.run_local_server(port=0)
        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(credentials.to_json(), encoding="utf-8")

    def credentials_mode(self) -> str:
        if self.service_account_path.exists():
            return "service_account"
        if self.token_path.exists():
            return "oauth_token"
        return "missing"

    def check_access(self, root_url: str) -> dict:
        folder_id = extract_drive_id(root_url)
        service = self._service()
        metadata = self._get_metadata(service, folder_id)
        return {
            "credential_mode": self.credentials_mode(),
            "folder_id": metadata.get("id", folder_id),
            "folder_name": metadata.get("name", ""),
            "mime_type": metadata.get("mimeType", ""),
            "web_view_link": metadata.get("webViewLink", ""),
        }

    def list_pdfs_recursive(self, root_url: str) -> list[dict]:
        folder_id = extract_drive_id(root_url)
        service = self._service()
        root = self._get_metadata(service, folder_id)
        files: list[dict] = []
        self._walk_folder(service, folder_id, root["name"], files)
        return files

    def get_file_metadata(self, file_id: str) -> dict:
        service = self._service()
        return self._get_metadata(service, file_id)

    def download_file(self, file_id: str, target_path: Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        service = self._service()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

    def upload_new_version(self, file_id: str, source_path: Path) -> dict:
        from googleapiclient.http import MediaFileUpload

        service = self._service()
        media = MediaFileUpload(str(source_path), mimetype=PDF_MIME_TYPE, resumable=False)
        return (
            service.files()
            .update(
                fileId=file_id,
                media_body=media,
                fields=_FILE_FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )

    def copy_to_archive_folder(self, file_id: str, archive_folder_id: str, name: str | None = None) -> dict:
        service = self._service()
        body = {"parents": [archive_folder_id]}
        if name:
            body["name"] = name
        return (
            service.files()
            .copy(
                fileId=file_id,
                body=body,
                fields=_FILE_FIELDS,
                supportsAllDrives=True,
            )
            .execute()
        )

    def find_or_create_archive_folder(self, parent_folder_id: str, name: str = "_Archiv") -> str:
        service = self._service()
        escaped_name = name.replace("'", "\\'")
        response = (
            service.files()
            .list(
                q=(
                    f"'{parent_folder_id}' in parents and trashed = false "
                    f"and mimeType = '{FOLDER_MIME_TYPE}' and name = '{escaped_name}'"
                ),
                fields="files(id, name)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1,
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        folder = (
            service.files()
            .create(
                body={"name": name, "mimeType": FOLDER_MIME_TYPE, "parents": [parent_folder_id]},
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return folder["id"]

    def list_changed_files(self, folder_id: str) -> list[dict]:
        service = self._service()
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false and mimeType = '{PDF_MIME_TYPE}'",
                fields=f"files({_FILE_FIELDS})",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1000,
            )
            .execute()
        )
        return response.get("files", [])

    def _service(self):
        from googleapiclient.discovery import build

        credentials = self._credentials()
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self.service_account_path.exists():
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(str(self.service_account_path), scopes=SCOPES)

        if not self.token_path.exists():
            raise DriveSetupError(
                "Google Drive ist noch nicht autorisiert. Lege data/credentials/service_account.json ab "
                "oder führe `python3 drive_tools.py authorize` aus."
            )

        credentials = Credentials.from_authorized_user_info(json.loads(self.token_path.read_text(encoding="utf-8")), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        if not credentials.valid:
            raise DriveSetupError("Das Google-Token ist ungültig. Führe `python3 drive_tools.py authorize` erneut aus.")
        return credentials

    def _walk_folder(self, service, folder_id: str, folder_path: str, files: list[dict]) -> None:
        page_token = None
        while True:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=f"nextPageToken, files({_FILE_FIELDS})",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    pageToken=page_token,
                    pageSize=1000,
                )
                .execute()
            )

            for item in response.get("files", []):
                mime_type = item.get("mimeType")
                if mime_type == FOLDER_MIME_TYPE:
                    self._walk_folder(service, item["id"], f"{folder_path}/{item['name']}", files)
                elif mime_type == PDF_MIME_TYPE:
                    files.append(item | {"folder_path": folder_path})

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    def _get_metadata(self, service, file_id: str) -> dict:
        try:
            return (
                service.files()
                .get(fileId=file_id, fields=_FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )
        except Exception as exc:
            try:
                from googleapiclient.errors import HttpError
                if isinstance(exc, HttpError) and exc.resp.status == 404:
                    raise DriveFileNotFoundError(
                        f"Drive-Datei nicht gefunden (gelöscht?): {file_id}"
                    ) from exc
            except ImportError:
                pass
            raise DriveSetupError(
                "Der Drive-Ordner konnte nicht gelesen werden. Pruefe, ob der autorisierte Account Zugriff auf den Ordner hat."
            ) from exc


def extract_drive_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise DriveSetupError("Drive-Ordner-URL fehlt.")

    parsed = urlparse(value)
    if parsed.netloc:
        marker = "/folders/"
        if marker in parsed.path:
            return parsed.path.split(marker, 1)[1].split("/", 1)[0]
        if "id=" in parsed.query:
            return parsed.query.split("id=", 1)[1].split("&", 1)[0]

    return value


_FILE_FIELDS = "id, name, mimeType, parents, modifiedTime, size, md5Checksum, webViewLink"
