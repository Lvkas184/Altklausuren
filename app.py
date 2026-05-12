from __future__ import annotations

import sys
import os
import shutil
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from flask import Flask, abort, flash, make_response, redirect, render_template, request, send_file, session, url_for
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from auth import AuthConfig, AuthError, build_login_url, clear_user, current_user, handle_callback, user_from_forward_auth
from pdf_workflow import PdfProcessingError, append_submission, detect_exam_boundaries, generate_single_page_pdf, regenerate_current_pdf, split_collection
from drive_client import DriveSetupError
from drive_sync import (
    CONFLICT,
    DRIVE_NEW,
    ERROR,
    SYNCED,
    UNMAPPED,
    accept_drive_version,
    load_drive_config,
    poll_drive_changes,
    push_subject_to_drive,
    save_drive_config,
    sync_drive_folder,
    sync_local_folder,
)
from config import load_dotenv
from storage import Catalog


BASE_DIR = Path(__file__).resolve().parent
if os.getenv("ALTKLAUSUREN_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.getenv("ALTKLAUSUREN_DATA_DIR", BASE_DIR / "data")).expanduser()
UPLOAD_LIMIT_MB = int(os.getenv("UPLOAD_LIMIT_MB", "80"))


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes"}


app = Flask(__name__)
_secret_key = os.getenv("SECRET_KEY", "")
if not _secret_key:
    import warnings
    warnings.warn(
        "SECRET_KEY ist nicht gesetzt – die App läuft mit einem unsicheren Standardwert. "
        "Setze SECRET_KEY in .env oder als Umgebungsvariable.",
        stacklevel=1,
    )
    _secret_key = "dev-altklausuren-local"
app.config["SECRET_KEY"] = _secret_key
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT_MB * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if _env_flag("SESSION_COOKIE_SECURE", default=_env_flag("AUTH_ENABLED") and os.getenv("GOOGLE_REDIRECT_URI", "").startswith("https://")):
    app.config["SESSION_COOKIE_SECURE"] = True
if os.getenv("TRUST_PROXY_HEADERS", "").lower() in {"1", "true", "yes"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

catalog = Catalog(DATA_DIR)

ROLE_LEVELS = {"viewer": 0, "editor": 1, "admin": 2}


def active_role() -> str:
    if not AuthConfig.from_env().enabled:
        return "admin"
    user = current_user() or {}
    admin_emails = {
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    if user.get("email", "").lower() in admin_emails:
        return "admin"
    return user.get("role", "viewer")


def can(min_role: str) -> bool:
    return ROLE_LEVELS.get(active_role(), 0) >= ROLE_LEVELS[min_role]


def require_role(min_role: str):
    def decorator(route):
        @wraps(route)
        def wrapped(*args, **kwargs):
            if not can(min_role):
                flash("Dafür fehlen dir die Rechte.", "error")
                return redirect(url_for("index"))
            return route(*args, **kwargs)

        return wrapped

    return decorator


@app.context_processor
def inject_permissions():
    role = active_role()
    return {
        "active_role": role,
        "can_view": True,
        "can_edit": ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS["editor"],
        "can_admin": ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS["admin"],
    }


@app.before_request
def require_login():
    auth_config = AuthConfig.from_env()
    if not auth_config.enabled:
        return None

    public_endpoints = {"healthz", "favicon", "login", "google_login", "auth_callback", "logout", "continue_forward_auth_login", "static"}
    if request.endpoint in public_endpoints:
        return None

    if session.get("logged_out"):
        session.pop("logged_out")
        return redirect(url_for("login", next=request.full_path))

    if auth_config.provider == "forward_auth":
        try:
            forwarded_user = user_from_forward_auth(auth_config, request)
        except AuthError as exc:
            clear_user()
            flash(str(exc), "error")
            return redirect(url_for("login", next=request.full_path))
        if forwarded_user:
            session["user"] = forwarded_user
            return None
        clear_user()
        return redirect(url_for("login", next=request.full_path))

    if current_user():
        return None

    return redirect(url_for("login", next=request.full_path))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}, 200


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/login")
def login():
    auth_config = AuthConfig.from_env()
    if not auth_config.enabled:
        flash("Login ist lokal deaktiviert.", "success")
        return redirect(url_for("index"))

    next_url = request.args.get("next", "")
    if _is_safe_next(next_url):
        session["login_next"] = next_url

    if auth_config.provider == "forward_auth":
        if not session.get("logged_out"):
            try:
                forwarded_user = user_from_forward_auth(auth_config, request)
            except AuthError as exc:
                flash(str(exc), "error")
                forwarded_user = None
            if forwarded_user:
                session["user"] = forwarded_user
                session.pop("logged_out", None)
                next_url = session.pop("login_next", "")
                if _is_safe_next(next_url):
                    return redirect(next_url)
                return redirect(url_for("index"))

    return render_template(
        "login.html",
        allowed_domain=auth_config.allowed_domain,
        auth_enabled=True,
        auth_provider=auth_config.provider,
        current_user=current_user(),
    )


@app.get("/login/google")
def google_login():
    auth_config = AuthConfig.from_env()
    if not auth_config.enabled:
        flash("Login ist lokal deaktiviert.", "success")
        return redirect(url_for("index"))
    if auth_config.provider != "google":
        flash("Google-OAuth ist in diesem Auth-Modus nicht aktiv.", "error")
        return redirect(url_for("login"))

    try:
        return redirect(build_login_url(auth_config))
    except AuthError as exc:
        flash(str(exc), "error")
        return redirect(url_for("login"))


@app.get("/auth/callback")
def auth_callback():
    auth_config = AuthConfig.from_env()
    try:
        handle_callback(auth_config, request)
    except AuthError as exc:
        clear_user()
        flash(str(exc), "error")
        return redirect(url_for("login"))

    next_url = session.pop("login_next", "")
    if _is_safe_next(next_url):
        return redirect(next_url)
    return redirect(url_for("index"))


@app.get("/login/continue")
def continue_forward_auth_login():
    session.pop("logged_out", None)
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    clear_user()
    flash("Du wurdest abgemeldet.", "success")
    return redirect(url_for("login"))


def _is_safe_next(next_url: str) -> bool:
    return (
        bool(next_url)
        and next_url.startswith("/")
        and not next_url.startswith("//")
        and next_url != "/favicon.ico"
        and not next_url.startswith("/static/")
    )


@app.get("/")
def index():
    drive_config = load_drive_config(DATA_DIR)
    subjects = []
    for subject in catalog.list_subjects():
        subject_dir = catalog.subject_dir(subject["id"])
        current_path = subject_dir / "current.pdf"
        submissions = subject.get("submissions", [])
        latest = submissions[-1] if submissions else None
        category = _subject_category(subject)
        subjects.append(
            subject
            | {
                "category": category,
                "has_current_pdf": current_path.exists(),
                "has_single_pdf": (subject_dir / "single.pdf").exists(),
                "latest_submission": latest,
            }
        )
    ready_count = sum(1 for subject in subjects if subject["has_current_pdf"])
    return render_template(
        "index.html",
        subjects=subjects,
        max_upload_mb=UPLOAD_LIMIT_MB,
        ready_count=ready_count,
        drive_config=drive_config,
        subject_categories=_group_subjects_by_category(subjects),
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


def _subject_category(subject: dict) -> str:
    sync = subject.get("drive_sync") or {}
    folder_path = sync.get("drive_folder_path", "")
    parts = [part for part in folder_path.split("/") if part]
    if parts and parts[0].lower() == "drive":
        parts = parts[1:]
    if parts:
        return parts[0]
    return "Nicht kategorisiert"


def _group_subjects_by_category(subjects: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for subject in subjects:
        grouped.setdefault(subject["category"], []).append(subject)
    categories = []
    for name, items in grouped.items():
        categories.append(
            {
                "name": name,
                "subjects": sorted(items, key=lambda item: item["title"].lower()),
                "ready_count": sum(1 for item in items if item["has_current_pdf"]),
            }
        )
    return sorted(categories, key=lambda item: (item["name"] == "Nicht kategorisiert", item["name"].lower()))


@app.post("/subjects")
@require_role("editor")
def create_subject():
    title = request.form.get("title", "").strip()
    code = request.form.get("code", "").strip()

    if not title:
        flash("Bitte gib einen Fachnamen ein.", "error")
        return redirect(url_for("index"))

    subject = catalog.create_subject(title=title, code=code)
    flash(f"{subject['title']} wurde angelegt.", "success")
    return redirect(url_for("subject_detail", subject_id=subject["id"]))


@app.post("/submissions")
@require_role("editor")
def create_submission():
    return _handle_submission_upload(request.form.get("subject_id", "").strip(), detail_redirect=False)


@app.get("/subjects/<subject_id>")
def subject_detail(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)

    subject_dir = catalog.subject_dir(subject_id)
    current_path = subject_dir / "current.pdf"
    subject = subject | {
        "has_current_pdf": current_path.exists(),
        "has_single_pdf": (subject_dir / "single.pdf").exists(),
        "latest_submission": subject.get("submissions", [])[-1] if subject.get("submissions") else None,
    }
    return render_template(
        "subject_detail.html",
        subject=subject,
        max_upload_mb=UPLOAD_LIMIT_MB,
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.post("/subjects/<subject_id>/submissions")
@require_role("editor")
def create_subject_submission(subject_id: str):
    return _handle_submission_upload(subject_id, detail_redirect=True)


def _handle_submission_upload(subject_id: str, *, detail_redirect: bool):
    uploaded = request.files.get("pdf")
    redirect_target = url_for("subject_detail", subject_id=subject_id) if detail_redirect and subject_id else url_for("index")

    if not subject_id or not catalog.get_subject(subject_id):
        flash("Bitte waehle ein Fach aus.", "error")
        return redirect(redirect_target)

    if not uploaded or not uploaded.filename:
        flash("Bitte waehle eine PDF-Datei aus.", "error")
        return redirect(redirect_target)

    if not uploaded.filename.lower().endswith(".pdf"):
        flash("Es koennen aktuell nur PDF-Dateien verarbeitet werden.", "error")
        return redirect(redirect_target)

    subject_dir = catalog.subject_dir(subject_id)
    incoming_dir = subject_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    source_name = secure_filename(uploaded.filename) or "upload.pdf"
    upload_path = incoming_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{source_name}"
    uploaded.save(upload_path)

    metadata = {
        "kind": request.form.get("kind", "Gedaechtnisprotokoll").strip() or "Gedaechtnisprotokoll",
        "term": request.form.get("term", "").strip(),
        "exam_date": request.form.get("exam_date", "").strip(),
        "instructor": request.form.get("instructor", "").strip(),
        "solution": request.form.get("solution", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "original_filename": source_name,
    }
    strip_uploaded_cover = request.form.get("strip_uploaded_cover") == "on"

    try:
        result = append_submission(
            subject=catalog.get_subject(subject_id),
            subject_dir=subject_dir,
            upload_path=upload_path,
            metadata=metadata,
            strip_uploaded_cover=strip_uploaded_cover,
        )
    except PdfProcessingError as exc:
        flash(str(exc), "error")
        return redirect(redirect_target)

    catalog.add_submission(
        subject_id,
        metadata
        | result
        | {
            "strip_uploaded_cover": strip_uploaded_cover,
            "collection_import": False,
        },
    )
    try:
        push_result = push_subject_to_drive(data_dir=DATA_DIR, subject_id=subject_id)
    except DriveSetupError as exc:
        flash(f"PDF wurde lokal erzeugt, aber Drive konnte nicht aktualisiert werden: {exc}", "error")
        return redirect(redirect_target)

    if push_result["status"] == UNMAPPED:
        flash("PDF wurde lokal erzeugt. Dieses Fach ist noch keiner Drive-Datei zugeordnet.", "error")
    elif push_result["status"] == CONFLICT:
        flash("PDF wurde lokal erzeugt, aber Drive wurde nicht überschrieben, weil dort eine neuere Aenderung liegt.", "error")
    elif push_result["status"] == ERROR:
        flash("PDF wurde lokal erzeugt, aber Drive konnte nicht aktualisiert werden.", "error")
    else:
        flash("PDF wurde eingeordnet, neu erzeugt und nach Drive hochgeladen.", "success")
    return redirect(redirect_target)


@app.post("/subjects/<subject_id>/submissions/<submission_id>")
@require_role("editor")
def update_subject_submission(subject_id: str, submission_id: str):
    if not catalog.get_submission(subject_id, submission_id):
        abort(404)
    catalog.update_submission(
        subject_id,
        submission_id,
        {
            "kind": request.form.get("kind", "").strip(),
            "term": request.form.get("term", "").strip(),
            "exam_date": request.form.get("exam_date", "").strip(),
            "instructor": request.form.get("instructor", "").strip(),
            "solution": request.form.get("solution", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "sort_order": int(request.form.get("sort_order", "0") or 0),
        },
    )
    return _regenerate_and_push(subject_id, "Deckblattdaten wurden gespeichert.")


@app.post("/subjects/<subject_id>/submissions/<submission_id>/delete")
@require_role("editor")
def delete_subject_submission(subject_id: str, submission_id: str):
    if not catalog.get_submission(subject_id, submission_id):
        abort(404)
    catalog.delete_submission(subject_id, submission_id)
    return _regenerate_and_push(subject_id, "Eintrag wurde gelöscht.")


@app.post("/subjects/<subject_id>/submissions/reorder")
@require_role("editor")
def reorder_subject_submissions(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    ordered = sorted(
        (
            (int(request.form.get(f"sort_order_{submission['id']}", submission.get("sort_order") or 0) or 0), submission["id"])
            for submission in subject.get("submissions", [])
        ),
        key=lambda item: item[0],
    )
    catalog.reorder_submissions(subject_id, [submission_id for _, submission_id in ordered])
    return _regenerate_and_push(subject_id, "Reihenfolge wurde gespeichert.")


@app.post("/subjects/<subject_id>/import-collection")
@require_role("admin")
def import_subject_collection(subject_id: str):
    subject = catalog.get_subject(subject_id)
    uploaded = request.files.get("pdf")
    if not subject:
        abort(404)
    if not uploaded or not uploaded.filename:
        flash("Bitte waehle eine bestehende DRUCK-PDF aus.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    if not uploaded.filename.lower().endswith(".pdf"):
        flash("Es koennen aktuell nur PDF-Dateien importiert werden.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    subject_dir = catalog.subject_dir(subject_id)
    incoming_dir = subject_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    source_name = secure_filename(uploaded.filename) or "druck-import.pdf"
    upload_path = incoming_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{source_name}"
    uploaded.save(upload_path)

    try:
        pages = len(PdfReader(str(upload_path)).pages)
    except Exception:
        flash("Die importierte Datei konnte nicht als PDF gelesen werden.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    if pages == 0:
        flash("Die importierte PDF enthält keine Seiten.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    current_path = subject_dir / "current.pdf"
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    if current_path.exists():
        shutil.copy2(current_path, archive_dir / f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf")
    shutil.copy2(upload_path, current_path)

    catalog.add_submission(
        subject_id,
        {
            "kind": "Importierte DRUCK-Sammlung",
            "term": request.form.get("term", "").strip(),
            "exam_date": request.form.get("exam_date", "").strip(),
            "instructor": request.form.get("instructor", "").strip(),
            "solution": request.form.get("solution", "").strip(),
            "notes": request.form.get("notes", "Bestehende Sammlung importiert.").strip(),
            "original_filename": source_name,
            "stored_upload": str(upload_path.relative_to(subject_dir)),
            "added_pages": pages,
            "existing_body_pages": 0,
            "current_pages": pages,
            "export_path": "",
            "strip_uploaded_cover": False,
            "collection_import": True,
        },
    )

    drive_file_id = request.form.get("drive_file_id", "").strip()
    if drive_file_id:
        catalog.update_drive_sync(
            subject_id,
            {
                "drive_file_id": drive_file_id,
                "drive_folder_id": request.form.get("drive_folder_id", "").strip(),
                "drive_filename": request.form.get("drive_filename", source_name).strip(),
                "sync_status": SYNCED,
            },
            current_pages=pages,
        )

    flash("Bestehende DRUCK-Sammlung wurde vollstaendig importiert.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/subjects/<subject_id>/regenerate")
@require_role("editor")
def regenerate_subject(subject_id: str):
    return _regenerate_and_push(subject_id, "PDF wurde neu erzeugt.")


def _regenerate_and_push(subject_id: str, success_message: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    try:
        result = regenerate_current_pdf(subject=subject, subject_dir=catalog.subject_dir(subject_id))
    except PdfProcessingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    catalog.set_current_pages(subject_id, result["current_pages"])
    try:
        push_result = push_subject_to_drive(data_dir=DATA_DIR, subject_id=subject_id)
    except DriveSetupError as exc:
        flash(f"{success_message} Drive konnte nicht aktualisiert werden: {exc}", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    if push_result["status"] == UNMAPPED:
        flash(f"{success_message} Dieses Fach ist noch keiner Drive-Datei zugeordnet.", "error")
    elif push_result["status"] == CONFLICT:
        flash(f"{success_message} Drive wurde wegen eines Konflikts nicht überschrieben.", "error")
    elif push_result["status"] == ERROR:
        flash(f"{success_message} Drive konnte nicht aktualisiert werden.", "error")
    else:
        flash(f"{success_message} Drive ist synchron.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/drive/config")
@require_role("admin")
def update_drive_config():
    root_url = request.form.get("root_url", "").strip()
    local_root_path = request.form.get("local_root_path", "").strip()
    if local_root_path:
        config = load_drive_config(DATA_DIR)
        config["local_root_path"] = local_root_path
        save_drive_config(DATA_DIR, config)
        flash("Lokaler Drive-Ordner wurde gespeichert.", "success")
        return redirect(url_for("index"))

    if not root_url:
        flash("Bitte gib eine Google-Drive-Ordner-URL ein.", "error")
        return redirect(url_for("index"))

    config = load_drive_config(DATA_DIR)
    config["root_url"] = root_url
    save_drive_config(DATA_DIR, config)
    flash("Drive-Ordner wurde gespeichert.", "success")
    return redirect(url_for("index"))


@app.post("/drive/push-all")
@require_role("admin")
def push_all_to_drive():
    subjects = catalog.list_subjects()
    pushed = skipped = conflicts = errors = 0
    for subject in subjects:
        try:
            result = push_subject_to_drive(data_dir=DATA_DIR, subject_id=subject["id"])
            status = result["status"]
            if status == SYNCED:
                pushed += 1
            elif status == UNMAPPED:
                skipped += 1
            elif status == CONFLICT:
                conflicts += 1
            else:
                errors += 1
        except DriveSetupError:
            errors += 1
    parts = [f"{pushed} hochgeladen"]
    if skipped:
        parts.append(f"{skipped} nicht verknüpft")
    if conflicts:
        parts.append(f"{conflicts} Konflikte")
    if errors:
        parts.append(f"{errors} Fehler")
    flash(
        "Sync abgeschlossen: " + ", ".join(parts) + ".",
        "error" if errors or conflicts else "success",
    )
    return redirect(url_for("index"))


@app.post("/drive/sync")
@require_role("admin")
def sync_drive():
    config = load_drive_config(DATA_DIR)
    root_url = request.form.get("root_url", "").strip() or config.get("root_url", "")
    if not root_url:
        flash("Bitte speichere zuerst einen Drive-Ordner.", "error")
        return redirect(url_for("index"))

    try:
        result = sync_drive_folder(data_dir=DATA_DIR, root_url=root_url)
    except DriveSetupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception as exc:
        flash(f"Drive-Sync ist fehlgeschlagen: {exc}", "error")
        return redirect(url_for("index"))

    flash(
        f"Drive-Sync abgeschlossen: {result['found']} PDFs gefunden, "
        f"{result['imported']} importiert, {result['skipped']} unverändert.",
        "success",
    )
    return redirect(url_for("index"))


@app.post("/drive/local-sync")
@require_role("admin")
def sync_local_drive():
    config = load_drive_config(DATA_DIR)
    local_root_path = request.form.get("local_root_path", "").strip() or config.get("local_root_path", "")
    if not local_root_path:
        flash("Bitte speichere zuerst einen lokalen Drive-Ordner.", "error")
        return redirect(url_for("index"))

    try:
        result = sync_local_folder(data_dir=DATA_DIR, root_path=local_root_path)
    except DriveSetupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception as exc:
        flash(f"Lokaler Drive-Sync ist fehlgeschlagen: {exc}", "error")
        return redirect(url_for("index"))

    flash(
        f"Lokaler Sync abgeschlossen: {result['found']} PDFs gefunden, "
        f"{result['imported']} importiert, {result['skipped']} unverändert.",
        "success",
    )
    return redirect(url_for("index"))


@app.post("/subjects/<subject_id>/drive/accept")
@require_role("admin")
def accept_subject_drive_version(subject_id: str):
    try:
        result = accept_drive_version(data_dir=DATA_DIR, subject_id=subject_id)
    except DriveSetupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    if result["status"] == UNMAPPED:
        flash("Dieses Fach ist noch keiner Drive-Datei zugeordnet.", "error")
    else:
        flash("Drive-Version wurde lokal uebernommen.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/subjects/<subject_id>/drive/force-push")
@require_role("admin")
def force_push_subject_drive_version(subject_id: str):
    try:
        result = push_subject_to_drive(data_dir=DATA_DIR, subject_id=subject_id, force=True)
    except DriveSetupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))

    if result["status"] == UNMAPPED:
        flash("Dieses Fach ist noch keiner Drive-Datei zugeordnet.", "error")
    else:
        flash("Lokale App-Version wurde erneut nach Drive hochgeladen.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/subjects/<subject_id>/drive/check")
@require_role("admin")
def check_subject_drive_version(subject_id: str):
    if not catalog.get_subject(subject_id):
        abort(404)
    try:
        result = poll_drive_changes(data_dir=DATA_DIR)
    except DriveSetupError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    flash(
        f"Drive-Sync geprueft: {result['checked']} verknüpfte Fächern, "
        f"{result['imported']} neue Drive-Versionen, {result['conflicts']} Konflikte.",
        "success",
    )
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/subjects/<subject_id>/drive/dismiss-conflict")
@require_role("admin")
def dismiss_subject_drive_conflict(subject_id: str):
    if not catalog.get_subject(subject_id):
        abort(404)
    catalog.update_drive_sync(
        subject_id,
        {
            "sync_status": SYNCED,
            "last_sync_error": "",
            "remote_drive_fingerprint": "",
            "remote_drive_modified_time": "",
            "remote_drive_md5": "",
        },
    )
    flash("Konfliktstatus wurde verworfen. Beim naechsten Sync wird Drive erneut geprueft.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.get("/subjects/<subject_id>/submissions/<submission_id>/split")
@require_role("editor")
def split_collection_view(subject_id: str, submission_id: str):
    subject = catalog.get_subject(subject_id)
    submission = catalog.get_submission(subject_id, submission_id)
    if not subject or not submission:
        abort(404)
    if not submission.get("collection_import"):
        flash("Nur DRUCK-Importe können aufgeteilt werden.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    subject_dir = catalog.subject_dir(subject_id)
    try:
        from pathlib import Path as _Path
        stored = submission.get("stored_upload", "")
        pdf_path = _Path(stored) if _Path(stored).is_absolute() else subject_dir / stored
        groups = detect_exam_boundaries(pdf_path)
    except Exception:
        groups = []
    if not groups:
        from pypdf import PdfReader as _R
        try:
            stored = submission.get("stored_upload", "")
            pdf_path = subject_dir / stored if not (subject_dir / stored).is_absolute() else _Path(stored)
            total = len(_R(str(pdf_path)).pages)
        except Exception:
            total = 1
        groups = [{"start_page": 1, "end_page": total, "semester": "", "exam_date": "",
                   "instructor": "", "solution": "unbekannt", "kind": "Gedaechtnisprotokoll",
                   "notes": "", "snippet": ""}]
    return render_template(
        "split_collection.html",
        subject=subject,
        submission=submission,
        groups=groups,
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.post("/subjects/<subject_id>/submissions/<submission_id>/split")
@require_role("editor")
def split_collection_execute(subject_id: str, submission_id: str):
    subject = catalog.get_subject(subject_id)
    submission = catalog.get_submission(subject_id, submission_id)
    if not subject or not submission:
        abort(404)

    indices = sorted({
        int(k.split("_")[2])
        for k in request.form
        if k.startswith("group_start_") and k.split("_")[2].isdigit()
    })

    groups = []
    for i in indices:
        start = request.form.get(f"group_start_{i}", "").strip()
        end = request.form.get(f"group_end_{i}", "").strip()
        if not start or not end or not start.isdigit() or not end.isdigit():
            continue
        groups.append({
            "start_page": int(start),
            "end_page": int(end),
            "kind": request.form.get(f"group_kind_{i}", "Gedaechtnisprotokoll").strip(),
            "semester": request.form.get(f"group_semester_{i}", "").strip(),
            "exam_date": request.form.get(f"group_exam_date_{i}", "").strip(),
            "instructor": request.form.get(f"group_instructor_{i}", "").strip(),
            "solution": request.form.get(f"group_solution_{i}", "unbekannt").strip(),
            "notes": request.form.get(f"group_notes_{i}", "").strip(),
        })

    groups = [g for g in groups if g["kind"] != "Deckblatt"]

    if not groups:
        flash("Keine Gruppen definiert.", "error")
        return redirect(url_for("split_collection_view", subject_id=subject_id, submission_id=submission_id))

    subject_dir = catalog.subject_dir(subject_id)
    try:
        new_submissions = split_collection(
            subject=subject,
            subject_dir=subject_dir,
            source_submission=submission,
            groups=groups,
        )
    except PdfProcessingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("split_collection_view", subject_id=subject_id, submission_id=submission_id))

    catalog.delete_submission(subject_id, submission_id)
    for sub in new_submissions:
        catalog.add_submission(subject_id, sub)

    return _regenerate_and_push(subject_id, f"Sammlung in {len(new_submissions)} Einträge aufgeteilt.")


@app.post("/subjects/<subject_id>/delete")
@require_role("admin")
def delete_subject(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    catalog.delete_subject(subject_id)
    subject_dir = catalog.subject_dir(subject_id)
    if subject_dir.exists():
        removed_dir = subject_dir.parent / "_removed"
        removed_dir.mkdir(parents=True, exist_ok=True)
        target = removed_dir / f"{subject_id}-{int(time.time())}"
        shutil.move(str(subject_dir), str(target))
    flash(f"Fach \"{subject['title']}\" wurde gelöscht.", "success")
    return redirect(url_for("index"))


@app.post("/subjects/<subject_id>/update")
@require_role("admin")
def update_subject(subject_id: str):
    if not catalog.get_subject(subject_id):
        abort(404)
    title = request.form.get("title", "").strip()
    code = request.form.get("code", "").strip()
    if not title:
        flash("Fachname darf nicht leer sein.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    no_cover = request.form.get("no_cover") == "1"
    catalog.update_subject(subject_id, title=title, code=code, no_cover=no_cover)
    flash("Fach wurde umbenannt.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.post("/subjects/<subject_id>/drive/relink")
@require_role("admin")
def relink_subject_drive(subject_id: str):
    if not catalog.get_subject(subject_id):
        abort(404)
    drive_file_id = request.form.get("drive_file_id", "").strip()
    drive_folder_id = request.form.get("drive_folder_id", "").strip()
    drive_filename = request.form.get("drive_filename", "").strip()
    if not drive_file_id:
        flash("Drive File-ID darf nicht leer sein.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    catalog.update_drive_sync(
        subject_id,
        {
            "drive_file_id": drive_file_id,
            "drive_folder_id": drive_folder_id,
            "drive_filename": drive_filename,
            "sync_status": SYNCED,
            "last_sync_error": "",
        },
    )
    flash("Drive-Verknuepfung wurde aktualisiert.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.get("/subjects/<subject_id>/print")
def print_subject(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    current_path = catalog.subject_dir(subject_id) / "current.pdf"
    if not current_path.exists():
        abort(404)
    return render_template(
        "print.html",
        subject=subject,
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.get("/subjects/<subject_id>/current.pdf")
def current_pdf(subject_id: str):
    return _send_current_pdf(subject_id, as_attachment=True)


@app.get("/subjects/<subject_id>/preview.pdf")
def preview_pdf(subject_id: str):
    return _send_current_pdf(subject_id, as_attachment=False)


@app.get("/subjects/<subject_id>/single.pdf")
def single_pdf(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    single_path = catalog.subject_dir(subject_id) / "single.pdf"
    if not single_path.exists():
        abort(404)
    return send_file(single_path, mimetype="application/pdf", as_attachment=True, download_name=f"{subject['slug']}-einzelseiten.pdf")


@app.get("/subjects/<subject_id>/single-preview.pdf")
def single_preview_pdf(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    single_path = catalog.subject_dir(subject_id) / "single.pdf"
    if not single_path.exists():
        abort(404)
    return send_file(single_path, mimetype="application/pdf", as_attachment=False)


def _send_current_pdf(subject_id: str, *, as_attachment: bool):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)

    current_path = catalog.subject_dir(subject_id) / "current.pdf"
    if not current_path.exists():
        abort(404)

    download_name = f"{subject['slug']}-altklausuren.pdf"
    return send_file(current_path, mimetype="application/pdf", as_attachment=as_attachment, download_name=download_name)



# ── Protokoll-Sessions ────────────────────────────────────────────────────────

_CONTRIBUTOR_COOKIE = "proto_contributor"


def _get_or_set_contributor_token(response=None):
    token = request.cookies.get(_CONTRIBUTOR_COOKIE)
    if not token:
        import secrets
        token = secrets.token_urlsafe(16)
        if response:
            response.set_cookie(_CONTRIBUTOR_COOKIE, token, max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True)
    return token


@app.post("/subjects/<subject_id>/sessions")
@require_role("editor")
def create_proto_session(subject_id: str):
    subject = catalog.get_subject(subject_id)
    if not subject:
        abort(404)
    semester = request.form.get("semester", "").strip()
    if not semester:
        flash("Semester ist erforderlich.", "error")
        return redirect(url_for("subject_detail", subject_id=subject_id))
    proto_sess = catalog.create_proto_session(subject_id, semester)
    flash(f"Session angelegt. Teilnahme-Link: {request.host_url}session/{proto_sess['token']}", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.get("/session/<token>/qr.png")
def proto_session_qr(token: str):
    import qrcode, io
    proto_sess = catalog.get_proto_session_by_token(token)
    if not proto_sess:
        abort(404)
    url = request.url_root.rstrip("/") + f"/session/{token}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from flask import send_file
    return send_file(buf, mimetype="image/png")


@app.get("/session/<token>")
def proto_session_view(token: str):
    proto_sess = catalog.get_proto_session_by_token(token)
    if not proto_sess:
        abort(404)
    subject = catalog.get_subject(proto_sess["subject_id"])
    contributor_token = request.cookies.get(_CONTRIBUTOR_COOKIE, "")
    own_contribution = None
    if contributor_token:
        contribs = catalog.get_proto_contributions(proto_sess["id"])
        own_contribution = next((c for c in contribs if c["contributor_token"] == contributor_token), None)
        others = [c for c in contribs if c["contributor_token"] != contributor_token]
    else:
        others = catalog.get_proto_contributions(proto_sess["id"])
    resp = make_response(render_template(
        "proto_session.html",
        session=proto_sess,
        subject=subject,
        own_contribution=own_contribution,
        others=others,
    ))
    if not contributor_token:
        import secrets
        contributor_token = secrets.token_urlsafe(16)
        resp.set_cookie(_CONTRIBUTOR_COOKIE, contributor_token, max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True)
    return resp


@app.post("/session/<token>/contribute")
def proto_session_contribute(token: str):
    proto_sess = catalog.get_proto_session_by_token(token)
    if not proto_sess or proto_sess["status"] != "open":
        return {"ok": False, "error": "session closed"}, 400
    contributor_token = request.cookies.get(_CONTRIBUTOR_COOKIE, "")
    new_token = not contributor_token
    if new_token:
        import secrets as _sec
        contributor_token = _sec.token_urlsafe(16)
    text = (request.json or {}).get("text", "") if request.is_json else request.form.get("text", "")
    text = text[:50_000]
    catalog.upsert_proto_contribution(proto_sess["id"], contributor_token, text)
    resp = make_response({"ok": True})
    if new_token:
        resp.set_cookie(_CONTRIBUTOR_COOKIE, contributor_token, max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True)
    return resp


@app.post("/subjects/<subject_id>/sessions/<session_id>/semester")
@require_role("editor")
def update_proto_session_semester(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    semester = request.form.get("semester", "").strip()
    catalog.update_proto_session_semester(session_id, semester)
    return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))


@app.post("/subjects/<subject_id>/sessions/<session_id>/update-pdf-header")
@require_role("editor")
def update_proto_session_pdf_header(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    pdf_title = request.form.get("pdf_title", "").strip()
    subtitle_mode = request.form.get("subtitle_mode", "default")
    if subtitle_mode == "none":
        pdf_subtitle = "__none__"
    elif subtitle_mode == "custom":
        pdf_subtitle = request.form.get("pdf_subtitle", "").strip() or "__none__"
    else:
        pdf_subtitle = ""
    catalog.save_proto_session_pdf_header(session_id, pdf_title, pdf_subtitle)
    return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))


@app.post("/subjects/<subject_id>/sessions/<session_id>/close")
@require_role("editor")
def close_proto_session(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    catalog.close_proto_session(session_id)
    flash("Session geschlossen.", "success")
    return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))


@app.post("/subjects/<subject_id>/sessions/<session_id>/reopen")
@require_role("editor")
def reopen_proto_session(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    if proto_sess["status"] == "released":
        flash("Freigegebene Sessions können nicht wieder geöffnet werden.", "error")
        return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))
    catalog.reopen_proto_session(session_id)
    flash("Session wieder geöffnet.", "success")
    return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))


@app.post("/subjects/<subject_id>/sessions/<session_id>/contributions/<contribution_id>/delete")
@require_role("editor")
def delete_proto_contribution(subject_id: str, session_id: str, contribution_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    contribution = catalog.get_proto_contribution_by_id(contribution_id)
    if not contribution or contribution["session_id"] != session_id:
        abort(404)
    catalog.delete_proto_contribution(contribution_id)
    return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))


@app.post("/subjects/<subject_id>/sessions/<session_id>/delete")
@require_role("editor")
def delete_proto_session(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    catalog.delete_proto_session(session_id)
    flash("Protokoll-Session gelöscht.", "success")
    return redirect(url_for("subject_detail", subject_id=subject_id))


@app.get("/subjects/<subject_id>/sessions/<session_id>")
@require_role("editor")
def proto_session_moderation(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    subject = catalog.get_subject(subject_id)
    contributions = catalog.get_proto_contributions(session_id)
    return render_template(
        "proto_session_moderation.html",
        session=proto_sess,
        subject=subject,
        contributions=contributions,
        session_url=f"{request.host_url}session/{proto_sess['token']}",
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.get("/subjects/<subject_id>/sessions/<session_id>/editor")
@require_role("editor")
def proto_session_editor(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    subject = catalog.get_subject(subject_id)
    contributions = catalog.get_proto_contributions(session_id)
    return render_template(
        "proto_session_editor.html",
        session=proto_sess,
        subject=subject,
        contributions=contributions,
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.post("/subjects/<subject_id>/sessions/<session_id>/editor/save")
@require_role("editor")
def save_proto_session_editor(subject_id: str, session_id: str):
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    content = (request.json or {}).get("content", "") if request.is_json else request.form.get("content", "")
    catalog.save_proto_session_editor(session_id, content)
    return {"ok": True}


@app.get("/subjects/<subject_id>/sessions/<session_id>/preview-pdf")
@require_role("editor")
def preview_proto_session_pdf(subject_id: str, session_id: str):
    from io import BytesIO
    from pdf_workflow import generate_proto_pdf
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    subject = catalog.get_subject(subject_id)
    buf = BytesIO()
    generate_proto_pdf(content=proto_sess["editor_content"], subject=subject, session=proto_sess, out_path=buf)
    buf.seek(0)
    return make_response(buf.read(), 200, {"Content-Type": "application/pdf", "Content-Disposition": "inline"})


@app.post("/subjects/<subject_id>/sessions/<session_id>/release")
@require_role("editor")
def release_proto_session(subject_id: str, session_id: str):
    from pdf_workflow import generate_proto_pdf
    proto_sess = catalog.get_proto_session_by_id(session_id)
    if not proto_sess or proto_sess["subject_id"] != subject_id:
        abort(404)
    if proto_sess["status"] != "closed":
        msg = "Session wurde bereits freigegeben." if proto_sess["status"] == "released" else "Nur geschlossene Sessions können freigegeben werden."
        flash(msg, "error")
        return redirect(url_for("proto_session_moderation", subject_id=subject_id, session_id=session_id))
    subject = catalog.get_subject(subject_id)
    subject_dir = catalog.subject_dir(subject_id)
    incoming_dir = subject_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt
    filename = f"{_dt.now().strftime('%Y%m%d-%H%M%S')}-protokoll.pdf"
    out_path = incoming_dir / filename
    generate_proto_pdf(content=proto_sess["editor_content"], subject=subject, session=proto_sess, out_path=out_path)
    metadata = {
        "kind": "Gedaechtnisprotokoll",
        "term": proto_sess["semester"],
        "exam_date": "",
        "instructor": "",
        "solution": "unbekannt",
        "notes": "",
        "original_filename": filename,
        "stored_upload": str(out_path.relative_to(subject_dir)),
        "added_pages": 0,
        "existing_body_pages": 0,
        "current_pages": 0,
        "export_path": "",
        "strip_uploaded_cover": False,
        "collection_import": False,
    }
    catalog.add_submission(subject_id, metadata)
    catalog.release_proto_session(session_id)
    return _regenerate_and_push(subject_id, f"Protokoll freigegeben und zur Sammlung hinzugefügt.")



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
