from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

import requests
from flask import Request, session, url_for
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
OPENID_SCOPES = "openid email profile"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    client_id: str
    client_secret: str
    redirect_uri: str
    drive_folder_id: str
    allowed_domain: str

    @classmethod
    def from_env(cls) -> "AuthConfig":
        enabled = os.getenv("AUTH_ENABLED", "").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", ""),
            drive_folder_id=os.getenv("DRIVE_ROOT_FOLDER_ID", ""),
            allowed_domain=os.getenv("ALLOWED_GOOGLE_DOMAIN", "forum-wi.de"),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in {
                "GOOGLE_CLIENT_ID": self.client_id,
                "GOOGLE_CLIENT_SECRET": self.client_secret,
                "GOOGLE_REDIRECT_URI": self.redirect_uri,
                "DRIVE_ROOT_FOLDER_ID": self.drive_folder_id,
            }.items()
            if not value
        ]
        if missing:
            raise AuthError(f"Auth ist aktiv, aber diese Variablen fehlen: {', '.join(missing)}")


def current_user() -> dict | None:
    return session.get("user")


def clear_user() -> None:
    session.pop("user", None)
    session.pop("oauth_state", None)


def build_login_url(config: AuthConfig) -> str:
    config.validate()
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    query = urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": f"{OPENID_SCOPES} {DRIVE_METADATA_SCOPE}",
            "access_type": "online",
            "prompt": "select_account",
            "state": state,
            "hd": config.allowed_domain,
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


def handle_callback(config: AuthConfig, request: Request) -> dict:
    config.validate()
    expected_state = session.get("oauth_state")
    if not expected_state or request.args.get("state") != expected_state:
        raise AuthError("OAuth-State ist ungueltig. Bitte erneut anmelden.")

    code = request.args.get("code")
    if not code:
        raise AuthError("Google hat keinen Autorisierungscode geliefert.")

    token_response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if token_response.status_code != 200:
        raise AuthError("Google-Login konnte nicht abgeschlossen werden.")

    token_data = token_response.json()
    claims = id_token.verify_oauth2_token(token_data["id_token"], GoogleRequest(), config.client_id)
    email = claims.get("email", "")
    if claims.get("email_verified") is not True:
        raise AuthError("Die Google-Mailadresse ist nicht verifiziert.")
    if config.allowed_domain and not email.endswith(f"@{config.allowed_domain}"):
        raise AuthError(f"Nur Konten der Domain {config.allowed_domain} duerfen diese App verwenden.")

    drive_access = check_drive_access(token_data["access_token"], config.drive_folder_id)
    if not drive_access["allowed"]:
        raise AuthError("Dieses Google-Konto hat keinen Zugriff auf den Altklausuren-Drive.")

    user = {
        "email": email,
        "name": claims.get("name", email),
        "picture": claims.get("picture", ""),
        "role": drive_access["role"],
    }
    session["user"] = user
    session.pop("oauth_state", None)
    return user


def check_drive_access(access_token: str, folder_id: str) -> dict:
    credentials = Credentials(token=access_token)
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    try:
        metadata = (
            service.files()
            .get(fileId=folder_id, fields="id, name, capabilities", supportsAllDrives=True)
            .execute()
        )
    except Exception:
        return {"allowed": False, "role": "none"}

    capabilities = metadata.get("capabilities", {})
    can_edit = bool(capabilities.get("canEdit") or capabilities.get("canAddChildren"))
    return {"allowed": True, "role": "editor" if can_edit else "viewer"}
