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

from flask import Request, session, url_for


AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
OPENID_SCOPES = "openid email profile"
DEFAULT_DEV_SECRET = "dev-altklausuren-local"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    provider: str
    client_id: str
    client_secret: str
    redirect_uri: str
    drive_folder_id: str
    allowed_domain: str
    forward_default_role: str
    forward_dev_enabled: bool
    forward_dev_email: str
    forward_dev_name: str
    forward_dev_groups: str
    forward_dev_entitlements: str

    @classmethod
    def from_env(cls) -> "AuthConfig":
        enabled = os.getenv("AUTH_ENABLED", "").lower() in {"1", "true", "yes"}
        provider = os.getenv("AUTH_PROVIDER", "google").lower().strip() or "google"
        if os.getenv("FORWARD_AUTH_ENABLED", "").lower() in {"1", "true", "yes"}:
            provider = "forward_auth"
        return cls(
            enabled=enabled,
            provider=provider,
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", ""),
            drive_folder_id=os.getenv("DRIVE_ROOT_FOLDER_ID", ""),
            allowed_domain=os.getenv("ALLOWED_GOOGLE_DOMAIN", "forum-wi.de"),
            forward_default_role=os.getenv("FORWARD_AUTH_DEFAULT_ROLE", "").lower().strip(),
            forward_dev_enabled=os.getenv("FORWARD_AUTH_DEV_ENABLED", "").lower() in {"1", "true", "yes"},
            forward_dev_email=os.getenv("FORWARD_AUTH_DEV_EMAIL", ""),
            forward_dev_name=os.getenv("FORWARD_AUTH_DEV_NAME", ""),
            forward_dev_groups=os.getenv("FORWARD_AUTH_DEV_GROUPS", ""),
            forward_dev_entitlements=os.getenv("FORWARD_AUTH_DEV_ENTITLEMENTS", ""),
        )

    def validate(self) -> None:
        if self.provider == "forward_auth":
            _validate_secret_key()
            if self.forward_default_role and self.forward_default_role not in {"viewer", "editor", "admin"}:
                raise AuthError("FORWARD_AUTH_DEFAULT_ROLE muss leer, viewer, editor oder admin sein.")
            return
        if self.provider != "google":
            raise AuthError(f"Unbekannter AUTH_PROVIDER: {self.provider}")
        missing = [
            name
            for name, value in {
                "GOOGLE_CLIENT_ID": self.client_id,
                "GOOGLE_CLIENT_SECRET": self.client_secret,
                "GOOGLE_REDIRECT_URI": self.redirect_uri,
                "DRIVE_ROOT_FOLDER_ID": self.drive_folder_id,
                "SECRET_KEY": os.getenv("SECRET_KEY", ""),
            }.items()
            if not value
        ]
        if missing:
            raise AuthError(f"Auth ist aktiv, aber diese Variablen fehlen: {', '.join(missing)}")
        placeholders = [
            name
            for name, value in {
                "GOOGLE_CLIENT_ID": self.client_id,
                "GOOGLE_CLIENT_SECRET": self.client_secret,
                "SECRET_KEY": os.getenv("SECRET_KEY", ""),
            }.items()
            if value.startswith("replace-with")
        ]
        if placeholders:
            raise AuthError(f"Auth ist aktiv, aber diese Variablen enthalten noch Platzhalter: {', '.join(placeholders)}")
        _validate_secret_key()


def _validate_secret_key() -> None:
    secret_key = os.getenv("SECRET_KEY", "")
    if not secret_key:
        raise AuthError("Auth ist aktiv, aber SECRET_KEY fehlt.")
    if secret_key == DEFAULT_DEV_SECRET:
        raise AuthError("Auth ist aktiv, aber SECRET_KEY nutzt noch den lokalen Entwicklungswert.")


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
    import requests
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token

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


def user_from_forward_auth(config: AuthConfig, request: Request) -> dict | None:
    config.validate()
    header_user = _user_from_authentik_headers(config, request)
    if header_user:
        return header_user
    if config.forward_dev_enabled:
        return _dev_forward_auth_user(config)
    return None


def _user_from_authentik_headers(config: AuthConfig, request: Request) -> dict | None:
    email = request.headers.get("X-authentik-email", "").strip()
    username = request.headers.get("X-authentik-username", "").strip()
    if not email and not username:
        return None

    if email and config.allowed_domain and not email.lower().endswith(f"@{config.allowed_domain.lower()}"):
        raise AuthError(f"Nur Konten der Domain {config.allowed_domain} duerfen diese App verwenden.")

    groups = _split_header_values(request.headers.get("X-authentik-groups", ""))
    entitlements = _split_header_values(request.headers.get("X-authentik-entitlements", ""))
    role = _role_from_claims(email=email, groups=groups, entitlements=entitlements, default_role=config.forward_default_role)
    if not role:
        raise AuthError("Dein ForumWI-Konto hat keine Berechtigung fuer die Altklausuren-App.")

    return {
        "email": email or username,
        "name": request.headers.get("X-authentik-name", "").strip() or username or email,
        "picture": "",
        "role": role,
        "username": username,
        "uid": request.headers.get("X-authentik-uid", "").strip(),
        "groups": sorted(groups),
        "entitlements": sorted(entitlements),
        "auth_provider": "forward_auth",
    }


def _dev_forward_auth_user(config: AuthConfig) -> dict:
    email = config.forward_dev_email.strip() or "dev@forum-wi.de"
    groups = _split_header_values(config.forward_dev_groups)
    entitlements = _split_header_values(config.forward_dev_entitlements)
    role = _role_from_claims(email=email, groups=groups, entitlements=entitlements, default_role=config.forward_default_role) or "viewer"
    return {
        "email": email,
        "name": config.forward_dev_name.strip() or email,
        "picture": "",
        "role": role,
        "username": email.split("@", 1)[0],
        "uid": "dev-user",
        "groups": sorted(groups),
        "entitlements": sorted(entitlements),
        "auth_provider": "forward_auth_dev",
    }


def _role_from_claims(*, email: str, groups: set[str], entitlements: set[str], default_role: str = "") -> str:
    admin_emails = _env_set("ADMIN_EMAILS")
    if email and email.lower() in admin_emails:
        return "admin"
    normalized_groups = {group.lower() for group in groups}
    normalized_entitlements = {entitlement.lower() for entitlement in entitlements}

    role_groups = {
        "admin": _env_set("AUTH_ROLE_ADMIN_GROUPS", "altklausuren-admin,Vorstand"),
        "editor": _env_set("AUTH_ROLE_EDITOR_GROUPS", "altklausuren-editor,Referat Altklausuren"),
        "viewer": _env_set("AUTH_ROLE_VIEWER_GROUPS", "altklausuren-viewer,Aktive"),
    }
    role_entitlements = {
        "admin": _env_set("AUTH_ROLE_ADMIN_ENTITLEMENTS", "altklausuren:admin"),
        "editor": _env_set("AUTH_ROLE_EDITOR_ENTITLEMENTS", "altklausuren:editor"),
        "viewer": _env_set("AUTH_ROLE_VIEWER_ENTITLEMENTS", "altklausuren:viewer"),
    }
    for role in ("admin", "editor", "viewer"):
        if normalized_groups & role_groups[role] or normalized_entitlements & role_entitlements[role]:
            return role
    return default_role if default_role in {"viewer", "editor", "admin"} else ""


def _split_header_values(value: str) -> set[str]:
    raw = value.strip()
    if not raw:
        return set()
    if raw.startswith("["):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return {str(item).strip() for item in decoded if str(item).strip()}
        except json.JSONDecodeError:
            pass
    normalized = raw.replace(";", ",").replace("|", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def _env_set(name: str, default: str = "") -> set[str]:
    return {item.lower() for item in _split_header_values(os.getenv(name, default))}


def check_drive_access(access_token: str, folder_id: str) -> dict:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

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
