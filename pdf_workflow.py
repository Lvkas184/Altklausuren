from __future__ import annotations

import shutil
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


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
        raise PdfProcessingError("Die hochgeladene PDF enthaelt keine Seiten.")

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

    return {
        "stored_upload": str(upload_path.relative_to(subject_dir)),
        "added_pages": added_pages,
        "existing_body_pages": existing_body_pages,
        "current_pages": len(writer.pages),
        "export_path": str(export_path.relative_to(subject_dir)),
    }


def _build_cover(subject: dict, entries: list[dict]) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 24 * mm
    y = height - 28 * mm

    pdf.setFillColor(colors.HexColor("#1f3a3d"))
    pdf.rect(0, height - 44 * mm, width, 44 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(margin_x, y, "Altklausurensammlung")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(margin_x, y - 9 * mm, subject["title"])

    y -= 38 * mm
    pdf.setFillColor(colors.HexColor("#2b2f33"))
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin_x, y, "Stand")
    pdf.setFont("Helvetica", 11)
    y -= 9 * mm
    pdf.drawString(margin_x, y, f"Aktualisiert am {date.today().strftime('%d.%m.%Y')}")
    y -= 7 * mm
    pdf.drawString(margin_x, y, f"Eintraege: {len(entries)}")
    if subject.get("code"):
        y -= 7 * mm
        pdf.drawString(margin_x, y, f"Modul: {subject['code']}")

    y -= 18 * mm
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin_x, y, "Inhalt")
    y -= 8 * mm

    styles = getSampleStyleSheet()
    style = styles["BodyText"]
    style.fontName = "Helvetica"
    style.fontSize = 10
    style.leading = 13

    if entries:
        for index, entry in enumerate(entries, start=1):
            label = entry.get("kind") or "Eintrag"
            term = entry.get("term") or "ohne Semester"
            exam_date = entry.get("exam_date") or "ohne Datum"
            notes = entry.get("notes") or ""
            line = f"{index}. {label} - {term} - {exam_date}"
            if notes:
                line += f"<br/><font color='#5a6470'>{notes}</font>"
            paragraph = Paragraph(line, style)
            available_width = width - 2 * margin_x
            paragraph_width, paragraph_height = paragraph.wrap(available_width, 22 * mm)
            if y - paragraph_height < 24 * mm:
                remaining = len(entries) - index + 1
                pdf.setFillColor(colors.HexColor("#5a6470"))
                pdf.setFont("Helvetica", 10)
                pdf.drawString(margin_x, y, f"... plus {remaining} weitere Eintraege")
                break
            paragraph.drawOn(pdf, margin_x, y - paragraph_height)
            y -= paragraph_height + 5 * mm
    else:
        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin_x, y, "Noch keine Eintraege vorhanden.")

    pdf.setFillColor(colors.HexColor("#6b7280"))
    pdf.setFont("Helvetica", 8)
    pdf.drawString(margin_x, 15 * mm, "Automatisch erzeugt durch die Altklausuren-App.")
    pdf.save()
    buffer.seek(0)
    return buffer
