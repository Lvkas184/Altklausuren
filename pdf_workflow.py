from __future__ import annotations

import copy
import shutil
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


class PdfProcessingError(Exception):
    pass


def append_submission(
    *,
    subject: dict,
    subject_dir: Path,
    upload_path: Path,
    metadata: dict,
    strip_uploaded_cover: bool,
) -> dict:
    current_path = subject_dir / "current.pdf"
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    try:
        incoming_reader = PdfReader(str(upload_path))
    except Exception as exc:
        raise PdfProcessingError("Die hochgeladene Datei konnte nicht als PDF gelesen werden.") from exc

    if len(incoming_reader.pages) == 0:
        raise PdfProcessingError("Die hochgeladene PDF enthält keine Seiten.")

    uploaded_start = 1 if strip_uploaded_cover and len(incoming_reader.pages) > 1 else 0
    if strip_uploaded_cover and len(incoming_reader.pages) == 1:
        raise PdfProcessingError("Das Deckblatt kann nicht entfernt werden, weil die PDF nur eine Seite hat.")

    writer = PdfWriter()
    existing_body_pages = 0

    entries = list(subject.get("submissions", []))
    entries.append(metadata)
    cover_pdf = _build_cover(subject, entries)
    cover_reader = PdfReader(cover_pdf)
    writer.add_page(cover_reader.pages[0])

    if current_path.exists():
        backup_path = archive_dir / f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        shutil.copy2(current_path, backup_path)
        try:
            current_reader = PdfReader(str(current_path))
        except Exception as exc:
            raise PdfProcessingError("Die bestehende Sammlung konnte nicht als PDF gelesen werden.") from exc

        for page in current_reader.pages[1:]:
            writer.add_page(page)
            existing_body_pages += 1

    added_pages = 0
    for page in incoming_reader.pages[uploaded_start:]:
        writer.add_page(page)
        added_pages += 1

    exports_dir = subject_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_path = exports_dir / f"{subject['slug']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    with current_path.open("wb") as output:
        writer.write(output)
    shutil.copy2(current_path, export_path)
    generate_single_page_pdf(current_path)

    return {
        "stored_upload": str(upload_path.relative_to(subject_dir)),
        "added_pages": added_pages,
        "existing_body_pages": existing_body_pages,
        "current_pages": len(writer.pages),
        "export_path": str(export_path.relative_to(subject_dir)),
    }


def regenerate_current_pdf(*, subject: dict, subject_dir: Path) -> dict:
    current_path = subject_dir / "current.pdf"
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    submissions = sorted(
        subject.get("submissions", []),
        key=lambda item: (int(item.get("sort_order") or 0), item.get("added_at", ""), item.get("id", "")),
    )

    if current_path.exists():
        backup_path = archive_dir / f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        shutil.copy2(current_path, backup_path)

    if len(submissions) == 1 and submissions[0].get("collection_import"):
        source = _stored_upload_path(subject_dir, submissions[0])
        _validate_pdf(source)
        shutil.copy2(source, current_path)
        page_count = len(PdfReader(str(current_path)).pages)
        return {"current_pages": page_count, "regenerated": True, "preserved_import": True}

    writer = PdfWriter()
    cover_pdf = _build_cover(subject, submissions)
    cover_reader = PdfReader(cover_pdf)
    writer.add_page(cover_reader.pages[0])

    body_pages = 0
    for submission in submissions:
        upload_path = _stored_upload_path(subject_dir, submission)
        reader = _validate_pdf(upload_path)
        start = _body_start_for_submission(submission, len(reader.pages), len(submissions))
        for page in reader.pages[start:]:
            writer.add_page(page)
            body_pages += 1

    exports_dir = subject_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_path = exports_dir / f"{subject['slug']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    with current_path.open("wb") as output:
        writer.write(output)
    shutil.copy2(current_path, export_path)
    generate_single_page_pdf(current_path)

    return {
        "current_pages": len(writer.pages),
        "body_pages": body_pages,
        "export_path": str(export_path.relative_to(subject_dir)),
        "regenerated": True,
    }


def generate_single_page_pdf(current_path: Path) -> None:
    """Split 2-up landscape pages into individual portrait pages → single.pdf."""
    single_path = current_path.parent / "single.pdf"
    try:
        reader = PdfReader(str(current_path))
    except Exception:
        return
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if w > h:
            mid = w / 2.0
            left = copy.deepcopy(page)
            left.mediabox.left = 0
            left.mediabox.right = mid
            left.mediabox.bottom = 0
            left.mediabox.top = h
            left.cropbox.left = 0
            left.cropbox.right = mid
            left.cropbox.bottom = 0
            left.cropbox.top = h
            writer.add_page(left)
            right = copy.deepcopy(page)
            right.mediabox.left = mid
            right.mediabox.right = w
            right.mediabox.bottom = 0
            right.mediabox.top = h
            right.cropbox.left = mid
            right.cropbox.right = w
            right.cropbox.bottom = 0
            right.cropbox.top = h
            writer.add_page(right)
        else:
            writer.add_page(page)
    if writer.pages:
        with single_path.open("wb") as f:
            writer.write(f)


def _stored_upload_path(subject_dir: Path, submission: dict) -> Path:
    stored = submission.get("stored_upload", "")
    if not stored:
        raise PdfProcessingError("Ein Eintrag hat keine gespeicherte Upload-Datei.")
    path = Path(stored)
    if not path.is_absolute():
        path = subject_dir / path
    if not path.exists():
        raise PdfProcessingError(f"Die gespeicherte Upload-Datei fehlt: {stored}")
    return path


def _validate_pdf(path: Path) -> PdfReader:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise PdfProcessingError("Eine gespeicherte PDF konnte nicht gelesen werden.") from exc
    if len(reader.pages) == 0:
        raise PdfProcessingError("Eine gespeicherte PDF enthält keine Seiten.")
    return reader


def _body_start_for_submission(submission: dict, page_count: int, submission_count: int) -> int:
    if submission.get("collection_import") and submission_count > 1 and page_count > 1:
        return 1
    if submission.get("strip_uploaded_cover") and page_count > 1:
        return 1
    return 0


def _build_cover(subject: dict, entries: list[dict]) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left = 20 * mm
    top = height - 34 * mm

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 18)
    pdf.drawString(left, top, "Forum")
    pdf.drawString(left, top - 9 * mm, "Wirtschaftsinformatik")
    pdf.drawString(left, top - 18 * mm, "Karlsruher Institut f\u00fcr Technologie")

    pdf.setFont("Helvetica", 9)
    pdf.drawString(left, top - 29 * mm, "Kaiserstra\u00dfe 93, Geb. 05.20, Raum 3C-01  \u2022  76131 Karlsruhe")
    pdf.drawString(left, top - 36 * mm, "Telefon: (0721) 608 - 46879")
    pdf.drawString(left, top - 43 * mm, "Internet: www.forum-wi.de / altklausuren@forum-wi.de")

    logo = _template_logo()
    if logo:
        pdf.drawImage(logo, width - 72 * mm, top - 45 * mm, width=48 * mm, height=48 * mm, mask="auto")

    rule_y = top - 45 * mm
    pdf.setLineWidth(0.8)
    pdf.line(left, rule_y, width - 20 * mm, rule_y)

    title_y = rule_y - 24 * mm
    pdf.setFont("Helvetica", 36)
    pdf.drawString(left, title_y, subject["title"])
    pdf.setFont("Helvetica", 20)
    pdf.drawString(left, title_y - 13 * mm, "Klausurensammlung")

    _draw_exam_table(pdf, left + 3 * mm, title_y - 31 * mm, entries)

    footer_y = 18 * mm
    pdf.line(left, footer_y, width - 20 * mm, footer_y)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(left, footer_y - 6 * mm, subject["title"])

    pdf.save()
    buffer.seek(0)
    return buffer


def _draw_exam_table(pdf: canvas.Canvas, x: float, y_top: float, entries: list[dict]) -> None:
    headers = ["Pr\u00fcfungsdatum", "Dozent", "L\u00f6sung"]
    widths = [61 * mm, 64 * mm, 29 * mm]
    row_height = 7.55 * mm
    rows = max(13, min(18, len(entries) + 1))
    table_width = sum(widths)
    table_height = row_height * (rows + 1)

    pdf.setFillColor(colors.HexColor("#d9d9d9"))
    pdf.rect(x, y_top - row_height, table_width, row_height, fill=1, stroke=0)
    pdf.setFillColor(colors.black)

    pdf.setLineWidth(0.8)
    for index in range(rows + 2):
        y = y_top - index * row_height
        pdf.line(x, y, x + table_width, y)

    current_x = x
    pdf.line(current_x, y_top, current_x, y_top - table_height)
    for width in widths:
        current_x += width
        pdf.line(current_x, y_top, current_x, y_top - table_height)

    pdf.setFont("Helvetica-Bold", 14)
    current_x = x + 2 * mm
    for header, width in zip(headers, widths):
        pdf.drawString(current_x, y_top - 5.7 * mm, header)
        current_x += width

    pdf.setFont("Helvetica", 9)
    sorted_entries = sorted(entries, key=lambda entry: entry.get("exam_date") or entry.get("term") or "", reverse=True)
    for index, entry in enumerate(sorted_entries[:rows], start=1):
        row_y = y_top - index * row_height - 5.0 * mm
        exam_date = _display_exam_date(entry)
        instructor = entry.get("instructor") or entry.get("dozent") or ""
        solution = _display_solution(entry)
        values = [exam_date, instructor, solution]
        current_x = x + 2 * mm
        for value, width in zip(values, widths):
            pdf.drawString(current_x, row_y, _fit(value, width - 4 * mm, pdf, "Helvetica", 9))
            current_x += width


def _display_exam_date(entry: dict) -> str:
    if entry.get("exam_date"):
        try:
            return datetime.strptime(entry["exam_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            return entry["exam_date"]
    return entry.get("term") or ""


def _display_solution(entry: dict) -> str:
    value = (entry.get("solution") or entry.get("has_solution") or "").strip().lower()
    if value in {"yes", "ja", "true", "1", "mit loesung", "mit l\u00f6sung"}:
        return "Ja"
    if value in {"no", "nein", "false", "0", "ohne loesung", "ohne l\u00f6sung"}:
        return "Nein"
    return entry.get("solution") or ""


def _fit(value: str, width: float, pdf: canvas.Canvas, font: str, size: int) -> str:
    text = str(value)
    if pdf.stringWidth(text, font, size) <= width:
        return text
    while text and pdf.stringWidth(f"{text}...", font, size) > width:
        text = text[:-1]
    return f"{text}..." if text else ""


def _template_logo() -> ImageReader | None:
    template_path = Path(__file__).resolve().parent / "Deckblatt_Altklausuren.docx"
    if not template_path.exists():
        return None
    try:
        with zipfile.ZipFile(template_path) as archive:
            image = archive.read("word/media/image1.png")
        return ImageReader(BytesIO(image))
    except Exception:
        return None
