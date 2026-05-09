from __future__ import annotations

import sys
import os
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from auth import AuthConfig, AuthError, build_login_url, clear_user, current_user, handle_callback, user_from_forward_auth
from pdf_workflow import PdfProcessingError, append_submission, generate_single_page_pdf, regenerate_current_pdf
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
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-altklausuren-local")
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
                flash("Dafuer fehlen dir die Rechte.", "error")
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

    public_endpoints = {"healthz", "favicon", "login", "google_login", "auth_callback", "logout", "static"}
    if request.endpoint in public_endpoints:
        return None

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
        try:
            forwarded_user = user_from_forward_auth(auth_config, request)
        except AuthError as exc:
            flash(str(exc), "error")
            forwarded_user = None
        if forwarded_user:
            session["user"] = forwarded_user
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


@app.get("/logout")
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
        flash("PDF wurde lokal erzeugt, aber Drive wurde nicht ueberschrieben, weil dort eine neuere Aenderung liegt.", "error")
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
    return _regenerate_and_push(subject_id, "Eintrag wurde geloescht.")


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
        flash("Die importierte PDF enthaelt keine Seiten.", "error")
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
        flash(f"{success_message} Drive wurde wegen eines Konflikts nicht ueberschrieben.", "error")
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
        f"{result['imported']} importiert, {result['skipped']} unveraendert.",
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
        f"{result['imported']} importiert, {result['skipped']} unveraendert.",
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
        f"Drive-Sync geprueft: {result['checked']} verknuepfte Faecher, "
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
