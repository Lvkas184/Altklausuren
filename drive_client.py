from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


PDF_MIME_TYPE = "application/pdf"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveSetupError(Exception):
    pass


class DriveClient:
    def __init__(self, credentials_dir: Path):
        self.credentials_dir = credentials_dir
        self.client_secret_path = credentials_dir / "client_secret.json"
        self.token_path = credentials_dir / "token.json"

    def authorize(self) -> None:
        if not self.client_secret_path.exists():
            raise DriveSetupError(
                "credentials/client_secret.json fehlt. Lege dort die OAuth-Client-Datei aus der Google Cloud Console ab."
            )

        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_path), SCOPES)
        credentials = flow.run_local_server(port=0)
        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(credentials.to_json(), encoding="utf-8")

    def list_pdfs_recursive(self, root_url: str) -> list[dict]:
        folder_id = extract_drive_id(root_url)
        service = self._service()
        root = self._get_metadata(service, folder_id)
        files: list[dict] = []
        self._walk_folder(service, folder_id, root["name"], files)
        return files

    def download_file(self, file_id: str, target_path: Path) -> None:
        service = self._service()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

    def _service(self):
        credentials = self._credentials()
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _credentials(self):
        if not self.token_path.exists():
            raise DriveSetupError("Google Drive ist noch nicht autorisiert. Fuehre zuerst `python3 drive_tools.py authorize` aus.")

        credentials = Credentials.from_authorized_user_info(json.loads(self.token_path.read_text(encoding="utf-8")), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        if not credentials.valid:
            raise DriveSetupError("Das Google-Token ist ungueltig. Fuehre `python3 drive_tools.py authorize` erneut aus.")
        return credentials

    def _walk_folder(self, service, folder_id: str, folder_path: str, files: list[dict]) -> None:
        page_token = None
        while True:
            response = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, md5Checksum, webViewLink)",
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
                .get(fileId=file_id, fields="id, name, mimeType", supportsAllDrives=True)
                .execute()
            )
        except Exception as exc:
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
