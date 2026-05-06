from __future__ import annotations

import sys
import os
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from auth import AuthConfig, AuthError, build_login_url, clear_user, current_user, handle_callback
from pdf_workflow import PdfProcessingError, append_submission
from drive_client import DriveSetupError
from drive_sync import load_drive_config, save_drive_config, sync_drive_folder, sync_local_folder
from storage import Catalog


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_LIMIT_MB = 80

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-altklausuren-local")
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT_MB * 1024 * 1024

catalog = Catalog(DATA_DIR)


@app.before_request
def require_login():
    auth_config = AuthConfig.from_env()
    if not auth_config.enabled:
        return None

    public_endpoints = {"login", "auth_callback", "logout", "static"}
    if request.endpoint in public_endpoints:
        return None

    if current_user():
        return None

    return redirect(url_for("login", next=request.full_path))


@app.get("/login")
def login():
    auth_config = AuthConfig.from_env()
    if not auth_config.enabled:
        flash("Login ist lokal deaktiviert.", "success")
        return redirect(url_for("index"))

    try:
        return redirect(build_login_url(auth_config))
    except AuthError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))


@app.get("/auth/callback")
def auth_callback():
    auth_config = AuthConfig.from_env()
    try:
        handle_callback(auth_config, request)
    except AuthError as exc:
        clear_user()
        flash(str(exc), "error")
        return redirect(url_for("login"))

    return redirect(url_for("index"))


@app.get("/logout")
def logout():
    clear_user()
    flash("Du wurdest abgemeldet.", "success")
    return redirect(url_for("login"))


@app.get("/")
def index():
    drive_config = load_drive_config(DATA_DIR)
    subjects = []
    for subject in catalog.list_subjects():
        current_path = catalog.subject_dir(subject["id"]) / "current.pdf"
        submissions = subject.get("submissions", [])
        latest = submissions[-1] if submissions else None
        subjects.append(
            subject
            | {
                "has_current_pdf": current_path.exists(),
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
        auth_enabled=AuthConfig.from_env().enabled,
        current_user=current_user(),
    )


@app.post("/subjects")
def create_subject():
    title = request.form.get("title", "").strip()
    code = request.form.get("code", "").strip()

    if not title:
        flash("Bitte gib einen Fachnamen ein.", "error")
        return redirect(url_for("index"))

    subject = catalog.create_subject(title=title, code=code)
    flash(f"{subject['title']} wurde angelegt.", "success")
    return redirect(url_for("index"))


@app.post("/submissions")
def create_submission():
    subject_id = request.form.get("subject_id", "").strip()
    uploaded = request.files.get("pdf")

    if not subject_id or not catalog.get_subject(subject_id):
        flash("Bitte waehle ein Fach aus.", "error")
        return redirect(url_for("index"))

    if not uploaded or not uploaded.filename:
        flash("Bitte waehle eine PDF-Datei aus.", "error")
        return redirect(url_for("index"))

    if not uploaded.filename.lower().endswith(".pdf"):
        flash("Es koennen aktuell nur PDF-Dateien verarbeitet werden.", "error")
        return redirect(url_for("index"))

    subject_dir = catalog.subject_dir(subject_id)
    incoming_dir = subject_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    source_name = secure_filename(uploaded.filename) or "upload.pdf"
    upload_path = incoming_dir / source_name
    uploaded.save(upload_path)

    metadata = {
        "kind": request.form.get("kind", "Gedaechtnisprotokoll").strip() or "Gedaechtnisprotokoll",
        "term": request.form.get("term", "").strip(),
        "exam_date": request.form.get("exam_date", "").strip(),
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
        return redirect(url_for("index"))

    catalog.add_submission(subject_id, metadata | result)
    flash("PDF wurde eingeordnet und die Sammlung wurde neu erzeugt.", "success")
    return redirect(url_for("index"))


@app.post("/drive/config")
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


@app.get("/subjects/<subject_id>/current.pdf")
def current_pdf(subject_id: str):
    return _send_current_pdf(subject_id, as_attachment=True)


@app.get("/subjects/<subject_id>/preview.pdf")
def preview_pdf(subject_id: str):
    return _send_current_pdf(subject_id, as_attachment=False)


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
